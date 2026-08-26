"""Ingest the official (free, no-auth) FPL API into local SQLite.

Endpoints used
--------------
* ``bootstrap-static/``     teams, players, positions, availability, events
* ``fixtures/``             all fixtures + scores + kickoff times
* ``element-summary/{id}/`` per-player match history for the current season
* ``event/{gw}/live/``      per-player live stats for a finished gameweek

The current-season per-player history is written into ``player_gw`` with
``source='fpl'`` so it composes with the ``vaastav`` historical backfill.
"""
from __future__ import annotations

import json

from .. import config, db, progress
from ..http import get_text, utcnow_iso

BASE = "https://fantasy.premierleague.com/api"


def _snapshot(conn, endpoint: str, payload: str, season: str) -> None:
    db.upsert(conn, "raw_snapshot", [{
        "source": "fpl", "endpoint": endpoint, "season": season,
        "retrieved_utc": utcnow_iso(), "payload": payload,
    }])


def fetch_bootstrap(use_cache: bool = False) -> dict:
    return json.loads(get_text(f"{BASE}/bootstrap-static/", use_cache=use_cache))


def fetch_fixtures(use_cache: bool = False) -> list:
    return json.loads(get_text(f"{BASE}/fixtures/", use_cache=use_cache))


def fetch_element_summary(pid: int, use_cache: bool = True) -> dict:
    return json.loads(get_text(f"{BASE}/element-summary/{pid}/", use_cache=use_cache))


def ingest_bootstrap(conn, season: str | None = None, *, use_cache: bool = False) -> dict:
    """Load teams + players + events from bootstrap-static. Returns the payload."""
    season = season or config.CURRENT_SEASON
    boot = fetch_bootstrap(use_cache=use_cache)
    _snapshot(conn, "bootstrap-static", json.dumps(boot), season)

    db.upsert(conn, "team", [{
        "season": season, "team_id": t["id"], "name": t["name"],
        "short_name": t.get("short_name"), "code": t.get("code"),
        "understat_name": None,
    } for t in boot["teams"]])

    players = []
    for e in boot["elements"]:
        players.append({
            "season": season,
            "player_id": e["id"],
            "code": e.get("code"),
            "web_name": e.get("web_name"),
            "full_name": f"{e.get('first_name','')} {e.get('second_name','')}".strip(),
            "team_id": e.get("team"),
            "position": config.ELEMENT_TYPE_TO_POSITION.get(e.get("element_type")),
            "understat_id": None,
            "now_cost": (e.get("now_cost") or 0) / 10.0,  # tenths of £m -> £m
            "status": e.get("status"),
            "chance_next": (e["chance_of_playing_next_round"] / 100.0
                            if e.get("chance_of_playing_next_round") is not None else None),
        })
    db.upsert(conn, "player", players)
    return boot


def ingest_fixtures(conn, season: str | None = None, *, use_cache: bool = False) -> list:
    season = season or config.CURRENT_SEASON
    fixtures = fetch_fixtures(use_cache=use_cache)
    _snapshot(conn, "fixtures", json.dumps(fixtures), season)
    db.upsert(conn, "fixture", [{
        "season": season, "fixture_id": f["id"], "gw": f.get("event"),
        "kickoff_utc": f.get("kickoff_time"), "team_h": f.get("team_h"),
        "team_a": f.get("team_a"), "team_h_score": f.get("team_h_score"),
        "team_a_score": f.get("team_a_score"), "finished": int(bool(f.get("finished"))),
    } for f in fixtures])

    # Per-team match results for finished fixtures (two rows per fixture).
    tm = []
    for f in fixtures:
        if not f.get("finished") or f.get("team_h_score") is None:
            continue
        for home in (True, False):
            tm.append({
                "season": season, "team_id": f["team_h"] if home else f["team_a"],
                "fixture_id": f["id"], "gw": f.get("event"),
                "kickoff_utc": f.get("kickoff_time"),
                "opponent_id": f["team_a"] if home else f["team_h"],
                "was_home": int(home),
                "goals_for": f["team_h_score"] if home else f["team_a_score"],
                "goals_against": f["team_a_score"] if home else f["team_h_score"],
            })
    db.upsert(conn, "team_match", tm)
    return fixtures


