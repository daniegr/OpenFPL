"""Best-effort Understat ingestion for advanced stats (xG, xA, deep, PPDA ...).

Understat is free but periodically changes its page structure and applies bot
protection. This module therefore:

  * parses whichever ``JSON.parse('...')`` blocks are present (format-tolerant),
  * writes what it finds into ``understat_player_match`` / ``understat_team_match``,
  * and **fails soft** — a blocked or restructured page returns an empty result
    rather than aborting the pipeline.

When Understat is unavailable the pipeline degrades gracefully to FPL-only
features (the OpenFPL models tolerate the missing columns via ``np.nan_to_num``).
This mirrors the project's risk register: "abstract every external source ...
degrade gracefully to FPL-only features".

Understat's season year is the *starting* year, e.g. 2024 for the 2024-25 season.
"""
from __future__ import annotations

import json
import re

from .. import db
from ..http import get_text, utcnow_iso

BASE = "https://understat.com"
_VAR_RE = re.compile(r"var\s+(\w+)\s*=\s*JSON\.parse\('(.+?)'\)\s*;", re.S)


def season_to_year(season: str) -> int:
    """'2024-25' -> 2024."""
    return int(season.split("-")[0])


def _parse_json_vars(html: str) -> dict[str, object]:
    out: dict[str, object] = {}
    for name, blob in _VAR_RE.findall(html):
        try:
            out[name] = json.loads(bytes(blob, "utf-8").decode("unicode_escape"))
        except Exception:  # noqa: BLE001 - tolerate any single malformed block
            continue
    return out


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def ingest_team_season(conn, season: str, understat_team: str, *,
                       use_cache: bool = True) -> int:
    """Ingest one team's per-match Understat stats for a season. Returns rows."""
    year = season_to_year(season)
    url = f"{BASE}/team/{understat_team}/{year}"
    try:
        html = get_text(url, use_cache=use_cache)
    except Exception:
        return 0
    vars_ = _parse_json_vars(html)
    dates = vars_.get("datesData")
    if not isinstance(dates, list):
        return 0
    db.upsert(conn, "raw_snapshot", [{
        "source": "understat", "endpoint": f"team/{understat_team}/{year}",
        "season": season, "retrieved_utc": utcnow_iso(), "payload": url,
    }])
    rows = []
    for m in dates:
        if not m.get("isResult"):
            continue
        is_home = m.get("side") == "h" or (m.get("h", {}).get("title") == understat_team)
        xg = m.get("xG", {})
        ppda = m.get("ppda", {}) or {}
        ppda_a = m.get("ppda_allowed", {}) or {}
        rows.append({
            "season": season, "understat_team": understat_team,
            "match_date": (m.get("datetime") or "")[:10],
            "understat_match_id": str(m.get("id")),
            "is_home": int(bool(is_home)),
            "xg": _num(xg.get("h") if is_home else xg.get("a")),
            "xga": _num(xg.get("a") if is_home else xg.get("h")),
            "deep": _num(m.get("deep")), "deep_allowed": _num(m.get("deep_allowed")),
            "ppda_att": _num(ppda.get("att")), "ppda_def": _num(ppda.get("def")),
            "ppda_allowed_att": _num(ppda_a.get("att")),
            "ppda_allowed_def": _num(ppda_a.get("def")),
        })
    return db.upsert(conn, "understat_team_match", rows)


def ingest_player_matches(conn, season: str, understat_id: str, *,
                          use_cache: bool = True) -> int:
    """Ingest one player's per-match Understat stats. Returns rows written."""
    url = f"{BASE}/player/{understat_id}"
    try:
        html = get_text(url, use_cache=use_cache)
    except Exception:
        return 0
    vars_ = _parse_json_vars(html)
    matches = vars_.get("matchesData")
    if not isinstance(matches, list):
        return 0
    rows = []
    for m in matches:
        if season and m.get("season") and str(m.get("season")) != str(season_to_year(season)):
            continue
        rows.append({
            "season": season, "understat_id": str(understat_id),
            "match_date": (m.get("date") or "")[:10],
            "understat_match_id": str(m.get("id")),
            "goals": _num(m.get("goals")), "shots": _num(m.get("shots")),
            "xg": _num(m.get("xG")), "assists": _num(m.get("assists")),
            "key_passes": _num(m.get("key_passes")), "xa": _num(m.get("xA")),
            "xgchain": _num(m.get("xGChain")), "xgbuildup": _num(m.get("xGBuildup")),
            "minutes": _num(m.get("time")),
        })
    return db.upsert(conn, "understat_player_match", rows)


def available(use_cache: bool = False) -> bool:
    """Cheap probe: is Understat currently serving parseable data?"""
    try:
        html = get_text(f"{BASE}/league/EPL/2024", use_cache=use_cache)
    except Exception:
        return False
    return bool(_parse_json_vars(html))
