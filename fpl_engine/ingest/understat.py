"""Best-effort Understat ingestion for advanced stats (xG, xA, deep, PPDA ...).

Understat is free but periodically changes how it serves data. Since its
Dec-2025 redesign the HTML pages no longer embed ``JSON.parse(...)`` blocks;
the data comes from JSON endpoints that answer only to XHR requests:

  * ``GET  /getLeagueData/{league}/{year}``  -> {teams: {id: {title, history[]}},
                                                 dates: [matches], players: [...]}
    one call per season covers every club's per-match xG/xGA/deep/PPDA.
  * ``GET  /getPlayerData/{player_id}``      -> {matches: [...all seasons...]}
    per-match goals/shots/xG/xA/key passes/xGChain/xGBuildup/minutes.
  * ``POST /main/getPlayersStats/``          -> season aggregates incl. ids,
    names and clubs (used for FPL<->Understat player resolution).

This module still **fails soft**: a blocked/empty/restructured response yields
no rows rather than aborting the pipeline, and the pipeline degrades to the
FPL-only features (plus FPL's own xG stand-ins). Understat's season year is
the *starting* year, e.g. 2024 for the 2024-25 season.
"""
from __future__ import annotations

import json

from .. import config, db
from ..http import get_text, post_text, utcnow_iso

BASE = "https://understat.com"
LEAGUE = "EPL"
# the JSON endpoints 404 without the XHR marker
_XHR = {"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/league/{LEAGUE}"}


def season_to_year(season: str) -> int:
    """'2024-25' -> 2024."""
    return int(season.split("-")[0])


def year_to_season(year: int | str) -> str:
    """2024 -> '2024-25'."""
    y = int(year)
    return f"{y}-{str(y + 1)[-2:]}"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _json(text: str):
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# fetchers (fail soft: None / [] on any problem)
# --------------------------------------------------------------------------

def fetch_league_data(season: str, *, use_cache: bool = True) -> dict | None:
    """teams/dates/players for one season, or None when unavailable."""
    url = f"{BASE}/getLeagueData/{LEAGUE}/{season_to_year(season)}"
    try:
        data = _json(get_text(url, use_cache=use_cache, headers=_XHR))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict) or not isinstance(data.get("teams"), dict):
        return None
    return data


def fetch_league_players(season: str, *, use_cache: bool = True) -> list[dict]:
    """Season aggregates per player (id, player_name, team_title, ...)."""
    try:
        data = _json(post_text(f"{BASE}/main/getPlayersStats/",
                               {"league": LEAGUE, "season": season_to_year(season)},
                               use_cache=use_cache, headers=_XHR))
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, dict) or not data.get("success"):
        return []
    return [p for p in (data.get("players") or []) if isinstance(p, dict)]


def fetch_player_matches(understat_id: str, *, use_cache: bool = True) -> list[dict]:
    """All of one player's matches (every season/league Understat covers).

    Prefers ``POST /main/getPlayerMatches/{id}`` (matches only, ~5x smaller)
    and falls back to ``GET /getPlayerData/{id}`` (matches + every shot).
    """
    try:
        data = _json(post_text(f"{BASE}/main/getPlayerMatches/{understat_id}", {},
                               use_cache=use_cache, headers=_XHR))
        resp = (data or {}).get("response") if isinstance(data, dict) else None
        ms = (resp or {}).get("matches") if isinstance(resp, dict) else None
        if isinstance(ms, list) and ms:
            return [m for m in ms if isinstance(m, dict)]
    except Exception:  # noqa: BLE001
        pass
    try:
        data = _json(get_text(f"{BASE}/getPlayerData/{understat_id}",
                              use_cache=use_cache, headers=_XHR))
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, dict):
        return []
    return [m for m in (data.get("matches") or []) if isinstance(m, dict)]


# --------------------------------------------------------------------------
# normalisers (pure; unit-tested)
# --------------------------------------------------------------------------

