"""Assemble expected points from the component models via the scoring YAML.

For each player and each of his team's fixtures in the target gameweek:

  exposure   = E[minutes]/90 from the minutes model
  E[goals]   = xG90 · exposure · fixture attack scaler (team model)
  E[assists] = xA90 · exposure · fixture attack scaler
  P(CS)      = P(60+) · exp(-λ_opponent)
  conceded   = E[floor(GA/2)] under GA ~ Poisson(λ_opponent), on-pitch share
  saves      = E[floor(S/3)] under S ~ Poisson(saves90·exposure)
  bonus/cards/residual = shrunk per-90 rates · exposure

Every point value comes from config/scoring_rules_*.yaml — the scoring engine
stays the single source of truth. Double gameweeks sum naturally over the
player's fixtures; blank gameweeks yield 0.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .. import scoring
from . import minutes_model, rates as rates_mod, team_model

ATTACK_SCALER_CAP = (0.55, 1.75)


def _e_floor_div(lam: float, per: int, kmax: int = 12) -> float:
    """E[floor(X/per)] for X ~ Poisson(lam)."""
    if lam <= 0:
        return 0.0
    e, p = 0.0, math.exp(-lam)
    total = p
    for k in range(1, kmax + 1):
        p *= lam / k
        total += p
        e += (k // per) * p
    # tail correction: assume tail mass sits at kmax
    e += (1 - total) * (kmax // per)
    return e


def first_kickoff(conn, season: str, gw: int) -> str | None:
    r = conn.execute(
        "SELECT MIN(kickoff_utc) k FROM fixture WHERE season=? AND gw=?",
        (season, gw)).fetchone()
    if r and r["k"]:
        return r["k"]
    r = conn.execute(   # historical seasons: fixtures live in team_match only
        "SELECT MIN(kickoff_utc) k FROM team_match WHERE season=? AND gw=?",
        (season, gw)).fetchone()
    return r["k"] if r and r["k"] else None


def _gw_fixtures(conn, season: str, gw: int) -> list:
    rows = conn.execute(
        "SELECT f.fixture_id, f.team_h, f.team_a, th.code hcode, ta.code acode "
        "FROM fixture f JOIN team th ON th.season=f.season AND th.team_id=f.team_h "
        "JOIN team ta ON ta.season=f.season AND ta.team_id=f.team_a "
        "WHERE f.season=? AND f.gw=?", (season, gw)).fetchall()
    if rows:
        return rows
    return conn.execute(   # derive from the home-perspective team_match rows
        "SELECT tm.fixture_id, tm.team_id team_h, tm.opponent_id team_a, "
        "th.code hcode, ta.code acode FROM team_match tm "
        "JOIN team th ON th.season=tm.season AND th.team_id=tm.team_id "
        "JOIN team ta ON ta.season=tm.season AND ta.team_id=tm.opponent_id "
        "WHERE tm.season=? AND tm.gw=? AND tm.was_home=1", (season, gw)).fetchall()


def xpts_predict_gw(conn, season: str, gw: int, *, as_of: str | None = None,
                    use_availability: bool = True,
                    minutes_bundle=None, rules: dict | None = None,
                    penalty_takers: dict[int, int] | None = None) -> pd.DataFrame:
    """Expected points per player for one gameweek (point-in-time at as_of).

    Returns player_id-indexed frame with the prediction and its components.
    ``penalty_takers`` maps player_id -> penalties_order (1 = first choice),
    available live from bootstrap; first-choice takers get a small xG90 boost.
    """
    rules = rules or scoring.load_rules()
    as_of = as_of or first_kickoff(conn, season, gw)
    if as_of is None:
        return pd.DataFrame()

    fixtures = _gw_fixtures(conn, season, gw)
    if not fixtures:
        return pd.DataFrame()

    tm = team_model.fit(conn, as_of)
    clf, meta = minutes_bundle or minutes_model.load()
    if clf is None:
        raise RuntimeError("minutes model not trained — run "
                           "`python -m fpl_engine backtest --retrain-minutes` once")
    mins = minutes_model.predict_gw(conn, season, as_of, clf, meta,
                                    use_availability=use_availability)
    rates = rates_mod.fit(conn, season, as_of, rules=rules)
    df = mins.merge(rates.drop(columns=["position"]), on="player_id", how="left")
    team_of = {r["player_id"]: r["team_id"] for r in conn.execute(
        "SELECT player_id, team_id FROM player WHERE season=?", (season,))}
    df["team_id"] = df["player_id"].map(team_of)

    # per-team fixture list: (λ_for, λ_against)
    team_fixtures: dict[int, list[tuple[float, float]]] = {}
    for f in fixtures:
        lh, la = tm.fixture(f["hcode"], f["acode"])
        team_fixtures.setdefault(f["team_h"], []).append((lh, la))
        team_fixtures.setdefault(f["team_a"], []).append((la, lh))
    league = max(1e-6, tm.league_rate)

    pen = penalty_takers or {}
    p_goal = rules["goal"]
    p_cs = rules["clean_sheet"]
    p_app_any, p_app_60 = rules["appearance"]["played_any"], rules["appearance"]["played_60"]
    gc_per, gc_pts = rules["goals_conceded"]["per"], rules["goals_conceded"]["points"]

    rows = []
    for r in df.itertuples():
        fx = team_fixtures.get(r.team_id, [])
        pos = r.position or "MID"
        exposure = (r.e_min or 0.0) / 90.0
        p_play = (r.p_sub or 0) + (r.p_full or 0)
        xg90 = (r.xg90 or 0.0) + (0.10 if pen.get(r.player_id) == 1 else 0.0)
        total = 0.0
        e_goals = e_assists = e_cs = 0.0
        for lam_for, lam_against in fx:
            scaler = float(np.clip(lam_for / league, *ATTACK_SCALER_CAP))
            g = xg90 * exposure * scaler
            a = (r.xa90 or 0.0) * exposure * scaler
            cs = (r.p_full or 0.0) * math.exp(-lam_against)
            e_goals += g
            e_assists += a
            e_cs += cs
            total += g * p_goal.get(pos, 4) + a * rules["assist"]
            total += cs * p_cs.get(pos, 0)
            if pos in ("GK", "DEF"):
                total += (gc_pts * _e_floor_div(lam_against * max(p_play, 0.0),
                                                gc_per))
            if pos == "GK":
                total += _e_floor_div((r.saves90 or 0.0) * exposure, rules["saves_per_point"])
            total += (r.bonus90 or 0.0) * exposure
            total += (r.yellow_cards90 or 0.0) * exposure * rules["card"]["yellow"]
            total += (r.residual90 or 0.0) * exposure
            total += (r.p_sub or 0.0) * p_app_any + (r.p_full or 0.0) * p_app_60
        rows.append({
            "player_id": r.player_id, "position": pos, "team_id": r.team_id,
            "n_fixtures": len(fx),
            "p_play": round(p_play, 4), "p_60": round(r.p_full or 0.0, 4),
            "e_min": round(r.e_min or 0.0, 1),
            "e_goals": round(e_goals, 3), "e_assists": round(e_assists, 3),
            "p_cs": round(e_cs, 3),
            "prediction": round(total, 3),
        })
    return pd.DataFrame(rows)
