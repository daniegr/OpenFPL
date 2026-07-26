"""Tests for the point-in-time feature builder."""
import numpy as np
import pandas as pd

from fpl_engine import config, features


def test_sample_columns_match_reference():
    """The generated column spec must equal the reference samples.csv header."""
    import os
    ref = os.path.join(config.DATA_DIR, "samples.csv")
    want = list(pd.read_csv(ref, nrows=0).columns)
    assert config.sample_columns() == want


def test_build_produces_exact_columns(conn):
    df = features.build_samples(conn, "2024-25", 4)
    assert list(df.columns) == config.sample_columns()
    assert len(df) == 2  # one MID, one GK


def test_point_in_time_excludes_future(conn):
    """The GW4 build must not see the (planted) GW4 row worth 99 points."""
    df = features.build_samples(conn, "2024-25", 4)
    mid = df[df["player"] == "Mid Player"].iloc[0]
    # History before GW4 is GW1..GW3 points [6, 2, 8] -> mean last 3 = 5.333
    assert abs(mid["player fpl points 3"] - (6 + 2 + 8) / 3) < 1e-9
    # window-1 = most recent (GW3) = 8, NOT the future 99
    assert abs(mid["player fpl points 1"] - 8) < 1e-9


def test_position_nulls_applied(conn):
    df = features.build_samples(conn, "2024-25", 4)
    mid = df[df["player"] == "Mid Player"].iloc[0]
    gk = df[df["player"] == "Keep Er"].iloc[0]
    # MID: saves nulled; GK: goals scored nulled
    assert all(np.isnan(mid[f"player saves {w}"]) for w in config.WINDOWS)
    assert all(np.isnan(gk[f"player goals scored {w}"]) for w in config.WINDOWS)


def test_team_and_opponent_features(conn):
    df = features.build_samples(conn, "2024-25", 4)
    mid = df[df["player"] == "Mid Player"].iloc[0]  # team Alpha vs Beta
    assert abs(mid["team goals scored 1"] - 2) < 1e-9
    assert abs(mid["opponent goals scored 1"] - 1) < 1e-9


def test_league_rank_is_nan_for_players(conn):
    df = features.build_samples(conn, "2024-25", 4)
    for _, row in df.iterrows():
        assert np.isnan(row["team league rank 1"])
        assert np.isnan(row["status team league rank"])