def ingest_current_season_history(conn, season: str | None = None, *,
                                  boot: dict | None = None,
                                  use_cache: bool = True,
                                  limit: int | None = None) -> int:
    """Pull per-player match history (element-summary) for every player.

    Writes finished matches this season into ``player_gw`` (source='fpl').
    ``limit`` caps how many players are fetched (useful for quick smoke tests).
    Returns the number of player-gw rows written.
    """
    season = season or config.CURRENT_SEASON
    boot = boot or fetch_bootstrap(use_cache=use_cache)
    # Fixture id -> (gw, kickoff) to attach point-in-time info.
    fixtures = {f["id"]: f for f in fetch_fixtures(use_cache=use_cache)}
    code_by_id = {e["id"]: e.get("code") for e in boot["elements"]}
    name_by_id = {e["id"]: f"{e.get('first_name','')} {e.get('second_name','')}".strip()
                  for e in boot["elements"]}

    elements = boot["elements"]
    if limit is not None:
        elements = elements[:limit]

    total = len(elements)
    progress.step(f"Fetching per-player history for {total} players "
                  f"(polite rate-limited; ~{max(1, total * 6 // 100 // 6)} min)…")
    written = 0
    team_xg: dict[tuple, float] = {}     # (team_id, fixture_id) -> sum player xG
    for i, e in enumerate(elements, 1):
        pid = e["id"]
        if i % 25 == 0 or i == total:
            progress.log(f"    …{i}/{total} players ({written} match rows)")
        try:
            summ = fetch_element_summary(pid, use_cache=use_cache)
        except Exception:
            continue
        _snapshot(conn, f"element-summary/{pid}", json.dumps(summ), season)
        rows = []
        for h in summ.get("history", []):
            fx = fixtures.get(h.get("fixture"))
            rows.append(_history_row(season, pid, code_by_id.get(pid),
                                     name_by_id.get(pid), h, fx))
        for row in rows:
            if row["xg"] is not None and row["team_id"] is not None:
                k = (row["team_id"], row["fixture_id"])
                team_xg[k] = team_xg.get(k, 0.0) + row["xg"]
        written += db.upsert(conn, "player_gw", rows)
    _update_team_xg(conn, season, team_xg, fixtures)
    return written


def _update_team_xg(conn, season, team_xg, fixtures) -> None:
    """Team xG/xGA per match from summed player xG (xGA = opponent's xG)."""
    for (team_id, fid), xg in team_xg.items():
        fx = fixtures.get(fid) or {}
        opp = fx.get("team_a") if fx.get("team_h") == team_id else fx.get("team_h")
        xga = team_xg.get((opp, fid))
        conn.execute(
            "UPDATE team_match SET xg=?, xga=? WHERE season=? AND team_id=? "
            "AND fixture_id=?", (xg, xga, season, team_id, fid))


def _history_row(season, pid, code, name, h, fx) -> dict:
    opponent = h.get("opponent_team")
    return {
        "season": season, "gw": h.get("round"), "source": "fpl",
        "player_id": pid, "fixture_id": h.get("fixture") or 0,
        "player_code": code, "full_name": name,
        "team_id": (fx.get("team_h") if h.get("was_home") else fx.get("team_a")) if fx else None,
        "opponent_id": opponent,
        "was_home": int(bool(h.get("was_home"))),
        "kickoff_utc": h.get("kickoff_time"),
        "minutes": h.get("minutes"), "total_points": h.get("total_points"),
        "goals_scored": h.get("goals_scored"), "assists": h.get("assists"),
        "clean_sheets": h.get("clean_sheets"), "goals_conceded": h.get("goals_conceded"),
        "own_goals": h.get("own_goals"), "penalties_saved": h.get("penalties_saved"),
        "penalties_missed": h.get("penalties_missed"), "yellow_cards": h.get("yellow_cards"),
        "red_cards": h.get("red_cards"), "saves": h.get("saves"),
        "bonus": h.get("bonus"), "bps": h.get("bps"),
        "influence": _f(h.get("influence")), "creativity": _f(h.get("creativity")),
        "threat": _f(h.get("threat")), "starts": h.get("starts"),
        # FPL's own (Opta) expected stats — the free stand-in for Understat
        "xg": _f(h.get("expected_goals")), "xa": _f(h.get("expected_assists")),
        "xgi": _f(h.get("expected_goal_involvements")),
        "xgc": _f(h.get("expected_goals_conceded")),
        # per-gw price + raw DefCon stats (2025-26 rule era)
        "price": _f(h.get("value")),
        "defcon": _f(h.get("defensive_contribution")),
        "tackles": _f(h.get("tackles")),
        "cbi": _f(h.get("clearances_blocks_interceptions")),
        "recoveries": _f(h.get("recoveries")),
    }


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def ingest_all(conn, season: str | None = None, *, use_cache: bool = False,
               history: bool = True, history_limit: int | None = None) -> dict:
    """Full FPL pull: bootstrap + fixtures (+ per-player history)."""
    season = season or config.CURRENT_SEASON
    progress.step(f"Fetching bootstrap (players, teams, prices) for {season}…")
    boot = ingest_bootstrap(conn, season, use_cache=use_cache)
    progress.step("Fetching fixtures…")
    ingest_fixtures(conn, season, use_cache=use_cache)

    # Skip the slow per-player history fetch when no matches have been played
    # (pre-season): element-summary returns empty history for everyone.
    finished = any(ev.get("finished") for ev in boot.get("events", []))
    n = 0
    if history and not finished:
        progress.step("Pre-season: no matches played yet — skipping per-player "
                      "history (form comes from the historical backfill).")
    elif history:
        n = ingest_current_season_history(conn, season, boot=boot,
                                          use_cache=use_cache, limit=history_limit)
        progress.step(f"Per-player history done ({n} rows).")
    return {"players": len(boot["elements"]), "teams": len(boot["teams"]),
            "history_rows": n}
