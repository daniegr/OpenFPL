"""Local SQLite storage — the single, simple, file-based store for the pipeline.

No server, no external dependency: just stdlib ``sqlite3`` against one file
(``data/fpl.sqlite`` by default, override with ``FPL_DB_PATH``).

Design notes
------------
* ``raw_snapshot`` keeps immutable, timestamped copies of every API response so
  the pipeline has point-in-time provenance and can be replayed offline.
* ``player_gw`` is the normalised per-player-per-gameweek fact table that the
  feature builder reads. Rows are keyed by (season, gw, source, player_id) and
  carry a ``kickoff_utc`` used to enforce point-in-time (as_of) discipline.
* ``understat_player_match`` holds Understat advanced stats per player per match.
* Everything is idempotent: ingestors ``INSERT OR REPLACE`` on stable keys so a
  re-run never duplicates rows.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterable, Iterator, Sequence

from . import config

SCHEMA = """
-- Immutable, timestamped raw API/scrape payloads (provenance + offline replay).
CREATE TABLE IF NOT EXISTS raw_snapshot (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,          -- 'fpl', 'understat', 'vaastav'
    endpoint     TEXT NOT NULL,          -- e.g. 'bootstrap-static', 'element-summary/123'
    season       TEXT,
    retrieved_utc TEXT NOT NULL,         -- ISO8601 UTC, when we fetched it
    payload      TEXT NOT NULL           -- raw response body (JSON/CSV text)
);
CREATE INDEX IF NOT EXISTS ix_raw_source_endpoint ON raw_snapshot(source, endpoint);

-- Teams (per season; ids differ across seasons in FPL).
CREATE TABLE IF NOT EXISTS team (
    season      TEXT NOT NULL,
    team_id     INTEGER NOT NULL,        -- FPL team id within the season
    name        TEXT NOT NULL,
    short_name  TEXT,
    code        INTEGER,                 -- FPL stable cross-season club code
    understat_name TEXT,                 -- resolved Understat title
    PRIMARY KEY (season, team_id)
);

-- Players (per season).
CREATE TABLE IF NOT EXISTS player (
    season      TEXT NOT NULL,
    player_id   INTEGER NOT NULL,        -- FPL element id within the season
    code        INTEGER,                 -- FPL stable cross-season player code
    web_name    TEXT,
    full_name   TEXT NOT NULL,
    team_id     INTEGER,
    position    TEXT,                    -- GK/DEF/MID/FWD
    understat_id TEXT,                   -- resolved Understat player id
    now_cost    REAL,                    -- current price in £m (e.g. 5.5)
    status      TEXT,                    -- FPL availability code (a/d/i/s/u)
    chance_next REAL,                    -- chance of playing next round in [0,1]
    PRIMARY KEY (season, player_id)
);
CREATE INDEX IF NOT EXISTS ix_player_code ON player(code);

-- Fixtures (per season).
CREATE TABLE IF NOT EXISTS fixture (
    season       TEXT NOT NULL,
    fixture_id   INTEGER NOT NULL,
    gw           INTEGER,
    kickoff_utc  TEXT,
    team_h       INTEGER,
    team_a       INTEGER,
    team_h_score INTEGER,
    team_a_score INTEGER,
    finished     INTEGER,
    PRIMARY KEY (season, fixture_id)
);

-- Normalised per-player-per-gameweek FPL facts (the modelling grain).
CREATE TABLE IF NOT EXISTS player_gw (
    season          TEXT NOT NULL,
    gw              INTEGER NOT NULL,
    source          TEXT NOT NULL,       -- 'fpl' | 'vaastav'
    player_id       INTEGER NOT NULL,
    fixture_id      INTEGER NOT NULL DEFAULT 0,  -- discriminates double-gameweeks
    player_code     INTEGER,             -- cross-season stable code (for joins)
    full_name       TEXT,
    team_id         INTEGER,
    opponent_id     INTEGER,
    was_home        INTEGER,
    kickoff_utc     TEXT,                -- point-in-time key
    minutes         REAL,
    total_points    REAL,
    goals_scored    REAL,
    assists         REAL,
    clean_sheets    REAL,
    goals_conceded  REAL,
    own_goals       REAL,
    penalties_saved REAL,
    penalties_missed REAL,
    yellow_cards    REAL,
    red_cards       REAL,
    saves           REAL,
    bonus           REAL,
    bps             REAL,
    influence       REAL,
    creativity      REAL,
    threat          REAL,
    starts          REAL,
    xg              REAL,                -- FPL (Opta) expected goals, per match
    xa              REAL,                -- FPL expected assists
    xgi             REAL,                -- FPL expected goal involvements
    xgc             REAL,                -- FPL expected goals conceded (on pitch)
    PRIMARY KEY (season, gw, source, player_id, fixture_id)
);
CREATE INDEX IF NOT EXISTS ix_player_gw_code ON player_gw(player_code, season, gw);

