"""Per-90 event rates with empirical-Bayes shrinkage.

For each player (identified by stable ``player_code`` so history crosses
seasons) we estimate time-decayed per-90 rates for the events the scoring
rules pay for: xG, xA, saves, bonus, yellow cards — plus a **residual rate**:
the per-90 difference between actual FPL points and the points reconstructed
from modelled events via the scoring engine. The residual absorbs DefCon
(tackles/CBI are not in the database) and any other unmodelled scraps, and is
strongly shrunk toward its position prior.

Shrinkage: rate = (Σ w·stat + k·prior_pos) / (Σ w·mins/90 + k) — a player with
little recent playing time regresses to his position's league rate instead of
producing wild small-sample estimates. Priors are computed from the data at
fit time, never hardcoded.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from .. import scoring

HALF_LIFE_DAYS = 240.0
K_EFFECTIVE_90S = 6.0
STATS = ["xg", "xa", "saves", "bonus", "yellow_cards", "residual"]


def _parse(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True, format="ISO8601")


def fit(conn, season: str, as_of: str, *, rules: dict | None = None) -> pd.DataFrame:
    """Return one row per current-season player with shrunk per-90 rates.

    Uses all player_gw history strictly before ``as_of`` across seasons.
    """
    rules = rules or scoring.load_rules()
    hist = pd.read_sql_query(
        "SELECT pg.player_code, pg.kickoff_utc, pg.minutes, pg.total_points, "
        "pg.goals_scored, pg.assists, pg.clean_sheets, pg.goals_conceded, "
        "pg.own_goals, pg.penalties_saved, pg.penalties_missed, "
        "pg.yellow_cards, pg.red_cards, pg.saves, pg.bonus, pg.xg, pg.xa, "
        "(SELECT position FROM player p WHERE p.season=pg.season "
        " AND p.player_id=pg.player_id) position "
        "FROM player_gw pg WHERE pg.kickoff_utc < ? AND pg.minutes > 0",
        conn, params=(as_of,))
    players = pd.read_sql_query(
        "SELECT player_id, code player_code, position FROM player "
        "WHERE season=?", conn, params=(season,))
    if hist.empty:
        out = players.copy()
        for s in STATS:
            out[f"{s}90"] = 0.0
        return out

    # residual = actual points - reconstruction from the events we model
    # (defcon zeroed) -> per-match unmodelled points, mostly DefCon
    def _base_points(r):
        return scoring.points_without_defcon({
            "minutes": r.minutes, "goals_scored": r.goals_scored,
            "assists": r.assists, "clean_sheets": r.clean_sheets,
            "goals_conceded": r.goals_conceded, "own_goals": r.own_goals,
            "penalties_saved": r.penalties_saved,
            "penalties_missed": r.penalties_missed,
            "yellow_cards": r.yellow_cards, "red_cards": r.red_cards,
            "saves": r.saves, "bonus": r.bonus,
        }, r.position or "MID", rules=rules)

    hist["residual"] = (hist["total_points"].fillna(0)
                        - np.array([_base_points(r) for r in hist.itertuples()]))
    # xg missing (old seasons without Understat/Opta) -> fall back to goals
    hist["xg"] = hist["xg"].fillna(hist["goals_scored"]).fillna(0)
    # FPL assists are much broader than Opta xA (rebounds, won penalties,
    # deflected passes all count), so pure xA systematically lowballs the
    # assist rate (GW1 2026-27: league xA 15.4 vs 24 FPL assists). Blend the
    # stable estimator with the realised FPL-definition rate 50/50.
    hist["xa"] = np.where(hist["xa"].notna(),
                          0.5 * hist["xa"].fillna(0) + 0.5 * hist["assists"].fillna(0),
                          hist["assists"].fillna(0))

    ref = pd.Timestamp(datetime.fromisoformat(as_of.replace("Z", "+00:00")))
    days = (ref - _parse(hist["kickoff_utc"])).dt.days.clip(lower=0)
    hist["w"] = 0.5 ** (days / HALF_LIFE_DAYS)
    hist["w90"] = hist["w"] * hist["minutes"].fillna(0) / 90.0

    for s in STATS:
        hist[f"_w_{s}"] = hist["w"] * hist[s].fillna(0)

    agg = hist.groupby("player_code").agg(
        exposure=("w90", "sum"),
        **{f"sum_{s}": (f"_w_{s}", "sum") for s in STATS})

    # position priors: league per-90 rate per position (exposure-weighted)
    hist_pos = hist.dropna(subset=["position"])
    prior = {}
    for s in STATS:
        by_pos = hist_pos.groupby("position").apply(
            lambda d, s=s: (d["w"] * d[s].fillna(0)).sum()
            / max(1e-9, d["w90"].sum()), include_groups=False)
        prior[s] = by_pos.to_dict()

    out = players.merge(agg, left_on="player_code", right_index=True, how="left")
    out["exposure"] = out["exposure"].fillna(0.0)
    for s in STATS:
        pri = out["position"].map(prior[s]).fillna(0.0)
        out[f"{s}90"] = ((out[f"sum_{s}"].fillna(0) + K_EFFECTIVE_90S * pri)
                         / (out["exposure"] + K_EFFECTIVE_90S))
    return out[["player_id", "player_code", "position", "exposure"]
               + [f"{s}90" for s in STATS]]
