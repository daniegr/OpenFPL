"""Fetch a manager's FPL entry (squad ID) and derive current squad state.

Reads the free, public entry endpoints:
  * ``entry/{id}/``               basic info + current event
  * ``entry/{id}/history/``       per-gameweek history + chips
  * ``entry/{id}/event/{gw}/picks/``  the 15 picks (with selling prices)

From these we derive: the current 15-man squad (with per-player selling
prices), money in the bank, and an estimate of free transfers available for the
next gameweek. When the entry has no squad yet (pre-season, before the first
deadline), :func:`current_squad` returns ``None`` so the optimiser builds a
fresh squad from the budget instead.
"""
from __future__ import annotations

import json

from .http import get_text

BASE = "https://fantasy.premierleague.com/api"
DEFAULT_ENTRY = 883566  # https://fantasy.premierleague.com/en/entry/883566/history/
MAX_FREE_TRANSFERS = 5


def _get(path: str, use_cache: bool = False) -> dict | list:
    return json.loads(get_text(f"{BASE}/{path}", use_cache=use_cache))


def fetch_entry(entry_id: int, use_cache: bool = False) -> dict:
    return _get(f"entry/{entry_id}/", use_cache=use_cache)


def fetch_history(entry_id: int, use_cache: bool = False) -> dict:
    return _get(f"entry/{entry_id}/history/", use_cache=use_cache)


def fetch_picks(entry_id: int, gw: int, use_cache: bool = False) -> dict | None:
    try:
        return _get(f"entry/{entry_id}/event/{gw}/picks/", use_cache=use_cache)
    except Exception:
        return None  # no picks for that gw (e.g. before the first deadline)


def estimate_free_transfers(history: dict) -> int:
    """Estimate FTs available for the next gameweek from transfer history.

    Rule (2026-27): start with 1, accrue +1 each gameweek, bankable up to 5,
    minus transfers actually made (extra transfers were paid hits).
    """
    events = history.get("current", []) or []
    ft = 1
    for ev in events:
        made = ev.get("event_transfers", 0) or 0
        ft = min(MAX_FREE_TRANSFERS, max(0, ft - made) + 1)
    return max(1, ft)


def current_squad(entry_id: int, *, use_cache: bool = False) -> dict | None:
    """Return the manager's current squad state, or None if none exists yet.

    Returns dict with: entry_id, name, gw (the gw the picks are from),
    bank (£m), squad (list of {element, selling_price, purchase_price,
    is_captain, is_vice, multiplier}), free_transfers.
    """
    entry = fetch_entry(entry_id, use_cache=use_cache)
    history = fetch_history(entry_id, use_cache=use_cache)
    events = history.get("current", []) or []
    if not events:
        return None  # pre-season / no gameweek played yet

    last_gw = events[-1]["event"]
    picks = fetch_picks(entry_id, last_gw, use_cache=use_cache)
    if not picks or "picks" not in picks:
        return None

    et = picks.get("entry_history", {})
    squad = [{
        "element": p["element"],
        "selling_price": p.get("selling_price", 0) / 10.0,
        "purchase_price": p.get("purchase_price", 0) / 10.0,
        "is_captain": bool(p.get("is_captain")),
        "is_vice": bool(p.get("is_vice_captain")),
        "multiplier": p.get("multiplier", 1),
    } for p in picks["picks"]]

    return {
        "entry_id": entry_id,
        "name": entry.get("name"),
        "gw": last_gw,
        "bank": et.get("bank", 0) / 10.0,
        "squad": squad,
        "free_transfers": estimate_free_transfers(history),
    }
