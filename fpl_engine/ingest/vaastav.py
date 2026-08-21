"""Free historical backfill from the vaastav FPL dataset.

Why this exists
---------------
Rolling-window features (form over the last 1/3/5/10/38 matches) need match
history. At the start of a season the official FPL API has none, so early-season
predictions require *last season's* data. The vaastav dataset
(github.com/vaastav/Fantasy-Premier-League) publishes clean, free, per-gameweek
CSVs for every recent season, which we load into the same ``player_gw`` table
(``source='vaastav'``) so the feature builder sees one continuous history.
"""
from __future__ import annotations

import csv
import io

from .. import config, db, progress
from ..http import get_text, utcnow_iso

RAW = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"


def _csv_rows(url: str, use_cache: bool) -> list[dict]:
    txt = get_text(url, use_cache=use_cache)
    return list(csv.DictReader(io.StringIO(txt)))


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def ingest_season(conn, season: str, *, use_cache: bool = True) -> dict:
    """Backfill teams, player code map, and per-gw facts for one season."""
    base = f"{RAW}/{season}"

    # 1) Teams (id -> name) for opponent resolution.
    teams = _csv_rows(f"{base}/teams.csv", use_cache)
    db.upsert(conn, "team", [{
        "season": season, "team_id": int(t["id"]), "name": t["name"],
        "short_name": t.get("short_name"),
        "code": int(t["code"]) if t.get("code") else None,
        "understat_name": None,
    } for t in teams])
    team_name_to_id = {t["name"]: int(t["id"]) for t in teams}

    # 2) Player code map (season element id -> stable FPL code) + player rows.
    praw = _csv_rows(f"{base}/players_raw.csv", use_cache)
    id_to_code = {}
    players = []
    for p in praw:
        pid = int(p["id"])
        code = int(p["code"]) if p.get("code") else None
        id_to_code[pid] = code
        players.append({
            "season": season, "player_id": pid, "code": code,
            "web_name": p.get("web_name"),
            "full_name": f"{p.get('first_name','')} {p.get('second_name','')}".strip(),
            "team_id": int(p["team"]) if p.get("team") else None,
            "position": config.ELEMENT_TYPE_TO_POSITION.get(
                int(p["element_type"]) if p.get("element_type") else None),
            "understat_id": None,
        })
    db.upsert(conn, "player", players)

    # 3) Per-gameweek facts.
    merged = _csv_rows(f"{base}/gws/merged_gw.csv", use_cache)
    db.upsert(conn, "raw_snapshot", [{
        "source": "vaastav", "endpoint": f"{season}/merged_gw",
        "season": season, "retrieved_utc": utcnow_iso(),
        "payload": f"{len(merged)} rows",  # keep snapshot table lean for big CSVs
    }])
    rows = []
    for m in merged:
        pid = int(m["element"]) if m.get("element") else None
        gw = int(m["GW"]) if m.get("GW") else (int(m["round"]) if m.get("round") else None)
        rows.append({
            "season": season, "gw": gw, "source": "vaastav",
            "player_id": pid, "fixture_id": int(m["fixture"]) if m.get("fixture") else 0,
            "player_code": id_to_code.get(pid),
            "full_name": m.get("name"),
            "team_id": team_name_to_id.get(m.get("team")),
            "opponent_id": int(m["opponent_team"]) if m.get("opponent_team") else None,
            "was_home": 1 if str(m.get("was_home")).lower() in ("true", "1") else 0,
            "kickoff_utc": m.get("kickoff_time"),
            "minutes": _num(m.get("minutes")), "total_points": _num(m.get("total_points")),
            "goals_scored": _num(m.get("goals_scored")), "assists": _num(m.get("assists")),
            "clean_sheets": _num(m.get("clean_sheets")),
            "goals_conceded": _num(m.get("goals_conceded")),
            "own_goals": _num(m.get("own_goals")),
            "penalties_saved": _num(m.get("penalties_saved")),
            "penalties_missed": _num(m.get("penalties_missed")),
            "yellow_cards": _num(m.get("yellow_cards")),
            "red_cards": _num(m.get("red_cards")), "saves": _num(m.get("saves")),
            "bonus": _num(m.get("bonus")), "bps": _num(m.get("bps")),
            "influence": _num(m.get("influence")), "creativity": _num(m.get("creativity")),
            "threat": _num(m.get("threat")), "starts": _num(m.get("starts")),
            # FPL expected stats (present from 2022-23; None before)
            "xg": _num(m.get("expected_goals")), "xa": _num(m.get("expected_assists")),
            "xgi": _num(m.get("expected_goal_involvements")),
            "xgc": _num(m.get("expected_goals_conceded")),
        })
    n = db.upsert(conn, "player_gw", rows)

    # Derive per-team match results from the (repeated) player rows.
    tm: dict[tuple, dict] = {}
    team_xg: dict[tuple, float] = {}     # (team_id, fixture) -> summed player xG
    for m in merged:
        was_home = str(m.get("was_home")).lower() in ("true", "1")
        team_id = team_name_to_id.get(m.get("team"))
        fixture = int(m["fixture"]) if m.get("fixture") else None
        if team_id is None or fixture is None:
            continue
        gf = _num(m.get("team_h_score")) if was_home else _num(m.get("team_a_score"))
        ga = _num(m.get("team_a_score")) if was_home else _num(m.get("team_h_score"))
        pxg = _num(m.get("expected_goals"))
        if pxg is not None:
            team_xg[(team_id, fixture)] = team_xg.get((team_id, fixture), 0.0) + pxg
        tm[(season, team_id, fixture)] = {
            "season": season, "team_id": team_id, "fixture_id": fixture,
            "gw": int(m["GW"]) if m.get("GW") else None,
            "kickoff_utc": m.get("kickoff_time"),
            "opponent_id": int(m["opponent_team"]) if m.get("opponent_team") else None,
            "was_home": int(was_home), "goals_for": gf, "goals_against": ga,
        }
    for (s_, team_id, fixture), d in tm.items():
        d["xg"] = team_xg.get((team_id, fixture))
        d["xga"] = team_xg.get((d["opponent_id"], fixture))
    db.upsert(conn, "team_match", list(tm.values()))

    return {"season": season, "teams": len(teams), "players": len(players),
            "player_gw_rows": n, "team_matches": len(tm)}


def ingest_seasons(conn, seasons: list[str] | None = None, *,
                   use_cache: bool = True) -> list[dict]:
    seasons = seasons or config.BACKFILL_SEASONS
    out = []
    for s in seasons:
        progress.step(f"Backfilling historical season {s} (downloading free "
                      f"vaastav data)…")
        try:
            res = ingest_season(conn, s, use_cache=use_cache)
            progress.log(f"    {s}: {res['player_gw_rows']} player-gw rows, "
                         f"{res['team_matches']} team matches")
            out.append(res)
        except Exception as e:  # noqa: BLE001 - a missing season should not abort
            progress.log(f"    {s}: skipped ({e})")
            out.append({"season": s, "error": str(e)})
    return out