-- Per-team-per-match results (drives team/opponent goals + league-rank features).
CREATE TABLE IF NOT EXISTS team_match (
    season       TEXT NOT NULL,
    team_id      INTEGER NOT NULL,
    fixture_id   INTEGER NOT NULL,
    gw           INTEGER,
    kickoff_utc  TEXT,
    opponent_id  INTEGER,
    was_home     INTEGER,
    goals_for    REAL,
    goals_against REAL,
    xg           REAL,                   -- team xG = sum of its players' FPL xG
    xga          REAL,                   -- team xGA = opponent's xG
    PRIMARY KEY (season, team_id, fixture_id)
);
CREATE INDEX IF NOT EXISTS ix_team_match ON team_match(season, team_id, kickoff_utc);

-- Understat advanced stats per player per match.
CREATE TABLE IF NOT EXISTS understat_player_match (
    season       TEXT NOT NULL,
    understat_id TEXT NOT NULL,
    match_date   TEXT NOT NULL,          -- YYYY-MM-DD (point-in-time key)
    understat_match_id TEXT,
    goals        REAL,
    shots        REAL,
    xg           REAL,
    assists      REAL,
    key_passes   REAL,
    xa           REAL,
    xgchain      REAL,
    xgbuildup    REAL,
    minutes      REAL,
    PRIMARY KEY (season, understat_id, match_date, understat_match_id)
);

-- Understat team-level per-match stats (xG, deep, ppda ...).
CREATE TABLE IF NOT EXISTS understat_team_match (
    season       TEXT NOT NULL,
    understat_team TEXT NOT NULL,
    match_date   TEXT NOT NULL,
    understat_match_id TEXT,
    is_home      INTEGER,
    xg           REAL,
    xga          REAL,
    deep         REAL,
    deep_allowed REAL,
    ppda_att     REAL,
    ppda_def     REAL,
    ppda_allowed_att REAL,
    ppda_allowed_def REAL,
    PRIMARY KEY (season, understat_team, match_date, understat_match_id)
);

-- Persisted, point-in-time OpenFPL feature samples (one row per prediction).
CREATE TABLE IF NOT EXISTS samples (
    season    TEXT NOT NULL,
    gw        INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    built_utc TEXT NOT NULL,
    row_json  TEXT NOT NULL,             -- full 235-column row as JSON
    PRIMARY KEY (season, gw, player_id)
);

-- Manual entity-resolution overrides (checked into version control via export).
CREATE TABLE IF NOT EXISTS entity_override (
    kind        TEXT NOT NULL,           -- 'player' | 'team'
    season      TEXT NOT NULL,
    fpl_key     TEXT NOT NULL,           -- fpl id or name
    understat_key TEXT NOT NULL,
    PRIMARY KEY (kind, season, fpl_key)
);
"""


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open (creating parent dir if needed) a connection with sane pragmas."""
    path = db_path or config.DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# Columns added after the initial schema. init_db() adds any that are missing so
# a database created by an earlier version self-heals without a full re-pull.
_COLUMN_MIGRATIONS = {
    "team": [
        ("code", "INTEGER"),
    ],
    "player_gw": [
        ("xg", "REAL"), ("xa", "REAL"), ("xgi", "REAL"), ("xgc", "REAL"),
    ],
    "team_match": [
        ("xg", "REAL"), ("xga", "REAL"),
    ],
    "player": [
        ("now_cost", "REAL"),
        ("status", "TEXT"),
        ("chance_next", "REAL"),
    ],
}


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table, cols in _COLUMN_MIGRATIONS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init_db(db_path: str | None = None) -> None:
    """Create all tables if they do not yet exist and apply column migrations.

    Idempotent: safe to call at the start of every command. Brings a database
    created by an earlier schema version up to date (new tables via
    ``CREATE TABLE IF NOT EXISTS``, new columns via ``ALTER TABLE``).
    """
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        _ensure_columns(conn)
        conn.commit()


@contextmanager
def session(db_path: str | None = None) -> Iterator[sqlite3.Connection]:
    """Context manager that commits on success and always closes."""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert(conn: sqlite3.Connection, table: str, rows: Iterable[dict]) -> int:
    """INSERT OR REPLACE a batch of dict rows. Returns rows written."""
    rows = list(rows)
    if not rows:
        return 0
    cols: Sequence[str] = list(rows[0].keys())
    placeholders = ",".join(["?"] * len(cols))
    collist = ",".join(cols)
    sql = f"INSERT OR REPLACE INTO {table} ({collist}) VALUES ({placeholders})"
    conn.executemany(sql, [[r.get(c) for c in cols] for r in rows])
    return len(rows)
