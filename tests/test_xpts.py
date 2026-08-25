"""Unit tests for the xPts component engine (no network, no real models)."""
import math

import numpy as np
import pandas as pd
import pytest

from fpl_engine.xpts import team_model
from fpl_engine.xpts.engine import _e_floor_div


# ---------------------------------------------------------------- team model

class _FakeConn:
    """Minimal sqlite3.Row-ish cursor over dict rows."""
    def __init__(self, rows):
        self.rows = rows

    def execute(self, q, args=()):
        class _C:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows
        return _C(self.rows)


def _match(code, opp, home, gf, kick="2025-01-01T15:00:00Z"):
    return {"season": "s", "team_id": 0, "opponent_id": 0, "was_home": home,
            "kickoff_utc": kick, "goals_for": gf, "xg": None,
            "code": code, "opp_code": opp}


def test_team_model_orders_strengths():
    rows = []
    # team 1 scores 3 per game, team 2 scores 1, team 3 concedes heavily
    for k in range(8):
        kick = f"2025-01-{k+1:02d}T15:00:00Z"
        rows.append(_match(1, 3, 1, 3, kick))
        rows.append(_match(3, 1, 0, 0, kick))
        rows.append(_match(2, 3, 1, 1, kick))
        rows.append(_match(3, 2, 0, 0, kick))
    tm = team_model.fit(_FakeConn(rows), "2025-02-01T00:00:00Z")
    assert tm.attack[1] > tm.attack[2]            # 1 is the stronger attack
    lam1, _ = tm.fixture(1, 3)
    lam2, _ = tm.fixture(2, 3)
    assert lam1 > lam2 > 0
    # clean-sheet probability is a proper probability and higher against 3
    # (who never scores) than against 1
    p_vs3 = tm.p_clean_sheet(2, 3, True)
    p_vs1 = tm.p_clean_sheet(2, 1, True)
    assert 0 < p_vs1 < p_vs3 <= 1


def test_team_model_shrinks_unknown_teams_to_prior():
    tm = team_model.fit(_FakeConn([]), "2025-02-01T00:00:00Z")
    lam_h, lam_a = tm.fixture(999, 998)   # two teams it has never seen
    assert lam_h > lam_a                   # home advantage survives shrinkage
    assert 0.3 < lam_h < 3.0               # sane league-ish goal rate


# ---------------------------------------------------------- floor-division E

def test_e_floor_div_matches_monte_carlo():
    rng = np.random.default_rng(7)
    for lam, per in ((1.3, 2), (2.8, 3), (0.4, 2)):
        sim = np.floor_divide(rng.poisson(lam, 200_000), per).mean()
        assert _e_floor_div(lam, per) == pytest.approx(sim, abs=0.01)


def test_e_floor_div_edge_cases():
    assert _e_floor_div(0.0, 2) == 0.0
    assert _e_floor_div(-1.0, 3) == 0.0
    assert _e_floor_div(1e-9, 2) < 1e-6


# ------------------------------------------------------------- scoring hookup

def test_scoring_yaml_drives_combination():
    """The engine must take point values from the YAML, not constants."""
    from fpl_engine import scoring
    rules = scoring.load_rules()
    # spot-check the fields the combiner reads
    assert set(rules["goal"]) == {"GK", "DEF", "MID", "FWD"}
    assert rules["appearance"]["played_60"] == 2
    assert rules["goals_conceded"]["per"] >= 1
    assert isinstance(rules["assist"], int)
