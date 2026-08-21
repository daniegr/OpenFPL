"""Tests for the chip-aware optimiser (fpl_engine.optimise.chips).

Synthetic pools keep the MILP tiny and deterministic: no network, no models.
"""
import pandas as pd

from fpl_engine.optimise import chips, milp


def _pool(n_per_pos=6, gws=(1, 2)):
    rows, pid = [], 1
    for pos, n in {"GK": n_per_pos, "DEF": n_per_pos,
                   "MID": n_per_pos, "FWD": n_per_pos}.items():
        for k in range(n):
            row = {"player_id": pid, "player": f"{pos}{k}", "position": pos,
                   "team_id": (pid % 12) + 1, "price": 4.0 + (k % 5),
                   "ep_total": 0.0}
            for g in gws:
                row[f"ep_gw{g}"] = float(2 + (k * 1.3) % 7)
            rows.append(row)
            pid += 1
    return pd.DataFrame(rows)


def _owned(pool, gws=(1, 2)):
    scratch = milp.build_from_scratch(pool, list(gws)[:1], budget=100.0)
    names = {r.player: r.player_id for r in pool.itertuples()}
    return {names[n]: float(pool.set_index("player_id").loc[names[n], "price"])
            for n, _, _ in scratch.per_gw[0]["squad"]}


def test_no_chips_matches_base_structure():
    pool = _pool()
    owned = _owned(pool)
    plans = chips.optimise_with_chips(pool, [1, 2], initial=owned, bank=0.0,
                                      free_transfers=1, ft_value=0.0)
    assert len(plans) == 1
    p = plans[0]
    assert p.status == "Optimal"
    for g in p.per_gw:
        assert len(g["squad"]) == 15
        assert sum(s["in_xi"] for s in g["squad"]) == 11
        assert g["chip"] is None


def test_triple_captain_played_on_best_week():
    pool = _pool()
    owned = _owned(pool)
    # make GW2 clearly the bigger week for the best player
    best = pool.sort_values("ep_gw1", ascending=False).iloc[0].player_id
    pool.loc[pool.player_id == best, "ep_gw2"] = 20.0
    plans = chips.optimise_with_chips(
        pool, [1, 2], initial=owned, bank=0.0, free_transfers=1, ft_value=0.0,
        chip_gws={"triple_captain": [1, 2]})
    played = [g for g in plans[0].per_gw if g["chip"] == "triple_captain"]
    assert len(played) == 1
    assert played[0]["gw"] == 2  # TC lands on the 20-point captain week


def test_bench_boost_counts_bench():
    pool = _pool()
    owned = _owned(pool)
    plans = chips.optimise_with_chips(
        pool, [1], initial=owned, bank=0.0, free_transfers=1, ft_value=0.0,
        chip_gws={"bench_boost": [1]}, chip_force={"bench_boost": 1})
    g = plans[0].per_gw[0]
    assert g["chip"] == "bench_boost"
    total = sum(s["ep"] for s in g["squad"])
    cap_ep = next(s["ep"] for s in g["squad"] if s["is_captain"])
    assert abs(g["xi_points"] - (total + cap_ep)) < 0.05


def test_free_hit_reverts_squad():
    pool = _pool(n_per_pos=8, gws=(1, 2, 3))
    owned = _owned(pool, gws=(1, 2, 3))
    # make one non-owned MID enormous in GW2 only -> ideal Free Hit bait
    mids = [p for p in pool[pool.position == "MID"].player_id if p not in owned]
    pool.loc[pool.player_id == mids[0], "ep_gw2"] = 40.0
    plans = chips.optimise_with_chips(
        pool, [1, 2, 3], initial=owned, bank=0.0, free_transfers=1,
        ft_value=0.0, max_transfers_per_gw=1,
        chip_gws={"freehit": [2]}, chip_force={"freehit": 2})
    per = plans[0].per_gw
    assert per[1]["chip"] == "freehit"
    # FH week fields the monster; the week after, the squad reverts (he is
    # gone again unless transferred in normally)
    fh_ids = {s["player_id"] for s in per[1]["squad"]}
    assert mids[0] in fh_ids
    ids_before = {s["player_id"] for s in per[0]["squad"]}
    ids_after = {s["player_id"] for s in per[2]["squad"]}
    moved_after = {p["player_id"] for p in per[2]["transfers_in"]}
    assert ids_after - moved_after <= ids_before  # continuity skips FH week
    assert per[1]["n_transfers"] == 0
    assert per[1]["hits"] == 0