def team_rows_from_league(data: dict, season: str) -> list[dict]:
    """understat_team_match rows for every club from a getLeagueData payload."""
    # (club title, date, side) -> understat match id, from the fixture list
    match_id: dict[tuple, str] = {}
    for m in data.get("dates") or []:
        if not isinstance(m, dict) or not m.get("isResult"):
            continue
        d = str(m.get("datetime") or "")[:10]
        for side in ("h", "a"):
            title = (m.get(side) or {}).get("title")
            if title:
                match_id[(title, d, side)] = str(m.get("id"))
    rows = []
    for t in (data.get("teams") or {}).values():
        title = t.get("title")
        for h in t.get("history") or []:
            d = str(h.get("date") or "")[:10]
            side = h.get("h_a")
            ppda = h.get("ppda") or {}
            ppda_a = h.get("ppda_allowed") or {}
            rows.append({
                "season": season, "understat_team": title, "match_date": d,
                "understat_match_id": match_id.get((title, d, side), f"{d}:{side}"),
                "is_home": int(side == "h"),
                "xg": _num(h.get("xG")), "xga": _num(h.get("xGA")),
                "deep": _num(h.get("deep")), "deep_allowed": _num(h.get("deep_allowed")),
                "ppda_att": _num(ppda.get("att")), "ppda_def": _num(ppda.get("def")),
                "ppda_allowed_att": _num(ppda_a.get("att")),
                "ppda_allowed_def": _num(ppda_a.get("def")),
            })
    return rows


def player_rows_from_matches(understat_id: str, matches: list[dict], *,
                             epl_titles: set[str] | None = None) -> list[dict]:
    """understat_player_match rows (all seasons) from a getPlayerData payload.

    ``epl_titles`` restricts rows to matches involving those clubs (the
    player's other-league matches are otherwise included, as before).
    """
    rows = []
    for m in matches:
        if epl_titles and not ({m.get("h_team"), m.get("a_team")} & epl_titles):
            continue
        year = m.get("season")
        rows.append({
            "season": year_to_season(year) if year else None,
            "understat_id": str(understat_id),
            "match_date": str(m.get("date") or "")[:10],
            "understat_match_id": str(m.get("id")),
            "goals": _num(m.get("goals")), "shots": _num(m.get("shots")),
            "xg": _num(m.get("xG")), "assists": _num(m.get("assists")),
            "key_passes": _num(m.get("key_passes")), "xa": _num(m.get("xA")),
            "xgchain": _num(m.get("xGChain")), "xgbuildup": _num(m.get("xGBuildup")),
            "minutes": _num(m.get("time")),
        })
    return [r for r in rows if r["season"] and r["match_date"]]


# --------------------------------------------------------------------------
# ingestion
# --------------------------------------------------------------------------

_LEAGUE_CACHE: dict[str, dict | None] = {}


def league_data(season: str, *, use_cache: bool = True) -> dict | None:
    """getLeagueData payload for a season, memoised per process."""
    if season not in _LEAGUE_CACHE:
        _LEAGUE_CACHE[season] = fetch_league_data(season, use_cache=use_cache)
    return _LEAGUE_CACHE[season]


def ingest_league_teams(conn, season: str, *, use_cache: bool = True) -> int:
    """Per-match team stats for every club in a season (one request)."""
    data = league_data(season, use_cache=use_cache)
    if not data:
        return 0
    db.upsert(conn, "raw_snapshot", [{
        "source": "understat",
        "endpoint": f"getLeagueData/{LEAGUE}/{season_to_year(season)}",
        "season": season, "retrieved_utc": utcnow_iso(),
        "payload": f"{len(data.get('teams') or {})} teams",
    }])
    return db.upsert(conn, "understat_team_match", team_rows_from_league(data, season))


def ingest_team_season(conn, season: str, understat_team: str, *,
                       use_cache: bool = True) -> int:
    """Compatibility shim: one club's rows (the league call covers all clubs)."""
    data = league_data(season, use_cache=use_cache)
    if not data:
        return 0
    rows = [r for r in team_rows_from_league(data, season)
            if r["understat_team"] == understat_team]
    return db.upsert(conn, "understat_team_match", rows)


def ingest_player_matches(conn, season: str, understat_id: str, *,
                          use_cache: bool = True,
                          epl_titles: set[str] | None = None) -> int:
    """One player's per-match stats - every season Understat has, so a single
    call also refreshes the history the trailing windows need. Returns rows."""
    matches = fetch_player_matches(understat_id, use_cache=use_cache)
    if not matches:
        return 0
    rows = player_rows_from_matches(understat_id, matches, epl_titles=epl_titles)
    if rows:
        db.upsert(conn, "raw_snapshot", [{
            "source": "understat", "endpoint": f"getPlayerData/{understat_id}",
            "season": season, "retrieved_utc": utcnow_iso(),
            "payload": f"{len(rows)} matches",
        }])
    return db.upsert(conn, "understat_player_match", rows)


def available(use_cache: bool = False) -> bool:
    """Cheap probe: does Understat serve parseable league data?

    Probes the most recent *completed* season so a not-yet-started season
    (empty at Understat) doesn't read as an outage.
    """
    year = season_to_year(config.CURRENT_SEASON) - 1
    return fetch_league_data(year_to_season(year), use_cache=use_cache) is not None
