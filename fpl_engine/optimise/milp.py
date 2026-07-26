"""Multi-period FPL squad optimiser (mixed-integer linear program).

Given per-player projected points across a planning horizon, choose — for every
gameweek in the horizon — the 15-man squad, starting XI, captain and the
transfers to make, so as to maximise discounted expected points **net of the
-4 point cost of transfers beyond the free allowance**. Free transfers accrue
(+1 per gameweek, bankable up to 5) and are modelled explicitly, so the
optimiser decides for itself whether a hit is worth taking.

Two entry points share one model:
  * :func:`optimise` — from an existing squad (suggests transfers / hits).
  * :func:`build_from_scratch` — no squad yet (pre-season): pick a fresh 15
    within budget (initial squad selection is free).

Solver: PuLP with the bundled CBC (free, no external install). HiGHS is used
automatically if available.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import pandas as pd
import pulp

warnings.filterwarnings("ignore", category=DeprecationWarning, module="pulp")

POSITION_QUOTA = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
XI_MIN = {"GK": 1, "DEF": 3, "MID": 2, "FWD": 1}
XI_MAX = {"GK": 1, "DEF": 5, "MID": 5, "FWD": 3}
MAX_PER_CLUB = 3
SQUAD_SIZE = 15
XI_SIZE = 11
MAX_FREE_TRANSFERS = 5


@dataclass
class Plan:
    gws: list[int]
    objective: float
    per_gw: list[dict] = field(default_factory=list)  # squad/xi/captain/transfers
    status: str = ""

    def summary(self) -> str:
        lines = [f"Objective (decayed pts, net of hits): {self.objective:.2f}  "
                 f"[{self.status}]"]
        for g in self.per_gw:
            lines.append(
                f"\nGW{g['gw']}:  proj XI pts {g['xi_points']:.1f}  "
                f"(captain {g['captain']}, x2)  "
                f"transfers {g['n_transfers']} (free {g['free_used']}, "
                f"hits {g['hits']} = {-4 * g['hits']} pts)")
            if g["transfers_in"]:
                lines.append("  IN : " + ", ".join(g["transfers_in"]))
                lines.append("  OUT: " + ", ".join(g["transfers_out"]))
        return "\n".join(lines)


def _solve(prob: pulp.LpProblem, time_limit: int):
    for name in ("HiGHS_CMD", "PULP_CBC_CMD"):
        if name in pulp.listSolvers(onlyAvailable=True):
            solver = getattr(pulp, name)(msg=False, timeLimit=time_limit)
            prob.solve(solver)
            return
    prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit))


def optimise(proj: pd.DataFrame, gws: list[int], *,
             initial: dict[int, float] | None = None,
             bank: float = 0.0, free_transfers: int = 1, budget: float = 100.0,
             decay: float = 0.85, hit_cost: float = 4.0, bench_weight: float = 0.1,
             max_transfers_per_gw: int = 3, time_limit: int = 30) -> Plan:
    """Optimise squad + transfers over ``gws``.

    ``proj`` rows need: player_id, player, position, team_id, price, ep_gw{g}.
    ``initial`` maps owned player_id -> selling price (None => build from scratch,
    where the first-gameweek squad is free and the budget applies).
    """
    scratch = initial is None
    P = proj.reset_index(drop=True)
    ids = list(P["player_id"])
    idx = {pid: i for i, pid in enumerate(ids)}
    pos = dict(zip(P["player_id"], P["position"]))
    club = dict(zip(P["player_id"], P["team_id"]))
    price = dict(zip(P["player_id"], P["price"]))
    name = dict(zip(P["player_id"], P["player"]))
    ep = {g: dict(zip(P["player_id"], P[f"ep_gw{g}"])) for g in gws}

    prev = {pid: (0 if scratch else (1 if pid in initial else 0)) for pid in ids}
    sell = {pid: (initial.get(pid, price[pid]) if not scratch else price[pid])
            for pid in ids}
    start_bank = budget if scratch else bank + sum(sell[p] for p in ids if prev[p])
    # (for existing squad, total spending power = squad sale value + bank; the
    #  per-gw bank recursion below keeps it consistent)

    T = list(range(len(gws)))
    prob = pulp.LpProblem("fpl", pulp.LpMaximize)

    squad = pulp.LpVariable.dicts("sq", (ids, T), cat="Binary")
    xi = pulp.LpVariable.dicts("xi", (ids, T), cat="Binary")
    cap = pulp.LpVariable.dicts("cap", (ids, T), cat="Binary")
    tin = pulp.LpVariable.dicts("in", (ids, T), cat="Binary")
    tout = pulp.LpVariable.dicts("out", (ids, T), cat="Binary")
    fused = pulp.LpVariable.dicts("fused", T, lowBound=0, cat="Integer")
    paid = pulp.LpVariable.dicts("paid", T, lowBound=0, cat="Integer")
    ftv = pulp.LpVariable.dicts("ft", T, lowBound=0, upBound=MAX_FREE_TRANSFERS,
                                cat="Integer")

    clubs = set(club.values())

    # --- objective ---
    obj = []
    for t, g in enumerate(gws):
        d = decay ** t
        for pid in ids:
            e = ep[g][pid]
            obj.append(d * e * xi[pid][t])          # starter points
            obj.append(d * e * cap[pid][t])          # captain doubles
            obj.append(d * bench_weight * e * (squad[pid][t] - xi[pid][t]))
        obj.append(-hit_cost * paid[t])
    prob += pulp.lpSum(obj)

    # --- per-gameweek structural constraints ---
    for t in T:
        prob += pulp.lpSum(squad[p][t] for p in ids) == SQUAD_SIZE
        for pp, q in POSITION_QUOTA.items():
            prob += pulp.lpSum(squad[p][t] for p in ids if pos[p] == pp) == q
        for cl in clubs:
            prob += pulp.lpSum(squad[p][t] for p in ids if club[p] == cl) <= MAX_PER_CLUB
        # starting XI
        prob += pulp.lpSum(xi[p][t] for p in ids) == XI_SIZE
        for p in ids:
            prob += xi[p][t] <= squad[p][t]
            prob += cap[p][t] <= xi[p][t]
        prob += pulp.lpSum(cap[p][t] for p in ids) == 1
        for pp in POSITION_QUOTA:
            n = pulp.lpSum(xi[p][t] for p in ids if pos[p] == pp)
            prob += n >= XI_MIN[pp]
            prob += n <= XI_MAX[pp]

    # --- transfer / bank / free-transfer dynamics ---
    running_bank = start_bank
    for t in T:
        for p in ids:
            prior = prev[p] if t == 0 else squad[p][t - 1]
            prob += squad[p][t] == prior + tin[p][t] - tout[p][t]
            prob += tin[p][t] + tout[p][t] <= 1
        nt = pulp.lpSum(tin[p][t] for p in ids)
        # bank recursion: sells add sell price, buys subtract buy price
        running_bank = (running_bank
                        + pulp.lpSum(sell[p] * tout[p][t] for p in ids)
                        - pulp.lpSum(price[p] * tin[p][t] for p in ids))
        prob += running_bank >= 0
        # free-transfer accounting
        cap_transfers = SQUAD_SIZE if (scratch and t == 0) else max_transfers_per_gw
        prob += nt <= cap_transfers
        prob += fused[t] <= nt
        prob += paid[t] == nt - fused[t]
        if scratch and t == 0:
            # Building the initial squad is free (no hits, unlimited transfers).
            prob += paid[t] == 0
        else:
            prob += fused[t] <= ftv[t]
            if t == 0:
                prob += ftv[t] == free_transfers          # existing squad
            elif scratch and t == 1:
                prob += ftv[t] <= 1                        # 1 FT after a fresh build
                prob += ftv[t] >= 1
            else:
                prob += ftv[t] <= ftv[t - 1] - fused[t - 1] + 1
                prob += ftv[t] >= 1

    _solve(prob, time_limit)
    return _extract(prob, ids, gws, name, pos, ep, squad, xi, cap, tin, tout,
                    fused, paid)


def build_from_scratch(proj: pd.DataFrame, gws: list[int], *, budget: float = 100.0,
                       **kw) -> Plan:
    """Pick a fresh squad from budget (initial selection is free)."""
    return optimise(proj, gws, initial=None, budget=budget, **kw)


def _val(v) -> int:
    return int(round(pulp.value(v) or 0))


def _extract(prob, ids, gws, name, pos, ep, squad, xi, cap, tin, tout,
             fused, paid) -> Plan:
    plan = Plan(gws=gws, objective=pulp.value(prob.objective) or 0.0,
                status=pulp.LpStatus[prob.status])
    for t, g in enumerate(gws):
        squad_ids = [p for p in ids if _val(squad[p][t])]
        xi_ids = [p for p in ids if _val(xi[p][t])]
        cap_id = next((p for p in ids if _val(cap[p][t])), None)
        ins = [name[p] for p in ids if _val(tin[p][t])]
        outs = [name[p] for p in ids if _val(tout[p][t])]
        xi_pts = sum(ep[g][p] for p in xi_ids) + (ep[g][cap_id] if cap_id else 0)
        plan.per_gw.append({
            "gw": g,
            "squad": [(name[p], pos[p], round(ep[g][p], 2)) for p in
                      sorted(squad_ids, key=lambda p: (pos[p], -ep[g][p]))],
            "xi": [name[p] for p in xi_ids],
            "captain": name[cap_id] if cap_id else None,
            "xi_points": xi_pts,
            "transfers_in": ins, "transfers_out": outs,
            "n_transfers": len(ins),
            "free_used": _val(fused[t]), "hits": _val(paid[t]),
        })
    return plan
