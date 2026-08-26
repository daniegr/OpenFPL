"""Odds → Poisson-rate conversion (pure functions, no I/O)."""
import math

from fpl_engine.xpts import odds_model as om


def test_demargin_strips_overround_proportionally():
    p = om.demargin(2.0, 3.5, 4.0)
    assert math.isclose(sum(p), 1.0, abs_tol=1e-9)
    assert p[0] > p[2] > p[1] * 0.8          # ordering follows the odds
    raw = [1 / 2.0, 1 / 3.5, 1 / 4.0]
    assert math.isclose(p[0] / p[1], raw[0] / raw[1], rel_tol=1e-9)


def test_demargin_rejects_missing_or_bad_odds():
    assert om.demargin(2.0, None, 4.0) == []
    assert om.demargin(2.0, 0.0, 4.0) == []
    assert om.demargin() == []


def test_outcome_probs_sum_to_one():
    ph, pd_, pa, _ = om.outcome_probs(1.5, 1.1)
    assert math.isclose(ph + pd_ + pa, 1.0, abs_tol=1e-6)


def test_implied_rates_round_trip():
    for true in [(1.8, 1.0), (1.2, 1.2), (2.6, 0.7), (0.8, 1.9)]:
        rec = om.implied_rates(*om.outcome_probs(*true))
        assert abs(rec[0] - true[0]) < 0.05 and abs(rec[1] - true[1]) < 0.05


def test_implied_rates_without_totals_market():
    ph, pd_, pa, _ = om.outcome_probs(1.6, 1.0)
    lh, la = om.implied_rates(ph, pd_, pa, None)
    assert lh > la                            # ordering preserved
    assert 0.2 <= la <= lh <= 4.0
