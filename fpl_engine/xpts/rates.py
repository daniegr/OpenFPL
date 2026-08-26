"""Per-90 event rates with empirical-Bayes shrinkage.

For each player (identified by stable ``player_code`` so history crosses
seasons) we estimate time-decayed per-90 rates for the events the scoring
rules pay for: xG, xA, saves, yellow cards — plus two structured extras:

* **Bonus** is not a flat rate: it is driven by the same events the fixture
  scaler multiplies (goals, assists, clean sheets). We fit a league-wide
  per-position weighted least squares ``bonus ~ goals + assists + cs`` and
  keep only each player's *deviation* from that fit as a flat per-90 rate
  (``bonus_resid90``); the engine reconstructs E[bonus] from its own expected
  events, so a striker in a great fixture is credited the bonus that comes
  with the goals he is expected to score there. The coefficients ride along
  in ``DataFrame.attrs["bonus_coef"]`` (read them before merging — pandas
  drops attrs on merge).
* **Residual rate** = actual points minus reconstructed points, which absorbs
  DefCon (no raw tackles/CBI stats exist in the DB). DefCon has only existed
  since the season named in the scoring rules
  (``defensive_contribution.since``); matches before that era are excluded
  from the residual estimate — otherwise a DefCon regular's rate is diluted
  toward zero by seasons where the rule did not exist. Era data is scarce,
  so the residual uses a smaller shrinkage constant than the base stats.

Shrinkage: rate = (Σ w·stat + k·prior_pos) / (Σ w·mins/90 + k) — a player with
little recent playing time regresses to his position's league rate instead of
producing wild small-sample estimates. Priors are computed from the data at
fit time, never hardcoded.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from .. import scoring

HALF_LIFE_DAYS = 240.0
SEASON_BREAK_DECAY = 0.7    # extra weight factor per season boundary — an
                            # outlier season (Salah 2024-25) must not be
                            # carried whole across the summer
K_EFFECTIVE_90S = 6.0
K_RESIDUAL_90S = 3.0    # DefCon era is short — trust the player's own rate sooner
BASE_STATS = ["xg", "xa", "saves", "yellow_cards"]
STATS = BASE_STATS + ["bonus_resid", "defcon_cross", "residual"]


def _parse(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True, format="ISO8601")


def fit(conn, season: str, as_of: str, *, rules: dict | None = None) -> pd.DataFrame:
    """Return one row per current-season player with shrunk per-90 rates.

    Uses all player_gw history strictly before ``as_of`` across seasons.
    League-level bonus coefficients ride along in ``.attrs["bonus_coef"]``.
    """
    rules = rules or scoring.load_rules()
    hist = pd.read_sql_query(
        "SELECT pg.season, pg.player_code, pg.kickoff_utc, pg.minutes, pg.total_points, "
        "pg.goals_scored, pg.assists, pg.clean_sheets, pg.goals_conceded, "
        "pg.own_goals, pg.penalties_saved, pg.penalties_missed, "
        "pg.yellow_cards, pg.red_cards, pg.saves, pg.bonus, pg.xg, pg.xa, pg.defcon, "
        "(SELECT position FROM player p WHERE p.season=pg.season "
        " AND p.player_id=pg.player_id) position "
        "FROM player_gw pg WHERE pg.kickoff_utc < ? AND pg.minutes > 0",
        conn, params=(as_of,))
    players = pd.read_sql_query(
        "SELECT player_id, code player_code, position FROM player "
        "WHERE season=?", conn, params=(season,))
    if hist.empty:
        out = players.copy()
        for s in STATS:
            out[f"{s}90"] = 0.0
        out["exposure"] = 0.0
        out = out[["player_id", "player_code", "position", "exposure"]
                  + [f"{s}90" for s in STATS]]
        out.attrs["bonus_coef"] = {}
        return out

    # decay weights first — every estimate below is decay-weighted
    ref = pd.Timestamp(datetime.fromisoformat(as_of.replace("Z", "+00:00")))
    days = (ref - _parse(hist["kickoff_utc"])).dt.days.clip(lower=0)
    hist["w"] = 0.5 ** (days / HALF_LIFE_DAYS)
    if SEASON_BREAK_DECAY < 1.0:   # an outlier season shouldn't be carried whole
        cur = int(season[:4])
        n_breaks = (cur - hist["season"].str[:4].astype(int)).clip(lower=0)
        hist["w"] *= SEASON_BREAK_DECAY ** n_breaks
    hist["w90"] = hist["w"] * hist["minutes"].fillna(0) / 90.0

    # DefCon: raw counts exist from the rule era on (vaastav/FPL both publish
    # them); the modelled event is *crossing the position threshold*
    thr = (rules.get("defensive_contribution") or {}).get("threshold", {})
    thr_of = hist["position"].map(thr).astype(float)   # NaN -> never crosses
    hist["defcon_cross"] = np.where(
        hist["defcon"].notna(),
        (hist["defcon"] >= thr_of.fillna(np.inf)).astype(float), np.nan)
    hist["has_dc"] = hist["defcon"].notna().astype(float)

    # residual = actual points - full reconstruction (including actual DefCon
    # where counts exist) -> genuinely unmodelled scraps only
    def _base_points(r):
        t = thr.get(r.position or "MID")
        crossed = (1 if (r.defcon is not None and t is not None
                         and r.defcon >= t) else 0)
        return scoring.points_from_events({
            "minutes": r.minutes, "goals_scored": r.goals_scored,
            "assists": r.assists, "clean_sheets": r.clean_sheets,
            "goals_conceded": r.goals_conceded, "own_goals": r.own_goals,
            "penalties_saved": r.penalties_saved,
            "penalties_missed": r.penalties_missed,
            "yellow_cards": r.yellow_cards, "red_cards": r.red_cards,
            "saves": r.saves, "bonus": r.bonus,
            "defensive_contribution": crossed,
        }, r.position or "MID", rules=rules)

    hist["residual"] = (hist["total_points"].fillna(0)
                        - np.array([_base_points(r) for r in hist.itertuples()]))
    era = (rules.get("defensive_contribution") or {}).get("since")
    hist["in_era"] = 1.0 if era is None else (hist["season"] >= era).astype(float)

    # xg missing (old seasons without Understat/Opta) -> fall back to goals
    hist["xg"] = hist["xg"].fillna(hist["goals_scored"]).fillna(0)
    # FPL assists are much broader than Opta xA (rebounds, won penalties,
    # deflected passes all count), so pure xA systematically lowballs the
    # assist rate (GW1 2026-27: league xA 15.4 vs 24 FPL assists). Blend the
    # stable estimator with the realised FPL-definition rate 50/50.
    hist["xa"] = np.where(hist["xa"].notna(),
                          0.5 * hist["xa"].fillna(0) + 0.5 * hist["assists"].fillna(0),
                          hist["assists"].fillna(0))

    # league bonus structure: per-position weighted least squares on the
    # events the engine models; each player keeps only his deviation
    hb = hist.dropna(subset=["position"])
    bonus_coef: dict[str, list[float]] = {}
    for pos, d in hb.groupby("position"):
        X = np.c_[d["goals_scored"].fillna(0), d["assists"].fillna(0),
                  d["clean_sheets"].fillna(0), np.ones(len(d))]
        y = d["bonus"].fillna(0).to_numpy(float)
        sw = np.sqrt(d["w"].to_numpy(float))
        coef, *_ = np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)
        bonus_coef[pos] = [float(v) for v in coef]
    for name, i in (("g", 0), ("a", 1), ("cs", 2), ("c0", 3)):
        hist[f"_bc_{name}"] = hist["position"].map(
            {p: c[i] for p, c in bonus_coef.items()}).fillna(0.0)
    hist["bonus_resid"] = (hist["bonus"].fillna(0)
                           - hist["_bc_g"] * hist["goals_scored"].fillna(0)
                           - hist["_bc_a"] * hist["assists"].fillna(0)
                           - hist["_bc_cs"] * hist["clean_sheets"].fillna(0)
                           - hist["_bc_c0"])

    gates = {"residual": hist["in_era"], "defcon_cross": hist["has_dc"]}
    for s in STATS:
        hist[f"_w_{s}"] = hist["w"] * gates.get(s, 1.0) * hist[s].fillna(0)
    hist["w90_era"] = hist["w90"] * hist["in_era"]
    hist["w90_dc"] = hist["w90"] * hist["has_dc"]

    agg = hist.groupby("player_code").agg(
        exposure=("w90", "sum"), exposure_era=("w90_era", "sum"),
        exposure_dc=("w90_dc", "sum"),
        **{f"sum_{s}": (f"_w_{s}", "sum") for s in STATS})

    # position priors: league per-90 rate per position (exposure-weighted);
    # the residual prior comes from the DefCon era only
    hist_pos = hist.dropna(subset=["position"])
    era_pos = hist_pos[hist_pos["in_era"] > 0]
    dc_pos = hist_pos[hist_pos["has_dc"] > 0]
    prior = {}
    for s in STATS:
        src = hist_pos
        if s == "residual" and len(era_pos):
            src = era_pos
        elif s == "defcon_cross":
            src = dc_pos if len(dc_pos) else hist_pos.iloc[0:0]
        if not len(src):
            prior[s] = {}
            continue
        by_pos = src.groupby("position").apply(
            lambda d, s=s: (d["w"] * d[s].fillna(0)).sum()
            / max(1e-9, d["w90"].sum()), include_groups=False)
        prior[s] = by_pos.to_dict()

    out = players.merge(agg, left_on="player_code", right_index=True, how="left")
    out["exposure"] = out["exposure"].fillna(0.0)
    out["exposure_era"] = out["exposure_era"].fillna(0.0)
    out["exposure_dc"] = out["exposure_dc"].fillna(0.0)
    expo_of = {"residual": out["exposure_era"], "defcon_cross": out["exposure_dc"]}
    for s in STATS:
        pri = out["position"].map(prior[s]).fillna(0.0)
        k = (K_RESIDUAL_90S if s in ("residual", "defcon_cross")
             else K_EFFECTIVE_90S)
        expo = expo_of.get(s, out["exposure"])
        out[f"{s}90"] = ((out[f"sum_{s}"].fillna(0) + k * pri) / (expo + k))
    out = out[["player_id", "player_code", "position", "exposure"]
              + [f"{s}90" for s in STATS]]
    out.attrs["bonus_coef"] = bonus_coef
    return out
