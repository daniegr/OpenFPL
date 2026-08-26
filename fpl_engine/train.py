"""Optional: retrain position-specific point models on the SQLite feature store.

Why
---
The shipped OpenFPL models have frozen weights. Re-running ``pull`` each week
already feeds fresh *form* into them (the features update), but the mapping from
features to points never changes. This module lets the mapping adapt too — it
refits position-specific XGBoost regressors on the accumulated, point-in-time
feature store and (at inference) **blends** them with the original OpenFPL
models, weighting the fresh model up as the new season accrues data. This is the
principled way to absorb rule changes (e.g. the 2026/27 BPS rework).

Discipline
----------
* **Forward-in-time only.** Training samples are built with the same point-in-time
  builder used for prediction, so feature row X at (season, gw) sees only matches
  before that gameweek. Validation holds out the latest season — never a random
  split.
* **Same feature space as OpenFPL.** We reuse OpenFPL's fitted ``xscaler`` and the
  per-position feature subset, so a retrained model consumes identical inputs and
  its predictions blend cleanly with the originals.

GPU
---
XGBoost trains on the GPU automatically when one is detected
(``device="cuda"``), falling back to CPU. Override with ``$FPL_DEVICE``.
"""
from __future__ import annotations

import json
import os
import subprocess

import numpy as np
import pandas as pd

from . import config, db, features, predict as predict_mod, progress

RETRAINED_DIR = os.path.join(config.MODELS_DIR, "retrained")
POSITIONS = ["GK", "DEF", "MID", "FWD"]

# Reasonable, lightly-regularised defaults for this small, noisy data regime.
XGB_PARAMS = dict(n_estimators=500, max_depth=4, learning_rate=0.03,
                  subsample=0.8, colsample_bytree=0.7, reg_lambda=1.0,
                  min_child_weight=5, tree_method="hist")


def detect_device() -> str:
    """Return 'cuda' if a GPU is available, else 'cpu'. Override via $FPL_DEVICE."""
    forced = os.environ.get("FPL_DEVICE")
    if forced:
        return forced
    try:
        out = subprocess.run(["nvidia-smi", "-L"], capture_output=True,
                             text=True, timeout=5)
        if out.returncode == 0 and "GPU" in out.stdout:
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _label_map(conn, season: str) -> dict[tuple, float]:
    """(season, gw, player_id) -> actual FPL points that gameweek (double-gw summed)."""
    rows = conn.execute(
        "SELECT gw, player_id, SUM(COALESCE(total_points,0)) pts FROM player_gw "
        "WHERE season=? GROUP BY gw, player_id", (season,))
    return {(season, r["gw"], r["player_id"]): r["pts"] for r in rows}


def build_training_frame(conn, seasons: list[str] | None = None, *, min_gw: int = 2,
                         max_gw: int = 38, gw_step: int = 1) -> pd.DataFrame:
    """Assemble a point-in-time (X, y) frame across seasons.

    X = the 228 OpenFPL features at (season, gw); y = the player's actual points
    that gameweek. ``gw_step`` subsamples gameweeks to trade coverage for speed.
    """
    seasons = seasons or config.BACKFILL_SEASONS
    frames = []
    for season in seasons:
        labels = _label_map(conn, season)
        avail = [r["gw"] for r in conn.execute(
            "SELECT DISTINCT gw FROM team_match WHERE season=? AND gw IS NOT NULL "
            "AND gw>=? AND gw<=? ORDER BY gw", (season, min_gw, max_gw))]
        gws = avail[::gw_step]
        progress.step(f"Training frame: {season} ({len(gws)} gameweeks)…")
        for gw in gws:
            try:
                df = features.build_samples(conn, season, gw, include_ids=True)
            except ValueError:
                continue
            if df.empty:
                continue
            df = df.copy()
            df["y"] = [labels.get((season, gw, int(pid)), 0.0) for pid in df["player_id"]]
            df["season_built"] = season
            df["gw_built"] = gw
            frames.append(df)
    if not frames:
        raise SystemExit("No training data. Run `python -m fpl_engine pull` first.")
    return pd.concat(frames, ignore_index=True)