def test_wildcard_makes_transfers_free():
    pool = _pool(n_per_pos=10)
    owned = _owned(pool)
    # cripple three owned mids, add three elite replacements -> WC week
    df = pool.set_index("player_id")
    owned_mids = [p for p in owned if df.loc[p, "position"] == "MID"][:3]
    free_mids = [p for p in pool[pool.position == "MID"].player_id
                 if p not in owned][:3]
    for p in owned_mids:
        pool.loc[pool.player_id == p, ["ep_gw1", "ep_gw2"]] = 0.0
    for p in free_mids:
        pool.loc[pool.player_id == p, ["ep_gw1", "ep_gw2"]] = 25.0
    plans = chips.optimise_with_chips(
        pool, [1, 2], initial=owned, bank=50.0, free_transfers=1,
        ft_value=0.0, max_transfers_per_gw=3,
        chip_gws={"wildcard": [1]}, chip_force={"wildcard": 1})
    g = plans[0].per_gw[0]
    assert g["chip"] == "wildcard"
    assert g["n_transfers"] >= 3
    assert g["hits"] == 0  # wildcard: no hit cost despite exceeding FTs


def test_locked_avoid_and_banned_clubs():
    pool = _pool()
    owned = _owned(pool)
    df = pool.set_index("player_id")
    not_owned = [p for p in pool.player_id if p not in owned]
    target = next(p for p in not_owned if df.loc[p, "position"] == "MID")
    worst_owned = min(owned, key=lambda p: df.loc[p, "ep_gw1"])
    plans = chips.optimise_with_chips(
        pool, [1, 2], initial=owned, bank=20.0, free_transfers=2,
        ft_value=0.0, locked={target}, avoid={worst_owned})
    per = plans[0].per_gw
    assert target in {s["player_id"] for s in per[-1]["squad"]}
    assert worst_owned not in {s["player_id"] for s in per[-1]["squad"]}


def test_alternative_plans_differ():
    pool = _pool()
    owned = _owned(pool)
    plans = chips.optimise_with_chips(pool, [1, 2], initial=owned, bank=5.0,
                                      free_transfers=2, ft_value=0.0, n_plans=2)
    assert len(plans) == 2
    sig = lambda pl: tuple(tuple(sorted(x["player_id"] for x in g["transfers_in"]))
                           for g in pl.per_gw)
    assert sig(plans[0]) != sig(plans[1]) or plans[0].per_gw[0]["chip"] != \
        plans[1].per_gw[0]["chip"]
    assert plans[0].objective >= plans[1].objective - 1e-6


def test_unlimited_first_makes_first_gw_moves_free():
    """Pre-GW1 deadline: any number of first-gw moves, no hits, FTs reset."""
    pool = _pool()
    owned = _owned(pool)
    pool["ep_gw2"] = 3.0                      # no incentive to move in GW2
    # one cheap must-have per outfield position that we don't own yet
    targets = []
    for pos in ("DEF", "MID", "FWD"):
        cand = pool[(pool.position == pos) & ~pool.player_id.isin(owned)]
        targets.append(int(cand.iloc[0].player_id))
    pool.loc[pool.player_id.isin(targets), "ep_gw1"] = 30.0
    pool.loc[pool.player_id.isin(targets), "price"] = 4.0
    kw = dict(initial=owned, bank=0.0, free_transfers=1, ft_value=0.0,
              max_transfers_per_gw=3, time_limit=20)
    base = chips.optimise_with_chips(pool, [1, 2], **kw)[0]
    unl = chips.optimise_with_chips(pool, [1, 2], unlimited_first=True, **kw)[0]
    assert len(base.per_gw[0]["transfers_in"]) == 3
    assert base.per_gw[0]["hits"] > 0           # 3 moves on 1 FT normally costs hits
    assert len(unl.per_gw[0]["transfers_in"]) == 3
    assert unl.per_gw[0]["hits"] == 0           # pre-deadline moves are free
    assert unl.per_gw[1]["free_after"] <= 1     # nothing banks into GW2
