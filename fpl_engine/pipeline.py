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


def _require_data(conn, season: str) -> None:
    """Fail with a helpful message if the database has not been populated yet."""
    n_players = conn.execute(
        "SELECT COUNT(*) FROM player WHERE season=?", (season,)).fetchone()[0]
    n_fixtures = conn.execute(
        "SELECT COUNT(*) FROM fixture WHERE season=?", (season,)).fetchone()[0]
    if not n_players or not n_fixtures:
        raise SystemExit(
            f"No {season} data in the database yet.\n"
            f"Pull the free data first:\n"
            f"    python -m fpl_engine pull\n"
            f"then re-run the optimiser.")
    n_priced = conn.execute(
        "SELECT COUNT(*) FROM player WHERE season=? AND now_cost IS NOT NULL",
        (season,)).fetchone()[0]
    if not n_priced:
        raise SystemExit(
            f"{season} players have no prices (database predates a schema "
            f"update). Refresh it:\n    python -m fpl_engine pull")


def next_gw(conn, season: str) -> int:
    """The next unfinished gameweek that has scheduled fixtures."""
    row = conn.execute(
        "SELECT MIN(gw) g FROM fixture WHERE season=? AND finished=0 AND gw IS NOT NULL",
        (season,)).fetchone()
    if row and row["g"]:
        return int(row["g"])
    row = conn.execute(
        "SELECT MIN(gw) g FROM fixture WHERE season=? AND gw IS NOT NULL",
        (season,)).fetchone()
    return int(row["g"]) if row and row["g"] else 1


def optimise_squad(conn, *, entry_id: int, season: str | None = None,
                   horizon: int = 5, budget: float = 100.0, bundle=None,
                   decay: float = 0.85, max_transfers_per_gw: int = 3,
                   keep_per_position: int = 30, time_limit: int = 40,
                   use_cache: bool = False) -> dict:
    """End-to-end squad optimisation for an FPL entry id.

    Fetches the manager's current squad (or None pre-season), projects points
    across the horizon, and runs the MILP — suggesting transfers/hits, or
    building a fresh squad from budget when no squad exists yet.
    """
    from . import manager
    from .optimise import milp, project

    from . import progress
    season = season or config.CURRENT_SEASON
    _require_data(conn, season)
    from .resolve import resolve_teams
    resolve_teams(conn, season)

    start = next_gw(conn, season)
    scheduled = [r["gw"] for r in conn.execute(
        "SELECT DISTINCT gw FROM fixture WHERE season=? AND gw>=? AND gw IS NOT NULL "
        "ORDER BY gw", (season, start))]
    gws = scheduled[:horizon] or [start]
    progress.step(f"Planning horizon GW{gws[0]}–GW{gws[-1]}")

    bundle = bundle or predict_mod.load_models()
    progress.step(f"Projecting points for {len(gws)} gameweeks…")
    proj = project.horizon_projections(conn, season, gws, bundle=bundle, decay=decay)

    progress.step(f"Fetching entry {entry_id}…")
    squad_state = manager.current_squad(entry_id, use_cache=use_cache)
    if squad_state is None:
        progress.step("No existing squad found — building a fresh squad from "
                      f"£{budget:.0f}m. Solving optimiser…")
        proj_p = project.prune(proj, keep_per_position=keep_per_position)
        plan = milp.build_from_scratch(
            proj_p, gws, budget=budget, decay=decay,
            max_transfers_per_gw=max_transfers_per_gw, time_limit=time_limit)
        mode = "build-from-scratch"
        state = {"bank": budget, "free_transfers": None}
    else:
        progress.step(f"Squad found ({squad_state['free_transfers']} FT, "
                      f"£{squad_state['bank']:.1f}m bank). Solving optimiser…")
        owned = {p["element"]: p["selling_price"] for p in squad_state["squad"]}
        proj = _ensure_players(conn, season, proj, owned, gws)
        proj_p = project.prune(proj, keep_per_position=keep_per_position,
                               must_keep=set(owned))
        plan = milp.optimise(
            proj_p, gws, initial=owned, bank=squad_state["bank"],
            free_transfers=squad_state["free_transfers"], budget=budget,
            decay=decay, max_transfers_per_gw=max_transfers_per_gw,
            time_limit=time_limit)
        mode = "optimise-transfers"
        state = {"bank": squad_state["bank"],
                 "free_transfers": squad_state["free_transfers"],
                 "manager": squad_state.get("name")}

    return {"mode": mode, "entry_id": entry_id, "gws": gws, "state": state,
            "plan": plan}


def _ensure_players(conn, season, proj, owned, gws):
    """Guarantee every owned player has a projection row (ep 0 if unprojectable)."""
    have = set(proj["player_id"])
    missing = [pid for pid in owned if pid not in have]
    if not missing:
        return proj
    rows = []
    for pid in missing:
        r = conn.execute(
            "SELECT p.player_id, p.full_name, p.position, p.team_id, p.now_cost, "
            "t.name team FROM player p LEFT JOIN team t "
            "ON p.season=t.season AND p.team_id=t.team_id "
            "WHERE p.season=? AND p.player_id=?", (season, pid)).fetchone()
        if not r:
            continue
        row = {"player_id": pid, "player": r["full_name"], "position": r["position"],
               "team_id": r["team_id"], "team": r["team"],
               "price": r["now_cost"] or 0.0, "available": 0.0, "ep_total": 0.0}
        for g in gws:
            row[f"ep_gw{g}"] = 0.0
        rows.append(row)
    return pd.concat([proj, pd.DataFrame(rows)], ignore_index=True) if rows else proj
