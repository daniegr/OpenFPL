"""Unit tests for the canonical FPL scoring engine."""
from fpl_engine import scoring


def test_gk_clean_sheet_and_saves():
    ev = {"minutes": 90, "clean_sheets": 1, "saves": 6, "goals_conceded": 0}
    # 2 (60+) + 4 (GK CS) + 2 (6 saves / 3) = 8
    assert scoring.points_from_events(ev, "GK") == 8


def test_mid_goal_and_assist():
    ev = {"minutes": 90, "goals_scored": 1, "assists": 1, "bonus": 3}
    # 2 + 5 (MID goal) + 3 (assist) + 3 (bonus) = 13
    assert scoring.points_from_events(ev, "MID") == 13


def test_def_goals_conceded_penalty():
    ev = {"minutes": 90, "goals_conceded": 3}
    # 2 appearance - 1 (per 2 conceded, floor(3/2)=1) = 1
    assert scoring.points_from_events(ev, "DEF") == 1


def test_sub_appearance_point():
    assert scoring.points_from_events({"minutes": 20}, "FWD") == 1
    assert scoring.points_from_events({"minutes": 0}, "FWD") == 0


def test_cards_and_own_goal():
    ev = {"minutes": 90, "yellow_cards": 1, "own_goals": 1}
    # 2 - 1 (yellow) - 2 (OG) = -1
    assert scoring.points_from_events(ev, "MID") == -1


def test_defcon_threshold_crossing():
    rules = scoring.load_rules()
    thr = rules["defensive_contribution"]["threshold"]["DEF"]
    below = {"minutes": 90, "goals_conceded": 0, "defensive_contribution": thr - 1}
    at = {"minutes": 90, "goals_conceded": 0, "defensive_contribution": thr}
    assert scoring.points_from_events(at, "DEF") - scoring.points_from_events(below, "DEF") == 2


def test_defcon_excluded_helper():
    ev = {"minutes": 90, "defensive_contribution": 99}
    assert scoring.points_without_defcon(ev, "DEF") == 2  # appearance only
