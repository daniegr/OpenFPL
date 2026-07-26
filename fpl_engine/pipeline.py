"""High-level pipeline orchestration used by the CLI.

One place that wires ingest -> resolve -> build -> predict so both the CLI and
tests share the same flow.
"""
from __future__ import annotations

import pandas as pd

from . import config, db, features, predict as predict_mod
from .ingest import fpl_api, understat, vaastav


def pull(conn, *, season: str | None = None, use_cache: bool = False,
         history: bool = True, backfill: bool = True,
         with_understat: bool = False) -> dict:
    """Pull all free data into SQLite: FPL live (+history), vaastav backfill."""
    season = season or config.CURRENT_SEASON
    summary = {"season": season}
    summary["fpl"] = fpl_api.ingest_all(conn, season, use_cache=use_cache,
                                        history=history)
    if backfill:
        summary["backfill"] = vaastav.ingest_seasons(conn, use_cache=use_cache)
    if with_understat and understat.available():
        summary["understat"] = _pull_understat(conn, season, use_cache=use_cache)
    else:
        summary["understat"] = "skipped/unavailable (FPL-only degradation)"
    return summary


def _pull_understat(conn, season: str, *, use_cache: bool) -> dict:
    from .resolve import resolve_teams
    resolve_teams(conn, season)
    teams = conn.execute(
        "SELECT understat_name FROM team WHERE season=? AND understat_name IS NOT NULL",
        (season,)).fetchall()
    n = 0
    for t in teams:
        n += understat.ingest_team_season(conn, season, t["understat_name"],
                                          use_cache=use_cache)
    return {"team_match_rows": n}


def build(conn, gw: int, *, season: str | None = None, store: bool = True) -> pd.DataFrame:
    season = season or config.CURRENT_SEASON
    from .resolve import resolve_teams
    resolve_teams(conn, season)
    df = features.build_samples(conn, season, gw)
    if store:
        features.store_samples(conn, df, season, gw)
    return df


def predict_gw(conn, gw: int, *, season: str | None = None,
               bundle=None) -> pd.DataFrame:
    """End-to-end: build point-in-time samples for the gw and run OpenFPL."""
    df = build(conn, gw, season=season, store=True)
    preds = predict_mod.predict(df, bundle=bundle)
    return preds.sort_values("prediction", ascending=False).reset_index(drop=True)
