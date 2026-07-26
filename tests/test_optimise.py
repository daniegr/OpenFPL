"""Tests for the multi-period MILP optimiser.

Uses a small, hand-built projection so constraint satisfaction and the
transfer / hit trade-off are deterministic and fast (no network, no models).
"""
import numpy as np
import pandas as pd
import pytest

from fpl_engine.optimise import milp


def _pool(n_per_pos=6, gws=(1,)):
    """A synthetic, budget-feasible player universe."""
    rows = []
    pid = 1
    quotas = {"GK": n_per_pos, "DEF": n_per_pos, "MID": n_per_pos, "FWD": n_per_pos}
    for pos, n in quotas.items():
        for k in range(n):
            row = {"player_id": pid, "player": f"{pos}{k}", "position": pos,
                   "team_id": (pid % 12) + 1, "price": 4.0 + (k % 5),
                   "ep_total": 0.0}
            for g in gws:
                row[f"ep_gw{g}"] = float(2 + (k * 1.3) % 7)  # varied points
            rows.append(row)
            pid += 1
    return pd.DataFrame(rows)


def _assert_valid_squad(gw_plan, pool):
    price = dict(zip(pool["player_id"], pool["price"]))
    pos = dict(zip(pool["player_id"], pool["position"]))
    names = {r["player"]: r["player_id"] for _, r in pool.iterrows()}
    squad = [names[n] for n, _, _ in gw_plan["squad"]]
    assert len(squad) == 15
    from collections import Counter
    c = Counter(pos[i] for i in squad)
    assert (c["GK"], c["DEF"], c["MID"], c["FWD"]) == (2, 5, 5, 3)
    # club limit
    club = dict(zip(pool["player_id"], pool["team_id"]))
    assert max(Counter(club[i] for i in squad).values()) <= 3
    # XI legal
    assert len(gw_plan["xi"]) == 11
    assert gw_plan["captain"] in gw_plan["xi"]


def test_scratch_squad_is_valid_and_within_budget():
    pool = _pool()
    plan = milp.build_from_scratch(pool, [1], budget=100.0, time_limit=20)
    assert plan.status == "Optimal"
    _assert_valid_squad(plan.per_gw[0], pool)


def test_no_pointless_transfers_when_squad_optimal():
    pool = _pool()
    scratch = milp.build_from_scratch(pool, [1], budget=100.0)
    names = {r["player"]: r["player_id"] for _, r in pool.iterrows()}
    owned = {names[n]: pool.set_index("player_id").loc[names[n], "price"]
             for n, _, _ in scratch.per_gw[0]["squad"]}
    plan = milp.optimise(pool, [1], initial=owned, bank=0.0, free_transfers=1)
    assert plan.per_gw[0]["n_transfers"] == 0  # already optimal -> hold


def test_takes_hit_only_when_worth_more_than_four():
    """With 1 FT, a 2nd transfer must be taken iff its marginal gain > 4."""
    # Two owned XI starters are near-worthless; two big upgrades are available.
    pool = _pool(n_per_pos=10)  # enough non-owned MIDs to buy two upgrades
    ep = {r.player_id: r.ep_gw1 for r in pool.itertuples()}
    names = {r.player: r.player_id for r in pool.itertuples()}
    # Own a valid 15 but force two MID starters to ~0 and make two elite MIDs.
    scratch = milp.build_from_scratch(pool, [1], budget=100.0)
    owned_names = [n for n, _, _ in scratch.per_gw[0]["squad"]]
    owned = {names[n]: pool.set_index("player_id").loc[names[n], "price"]
             for n in owned_names}

    # Elevate two non-owned MIDs to huge ep (clear upgrades over any bench cover).
    non_owned_mids = [pid for pid in pool[pool.position == "MID"].player_id
                      if pid not in owned][:2]
    for pid in non_owned_mids:
        pool.loc[pool.player_id == pid, "ep_gw1"] = 30.0
    # Cripple two owned MIDs so both are genuine holes with no good bench cover.
    owned_mids = [pid for pid in owned if pool.set_index("player_id").loc[pid, "position"] == "MID"][:2]
    for pid in owned_mids:
        pool.loc[pool.player_id == pid, "ep_gw1"] = 0.0

    plan = milp.optimise(pool, [1], initial=owned, bank=50.0, free_transfers=1,
                         budget=200.0, max_transfers_per_gw=3)
    g = plan.per_gw[0]
    # Second upgrade gains ~30 >> 4, so a hit must be taken (2 transfers, 1 hit).
    assert g["n_transfers"] == 2
    assert g["hits"] == 1
    assert g["free_used"] == 1
