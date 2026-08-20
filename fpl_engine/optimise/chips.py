"""Chip-aware multi-period FPL optimiser (superset of :mod:`milp`).

Extends the base multi-period MILP with the four FPL chips, solver-style user
constraints and alternative-plan generation, for use by the web app:

* **Wildcard** — unlimited free transfers that gameweek (changes permanent).
* **Free Hit** — unlimited squad change for one gameweek, reverting after.
* **Bench Boost** — all 15 players score that gameweek.
* **Triple Captain** — captain scores 3x instead of 2x.
* Target/hold players, avoid/sell players, do-not-buy clubs, forced moves,
  minimum-banked-FT targets, a terminal value per banked free transfer, and
  N alternative plans via no-good cuts.

The base module's guarantees (budget recursion on the actual bank, FT accrual
+1/gw bankable to 5, hits net of -4, 2/5/5/3 squad, <=3 per club, legal XI)
all carry over. :mod:`milp` itself is left untouched so its tests and CLI
behaviour are unchanged.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import pandas as pd
import pulp

from .milp import (MAX_FREE_TRANSFERS, MAX_PER_CLUB, POSITION_QUOTA, SQUAD_SIZE,
                   XI_MAX, XI_MIN, XI_SIZE)


def _solve(prob: pulp.LpProblem, time_limit: int) -> None:
    """CBC/HiGHS with a 1% optimality gap — plans converge far faster and a
    1% gap is far below projection noise."""
    for name in ("HiGHS_CMD", "PULP_CBC_CMD"):
        if name in pulp.listSolvers(onlyAvailable=True):
            solver = getattr(pulp, name)(msg=False, timeLimit=time_limit,
                                         gapRel=0.01)
            prob.solve(solver)
            return
    prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit, gapRel=0.01))

warnings.filterwarnings("ignore", category=DeprecationWarning, module="pulp")

CHIPS = ("wildcard", "freehit", "bench_boost", "triple_captain")


@dataclass
class ChipPlan:
    gws: list[int]
    objective: float
    status: str = ""
    per_gw: list[dict] = field(default_factory=list)


def optimise_with_chips(
    proj: pd.DataFrame,
    gws: list[int],
    *,
    initial: dict[int, float] | None = None,
    bank: float = 0.0,
    free_transfers: int = 1,
    budget: float = 100.0,
    decay: float = 0.85,
    hit_cost: float = 4.0,
    bench_weight: float = 0.1,
    ft_value: float = 1.5,
    max_transfers_per_gw: int = 3,
    time_limit: int = 60,
    chip_gws: dict[str, list[int]] | None = None,
    chip_force: dict[str, int] | None = None,
    locked: set[int] | None = None,
    avoid: set[int] | None = None,
    banned_clubs: set[int] | None = None,
    forced_in: dict[int, list[int]] | None = None,
    forced_out: dict[int, list[int]] | None = None,
    min_ft: dict[int, int] | None = None,
    n_plans: int = 1,
    on_progress=None,
) -> list[ChipPlan]:
    """Solve the chip-aware plan; return up to ``n_plans`` alternatives.

    ``chip_gws`` maps chip name -> gameweeks it may be played in (absent or
    empty = chip unavailable). ``chip_force`` pins a chip to a gameweek.
    ``forced_in``/``forced_out`` map gw -> player_ids that must move that gw.
    ``min_ft`` maps gw -> minimum banked FTs to hold *after* that gw's moves.
    """
    scratch = initial is None
    P = proj.reset_index(drop=True)
    ids = list(P["player_id"])
    pos = dict(zip(P["player_id"], P["position"]))
    club = dict(zip(P["player_id"], P["team_id"]))
    price = dict(zip(P["player_id"], P["price"]))
    name = dict(zip(P["player_id"], P["player"]))
    ep = {g: dict(zip(P["player_id"], P[f"ep_gw{g}"])) for g in gws}

    prev = {pid: (0 if scratch else (1 if pid in initial else 0)) for pid in ids}
    sell = {pid: (initial.get(pid, price[pid]) if not scratch else price[pid])
            for pid in ids}
    start_bank = budget if scratch else bank

    chip_gws = {c: set(v) for c, v in (chip_gws or {}).items() if v}
    chip_force = chip_force or {}
    tc_on = bool(chip_gws.get("triple_captain"))
    bb_on = bool(chip_gws.get("bench_boost"))
    fh_on = bool(chip_gws.get("freehit"))
    locked = locked or set()
    avoid = avoid or set()
    banned_clubs = banned_clubs or set()
    forced_in = forced_in or {}
    forced_out = forced_out or {}
    min_ft = min_ft or {}

    T = list(range(len(gws)))
    gw_of = dict(enumerate(gws))
    BIG_MONEY = budget + sum(sorted(price.values())[-15:]) + 10

    prob = pulp.LpProblem("fpl_chips", pulp.LpMaximize)

    squad = pulp.LpVariable.dicts("sq", (ids, T), cat="Binary")   # squad PLAYED at t
    # squad carried out of t (differs from played squad only on a Free Hit week)
    carry = (pulp.LpVariable.dicts("cy", (ids, T), cat="Binary") if fh_on
             else squad)
    xi = pulp.LpVariable.dicts("xi", (ids, T), cat="Binary")
    cap = pulp.LpVariable.dicts("cap", (ids, T), cat="Binary")
    tin = pulp.LpVariable.dicts("in", (ids, T), cat="Binary")
    tout = pulp.LpVariable.dicts("out", (ids, T), cat="Binary")
    fused = pulp.LpVariable.dicts("fused", T, lowBound=0, cat="Integer")
    paid = pulp.LpVariable.dicts("paid", T, lowBound=0, cat="Integer")
    ftv = pulp.LpVariable.dicts("ft", T, lowBound=0, upBound=MAX_FREE_TRANSFERS,
                                cat="Integer")
    bankv = pulp.LpVariable.dicts("bank", T, lowBound=0)

    # chip vars exist only for enabled chips; disabled chips are the constant 0
    chip = {c: (pulp.LpVariable.dicts(f"chip_{c}", T, cat="Binary")
                if chip_gws.get(c) else dict.fromkeys(T, 0)) for c in CHIPS}
    tcp = (pulp.LpVariable.dicts("tcp", (ids, T), cat="Binary") if tc_on
           else None)                                          # cap AND TC
    bbp = (pulp.LpVariable.dicts("bbp", (ids, T), cat="Binary") if bb_on
           else None)                                          # bench AND BB

    clubs = set(club.values())

    # --- chip availability / forcing ---
    for c in CHIPS:
        allowed = chip_gws.get(c)
        if not allowed:
            continue
        for t in T:
            if gw_of[t] not in allowed:
                prob += chip[c][t] == 0
        prob += pulp.lpSum(chip[c][t] for t in T) <= 1
        if c in chip_force:
            fg = chip_force[c]
            if fg in gws:
                prob += chip[c][gws.index(fg)] == 1
    if chip_gws:
        for t in T:
            prob += pulp.lpSum(chip[c][t] for c in CHIPS) <= 1
    wc, fh, bb, tc = (chip["wildcard"], chip["freehit"],
                      chip["bench_boost"], chip["triple_captain"])

    # --- objective ---
    obj = []
    for t, g in enumerate(gws):
        d = decay ** t
        for p in ids:
            e = ep[g][p]
            obj.append(d * e * (xi[p][t] + cap[p][t]))
            obj.append(d * bench_weight * e * (squad[p][t] - xi[p][t]))
            if tc_on:
                obj.append(d * e * tcp[p][t])
            if bb_on:
                obj.append(d * (1.0 - bench_weight) * e * bbp[p][t])
        obj.append(-hit_cost * paid[t])
    obj.append(ft_value * ftv[T[-1]])
    prob += pulp.lpSum(obj)

    # --- per-gameweek structure (played squad) ---
    for t in T:
        prob += pulp.lpSum(squad[p][t] for p in ids) == SQUAD_SIZE
        for pp, q in POSITION_QUOTA.items():
            prob += pulp.lpSum(squad[p][t] for p in ids if pos[p] == pp) == q
        for cl in clubs:
            prob += pulp.lpSum(squad[p][t] for p in ids if club[p] == cl) <= MAX_PER_CLUB
        prob += pulp.lpSum(xi[p][t] for p in ids) == XI_SIZE
        prob += pulp.lpSum(cap[p][t] for p in ids) == 1
        for p in ids:
            prob += xi[p][t] <= squad[p][t]
            prob += cap[p][t] <= xi[p][t]
            # chip product linearisations (maximisation pulls them up, so the
            # <= pair suffices — no lower bounds needed)
            if tc_on:
                prob += tcp[p][t] <= cap[p][t]
                prob += tcp[p][t] <= tc[t]
            if bb_on:
                prob += bbp[p][t] <= bb[t]
                prob += bbp[p][t] <= squad[p][t] - xi[p][t]
        for pp in POSITION_QUOTA:
            n = pulp.lpSum(xi[p][t] for p in ids if pos[p] == pp)
            prob += n >= XI_MIN[pp]
            prob += n <= XI_MAX[pp]

    # --- squad continuity, transfers, carry (Free Hit reverts) ---
    for t in T:
        for p in ids:
            base = prev[p] if t == 0 else carry[p][t - 1]
            if fh_on:
                # played squad follows base + transfers, unless FH (then free)
                prob += squad[p][t] - base - tin[p][t] + tout[p][t] <= 2 * fh[t]
                prob += squad[p][t] - base - tin[p][t] + tout[p][t] >= -2 * fh[t]
                prob += tin[p][t] <= 1 - fh[t]     # FH week: no real transfers
                prob += tout[p][t] <= 1 - fh[t]
                # carry = played squad normally; on FH, carry = base (revert)
                prob += carry[p][t] <= squad[p][t] + fh[t]
                prob += carry[p][t] >= squad[p][t] - fh[t]
                prob += carry[p][t] <= base + (1 - fh[t])
                prob += carry[p][t] >= base - (1 - fh[t])
            else:
                prob += squad[p][t] == base + tin[p][t] - tout[p][t]
            prob += tin[p][t] + tout[p][t] <= 1

    # --- bank recursion (actual bank only) + FH affordability ---
    for t in T:
        prev_bank = start_bank if t == 0 else bankv[t - 1]
        prob += bankv[t] == (prev_bank
                             + pulp.lpSum(sell[p] * tout[p][t] for p in ids)
                             - pulp.lpSum(price[p] * tin[p][t] for p in ids))
        if fh_on:
            # Free-Hit squad must be affordable from carried squad value + bank
            base_val = (pulp.lpSum(sell[p] * prev[p] for p in ids) if t == 0
                        else pulp.lpSum(sell[p] * carry[p][t - 1] for p in ids))
            prob += (pulp.lpSum(sell[p] * squad[p][t] for p in ids)
                     <= base_val + prev_bank + BIG_MONEY * (1 - fh[t]))

    # --- transfer counts, hits, free-transfer stock ---
    for t in T:
        nt = pulp.lpSum(tin[p][t] for p in ids)
        cap_n = SQUAD_SIZE if (scratch and t == 0) else max_transfers_per_gw
        prob += nt <= cap_n + SQUAD_SIZE * wc[t]      # WC lifts the per-gw cap
        prob += fused[t] <= nt
        prob += fused[t] <= ftv[t]
        # WC/FH weeks consume no FTs and cost no hits
        prob += fused[t] <= SQUAD_SIZE * (1 - wc[t] - fh[t]) + 0
        if scratch and t == 0:
            # building the initial 15 is free: no hits, no FTs consumed
            prob += paid[t] == 0
            prob += fused[t] == 0
        else:
            prob += paid[t] >= nt - fused[t] - SQUAD_SIZE * (wc[t] + fh[t])
        # FT stock recursion
        if t == 0:
            prob += ftv[t] == (1 if scratch else free_transfers)
        elif scratch and t == 1:
            prob += ftv[t] == 1
        else:
            prob += ftv[t] <= ftv[t - 1] - fused[t - 1] + 1
            prob += ftv[t] >= 1
        g = gw_of[t]
        if g in min_ft:
            prob += ftv[t] - fused[t] >= min_ft[g]

    # --- user constraints ---
    for p in locked:
        if p not in pos:
            continue
        if prev.get(p):
            for t in T:
                prob += squad[p][t] == 1          # hold: never sold
        else:
            prob += squad[p][T[-1]] == 1          # target: owned by horizon end
    for p in avoid:
        if p not in pos:
            continue
        for t in T:
            prob += tin[p][t] == 0
        prob += squad[p][T[-1]] == 0              # gone (or never bought) by end
        if not prev.get(p):
            for t in T:
                prob += squad[p][t] == 0
    for p in ids:
        if club[p] in banned_clubs and not prev.get(p):
            for t in T:
                prob += squad[p][t] == 0
    for g, plist in forced_in.items():
        if g in gws:
            for p in plist:
                if p in pos:
                    prob += tin[p][gws.index(g)] == 1
    for g, plist in forced_out.items():
        if g in gws:
            for p in plist:
                if p in pos:
                    prob += tout[p][gws.index(g)] == 1

    # --- solve, extract, cut, repeat for alternative plans ---
    plans: list[ChipPlan] = []
    for k in range(max(1, n_plans)):
        if on_progress:
            on_progress(f"Solving plan {k + 1}/{n_plans}…")
        _solve(prob, time_limit)
        if pulp.LpStatus[prob.status] not in ("Optimal", "Not Solved"):
            break
        plan = _extract(prob, ids, gws, name, pos, club, price, sell, ep,
                        squad, xi, cap, tin, tout, fused, paid, ftv, bankv, chip)
        if plan is None:
            break
        plans.append(plan)
        if k + 1 >= n_plans:
            break
        # no-good cut over the decision signature (transfers + chips)
        chosen, others = [], []
        for t in T:
            for p in ids:
                (chosen if _v(tin[p][t]) else others).append(tin[p][t])
                (chosen if _v(tout[p][t]) else others).append(tout[p][t])
            for c in CHIPS:
                (chosen if _v(chip[c][t]) else others).append(chip[c][t])
        prob += (pulp.lpSum(chosen) - pulp.lpSum(others) <= len(chosen) - 1)
    return plans


def _v(var) -> int:
    return int(round(pulp.value(var) or 0))


def _extract(prob, ids, gws, name, pos, club, price, sell, ep,
             squad, xi, cap, tin, tout, fused, paid, ftv, bankv, chip):
    plan = ChipPlan(gws=gws, objective=pulp.value(prob.objective) or 0.0,
                    status=pulp.LpStatus[prob.status])
    for t, g in enumerate(gws):
        squad_ids = [p for p in ids if _v(squad[p][t])]
        if len(squad_ids) != SQUAD_SIZE:
            return None  # infeasible/timeout garbage
        xi_ids = [p for p in ids if _v(xi[p][t])]
        cap_id = next((p for p in ids if _v(cap[p][t])), None)
        chips_played = [c for c in CHIPS if _v(chip[c][t])]
        chip_now = chips_played[0] if chips_played else None
        # vice = best remaining XI player by ep
        vice_id = None
        rest = sorted((p for p in xi_ids if p != cap_id),
                      key=lambda p: -ep[g][p])
        if rest:
            vice_id = rest[0]
        mult = 3 if chip_now == "triple_captain" else 2
        xi_pts = sum(ep[g][p] for p in xi_ids)
        if cap_id is not None:
            xi_pts += (mult - 1) * ep[g][cap_id]
        bench_ids = [p for p in squad_ids if p not in xi_ids]
        if chip_now == "bench_boost":
            xi_pts += sum(ep[g][p] for p in bench_ids)
        plan.per_gw.append({
            "gw": g,
            "chip": chip_now,
            "squad": [{
                "player_id": p, "name": name[p], "position": pos[p],
                "team_id": club[p], "price": price[p], "sell": sell[p],
                "ep": round(ep[g][p], 2),
                "in_xi": p in xi_ids,
                "is_captain": p == cap_id, "is_vice": p == vice_id,
            } for p in sorted(squad_ids,
                              key=lambda p: (["GK", "DEF", "MID", "FWD"].index(pos[p]),
                                             -ep[g][p]))],
            "captain": name.get(cap_id), "captain_id": cap_id,
            "vice": name.get(vice_id), "vice_id": vice_id,
            "xi_points": round(xi_pts, 2),
            "transfers_in": [{"player_id": p, "name": name[p]}
                             for p in ids if _v(tin[p][t])],
            "transfers_out": [{"player_id": p, "name": name[p]}
                              for p in ids if _v(tout[p][t])],
            "n_transfers": sum(_v(tin[p][t]) for p in ids),
            "free_used": _v(fused[t]), "hits": _v(paid[t]),
            "free_after": _v(ftv[t]) - _v(fused[t]),
            "bank": round(pulp.value(bankv[t]) or 0.0, 1),
        })
    return plan
