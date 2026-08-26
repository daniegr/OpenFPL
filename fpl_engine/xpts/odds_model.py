"""Betting-odds → Poisson goal rates.

Bookmaker odds are the sharpest free forecast of match outcomes — they price
team news, rotation, motivation and everything else the trailing-form team
model cannot see. This module turns a match's 1X2 (+ optional over/under 2.5)
odds into implied Poisson goal rates (λ_home, λ_away) that the xpts engine
blends with its own team model:

1. **De-margin**: decimal odds imply probabilities that sum to >1 (the
   bookmaker's overround). Proportional normalisation removes it.
2. **Invert**: find the (λ_home, λ_away) whose independent-Poisson outcome
   probabilities best match the de-margined market — least squares over a
   precomputed λ grid, then local refinement. The totals market, when
   present, pins down the overall goal level that 1X2 alone leaves loose.

Everything here is a pure function of the odds — no I/O, no state. The
engine-side blend weight lives in ``ODDS_WEIGHT`` (fitted by the backtest
sweep; 0 disables odds entirely).
"""
from __future__ import annotations

import math
from functools import lru_cache

ODDS_WEIGHT = 0.85      # λ_final = (1-w)·team model + w·odds. Backtest sweep
                        # (2024-25 + 2025-26): active-player spearman and
                        # captain improve monotonically in w; 0.7-1.0 are
                        # within noise, 0.85 keeps a team-model floor for
                        # fixtures whose odds are missing or stale.

MAX_GOALS = 10          # truncation for Poisson outcome sums
GRID = [round(0.2 + 0.05 * i, 2) for i in range(77)]   # λ ∈ [0.2, 4.0]
_GRID_PROBS: list[tuple[float, float, float, float, float, float]] = []


def demargin(*odds: float) -> list[float]:
    """Decimal odds -> probabilities, proportionally stripped of overround.

    Any non-positive/missing odd invalidates the set (returns [])."""
    if not odds or any(o is None or o <= 1e-9 for o in odds):
        return []
    raw = [1.0 / o for o in odds]
    s = sum(raw)
    return [r / s for r in raw]


@lru_cache(maxsize=8192)
def _pois_pmf(lam: float) -> tuple[float, ...]:
    p, out = math.exp(-lam), []
    for k in range(MAX_GOALS + 1):
        out.append(p)
        p *= lam / (k + 1)
    return tuple(out)


def outcome_probs(lam_h: float, lam_a: float) -> tuple[float, float, float, float]:
    """(P(home), P(draw), P(away), P(total>2.5)) under independent Poissons."""
    ph = pa = pd_ = po = 0.0
    h, a = _pois_pmf(lam_h), _pois_pmf(lam_a)
    for i in range(MAX_GOALS + 1):
        for j in range(MAX_GOALS + 1):
            p = h[i] * a[j]
            if i > j:
                ph += p
            elif i == j:
                pd_ += p
            else:
                pa += p
            if i + j >= 3:
                po += p
    return ph, pd_, pa, po


def _grid_probs():
    if not _GRID_PROBS:
        for lh in GRID:
            for la in GRID:
                ph, pd_, pa, po = outcome_probs(lh, la)
                _GRID_PROBS.append((lh, la, ph, pd_, pa, po))
    return _GRID_PROBS


def implied_rates(p_home: float, p_draw: float, p_away: float,
                  p_over25: float | None = None) -> tuple[float, float]:
    """Solve (λ_home, λ_away) matching the de-margined market probabilities."""
    use_o = p_over25 is not None
    best, best_l = (1.4, 1.2), float("inf")
    for lh, la, ph, pd_, pa, po in _grid_probs():
        l = (ph - p_home) ** 2 + (pd_ - p_draw) ** 2 + (pa - p_away) ** 2
        if use_o:
            l += (po - p_over25) ** 2
        if l < best_l:
            best, best_l = (lh, la), l

    def loss(lh: float, la: float) -> float:
        ph, pd_, pa, po = outcome_probs(lh, la)
        l = (ph - p_home) ** 2 + (pd_ - p_draw) ** 2 + (pa - p_away) ** 2
        if use_o:
            l += (po - p_over25) ** 2
        return l

    lh, la = best
    step = 0.025
    for _ in range(3):                   # local refinement below grid pitch
        cands = [(max(lh + dh, 0.05), max(la + da, 0.05))
                 for dh in (-step, 0.0, step) for da in (-step, 0.0, step)]
        lh, la = min(cands, key=lambda c: loss(*c))
        step /= 2
    return round(lh, 4), round(la, 4)


def fixture_odds_map(conn, season: str, fixture_ids: list[int]) -> dict[int, tuple[float, float]]:
    """{fixture_id: (λ_home, λ_away)} for the fixtures that have stored odds."""
    if not fixture_ids:
        return {}
    qs = ",".join("?" * len(fixture_ids))
    rows = conn.execute(
        f"SELECT fixture_id, lam_home, lam_away FROM match_odds "
        f"WHERE season=? AND fixture_id IN ({qs}) "
        f"AND lam_home IS NOT NULL AND lam_away IS NOT NULL",
        [season] + [int(f) for f in fixture_ids]).fetchall()
    return {int(r["fixture_id"]): (float(r["lam_home"]), float(r["lam_away"]))
            for r in rows}
