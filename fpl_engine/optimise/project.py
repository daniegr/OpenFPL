"""Build a per-player projection table across a planning horizon.

Produces one row per player with position, club, price, availability and the
OpenFPL-projected points for each gameweek in the horizon (plus a discounted
total). This is the input the MILP optimises over.

Forward projections use current form: no matches occur between now and a future
gameweek, so each horizon gameweek's point-in-time features equal today's form
applied against that gameweek's fixture (opponent). Fixture difficulty therefore
still varies across the horizon via the opponent features.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, features, minutes, predict as predict_mod

# Pre-season prior blend (stop-gap for the form model's stale trailing
# windows): at GW1 the model EP is blended with last season's shrunken
# points-per-90 x expected minutes, fading to zero once PRESEASON_BLEND_GWS
# gameweeks of the new season have been played.
PRESEASON_BLEND_MAX = 0.5
PRESEASON_BLEND_GWS = 3
PRIOR_SHRINK_MINS = 450.0      # minutes of position-mean rate mixed into each player


def preseason_weight(n_played: int) -> float:
    """Weight on the last-season prior after ``n_played`` finished gameweeks."""
    return PRESEASON_BLEND_MAX * max(0.0, 1.0 - n_played / PRESEASON_BLEND_GWS)


def _played_gws(conn, season: str) -> int:
    return conn.execute(
        "SELECT COUNT(DISTINCT gw) FROM fixture WHERE season=? AND finished=1",
        (season,)).fetchone()[0]


def preseason_priors(conn, season: str, profiles: dict) -> dict[int, float]:
    """player_id -> prior EP per gw: last season's total points per 90
    (shrunk toward the position mean by PRIOR_SHRINK_MINS minutes) times the
    expected minutes from the minutes profile. Players without last-season
    minutes or without a profile get no prior."""
    y = int(season.split("-")[0])
    prev = f"{y - 1}-{str(y)[-2:]}"
    agg = {r["player_code"]: (r["pts"] or 0.0, r["mins"] or 0.0) for r in conn.execute(
        """
        WITH m AS (
            SELECT player_code, MAX(total_points) AS pts, MAX(minutes) AS mins
            FROM player_gw WHERE season=? AND player_code IS NOT NULL
            GROUP BY player_code, gw, fixture_id
        )
        SELECT player_code, SUM(pts) AS pts, SUM(mins) AS mins FROM m
        GROUP BY player_code
        """, (prev,))}
    players = conn.execute("SELECT player_id, code, position FROM player WHERE season=?",
                           (season,)).fetchall()
    # position means (minutes-weighted) for shrinkage
    tot: dict[str, list[float]] = {}
    for p in players:
        pts, mins = agg.get(p["code"], (0.0, 0.0))
        t = tot.setdefault(p["position"], [0.0, 0.0])
        t[0] += pts
        t[1] += mins
    pos90 = {pos: (v[0] / v[1] * 90.0 if v[1] else 0.0) for pos, v in tot.items()}
    out: dict[int, float] = {}
    for p in players:
        pts, mins = agg.get(p["code"], (0.0, 0.0))
        prof = profiles.get(int(p["player_id"]))
        if mins <= 0 or not prof:
            continue
        rate = (pts + pos90.get(p["position"], 0.0) * PRIOR_SHRINK_MINS / 90.0) \
            / (mins + PRIOR_SHRINK_MINS) * 90.0
        out[int(p["player_id"])] = rate * prof["xmins"] / 90.0
    return out


def horizon_projections(conn, season: str, gws: list[int], *, bundle=None,
                        decay: float = 0.85, retrained=None,
                        blend: float = 0.0) -> pd.DataFrame:
    """Return a projection dataframe indexed by player_id.

    Columns: player_id, player, position, team, team_id, price, available,
    xmins, ep_gw{g} for each g, and ep_total (decayed sum).

    EP is scaled per player by the expected-minutes factor from
    ``fpl_engine.minutes`` (injury flags + start-pattern shifts relative to
    the trailing baseline the model features already assume). Players with
    no match history fall back to the plain availability multiplier.
    """
    bundle = bundle or predict_mod.load_models()

    # Static player attributes (price, club, availability) for the season.
    attrs = {r["player_id"]: dict(r) for r in conn.execute(
        "SELECT player_id, full_name, position, team_id, now_cost, status, "
        "chance_next FROM player WHERE season=?", (season,))}
    team_name = {r["team_id"]: r["name"] for r in conn.execute(
        "SELECT team_id, name FROM team WHERE season=?", (season,))}

    # Expected-minutes profiles at the horizon's point-in-time boundary (no
    # matches occur inside the horizon, so one profile serves every gw).
    profiles = minutes.minutes_profiles(
        conn, season, features.gw_as_of(conn, season, gws[0]) if gws else None)
    prior_w = preseason_weight(_played_gws(conn, season))
    priors = preseason_priors(conn, season, profiles) if prior_w > 0 else {}

    from .. import progress
    ep_by_gw: dict[int, dict[int, float]] = {}
    for g in gws:
        progress.log(f"    projecting GW{g}…")
        try:
            df = features.build_samples(conn, season, g, include_ids=True)
        except ValueError:
            continue  # gw not scheduled
        preds = predict_mod.predict(df, bundle=bundle, retrained=retrained,
                                    blend=blend)
        preds = preds.reset_index(drop=True)
        # align predictions to player_id via the id column carried on df
        # (predict preserves row order within each position block, so join by
        # the metadata key instead to be safe)
        merged = preds.merge(
            df[["player", "team", "position", "player_id"]].drop_duplicates(
                ["player", "team", "position"]),
            on=["player", "team", "position"], how="left")
        ep_by_gw[g] = {int(pid): float(ep) for pid, ep in
                       zip(merged["player_id"], merged["prediction"])
                       if pd.notna(pid)}

    rows = []
    for pid, a in attrs.items():
        if a["position"] not in ("GK", "DEF", "MID", "FWD"):
            continue
        eps = {g: ep_by_gw.get(g, {}).get(pid, np.nan) for g in gws}
        if all(np.isnan(v) for v in eps.values()):
            continue  # never plays in the horizon (e.g. no fixture)
        avail = a["chance_next"] if a["chance_next"] is not None else (
            1.0 if a["status"] in (None, "a") else 0.0)
        prof = profiles.get(pid)
        factor = prof["factor"] if prof else avail
        prior = priors.get(pid)
        vals = {}
        for g in gws:
            v = eps[g]
            if np.isnan(v):
                vals[g] = 0.0
                continue
            v = v * factor
            if prior is not None and prior_w > 0:
                v = (1.0 - prior_w) * v + prior_w * prior
            vals[g] = v
        total = sum((decay ** i) * vals[g] for i, g in enumerate(gws)
                    if not np.isnan(eps[g]))
        row = {
            "player_id": pid, "player": a["full_name"], "position": a["position"],
            "team_id": a["team_id"], "team": team_name.get(a["team_id"]),
            "price": a["now_cost"] or 0.0, "available": avail,
            "xmins": prof["xmins"] if prof else None,
            "prior_w": prior_w if prior is not None else 0.0,
            "ep_total": total,
        }
        for g in gws:
            row[f"ep_gw{g}"] = vals[g]
        rows.append(row)

    proj = pd.DataFrame(rows)
    return proj.sort_values("ep_total", ascending=False).reset_index(drop=True)


def prune(proj: pd.DataFrame, *, keep_per_position: int = 30,
          cheap_per_position: int = 8, must_keep: set[int] | None = None) -> pd.DataFrame:
    """Keep the strongest and the cheapest players per position (plus owned).

    Shrinks the MILP without changing the optimum: only strong players, the
    incumbent squad, and cheap "enabler" fodder (needed to afford premiums under
    the £100m budget) can appear in an optimal 15. Keeping cheap options per
    position guarantees the budget constraint stays feasible.
    """
    must_keep = must_keep or set()
    keep = proj[proj["player_id"].isin(must_keep)]
    top = (proj.sort_values("ep_total", ascending=False)
               .groupby("position", group_keys=False).head(keep_per_position))
    cheap = (proj.sort_values(["price", "ep_total"], ascending=[True, False])
                 .groupby("position", group_keys=False).head(cheap_per_position))
    out = (pd.concat([top, cheap, keep])
             .drop_duplicates("player_id").reset_index(drop=True))
    return out