def _scaled_X(df: pd.DataFrame, position: str, bundle) -> np.ndarray:
    """Scale features exactly as predict.predict does (OpenFPL feature space)."""
    _, xscaler, _, feats = bundle
    xfeatures = list(xscaler.feature_names_in_)
    data = df[xfeatures].to_numpy()
    scaled = np.nan_to_num(
        xscaler.transform(np.nan_to_num(data).astype("float32"))).astype("float32")
    idx = [xfeatures.index(f) for f in feats[position]]
    return scaled[:, idx]


def train(conn, *, seasons: list[str] | None = None, valid_season: str | None = None,
          gw_step: int = 1, out_dir: str = RETRAINED_DIR, bundle=None,
          device: str | None = None) -> dict:
    """Retrain per-position models with forward-in-time validation; save them."""
    import xgboost as xgb

    seasons = seasons or config.BACKFILL_SEASONS
    device = device or detect_device()
    progress.step(f"Retraining on {seasons} using device='{device}'")
    bundle = bundle or predict_mod.load_models()

    frame = build_training_frame(conn, seasons, gw_step=gw_step)
    # Forward-in-time split: hold out the latest season for validation.
    valid_season = valid_season or max(seasons)
    train_df = frame[frame["season_built"] != valid_season]
    valid_df = frame[frame["season_built"] == valid_season]

    os.makedirs(out_dir, exist_ok=True)
    metrics = {}
    for pos in POSITIONS:
        tr = train_df[train_df["position"] == pos]
        va = valid_df[valid_df["position"] == pos]
        if len(tr) < 200:
            progress.log(f"    {pos}: too few samples ({len(tr)}), skipping")
            continue
        Xtr, ytr = _scaled_X(tr, pos, bundle), tr["y"].to_numpy()
        model = xgb.XGBRegressor(device=device, **XGB_PARAMS)
        model.fit(Xtr, ytr)
        model.save_model(os.path.join(out_dir, f"{pos}.json"))
        m = {"n_train": int(len(tr))}
        if len(va) > 50:
            m.update(_stratified_rmse(va["y"].to_numpy(),
                                      model.predict(_scaled_X(va, pos, bundle))))
        metrics[pos] = m
        progress.step(f"    {pos}: trained on {len(tr)} rows  {m}")

    from .http import utcnow_iso
    meta = {"seasons": seasons, "valid_season": valid_season, "device": device,
            "trained_at": utcnow_iso(),
            "params": XGB_PARAMS, "metrics": metrics,
            "features": {p: list(bundle[3][p]) for p in POSITIONS}}
    with open(os.path.join(out_dir, "meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2, default=str)
    return meta


def _stratified_rmse(y_true, y_pred) -> dict:
    """RMSE by return category (OpenFPL convention) — the metric that matters."""
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    bins = {"Zeros": y_true <= 0, "Blanks": (y_true > 0) & (y_true <= 2),
            "Tickers": (y_true >= 3) & (y_true <= 4), "Haulers": y_true >= 5}
    out = {}
    for name, mask in bins.items():
        if mask.sum():
            out[f"rmse_{name}"] = float(np.sqrt(np.mean(
                (y_true[mask] - y_pred[mask]) ** 2)))
    out["rmse_all"] = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
    return out


def load_retrained(out_dir: str = RETRAINED_DIR):
    """Load retrained per-position models, or None if not trained yet."""
    import xgboost as xgb
    if not os.path.exists(os.path.join(out_dir, "meta.json")):
        return None
    models = {}
    for pos in POSITIONS:
        path = os.path.join(out_dir, f"{pos}.json")
        if os.path.exists(path):
            m = xgb.XGBRegressor()
            m.load_model(path)
            models[pos] = m
    return models or None


def season_blend_weight(conn, season: str, *, cap: float = 0.5, ramp: int = 19) -> float:
    """Blend weight for the retrained model, growing with in-season data.

    0 before any current-season match (trust the pretrained models), rising to
    ``cap`` around mid-season as the fresh model gathers new-season evidence.
    """
    n = conn.execute(
        "SELECT COUNT(DISTINCT gw) n FROM player_gw WHERE season=? AND source='fpl'",
        (season,)).fetchone()["n"]
    return min(cap, (n or 0) / ramp * cap)
