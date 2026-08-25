"""Forward-in-time backtest: replay past gameweeks and score every model
against what actually happened, with decision-relevant metrics.

Models compared per gameweek (all strictly point-in-time):
  xpts     — the component engine (fpl_engine.xpts)
  openfpl  — the frozen OpenFPL ensemble (on a subsample of gws; its feature
             build is expensive)
  ppg      — points-per-appearance so far (naive baseline)
  trail4   — mean of the last 4 gameweek scores (form baseline)

Metrics per gameweek:
  spearman     rank correlation between prediction and actual points
  p_at_20      |top-20 predicted ∩ top-20 actual| / 20
  captain      actual points of the #1 predicted player
  rmse         plain error magnitude (least decision-relevant, still reported)

The minutes classifier is (re)trained only on seasons *before* the backtest
season. The OpenFPL/xpts blend weight is fitted on the first half of the
season and evaluated on the second, then saved to models/xpts/blend.json for
live use.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from . import config, db, features, predict as predict_mod, progress, scoring
from .xpts import engine as xpts_engine, minutes_model

BLEND_PATH = os.path.join(config.MODELS_DIR, "xpts", "blend.json")


def _actuals(conn, season: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT gw, player_id, SUM(total_points) pts, SUM(minutes) mins "
        "FROM player_gw WHERE season=? GROUP BY gw, player_id",
        conn, params=(season,))


def _openfpl_gw(conn, season: str, gw: int, bundle) -> pd.DataFrame | None:
    """OpenFPL predictions with player ids, no availability multiplier."""
    try:
        df = features.build_samples(conn, season, gw, include_ids=True)
    except ValueError:
        return None
    preds = predict_mod.predict(df, bundle=bundle)
    merged = preds.merge(
        df[["player", "team", "position", "player_id"]].drop_duplicates(
            ["player", "team", "position"]),
        on=["player", "team", "position"], how="left")
    out = merged[["player_id", "prediction"]].dropna()
    out["player_id"] = out["player_id"].astype(int)
    return out


def _metrics(pred: pd.DataFrame, actual_gw: pd.DataFrame) -> dict | None:
    j = actual_gw.merge(pred, on="player_id", how="left")
    j["prediction"] = j["prediction"].fillna(0.0)
    if len(j) < 30 or j["prediction"].std() < 1e-9:
        return None
    rho = float(spearmanr(j["prediction"], j["pts"]).statistic)
    top_pred = set(j.nlargest(20, "prediction")["player_id"])
    top_act = set(j.nlargest(20, "pts")["player_id"])
    cap_row = j.loc[j["prediction"].idxmax()]
    return {
        "spearman": rho,
        "p_at_20": len(top_pred & top_act) / 20.0,
        "captain": float(cap_row["pts"]),
        "captain_best": float(j["pts"].max()),
        "rmse": float(np.sqrt(((j["prediction"] - j["pts"]) ** 2).mean())),
    }


def run(conn, season: str = "2025-26", *, gws: list[int] | None = None,
        openfpl_every: int = 4, retrain_minutes: bool = False,
        with_openfpl: bool = True) -> dict:
    train_seasons = [s for s in config.BACKFILL_SEASONS if s < season]
    if retrain_minutes or minutes_model.load()[0] is None:
        progress.step(f"Training minutes model on {train_seasons}…")
        meta = minutes_model.train(conn, seasons=train_seasons)
        progress.step(f"  holdout accuracy ({meta['valid_season']}): "
                      f"{meta['holdout_accuracy']:.3f}")
    clf, meta = minutes_model.load()

    actual = _actuals(conn, season)
    all_gws = sorted(int(g) for g in actual["gw"].dropna().unique())
    gws = [int(g) for g in gws] if gws else [g for g in all_gws if g >= 2]
    rules = scoring.load_rules()
    bundle = predict_mod.load_models() if with_openfpl else None

    per_model: dict[str, dict[int, dict]] = {}
    preds_store: dict[tuple[str, int], pd.DataFrame] = {}
    cum: dict[int, list] = {}
    trail: dict[int, list] = {}

    for g in gws:
        as_of = xpts_engine.first_kickoff(conn, season, g)
        act_g = actual[actual["gw"] == g][["player_id", "pts"]]
        progress.step(f"GW{g}…")

        x = xpts_engine.xpts_predict_gw(conn, season, g, as_of=as_of,
                                        use_availability=False,
                                        minutes_bundle=(clf, meta), rules=rules)
        if not x.empty:
            p = x[["player_id", "prediction"]]
            preds_store[("xpts", g)] = p
            m = _metrics(p, act_g)
            if m:
                per_model.setdefault("xpts", {})[g] = m

        # naive baselines from accumulated actuals
        hist = actual[actual["gw"] < g]
        played = hist[hist["mins"] > 0]
        ppg = (played.groupby("player_id")["pts"].mean().rename("prediction")
               .reset_index())
        m = _metrics(ppg, act_g)
        if m:
            per_model.setdefault("ppg", {})[g] = m
        t4 = (hist[hist["gw"] >= g - 4].groupby("player_id")["pts"].mean()
              .rename("prediction").reset_index())
        m = _metrics(t4, act_g)
        if m:
            per_model.setdefault("trail4", {})[g] = m

        if with_openfpl and (g - gws[0]) % openfpl_every == 0:
            o = _openfpl_gw(conn, season, g, bundle)
            if o is not None and not o.empty:
                preds_store[("openfpl", g)] = o
                m = _metrics(o, act_g)
                if m:
                    per_model.setdefault("openfpl", {})[g] = m

    # ---- blend fit: first half picks w, second half judges it ----
    blend_info = None
    ogws = sorted(g for (name, g) in preds_store if name == "openfpl")
    both = [g for g in ogws if ("xpts", g) in preds_store]
    if len(both) >= 4:
        half = both[:len(both) // 2]
        rest = both[len(both) // 2:]

        def blended_rho(w, sel):
            vals = []
            for g in sel:
                o = preds_store[("openfpl", g)].rename(columns={"prediction": "o"})
                x = preds_store[("xpts", g)].rename(columns={"prediction": "x"})
                jj = o.merge(x, on="player_id", how="outer").fillna(0)
                jj["prediction"] = (1 - w) * jj["o"] + w * jj["x"]
                m = _metrics(jj[["player_id", "prediction"]],
                             actual[actual["gw"] == g][["player_id", "pts"]])
                if m:
                    vals.append(m["spearman"])
            return float(np.mean(vals)) if vals else -1.0

        grid = [round(w, 2) for w in np.arange(0, 1.01, 0.1)]
        best_w = max(grid, key=lambda w: blended_rho(w, half))
        blend_info = {
            "weight": best_w, "season": season,
            "fit_gws": half, "eval_gws": rest,
            "eval_spearman": {"openfpl": blended_rho(0.0, rest),
                              "xpts": blended_rho(1.0, rest),
                              "blend": blended_rho(best_w, rest)},
        }
        os.makedirs(os.path.dirname(BLEND_PATH), exist_ok=True)
        with open(BLEND_PATH, "w", encoding="utf-8") as fh:
            json.dump(blend_info, fh, indent=2)

    summary = {}
    for name, res in per_model.items():
        arr = list(res.values())
        summary[name] = {k: round(float(np.mean([m[k] for m in arr])), 4)
                         for k in arr[0]}
        summary[name]["gws"] = len(arr)
    report = {"season": season, "gws": gws, "summary": summary,
              "blend": blend_info,
              "minutes_holdout_accuracy": meta.get("holdout_accuracy")}
    out_path = os.path.join(config.DATA_DIR, f"backtest_{season}.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({**report, "per_gw": {m: {str(g): v for g, v in res.items()}
                                        for m, res in per_model.items()}},
                  fh, indent=2, default=float)
    return report
