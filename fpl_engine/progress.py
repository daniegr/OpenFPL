"""Tiny progress-logging helper so long-running steps show they are alive.

Prints to stderr with an immediate flush (Windows terminals buffer stdout, which
is why a long ``pull`` can look frozen). Silence with ``FPL_QUIET=1``.
"""
from __future__ import annotations

import os
import sys

_QUIET = os.environ.get("FPL_QUIET", "") not in ("", "0", "false", "False")


def log(msg: str) -> None:
    if not _QUIET:
        print(msg, file=sys.stderr, flush=True)


def step(msg: str) -> None:
    """A top-level step marker."""
    log(f"[fpl] {msg}")
