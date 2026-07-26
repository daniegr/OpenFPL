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

from .. import config, features, predict as predict_mod


def horizon_projections(conn, season: str, gws: list[int], *, bundle=None,
                        decay: float = 0.85) -> pd.DataFrame:
    """Return a projection dataframe indexed by player_id.

    Columns: player_id, player, position, team, team_id, price, available,
    ep_gw{g} for each g, and ep_total (decayed sum).
    """
    bundle = bundle or predict_mod.load_models()

    # Static player attributes (price, club, availability) for the season.
    attrs = {r["player_id"]: dict(r) for r in conn.execute(
        "SELECT player_id, full_name, position, team_id, now_cost, status, "
        "chance_next FROM player WHERE season=?", (season,))}
    team_name = {r["team_id"]: r["name"] for r in conn.execute(
        "SELECT team_id, name FROM team WHERE season=?", (season,))}

    from .. import progress
    ep_by_gw: dict[int, dict[int, float]] = {}
    for g in gws:
        progress.log(f"    projecting GW{g}…")
        try:
            df = features.build_samples(conn, season, g, include_ids=True)
        except ValueError:
            continue  # gw not scheduled
        preds = predict_mod.predict(df, bundle=bundle)
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
        total = 0.0
        for i, g in enumerate(gws):
            v = eps[g]
            if not np.isnan(v):
                total += (decay ** i) * v * avail
        row = {
            "player_id": pid, "player": a["full_name"], "position": a["position"],
            "team_id": a["team_id"], "team": team_name.get(a["team_id"]),
            "price": a["now_cost"] or 0.0, "available": avail,
            "ep_total": total,
        }
        for g in gws:
            row[f"ep_gw{g}"] = 0.0 if np.isnan(eps[g]) else eps[g] * avail
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
