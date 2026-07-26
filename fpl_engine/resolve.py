"""Entity resolution: map FPL players/teams to their Understat identities.

Name matching across data providers is the most error-prone task in the
project, so this module:
  * uses a checked-in canonical team-name map plus fuzzy fallback,
  * fuzzy-matches player full names within a team, and
  * **fails loudly**: unresolved entities are reported, never silently dropped.

Overrides live in the ``entity_override`` table (kind='player'|'team') and take
precedence over any automatic match.
"""
from __future__ import annotations

import difflib
import unicodedata

from . import db

# FPL team name -> Understat team title. FPL and Understat mostly agree; the
# exceptions are listed explicitly. Extend as needed.
TEAM_UNDERSTAT_TITLE = {
    "Man City": "Manchester City",
    "Man Utd": "Manchester United",
    "Spurs": "Tottenham",
    "Tottenham": "Tottenham",
    "Newcastle": "Newcastle United",
    "Nott'm Forest": "Nottingham Forest",
    "Wolves": "Wolverhampton Wanderers",
    "Sheffield Utd": "Sheffield United",
    "Luton": "Luton",
    "Brighton": "Brighton",
    "West Ham": "West Ham",
    "Leicester": "Leicester",
    "Ipswich": "Ipswich",
}


def _norm(s: str) -> str:
    """Lowercase, strip accents/punctuation for robust fuzzy matching."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return "".join(ch for ch in s.lower() if ch.isalnum() or ch == " ").strip()


def understat_team_title(fpl_name: str) -> str:
    return TEAM_UNDERSTAT_TITLE.get(fpl_name, fpl_name)


def _overrides(conn, kind: str, season: str) -> dict[str, str]:
    return {r["fpl_key"]: r["understat_key"] for r in conn.execute(
        "SELECT fpl_key, understat_key FROM entity_override WHERE kind=? AND season=?",
        (kind, season))}


def resolve_teams(conn, season: str) -> dict[str, list]:
    """Write understat_name onto team rows. Returns {'resolved':[], 'unresolved':[]}."""
    ov = _overrides(conn, "team", season)
    resolved, unresolved = [], []
    for r in conn.execute("SELECT team_id, name FROM team WHERE season=?", (season,)):
        title = ov.get(str(r["team_id"])) or ov.get(r["name"]) or understat_team_title(r["name"])
        conn.execute("UPDATE team SET understat_name=? WHERE season=? AND team_id=?",
                     (title, season, r["team_id"]))
        (resolved if title else unresolved).append(r["name"])
    return {"resolved": resolved, "unresolved": unresolved}


def resolve_players(conn, season: str, understat_names: dict[str, str],
                    *, cutoff: float = 0.85) -> dict[str, list]:
    """Fuzzy-match FPL player full names to Understat player ids.

    ``understat_names`` maps understat_id -> player display name (from an
    Understat league pull). Returns resolved / unresolved / ambiguous lists.
    Unresolved players keep understat_id NULL (their Understat features stay NaN).
    """
    ov = _overrides(conn, "player", season)
    norm_index: dict[str, list[str]] = {}
    for uid, name in understat_names.items():
        norm_index.setdefault(_norm(name), []).append(uid)

    resolved, unresolved, ambiguous = [], [], []
    for r in conn.execute("SELECT player_id, full_name FROM player WHERE season=?", (season,)):
        key = str(r["player_id"])
        if key in ov:
            conn.execute("UPDATE player SET understat_id=? WHERE season=? AND player_id=?",
                         (ov[key], season, r["player_id"]))
            resolved.append(r["full_name"])
            continue
        nm = _norm(r["full_name"])
        uid = None
        if nm in norm_index and len(norm_index[nm]) == 1:
            uid = norm_index[nm][0]
        else:
            cand = difflib.get_close_matches(nm, list(norm_index), n=2, cutoff=cutoff)
            if len(cand) == 1 and len(norm_index[cand[0]]) == 1:
                uid = norm_index[cand[0]][0]
            elif len(cand) >= 2:
                ambiguous.append(r["full_name"])
        if uid:
            conn.execute("UPDATE player SET understat_id=? WHERE season=? AND player_id=?",
                         (uid, season, r["player_id"]))
            resolved.append(r["full_name"])
        else:
            unresolved.append(r["full_name"])
    return {"resolved": resolved, "unresolved": unresolved, "ambiguous": ambiguous}
