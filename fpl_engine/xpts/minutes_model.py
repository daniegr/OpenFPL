"""Minutes model: P(plays 0 / 1-59 / 60+) per player per team-match.

Most week-to-week prediction error in FPL is minutes, not form. This model
classifies each player-match into three bands from purely point-in-time
features (start streaks, recent minutes share, appearance recency), trained on
full historical seasons where zero-minute rows are present for every squad
member.

The trained classifier is cached in models/xpts/ (retrain with
``python -m fpl_engine backtest --retrain-minutes`` or whenever the cache is
missing). Availability (FPL's status/chance_next) is applied *on top* of the
model at prediction time: the played-probability mass is scaled by the
availability and the remainder moved to the 0-minutes class.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from .. import config

MODEL_DIR = os.path.join(config.MODELS_DIR, "xpts")
MODEL_PATH = os.path.join(MODEL_DIR, "minutes_xgb.json")
META_PATH = os.path.join(MODEL_DIR, "minutes_meta.json")

FEATURES = ["starts_l5", "mins_l1", "mins_l3", "mins_l5", "avg_mins_when_played",
            "apps_l10", "since_last_app", "season_min_share",
            "is_gk", "is_def", "is_mid", "is_fwd"]
LABELS = {0: "none", 1: "sub", 2: "full"}   # 0 min / 1-59 / 60+


def _frame(conn, seasons: list[str], before: str | None = None) -> pd.DataFrame:
    """One row per player-fixture with point-in-time features + label.

    Rows are ordered per player by kickoff; every feature uses shifted
    (strictly prior) values only.
    """
    q = ("SELECT season, player_id, player_code, fixture_id, kickoff_utc, "
         "minutes, starts, "
         "(SELECT position FROM player p WHERE p.season=pg.season AND "
         " p.player_id=pg.player_id) position "
         "FROM player_gw pg WHERE season IN (%s)" %
         ",".join("?" * len(seasons)))
    args = list(seasons)
    if before:
        q += " AND kickoff_utc < ?"
        args.append(before)
    df = pd.read_sql_query(q, conn, params=args)
    if df.empty:
        return df
    df["minutes"] = df["minutes"].fillna(0)
    df["played"] = (df["minutes"] > 0).astype(float)
    df["started"] = df["starts"].fillna(0).astype(float)
    df = df.sort_values(["player_code", "kickoff_utc"]).reset_index(drop=True)

    g = df.groupby("player_code", sort=False)
    df["starts_l5"] = g["started"].transform(
        lambda s: s.shift(1).rolling(5, min_periods=1).sum())
    for n in (1, 3, 5):
        df[f"mins_l{n}"] = g["minutes"].transform(
            lambda s, n=n: s.shift(1).rolling(n, min_periods=1).mean())
    played_mins = df["minutes"].where(df["played"] > 0)
    df["_pm"] = played_mins
    df["avg_mins_when_played"] = g["_pm"].transform(
        lambda s: s.shift(1).expanding().mean())
    df["apps_l10"] = g["played"].transform(
        lambda s: s.shift(1).rolling(10, min_periods=1).sum())
    # matches since last appearance
    def _since(s):
        out, count = [], 99.0
        for v in s:
            out.append(count)
            count = 0.0 if v > 0 else min(99.0, count + 1)
        return pd.Series(out, index=s.index)
    df["since_last_app"] = g["played"].transform(_since)
    cum_min = g["minutes"].transform(lambda s: s.shift(1).expanding().sum())
    cum_n = g["minutes"].transform(lambda s: s.shift(1).expanding().count())
    df["season_min_share"] = cum_min / (cum_n * 90.0)
    for pos, col in (("GK", "is_gk"), ("DEF", "is_def"),
                     ("MID", "is_mid"), ("FWD", "is_fwd")):
        df[col] = (df["position"] == pos).astype(float)
    df["label"] = np.select([df["minutes"] >= 60, df["minutes"] > 0], [2, 1], 0)
    return df.drop(columns=["_pm"])


def train(conn, *, seasons: list[str] | None = None,
          device: str | None = None) -> dict:
    """Train and cache the classifier; returns metadata with holdout accuracy."""
    import xgboost as xgb
    seasons = seasons or config.BACKFILL_SEASONS
    train_seasons, valid_season = seasons[:-1], seasons[-1]
    tr = _frame(conn, train_seasons)
    va = _frame(conn, [valid_season])
    clf = xgb.XGBClassifier(
        objective="multi:softprob", num_class=3, n_estimators=300, max_depth=5,
        learning_rate=0.06, subsample=0.9, colsample_bytree=0.8,
        eval_metric="mlogloss", device=device or "cpu")
    clf.fit(tr[FEATURES], tr["label"])
    acc = float((clf.predict(va[FEATURES]) == va["label"]).mean()) if len(va) else None
    # class-conditional mean minutes (for E[min] reconstruction)
    m_sub = float(tr.loc[tr.label == 1, "minutes"].mean() or 30.0)
    m_full = float(tr.loc[tr.label == 2, "minutes"].mean() or 84.0)
    os.makedirs(MODEL_DIR, exist_ok=True)
    clf.save_model(MODEL_PATH)
    meta = {"features": FEATURES, "train_seasons": train_seasons,
            "valid_season": valid_season, "holdout_accuracy": acc,
            "mean_minutes": {"sub": m_sub, "full": m_full}}
    with open(META_PATH, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
    return meta


def load():
    import xgboost as xgb
    if not (os.path.exists(MODEL_PATH) and os.path.exists(META_PATH)):
        return None, None
    clf = xgb.XGBClassifier()
    clf.load_model(MODEL_PATH)
    with open(META_PATH, encoding="utf-8") as fh:
        meta = json.load(fh)
    return clf, meta


def predict_gw(conn, season: str, as_of: str, clf, meta, *,
               use_availability: bool = True) -> pd.DataFrame:
    """P(none/sub/full) for every player of ``season`` from history < as_of.

    Feature rows are built from each player's most recent point-in-time state
    (cross-season via player_code, so early-season windows reach back).
    ``use_availability`` applies the *current* FPL status/chance_next on top —
    right for live predictions, wrong for historical backtests (the stored
    status is today's, not that gameweek's), so backtests disable it.
    """
    seasons = list(dict.fromkeys(config.BACKFILL_SEASONS + [season]))
    hist = _frame(conn, seasons, before=as_of)
    players = pd.read_sql_query(
        "SELECT player_id, code player_code, position, status, chance_next "
        "FROM player WHERE season=?", conn, params=(season,))
    if hist.empty:
        latest = pd.DataFrame(columns=FEATURES)
        feats = players.assign(**{f: np.nan for f in FEATURES})
    else:
        # roll each player's state one step forward past their last match
        last = hist.groupby("player_code", sort=False).tail(1).copy()
        last["starts_l5"] = hist.groupby("player_code")["started"].apply(
            lambda s: s.tail(5).sum()).reindex(last["player_code"]).values
        for n in (1, 3, 5):
            last[f"mins_l{n}"] = hist.groupby("player_code")["minutes"].apply(
                lambda s, n=n: s.tail(n).mean()).reindex(last["player_code"]).values
        pm = hist[hist["played"] > 0].groupby("player_code")["minutes"].mean()
        last["avg_mins_when_played"] = pm.reindex(last["player_code"]).values
        last["apps_l10"] = hist.groupby("player_code")["played"].apply(
            lambda s: s.tail(10).sum()).reindex(last["player_code"]).values
        gap = hist.groupby("player_code")["played"].apply(
            lambda s: 0.0 if s.iloc[-1] > 0 else min(
                99.0, float((s[::-1] == 0).cummin().sum())))
        last["since_last_app"] = gap.reindex(last["player_code"]).values
        tot = hist.groupby("player_code")["minutes"].agg(["sum", "count"])
        last["season_min_share"] = (tot["sum"] / (tot["count"] * 90.0)
                                    ).reindex(last["player_code"]).values
        latest = last.set_index("player_code")[
            [f for f in FEATURES if not f.startswith("is_")]]
        feats = players.merge(latest, left_on="player_code", right_index=True,
                              how="left")
    for pos, col in (("GK", "is_gk"), ("DEF", "is_def"),
                     ("MID", "is_mid"), ("FWD", "is_fwd")):
        feats[col] = (feats["position"] == pos).astype(float)

    proba = clf.predict_proba(feats[FEATURES].astype(float))
    out = players[["player_id", "position"]].copy()
    out["p_none"], out["p_sub"], out["p_full"] = proba[:, 0], proba[:, 1], proba[:, 2]

    if use_availability:
        # availability overlay: scale played mass, dump remainder on p_none
        avail = feats["chance_next"].where(
            feats["chance_next"].notna(),
            feats["status"].isin([None, "a"]).astype(float)).clip(0, 1).fillna(1.0)
        out["p_sub"] *= avail
        out["p_full"] *= avail
        out["p_none"] = 1.0 - out["p_sub"] - out["p_full"]
    out["e_min"] = (out["p_sub"] * meta["mean_minutes"]["sub"]
                    + out["p_full"] * meta["mean_minutes"]["full"])
    return out
