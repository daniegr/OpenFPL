"""Shared pytest fixtures: a tiny, offline, in-memory-style SQLite database."""
import os
import tempfile

import pytest

from fpl_engine import db


@pytest.fixture()
def conn():
    """A fresh, isolated SQLite DB seeded with a minimal, deterministic dataset."""
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    db.init_db(path)
    c = db.connect(path)

    # Two teams, season 2024-25.
    db.upsert(c, "team", [
        {"season": "2024-25", "team_id": 1, "name": "Alpha", "short_name": "ALP",
         "understat_name": "Alpha"},
        {"season": "2024-25", "team_id": 2, "name": "Beta", "short_name": "BET",
         "understat_name": "Beta"},
    ])
    # One player per team (a MID and a GK).
    db.upsert(c, "player", [
        {"season": "2024-25", "player_id": 10, "code": 1000, "web_name": "Mid",
         "full_name": "Mid Player", "team_id": 1, "position": "MID", "understat_id": None},
        {"season": "2024-25", "player_id": 20, "code": 2000, "web_name": "Keep",
         "full_name": "Keep Er", "team_id": 2, "position": "GK", "understat_id": None},
    ])
    # Player match history (GW1..GW3), plus a FUTURE GW4 row that must be excluded.
    pgw = []
    for gw, mins, pts in [(1, 90, 6), (2, 60, 2), (3, 90, 8), (4, 90, 99)]:
        kickoff = f"2024-08-0{gw}T14:00:00Z"
        pgw.append({"season": "2024-25", "gw": gw, "source": "vaastav",
                    "player_id": 10, "fixture_id": gw, "player_code": 1000,
                    "full_name": "Mid Player", "team_id": 1, "opponent_id": 2,
                    "was_home": 1, "kickoff_utc": kickoff, "minutes": mins,
                    "total_points": pts, "goals_scored": 1, "assists": 0,
                    "clean_sheets": 0, "goals_conceded": 1, "own_goals": 0,
                    "penalties_saved": 0, "penalties_missed": 0, "yellow_cards": 0,
                    "red_cards": 0, "saves": 0, "bonus": 0, "bps": 20,
                    "influence": 10.0, "creativity": 5.0, "threat": 8.0, "starts": 1})
    db.upsert(c, "player_gw", pgw)
    # Team match history for both clubs (used by team/opponent features).
    tm = []
    for gw in (1, 2, 3, 4):
        kickoff = f"2024-08-0{gw}T14:00:00Z"
        tm.append({"season": "2024-25", "team_id": 1, "fixture_id": gw, "gw": gw,
                   "kickoff_utc": kickoff, "opponent_id": 2, "was_home": 1,
                   "goals_for": 2, "goals_against": 1})
        tm.append({"season": "2024-25", "team_id": 2, "fixture_id": gw, "gw": gw,
                   "kickoff_utc": kickoff, "opponent_id": 1, "was_home": 0,
                   "goals_for": 1, "goals_against": 2})
    db.upsert(c, "team_match", tm)
    # A fixture defining the GW4 matchup we build features for.
    db.upsert(c, "fixture", [{
        "season": "2024-25", "fixture_id": 4, "gw": 4,
        "kickoff_utc": "2024-08-04T14:00:00Z", "team_h": 1, "team_a": 2,
        "team_h_score": None, "team_a_score": None, "finished": 0}])
    c.commit()
    yield c
    c.close()
    try:
        os.remove(path)
    except PermissionError:
        pass  # Windows: WAL handles can outlive close(); temp dir cleans up
