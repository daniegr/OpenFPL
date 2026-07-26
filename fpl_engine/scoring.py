"""Canonical FPL points calculator — the single source of truth.

Every model, simulation and reconciliation calls :func:`points_from_events`.
Scoring constants live only in ``config/scoring_rules_*.yaml``; none are
hardcoded here (principle #2 / #6 in CLAUDE.md).
"""
from __future__ import annotations

import functools
import math
import os

import yaml

from . import config

DEFAULT_RULES = os.path.join(config.CONFIG_DIR, "scoring_rules_2026_27.yaml")


@functools.lru_cache(maxsize=4)
def load_rules(path: str = DEFAULT_RULES) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def points_from_events(events: dict, position: str, *,
                       rules: dict | None = None) -> int:
    """Compute FPL points for one player-match from raw event counts.

    ``events`` keys (all optional, default 0): minutes, goals_scored, assists,
    clean_sheets (0/1), goals_conceded, saves, penalties_saved,
    penalties_missed, yellow_cards, red_cards, own_goals, bonus,
    defensive_contribution (0/1 flag or the raw action count).
    """
    r = rules or load_rules()
    g = lambda k: events.get(k) or 0  # noqa: E731 - concise local accessor
    minutes = g("minutes")
    pts = 0

    # Appearance
    if minutes >= 60:
        pts += r["appearance"]["played_60"]
    elif minutes > 0:
        pts += r["appearance"]["played_any"]

    # Attacking returns
    pts += int(g("goals_scored")) * r["goal"].get(position, 0)
    pts += int(g("assists")) * r["assist"]

    # Clean sheet (only counts with 60+ minutes)
    if g("clean_sheets") and minutes >= 60:
        pts += r["clean_sheet"].get(position, 0)

    # Goalkeeping
    if position == "GK":
        pts += (int(g("saves")) // r["saves_per_point"])
        pts += int(g("penalties_saved")) * r["penalty_save"]

    # Goals conceded penalty (GK/DEF)
    if position in ("GK", "DEF"):
        conceded = int(g("goals_conceded"))
        pts += (conceded // r["goals_conceded"]["per"]) * r["goals_conceded"]["points"]

    # Discipline / misc
    pts += int(g("penalties_missed")) * r["penalty_miss"]
    pts += int(g("yellow_cards")) * r["card"]["yellow"]
    pts += int(g("red_cards")) * r["card"]["red"]
    pts += int(g("own_goals")) * r["own_goal"]

    # Defensive Contribution (threshold crossing)
    dc = g("defensive_contribution")
    thr = r["defensive_contribution"]["threshold"].get(position)
    if thr is not None and dc:
        crossed = dc >= thr if dc > 1 else bool(dc)  # accept raw count or 0/1 flag
        if crossed:
            pts += r["defensive_contribution"]["points"]

    # Bonus (added as provided by the BPS ranking)
    pts += int(g("bonus"))
    return pts


def points_without_defcon(events: dict, position: str, *,
                          rules: dict | None = None) -> int:
    """Classic scoring (no DefCon) — used to reconcile pre-2025/26 seasons."""
    ev = dict(events)
    ev["defensive_contribution"] = 0
    return points_from_events(ev, position, rules=rules)
