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


def _subset_matches(full_name: str, players: list[tuple[str, str]],
                    *, min_tokens: int = 1) -> list[str]:
    """Understat names whose tokens are all contained in the FPL full name
    ("Alejandro Garnacho" in "Alejandro Garnacho Ferreyra", "Kaoru Mitoma" in
    "Mitoma Kaoru", "Alisson" in "Alisson Becker")."""
    full = set(_norm(full_name).split())
    out = []
    for uid, uname in players:
        toks = uname.split()
        if len(toks) >= min_tokens and toks and set(toks) <= full:
            out.append(uid)
    return out


def _match_in_club(full_name: str, web_name: str | None,
                   club_players: list[tuple[str, str]]) -> str | None:
    """Within one club: (1) an Understat name equal to the FPL web_name wins
    outright ("Gabriel"); (2) an Understat name whose tokens all appear in
    the FPL full name; (3) the web_name surname as a token with an agreeing
    first initial ("Benjamin White" -> "Ben White").
    Returns uid, "ambiguous", or None - never guesses between candidates."""
    key = _norm((web_name or "").split(".")[-1])   # "B.Fernandes" -> "fernandes"
    if len(key) < 3:
        key = _norm((web_name or "").split(".")[0])  # "Bruno G." -> "bruno"
    if key and len(key) >= 3:
        exact = [uid for uid, uname in club_players if uname == key]
        if len(exact) == 1:
            return exact[0]
    sub = _subset_matches(full_name, club_players)
    if len(sub) == 1:
        return sub[0]
    if len(sub) > 1:
        return "ambiguous"
    if not key or len(key) < 3:
        return None
    first = _norm(full_name).split()[0][:1] if _norm(full_name) else ""
    hits = []
    for uid, uname in club_players:
        toks = uname.split()
        if key not in toks and key != uname:
            continue
        if first and len(toks) > 1 and not any(t.startswith(first) for t in toks[:-1]):
            continue   # surname matches but first initial disagrees
        hits.append(uid)
    if len(hits) == 1:
        return hits[0]
    return "ambiguous" if len(hits) > 1 else None


def resolve_players(conn, season: str, understat_names: dict[str, str],
                    *, cutoff: float = 0.85,
                    understat_teams: dict[str, str] | None = None) -> dict[str, list]:
    """Fuzzy-match FPL player full names to Understat player ids.

    ``understat_names`` maps understat_id -> player display name (from an
    Understat league pull). Pass 1 matches normalised full names (exact, then
    fuzzy). Pass 2, when ``understat_teams`` (understat_id -> club title) is
    given, matches the remaining players *within their own club* on the FPL
    ``web_name`` - this is what catches Understat "Bruno Fernandes" vs FPL
    "Bruno Miguel Borges Fernandes", "Ben White" vs "Benjamin White". A club
    with two candidates is reported ambiguous, never guessed. Returns resolved
    / unresolved / ambiguous lists; unresolved players keep understat_id NULL
    (their Understat features stay NaN and FPL xG stand-ins apply).
    """
    ov = _overrides(conn, "player", season)
    norm_index: dict[str, list[str]] = {}
    for uid, name in understat_names.items():
        norm_index.setdefault(_norm(name), []).append(uid)
    by_club: dict[str, list[tuple[str, str]]] = {}   # club title -> [(uid, norm name)]
    for uid, title in (understat_teams or {}).items():
        if uid in understat_names:
            by_club.setdefault(title, []).append((uid, _norm(understat_names[uid])))
    all_players = [(uid, _norm(n)) for uid, n in understat_names.items()] \
        if understat_teams else []

    resolved, unresolved, ambiguous = [], [], []
    for r in conn.execute(
            "SELECT p.player_id, p.full_name, p.web_name, t.understat_name AS club "
            "FROM player p LEFT JOIN team t ON t.season=p.season AND t.team_id=p.team_id "
            "WHERE p.season=?", (season,)):
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
                continue
        if uid is None and by_club.get(r["club"]):
            uid = _match_in_club(r["full_name"], r["web_name"], by_club[r["club"]])
            if uid == "ambiguous":
                ambiguous.append(r["full_name"])
                continue
        if uid is None and all_players:
            # summer transfers: Understat still lists the player at last
            # season's club -> league-wide, but only a unique >=2-token subset
            sub = _subset_matches(r["full_name"], all_players, min_tokens=2)
            if len(sub) == 1:
                uid = sub[0]
            elif len(sub) > 1:
                ambiguous.append(r["full_name"])
                continue
        if uid:
            conn.execute("UPDATE player SET understat_id=? WHERE season=? AND player_id=?",
                         (uid, season, r["player_id"]))
            resolved.append(r["full_name"])
        else:
            unresolved.append(r["full_name"])
    return {"resolved": resolved, "unresolved": unresolved, "ambiguous": ambiguous}
