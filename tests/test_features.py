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


def test_team_history_joins_by_stable_code_across_renames(conn):
    """"Alpha" (2024-25, code 11) renamed "Alpha FC" in 2025-26 must still
    find its match log; a bare name lookup for the new name must not."""
    from fpl_engine import db
    db.upsert(conn, "team", [
        {"season": "2024-25", "team_id": 1, "name": "Alpha", "short_name": "ALP",
         "code": 11, "understat_name": "Alpha"},
        {"season": "2025-26", "team_id": 7, "name": "Alpha FC", "short_name": "ALP",
         "code": 11, "understat_name": None},
    ])
    as_of = "2024-08-04T14:00:00Z"
    by_code = features._team_history(conn, {"name": "Alpha FC", "code": 11}, as_of)
    assert len(by_code) == 3 and all(r["goals_for"] == 2 for r in by_code)
    assert features._team_history(conn, "Alpha FC", as_of) == []
    # the original name path still works for code-less history rows
    assert len(features._team_history(conn, "Beta", as_of)) == 3


def test_promoted_club_gets_relegated_prior_not_nan(conn):
    """A club with no top-flight log borrows last season's relegated clubs'
    matches (point-in-time) instead of collapsing to NaN -> 0."""
    from fpl_engine import db
    db.upsert(conn, "team", [
        {"season": "2024-25", "team_id": 1, "name": "Alpha", "short_name": "ALP",
         "code": 11, "understat_name": "Alpha"},
        {"season": "2025-26", "team_id": 7, "name": "Alpha", "short_name": "ALP",
         "code": 11, "understat_name": None},
        {"season": "2025-26", "team_id": 9, "name": "Newbie", "short_name": "NEW",
         "code": 99, "understat_name": None},
    ])
    features._PRIOR_CACHE.clear()
    as_of = "2024-08-04T14:00:00Z"
    # Beta (2024-25) is absent from 2025-26 -> it is the relegated prior
    prior = features._relegated_prior_history(conn, "2025-26", as_of)
    assert len(prior) == 3 and all(r["goals_for"] == 1 for r in prior)
    assert features._team_history(conn, {"name": "Newbie", "code": 99}, as_of) == []


def _seed_fpl_xg(conn):
    """Attach FPL xG/xA to the seeded GW1..3 rows and team xG to team_match."""
    from fpl_engine import db
    rows = []
    for gw, xg, xa in [(1, 0.5, 0.1), (2, 0.2, 0.4), (3, 0.8, 0.3)]:
        rows.append({"season": "2024-25", "gw": gw, "source": "vaastav",
                     "player_id": 10, "fixture_id": gw, "player_code": 1000,
                     "full_name": "Mid Player", "team_id": 1, "opponent_id": 2,
                     "was_home": 1, "kickoff_utc": f"2024-08-0{gw}T14:00:00Z",
                     "minutes": 90, "total_points": 5, "goals_scored": 1,
                     "assists": 0, "clean_sheets": 0, "starts": 1,
                     "xg": xg, "xa": xa})
    db.upsert(conn, "player_gw", rows)
    tm = []
    for gw, axg, bxg in [(1, 1.5, 0.4), (2, 1.0, 0.9), (3, 2.0, 0.6)]:
        k = f"2024-08-0{gw}T14:00:00Z"
        tm.append({"season": "2024-25", "team_id": 1, "fixture_id": gw, "gw": gw,
                   "kickoff_utc": k, "opponent_id": 2, "was_home": 1,
                   "goals_for": 2, "goals_against": 1, "xg": axg, "xga": bxg})
        tm.append({"season": "2024-25", "team_id": 2, "fixture_id": gw, "gw": gw,
                   "kickoff_utc": k, "opponent_id": 1, "was_home": 0,
                   "goals_for": 1, "goals_against": 2, "xg": bxg, "xga": axg})
    db.upsert(conn, "team_match", tm)
    conn.commit()


def test_fpl_xg_stands_in_for_understat_when_missing(conn):
    _seed_fpl_xg(conn)
    df = features.build_samples(conn, "2024-25", 4)
    mid = df[df["player"] == "Mid Player"].iloc[0]
    assert abs(mid["player xg 1"] - 0.8) < 1e-9            # most recent (GW3)
    assert abs(mid["player xg 3"] - (0.5 + 0.2 + 0.8) / 3) < 1e-9
    assert abs(mid["player xa 1"] - 0.3) < 1e-9
    assert np.isnan(mid["player shots 1"])                 # no FPL equivalent
    assert abs(mid["team xg 1"] - 2.0) < 1e-9
    assert abs(mid["team xga 1"] - 0.6) < 1e-9
    assert abs(mid["opponent xg 1"] - 0.6) < 1e-9          # Beta's xG
    assert abs(mid["opponent xga 1"] - 2.0) < 1e-9


def test_understat_takes_precedence_over_fpl_xg(conn):
    from fpl_engine import db
    _seed_fpl_xg(conn)
    db.upsert(conn, "player", [{"season": "2024-25", "player_id": 10, "code": 1000,
        "web_name": "Mid", "full_name": "Mid Player", "team_id": 1,
        "position": "MID", "understat_id": "u10"}])
    db.upsert(conn, "understat_player_match", [{
        "season": "2024-25", "understat_id": "u10", "match_date": "2024-08-03",
        "understat_match_id": "m3", "goals": 0, "shots": 4, "xg": 0.11,
        "assists": 0, "key_passes": 2, "xa": 0.05, "xgchain": 0.3,
        "xgbuildup": 0.2, "minutes": 90}])
    conn.commit()
    df = features.build_samples(conn, "2024-25", 4)
    mid = df[df["player"] == "Mid Player"].iloc[0]
    assert abs(mid["player xg 1"] - 0.11) < 1e-9           # Understat, not FPL 0.8
    assert abs(mid["player shots 1"] - 4) < 1e-9


def test_promoted_club_gets_relegated_understat_prior(conn):
    from fpl_engine import db
    db.upsert(conn, "team", [
        {"season": "2024-25", "team_id": 1, "name": "Alpha", "short_name": "ALP",
         "code": 11, "understat_name": "Alpha"},
        {"season": "2025-26", "team_id": 7, "name": "Alpha", "short_name": "ALP",
         "code": 11, "understat_name": "Alpha"},
    ])
    db.upsert(conn, "understat_team_match", [
        {"season": "2024-25", "understat_team": "Beta", "match_date": f"2024-08-0{g}",
         "understat_match_id": f"m{g}", "is_home": 0, "xg": 0.9 + g / 10, "xga": 1.8,
         "deep": 3, "deep_allowed": 9, "ppda_att": 150, "ppda_def": 10,
         "ppda_allowed_att": 200, "ppda_allowed_def": 20} for g in (1, 2, 3)])
    conn.commit()
    features._PRIOR_CACHE.clear()
    prior = features._relegated_prior_understat(conn, "2025-26", "2024-08-04T14:00:00Z")
    assert len(prior) == 3 and prior[0]["match_date"] == "2024-08-03"
    assert abs(prior[0]["xg"] - 1.2) < 1e-9
    # point-in-time: nothing before the first match
    assert features._relegated_prior_understat(conn, "2025-26", "2024-08-01T00:00:00Z") == []
