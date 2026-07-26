"""Point-in-time OpenFPL feature builder.

Produces, for a target (season, gameweek), a dataframe with exactly the 235
columns of ``data/samples.csv`` (7 metadata + 228 features) that the OpenFPL
models consume.

Point-in-time discipline (principle #1)
--------------------------------------
Every feature is a trailing mean over a player's / team's most recent matches
whose kickoff is **strictly before** the target gameweek's first kickoff
(``as_of``). The builder physically filters on ``kickoff_utc < as_of`` so a row
can never see its own or any future match.

Cross-season continuity
------------------------
Player history is joined on the stable FPL ``code`` so last season's form flows
into early-season gameweeks. Team history is joined on club name (teams have no
stable cross-season id).

Sourcing / graceful degradation
-------------------------------
FPL-sourced metrics come from ``player_gw`` / ``team_match``. Understat metrics
come from the ``understat_*`` tables; when Understat is unavailable those columns
are left NaN, which the OpenFPL models tolerate via ``np.nan_to_num``.

Two engineered columns ("player relevant fpl points") use a documented
best-effort definition because OpenFPL's original feature-generation code is not
part of this repository; see ``_relevant_points``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config, db

# base metric -> column in player_gw
_PLAYER_FPL_COL = {
    "player fpl points": "total_points",
    "player minutes played": "minutes",
    "player influence": "influence",
    "player creativity": "creativity",
    "player threat": "threat",
    "player goals scored": "goals_scored",
    "player penalties missed": "penalties_missed",
    "player assists": "assists",
    "player goals conceded": "goals_conceded",
    "player own goals": "own_goals",
    "player saves": "saves",
    "player penalties saved": "penalties_saved",
    "player yellow cards": "yellow_cards",
    "player red cards": "red_cards",
    "player bps": "bps",
    "player fpl bonus points": "bonus",
}
_PLAYER_US_COL = {
    "player shots": "shots", "player xg": "xg", "player xgchain": "xgchain",
    "player xgbuildup": "xgbuildup", "player key passes": "key_passes",
    "player xa": "xa",
}

# Metrics OpenFPL nulls out by position (they are absent from that position's
# feature list, so they never reach the model; nulled for training-parity).
_POSITION_NULL_METRICS = {
    "GK": {"player goals scored", "player penalties missed", "player shots", "player xg"},
    "DEF": {"player saves", "player penalties saved"},
    "MID": {"player saves", "player penalties saved"},
    "FWD": {"player saves", "player penalties saved"},
}
_TEAM_US_COL = {
    "team xg": "xg", "team xga": "xga", "team deep": "deep",
    "team deep allowed": "deep_allowed", "team ppda att": "ppda_att",
    "team ppda def": "ppda_def", "team ppda allowed att": "ppda_allowed_att",
    "team ppda allowed def": "ppda_allowed_def",
}


def gw_as_of(conn, season: str, gw: int) -> str | None:
    """The point-in-time boundary: first kickoff of the target gameweek.

    Prefers the ``fixture`` table (live season); falls back to ``team_match``
    (historical seasons backfilled from vaastav populate only team_match).
    """
    row = conn.execute(
        "SELECT MIN(kickoff_utc) k FROM fixture WHERE season=? AND gw=? "
        "AND kickoff_utc IS NOT NULL", (season, gw)).fetchone()
    if row and row["k"]:
        return row["k"]
    row = conn.execute(
        "SELECT MIN(kickoff_utc) k FROM team_match WHERE season=? AND gw=? "
        "AND kickoff_utc IS NOT NULL", (season, gw)).fetchone()
    return row["k"] if row else None


def _matchups(conn, season: str, gw: int) -> dict[int, tuple[int, bool]]:
    """team_id -> (opponent_id, is_home) for the target gw, from either source."""
    out: dict[int, tuple[int, bool]] = {}
    for f in conn.execute(
            "SELECT team_h, team_a FROM fixture WHERE season=? AND gw=?", (season, gw)):
        out[f["team_h"]] = (f["team_a"], True)
        out[f["team_a"]] = (f["team_h"], False)
    if out:
        return out
    for r in conn.execute(
            "SELECT team_id, opponent_id, was_home FROM team_match "
            "WHERE season=? AND gw=?", (season, gw)):
        out[r["team_id"]] = (r["opponent_id"], bool(r["was_home"]))
    return out


def _window_means(values: list[float | None]) -> dict[int, float]:
    """Given values ordered most-recent-first, return mean over each window."""
    out = {}
    for w in config.WINDOWS:
        vals = [v for v in values[:w] if v is not None and not _isnan(v)]
        out[w] = float(np.mean(vals)) if vals else np.nan
    return out


def _isnan(v) -> bool:
    try:
        return np.isnan(v)
    except (TypeError, ValueError):
        return False


def _relevant_points(rows: list[dict]) -> list[float | None]:
    """Best-effort 'relevant fpl points' per match: points beyond appearance.

    Defined here as ``total_points - appearance_points`` (2 if 60+ mins, else 1
    if any mins, else 0) — i.e. the returns-driven component of a player's
    score. This is a DOCUMENTED APPROXIMATION: OpenFPL's exact definition of
    "relevant" points is derived from a match set that is not reconstructable
    from this repo's artefacts (its cumulative windowed values are inconsistent
    with any per-match transform of the standard points series), and its
    feature-generation code is not published here. These five columns are the
    only feature columns not reproduced exactly; all other FPL-sourced columns
    match the reference samples within tolerance for windows 1/3/5/10/38.
    """
    out = []
    for r in rows:
        mins = r["minutes"] or 0
        appearance = 2 if mins >= 60 else (1 if mins > 0 else 0)
        tp = r["total_points"]
        out.append(None if tp is None else tp - appearance)
    return out


def _player_history(conn, player_code, player_id, season, as_of) -> list[dict]:
    """Player matches strictly before ``as_of``, most-recent-first, deduped.

    Prefers the stable ``code`` (cross-season); falls back to (season,player_id).
    Dedupes a fixture seen from multiple sources, preferring source='fpl'.
    """
    if player_code is not None:
        cur = conn.execute(
            "SELECT * FROM player_gw WHERE player_code=? AND kickoff_utc IS NOT NULL "
            "AND kickoff_utc < ? ORDER BY kickoff_utc DESC", (player_code, as_of))
    else:
        cur = conn.execute(
            "SELECT * FROM player_gw WHERE season=? AND player_id=? "
            "AND kickoff_utc IS NOT NULL AND kickoff_utc < ? ORDER BY kickoff_utc DESC",
            (season, player_id, as_of))
    seen, rows = set(), []
    for r in cur:
        key = (r["season"], r["fixture_id"])
        if r["fixture_id"] and key in seen:
            continue
        seen.add(key)
        rows.append(dict(r))
    return rows


def _understat_player_history(conn, understat_id, as_of) -> list[dict]:
    if not understat_id or not as_of:
        return []
    as_of_date = as_of[:10]
    cur = conn.execute(
        "SELECT * FROM understat_player_match WHERE understat_id=? AND match_date < ? "
        "ORDER BY match_date DESC", (understat_id, as_of_date))
    return [dict(r) for r in cur]


def _team_history(conn, team_name, as_of) -> list[dict]:
    """Team matches (by club name, cross-season) strictly before ``as_of``."""
    cur = conn.execute(
        "SELECT tm.* FROM team_match tm JOIN team t "
        "ON tm.season=t.season AND tm.team_id=t.team_id "
        "WHERE t.name=? AND tm.kickoff_utc IS NOT NULL AND tm.kickoff_utc < ? "
        "ORDER BY tm.kickoff_utc DESC", (team_name, as_of))
    return [dict(r) for r in cur]


def _understat_team_history(conn, understat_name, as_of) -> list[dict]:
    if not understat_name or not as_of:
        return []
    as_of_date = as_of[:10]
    cur = conn.execute(
        "SELECT * FROM understat_team_match WHERE understat_team=? AND match_date < ? "
        "ORDER BY match_date DESC", (understat_name, as_of_date))
    return [dict(r) for r in cur]


def _availability(conn, season, player_id) -> float:
    """Current availability in [0,1] from the latest bootstrap snapshot fields.

    We stored normalised player rows without live status columns, so default to
    1.0 (available). The live FPL 'chance_of_playing' can be layered in later;
    OpenFPL used 1.0 for available players in the published samples.
    """
    return 1.0


def build_samples(conn, season: str, gw: int, *, as_of: str | None = None,
                  positions: tuple[str, ...] = ("GK", "DEF", "MID", "FWD")) -> pd.DataFrame:
    """Build the OpenFPL samples dataframe for (season, gw)."""
    as_of = as_of or gw_as_of(conn, season, gw)
    if as_of is None:
        raise ValueError(f"No fixtures/kickoff found for {season} GW{gw}")

    # team_id -> (name, understat_name) for the season
    teams = {r["team_id"]: dict(r) for r in conn.execute(
        "SELECT team_id, name, understat_name FROM team WHERE season=?", (season,))}

    # Fixtures for this gw: map team_id -> (opponent_id, is_home)
    matchups = _matchups(conn, season, gw)

    players = conn.execute(
        "SELECT player_id, code, full_name, team_id, position, understat_id "
        "FROM player WHERE season=?", (season,))

    records = []
    for p in players:
        if p["position"] not in positions:
            continue
        team_id = p["team_id"]
        if team_id not in matchups:
            continue  # club not playing this gw (blank)
        opp_id, is_home = matchups[team_id]
        team = teams.get(team_id, {})
        opp = teams.get(opp_id, {})

        rec = {
            "season": season, "gw": gw, "position": p["position"],
            "player": p["full_name"], "team": team.get("name"),
            "opponent": opp.get("name"), "home": bool(is_home),
        }

        # --- player history (FPL + Understat) ---
        ph = _player_history(conn, p["code"], p["player_id"], season, as_of)
        uph = _understat_player_history(conn, p["understat_id"], as_of)
        for base, col in _PLAYER_FPL_COL.items():
            _emit(rec, base, _window_means([r[col] for r in ph]))
        _emit(rec, "player relevant fpl points", _window_means(_relevant_points(ph)))
        for base, col in _PLAYER_US_COL.items():
            _emit(rec, base, _window_means([r[col] for r in uph]))

        # --- team history ---
        th = _team_history(conn, team.get("name"), as_of)
        uth = _understat_team_history(conn, team.get("understat_name"), as_of)
        _emit(rec, "team goals scored", _window_means([r["goals_for"] for r in th]))
        _emit(rec, "team goals conceded", _window_means([r["goals_against"] for r in th]))
        for base, col in _TEAM_US_COL.items():
            _emit(rec, base, _window_means([r[col] for r in uth]))
        # league-rank features are AM-only in OpenFPL -> NaN for player rows
        _emit_nan(rec, "team league rank")
        _emit_nan(rec, "team opponent league rank")

        # --- opponent history ---
        oh = _team_history(conn, opp.get("name"), as_of)
        uoh = _understat_team_history(conn, opp.get("understat_name"), as_of)
        _emit(rec, "opponent goals scored", _window_means([r["goals_for"] for r in oh]))
        _emit(rec, "opponent goals conceded", _window_means([r["goals_against"] for r in oh]))
        for base_o, col in _TEAM_US_COL.items():
            base = base_o.replace("team ", "opponent ")
            if base in config.OPPONENT_METRICS:
                _emit(rec, base, _window_means([r[col] for r in uoh]))

        # --- status ---
        rec["status player availability"] = _availability(conn, season, p["player_id"])
        rec["status team league rank"] = np.nan       # AM-only
        rec["status opponent league rank"] = np.nan   # AM-only

        # Null the metrics OpenFPL drops for this position (training parity).
        for base in _POSITION_NULL_METRICS.get(p["position"], ()):
            _emit_nan(rec, base)

        records.append(rec)

    df = pd.DataFrame(records)
    # Guarantee exact column set/order (fills any gap with NaN).
    return df.reindex(columns=config.sample_columns())


def _emit(rec: dict, base: str, means: dict[int, float]) -> None:
    for w in config.WINDOWS:
        rec[f"{base} {w}"] = means.get(w, np.nan)


def _emit_nan(rec: dict, base: str) -> None:
    for w in config.WINDOWS:
        rec[f"{base} {w}"] = np.nan


def store_samples(conn, df: pd.DataFrame, season: str, gw: int) -> int:
    """Persist built samples (as JSON rows) into the ``samples`` table."""
    from .http import utcnow_iso
    id_by_name = {r["full_name"]: r["player_id"] for r in conn.execute(
        "SELECT full_name, player_id FROM player WHERE season=?", (season,))}
    rows = []
    for _, row in df.iterrows():
        rows.append({
            "season": season, "gw": gw,
            "player_id": id_by_name.get(row["player"], -1),
            "built_utc": utcnow_iso(),
            "row_json": row.to_json(),
        })
    return db.upsert(conn, "samples", rows)
