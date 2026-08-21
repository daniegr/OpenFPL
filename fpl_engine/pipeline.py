"""High-level pipeline orchestration used by the CLI.

One place that wires ingest -> resolve -> build -> predict so both the CLI and
tests share the same flow.
"""
from __future__ import annotations

import pandas as pd

from . import config, db, features, predict as predict_mod
from .ingest import fpl_api, understat, vaastav
from . import progress


def pull(conn, *, season: str | None = None, use_cache: bool = False,
         history: bool = True, backfill: bool = True,
         with_understat: bool = False) -> dict:
    """Pull all free data into SQLite: FPL live (+history), vaastav backfill.

    ``use_cache`` applies ONLY to the static historical backfill (safe to cache
    across runs). Live FPL data (bootstrap, fixtures, current-season history) is
    ALWAYS fetched fresh so a scheduled run genuinely updates the data.
    """
    season = season or config.CURRENT_SEASON
    summary = {"season": season}
    summary["fpl"] = fpl_api.ingest_all(conn, season, use_cache=False,
                                        history=history)
    if backfill:
        summary["backfill"] = vaastav.ingest_seasons(conn, use_cache=use_cache)
    if with_understat and understat.available():
        summary["understat"] = _pull_understat(conn, season, use_cache=use_cache)
    else:
        summary["understat"] = "skipped/unavailable (FPL-only degradation)"
    return summary


def _needs_refresh(us_latest: str | None, fpl_latest: str | None) -> bool:
    """Re-fetch a player's Understat log only if FPL shows he has played a
    match newer than the latest Understat match we hold (or we hold none).
    Pre-season that is nobody; in-season it is the players who featured."""
    if not us_latest:
        return True
    if not fpl_latest:
        return False
    return str(fpl_latest)[:10] > str(us_latest)[:10]


def _pull_understat(conn, season: str, *, use_cache: bool,
                    history_seasons: int = 1, player_limit: int | None = None,
                    refresh_all: bool = False, workers: int = 2) -> dict:
    """Understat pull: club stats for the current + ``history_seasons`` previous
    seasons (one request each), FPL<->Understat player resolution from the
    season player lists, then per-player match logs (all seasons in one call).

    Player logs are pulled *incrementally*: only players whose FPL match log
    has a newer match than their latest Understat row (see ``_needs_refresh``)
    unless ``refresh_all``. Fetches run on a small thread pool (the per-host
    throttle in ``http`` still spaces request starts); rows are written and
    committed from this thread every 25 players so progress persists.
    """
    from .resolve import resolve_players, resolve_teams
    resolve_teams(conn, season)
    year = understat.season_to_year(season)
    seasons = [understat.year_to_season(y)
               for y in range(year - history_seasons, year + 1)]
    team_rows = 0
    names: dict[str, str] = {}
    clubs: dict[str, str] = {}
    titles: set[str] = set()
    current_ids: set[str] = set()
    for s_ in seasons:
        live = s_ == season
        progress.step(f"Understat: club match stats {s_}…")
        data = understat.league_data(s_, use_cache=use_cache and not live)
        if data:
            titles |= {t.get("title") for t in (data.get("teams") or {}).values()
                       if t.get("title")}
            team_rows += understat.ingest_league_teams(conn, s_,
                                                       use_cache=use_cache and not live)
        for pl in understat.fetch_league_players(s_, use_cache=use_cache and not live):
            names[str(pl.get("id"))] = pl.get("player_name")
            if pl.get("team_title"):
                clubs[str(pl.get("id"))] = pl["team_title"]   # latest season wins
            if live:
                current_ids.add(str(pl.get("id")))
    res = resolve_players(conn, season, names, understat_teams=clubs)
    progress.step(f"Understat: {len(res['resolved'])} players resolved, "
                  f"{len(res['unresolved'])} unresolved, "
                  f"{len(res['ambiguous'])} ambiguous (features stay NaN for those).")
    rows_ = conn.execute(
        "SELECT p.understat_id AS uid, "
        "       (SELECT MAX(g.kickoff_utc) FROM player_gw g "
        "         WHERE g.player_code = p.code AND g.minutes > 0) AS fpl_latest, "
        "       (SELECT MAX(u.match_date) FROM understat_player_match u "
        "         WHERE u.understat_id = p.understat_id) AS us_latest "
        "FROM player p WHERE p.season=? AND p.understat_id IS NOT NULL "
        "ORDER BY p.player_id", (season,)).fetchall()
    all_uids = [r["uid"] for r in rows_]
    uids = [r["uid"] for r in rows_
            if refresh_all or _needs_refresh(r["us_latest"], r["fpl_latest"])]
    if player_limit is not None:
        uids = uids[:player_limit]
    total = len(uids)
    progress.step(f"Understat: {total}/{len(all_uids)} player logs need a refresh "
                  f"(~{max(1, round(total * 3 / 60))} min)…")
    n = 0
    if total:
        from concurrent.futures import ThreadPoolExecutor

        def _fetch(uid):
            live = uid in current_ids
            return uid, understat.fetch_player_matches(
                uid, use_cache=use_cache and not live)

        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            for i, (uid, matches) in enumerate(ex.map(_fetch, uids), 1):
                if matches:
                    n += db.upsert(conn, "understat_player_match",
                                   understat.player_rows_from_matches(
                                       uid, matches, epl_titles=titles or None))
                if i % 25 == 0 or i == total:
                    conn.commit()   # keep partial progress if interrupted
                    progress.log(f"    …{i}/{total} players ({n} match rows)")
    return {"seasons": seasons, "team_match_rows": team_rows,
            "player_logs_refreshed": total, "player_match_rows": n,
            "players_resolved": len(res["resolved"]),
            "players_unresolved": len(res["unresolved"]),
            "players_ambiguous": len(res["ambiguous"])}


def build(conn, gw: int, *, season: str | None = None, store: bool = True) -> pd.DataFrame:
    season = season or config.CURRENT_SEASON
    from .resolve import resolve_teams
    resolve_teams(conn, season)
    df = features.build_samples(conn, season, gw)
    if store:
        features.store_samples(conn, df, season, gw)
    return df


def resolve_blend(conn, season: str, blend):
    """Resolve a --blend argument into (retrained_models_or_None, weight).

    ``blend`` may be None/0 (pure OpenFPL), 'auto' (weight from season progress),
    or a float in [0,1]. Returns (None, 0.0) if no retrained models exist.
    """
    if blend in (None, 0, 0.0, "0"):
        return None, 0.0
    from . import train
    retrained = train.load_retrained()
    if retrained is None:
        return None, 0.0
    weight = (train.season_blend_weight(conn, season) if blend == "auto"
              else float(blend))
    return retrained, weight


def predict_gw(conn, gw: int, *, season: str | None = None, bundle=None,
               blend=None) -> pd.DataFrame:
    """End-to-end: build point-in-time samples for the gw and run OpenFPL."""
    season = season or config.CURRENT_SEASON
    df = build(conn, gw, season=season, store=True)
    retrained, weight = resolve_blend(conn, season, blend)
    preds = predict_mod.predict(df, bundle=bundle, retrained=retrained, blend=weight)
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
                   use_cache: bool = False, blend=None) -> dict:
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
    retrained, weight = resolve_blend(conn, season, blend)
    if weight > 0:
        progress.step(f"Blending retrained model (weight {weight:.2f})")
    progress.step(f"Projecting points for {len(gws)} gameweeks…")
    proj = project.horizon_projections(conn, season, gws, bundle=bundle,
                                       decay=decay, retrained=retrained, blend=weight)

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
