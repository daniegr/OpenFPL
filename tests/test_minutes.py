"""Expected-minutes model: point-in-time discipline and factor behaviour."""
import os
import tempfile

import pytest

from fpl_engine import db, minutes

SEASON = "2025-26"
AS_OF = "2025-09-01T00:00:00Z"


def _match(code, pid, gw, mins, starts, day):
    return {"season": SEASON, "gw": gw, "source": "fpl", "player_id": pid,
            "fixture_id": gw, "player_code": code, "full_name": f"P{pid}",
            "team_id": 1, "opponent_id": 2, "was_home": 1,
            "kickoff_utc": f"2025-08-{day:02d}T14:00:00Z", "minutes": mins,
            "total_points": 2, "goals_scored": 0, "assists": 0,
            "clean_sheets": 0, "starts": starts}


@pytest.fixture()
def mconn():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    db.init_db(path)
    c = db.connect(path)
    db.upsert(c, "player", [
        # nailed starter, fit
        {"season": SEASON, "player_id": 1, "code": 100, "full_name": "Nailed",
         "team_id": 1, "position": "MID", "status": "a", "chance_next": None},
        # nailed starter, flagged 50%
        {"season": SEASON, "player_id": 2, "code": 200, "full_name": "Flagged",
         "team_id": 1, "position": "MID", "status": "d", "chance_next": 0.5},
        # was a starter, benched in the two most recent matches
        {"season": SEASON, "player_id": 3, "code": 300, "full_name": "Benched",
         "team_id": 1, "position": "MID", "status": "a", "chance_next": None},
        # new signing, no history
        {"season": SEASON, "player_id": 4, "code": 400, "full_name": "New",
         "team_id": 1, "position": "MID", "status": "a", "chance_next": None},
    ])
    rows = []
    for i, day in enumerate([5, 10, 15, 20, 25]):          # oldest -> newest
        rows.append(_match(100, 1, i + 1, 90, 1, day))     # always 90'
        rows.append(_match(200, 2, i + 1, 90, 1, day))     # always 90'
        # benched: started first 3, then two 10' cameos
        started = 1 if i < 3 else 0
        rows.append(_match(300, 3, i + 1, 90 if started else 10, started, day))
    # a FUTURE match (after as_of) that must be ignored
    rows.append(_match(100, 1, 9, 0, 0, 30))
    rows[-1]["kickoff_utc"] = "2025-09-05T14:00:00Z"
    db.upsert(c, "player_gw", rows)
    c.commit()
    yield c
    c.close()
    try:
        os.remove(path)
    except PermissionError:
        pass  # Windows file-lock flake; temp dir cleanup will get it


def test_nailed_starter_factor_is_one(mconn):
    prof = minutes.minutes_profiles(mconn, SEASON, AS_OF)[1]
    assert prof["p_start"] == 1.0
    assert prof["xmins"] == 90.0
    assert prof["factor"] == 1.0


def test_point_in_time_excludes_future_matches(mconn):
    # the future 0' match would drag p_start below 1 if it leaked in
    prof = minutes.minutes_profiles(mconn, SEASON, AS_OF)[1]
    assert prof["p_start"] == 1.0
    # with the boundary before all matches there is no history at all
    assert minutes.minutes_profiles(mconn, SEASON, "2025-08-01T00:00:00Z") == {}


def test_injury_flag_scales_expected_minutes(mconn):
    profs = minutes.minutes_profiles(mconn, SEASON, AS_OF)
    assert profs[2]["xmins"] == pytest.approx(45.0)
    assert profs[2]["factor"] == pytest.approx(0.5)


def test_benched_player_downweighted_below_trailing_average(mconn):
    prof = minutes.minutes_profiles(mconn, SEASON, AS_OF)[3]
    # recent benchings dominate via decay: xmins well below the plain average
    assert prof["baseline"] == pytest.approx((3 * 90 + 2 * 10) / 5)
    assert prof["xmins"] < prof["baseline"]
    assert 0 < prof["factor"] < 0.85


def test_no_history_means_no_profile(mconn):
    assert 4 not in minutes.minutes_profiles(mconn, SEASON, AS_OF)


def test_factor_never_exceeds_uplift_cap(mconn):
    for prof in minutes.minutes_profiles(mconn, SEASON, AS_OF).values():
        assert prof["factor"] <= minutes.MAX_UPLIFT


def test_season_break_uses_long_run_rate_not_stale_recency(mconn):
    """A nailed starter who missed the last two games of last season must not
    be punished at GW1: across a >GAP_DAYS gap the long window rules."""
    rows = []
    # 10 straight starts (days 1..10 of June), then two 0' matches (injured)
    for i in range(10):
        rows.append(_match(900, 9, i + 1, 90, 1, 1))
        rows[-1]["kickoff_utc"] = f"2025-06-{i + 1:02d}T14:00:00Z"
    for j, d in enumerate((12, 14)):
        rows.append(_match(900, 9, 11 + j, 0, 0, 1))
        rows[-1]["kickoff_utc"] = f"2025-06-{d:02d}T14:00:00Z"
    db.upsert(mconn, "player", [{"season": SEASON, "player_id": 9, "code": 900,
        "full_name": "Erling", "team_id": 1, "position": "FWD", "status": "a",
        "chance_next": None}])
    db.upsert(mconn, "player_gw", rows)
    mconn.commit()
    # in-season view (a week later): recent benchings bite
    soon = minutes.minutes_profiles(mconn, SEASON, "2025-06-21T00:00:00Z")[9]
    assert soon["stale"] is False and soon["factor"] < 0.9
    # pre-season view (two months later): long-run rate, uplift vs the
    # depressed short window, capped
    later = minutes.minutes_profiles(mconn, SEASON, "2025-08-15T00:00:00Z")[9]
    assert later["stale"] is True
    assert later["xmins"] == pytest.approx(75.0)          # 900/12 uniform
    assert later["factor"] == pytest.approx(minutes.MAX_UPLIFT)


def test_season_break_never_punishes_mid_season_absence(mconn):
    """Injured mid-season but a starter at the end: pre-season factor must be
    1.0 (avail), not the long-run average dragging him down."""
    rows = []
    for i in range(6):                                   # 6 x 0' (injured)
        rows.append(_match(910, 19, i + 1, 0, 0, 1))
        rows[-1]["kickoff_utc"] = f"2025-04-{i + 1:02d}T14:00:00Z"
    for i in range(6):                                   # then 6 x 90'
        rows.append(_match(910, 19, 7 + i, 90, 1, 1))
        rows[-1]["kickoff_utc"] = f"2025-05-{i + 1:02d}T14:00:00Z"
    db.upsert(mconn, "player", [{"season": SEASON, "player_id": 19, "code": 910,
        "full_name": "Cole", "team_id": 1, "position": "MID", "status": "a",
        "chance_next": None}])
    db.upsert(mconn, "player_gw", rows)
    mconn.commit()
    prof = minutes.minutes_profiles(mconn, SEASON, "2025-08-15T00:00:00Z")[19]
    assert prof["stale"] is True
    assert prof["xmins"] == pytest.approx(90.0)
    assert prof["factor"] == pytest.approx(1.0)
