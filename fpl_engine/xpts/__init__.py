"""xPts — component-based expected-points engine.

Instead of one regression on total points (the OpenFPL approach), xPts models
the structured processes that *generate* points and combines them through the
canonical scoring rules (config/scoring_rules_*.yaml):

  minutes model   -> P(0), P(1-59), P(60+) per player per match
  team model      -> time-decayed Poisson attack/defence -> fixture goal rates,
                     P(clean sheet), expected goals conceded
  event rates     -> empirical-Bayes-shrunk per-90 xG/xA/saves/bonus/cards and
                     a residual rate that absorbs DefCon + anything unmodelled
  combine         -> E[points] assembled from the pieces via the scoring YAML

Everything is point-in-time (only matches with kickoff < as_of are used) and
the scoring engine remains the single source of truth for point values.
"""
from .engine import xpts_predict_gw  # noqa: F401
