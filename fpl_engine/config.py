"""Central configuration: paths, season constants, and the OpenFPL column spec.

Kept deliberately simple and dependency-free so every other module can import
it without side effects.
"""
from __future__ import annotations

import os

# --- Paths -----------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
MODELS_DIR = os.path.join(ROOT, "models")
CONFIG_DIR = os.path.join(ROOT, "config")
CACHE_DIR = os.path.join(DATA_DIR, "cache")  # immutable, timestamped raw snapshots

# The single local SQLite database (the user's requested store).
DB_PATH = os.environ.get("FPL_DB_PATH", os.path.join(DATA_DIR, "fpl.sqlite"))

# --- Season / competition constants ---------------------------------------
# The season the pipeline is being run for (target predictions season).
CURRENT_SEASON = os.environ.get("FPL_SEASON", "2026-27")

# Prior seasons pulled from the free vaastav dataset so that early-season
# rolling windows (form carried over from last year) are populated.
BACKFILL_SEASONS = ["2022-23", "2023-24", "2024-25", "2025-26"]

# FPL element_type id -> OpenFPL position code. "AM" (assistant manager) is a
# derived/special class handled separately by OpenFPL.
ELEMENT_TYPE_TO_POSITION = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
POSITIONS = ["GK", "DEF", "MID", "FWD", "AM"]

# Trailing windows (in matches) used for every rolling feature.
WINDOWS = [1, 3, 5, 10, 38]

# --- OpenFPL sample column spec -------------------------------------------
# Metadata columns (identify a sample row).
META_COLUMNS = ["season", "gw", "position", "player", "team", "opponent", "home"]

# Base metrics that get expanded across WINDOWS. Grouped by source so the
# ingestors and the feature builder stay in sync. Order here defines the
# column order in the emitted samples table (matching data/samples.csv).
PLAYER_FPL_METRICS = [
    "player fpl points",
    "player relevant fpl points",
    "player minutes played",
    "player influence",
    "player creativity",
    "player threat",
    "player goals scored",
    "player penalties missed",
    "player assists",
    "player goals conceded",
    "player own goals",
    "player saves",
    "player penalties saved",
    "player yellow cards",
    "player red cards",
    "player bps",
    "player fpl bonus points",
]
PLAYER_UNDERSTAT_METRICS = [
    "player shots",
    "player xg",
    "player xgchain",
    "player xgbuildup",
    "player key passes",
    "player xa",
]
TEAM_METRICS = [
    "team goals scored",
    "team goals conceded",
    "team league rank",
    "team opponent league rank",
    "team xg",
    "team deep allowed",
    "team ppda allowed att",
    "team ppda allowed def",
    "team xga",
    "team deep",
    "team ppda att",
    "team ppda def",
]
OPPONENT_METRICS = [
    "opponent goals scored",
    "opponent goals conceded",
    "opponent xg",
    "opponent deep allowed",
    "opponent ppda allowed att",
    "opponent ppda allowed def",
    "opponent xga",
    "opponent deep",
    "opponent ppda att",
    "opponent ppda def",
]
STATUS_COLUMNS = [
    "status player availability",
    "status team league rank",
    "status opponent league rank",
]

WINDOWED_BASE_METRICS = (
    PLAYER_FPL_METRICS + PLAYER_UNDERSTAT_METRICS + TEAM_METRICS + OPPONENT_METRICS
)

# Metrics sourced from Understat (unavailable in FPL-only mode -> left NaN,
# which the OpenFPL models tolerate via np.nan_to_num).
UNDERSTAT_BASE_METRICS = set(PLAYER_UNDERSTAT_METRICS) | {
    "team xg", "team deep allowed", "team ppda allowed att", "team ppda allowed def",
    "team xga", "team deep", "team ppda att", "team ppda def",
    "opponent xg", "opponent deep allowed", "opponent ppda allowed att",
    "opponent ppda allowed def", "opponent xga", "opponent deep",
    "opponent ppda att", "opponent ppda def",
}


def sample_columns() -> list[str]:
    """Return the full ordered list of columns of the OpenFPL samples table."""
    cols = list(META_COLUMNS)
    for base in WINDOWED_BASE_METRICS:
        for w in WINDOWS:
            cols.append(f"{base} {w}")
    cols.extend(STATUS_COLUMNS)
    return cols
