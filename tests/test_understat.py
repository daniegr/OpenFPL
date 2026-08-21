"""Understat ingestor: pure normalisers against captured endpoint shapes.

No network: the dicts below mirror getLeagueData / getPlayerData payloads
as served after Understat's Dec-2025 redesign.
"""
from fpl_engine.ingest import understat

LEAGUE = {
    "dates": [
        {"id": "28778", "isResult": True,
         "h": {"id": "87", "title": "Liverpool"}, "a": {"id": "73", "title": "Bournemouth"},
         "goals": {"h": "4", "a": "2"}, "xG": {"h": "2.33", "a": "1.57"},
         "datetime": "2025-08-15 19:00:00"},
        {"id": "28790", "isResult": False,
         "h": {"id": "73", "title": "Bournemouth"}, "a": {"id": "87", "title": "Liverpool"},
         "datetime": "2026-01-10 15:00:00"},
    ],
    "teams": {
        "73": {"id": "73", "title": "Bournemouth", "history": [
            {"h_a": "a", "xG": 1.57, "xGA": 2.33, "ppda": {"att": 227, "def": 12},
             "ppda_allowed": {"att": 146, "def": 24}, "deep": 2, "deep_allowed": 6,
             "date": "2025-08-15 19:00:00"}]},
        "87": {"id": "87", "title": "Liverpool", "history": [
            {"h_a": "h", "xG": 2.33, "xGA": 1.57, "ppda": {"att": 300, "def": 30},
             "ppda_allowed": {"att": 100, "def": 10}, "deep": 9, "deep_allowed": 3,
             "date": "2025-08-15 19:00:00"}]},
    },
    "players": [{"id": "8260", "player_name": "Erling Haaland", "team_title": "Manchester City"}],
}

MATCHES = [
    {"goals": "1", "shots": "2", "xG": "0.157", "time": "90", "h_team": "Bournemouth",
     "a_team": "Manchester City", "date": "2026-05-19", "id": "29138", "season": "2025",
     "xA": "0.448", "assists": "0", "key_passes": "1", "xGChain": "0.512", "xGBuildup": "0.1"},
    {"goals": "2", "shots": "5", "xG": "1.9", "time": "88", "h_team": "Augsburg",
     "a_team": "Borussia Dortmund", "date": "2020-01-18", "id": "12562", "season": "2019",
     "xA": "0.0", "assists": "0", "key_passes": "0", "xGChain": "2.0", "xGBuildup": "0.0"},
]


def test_year_season_roundtrip():
    assert understat.year_to_season(2025) == "2025-26"
    assert understat.season_to_year("2025-26") == 2025


def test_team_rows_resolve_match_ids_and_sides():
    rows = understat.team_rows_from_league(LEAGUE, "2025-26")
    assert len(rows) == 2
    bou = next(r for r in rows if r["understat_team"] == "Bournemouth")
    assert bou["is_home"] == 0 and bou["understat_match_id"] == "28778"
    assert bou["match_date"] == "2025-08-15"
    assert bou["xg"] == 1.57 and bou["xga"] == 2.33
    assert bou["ppda_att"] == 227 and bou["ppda_allowed_def"] == 24
    assert bou["deep"] == 2 and bou["deep_allowed"] == 6
    liv = next(r for r in rows if r["understat_team"] == "Liverpool")
    assert liv["is_home"] == 1 and liv["understat_match_id"] == "28778"


def test_player_rows_label_seasons_and_filter_to_league():
    rows = understat.player_rows_from_matches("8260", MATCHES)
    assert [r["season"] for r in rows] == ["2025-26", "2019-20"]
    r = rows[0]
    assert r["understat_id"] == "8260" and r["understat_match_id"] == "29138"
    assert r["match_date"] == "2026-05-19" and r["minutes"] == 90
    assert abs(r["xg"] - 0.157) < 1e-9 and abs(r["xa"] - 0.448) < 1e-9
    assert r["shots"] == 2 and r["key_passes"] == 1
    epl_only = understat.player_rows_from_matches(
        "8260", MATCHES, epl_titles={"Bournemouth", "Manchester City"})
    assert len(epl_only) == 1 and epl_only[0]["season"] == "2025-26"


def test_fetchers_fail_soft_on_garbage(monkeypatch):
    monkeypatch.setattr(understat, "get_text", lambda *a, **k: "<html>not json</html>")
    monkeypatch.setattr(understat, "post_text", lambda *a, **k: "{}")
    assert understat.fetch_league_data("2024-25", use_cache=False) is None
    assert understat.fetch_player_matches("1", use_cache=False) == []
    assert understat.fetch_league_players("2024-25", use_cache=False) == []


def test_player_fetch_prefers_light_endpoint_then_falls_back(monkeypatch):
    import json
    light = {"response": {"success": True, "matches": MATCHES}}
    monkeypatch.setattr(understat, "post_text", lambda *a, **k: json.dumps(light))
    monkeypatch.setattr(understat, "get_text",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("fallback used")))
    assert len(understat.fetch_player_matches("8260", use_cache=False)) == 2
    # light endpoint empty -> fall back to getPlayerData
    monkeypatch.setattr(understat, "post_text",
                        lambda *a, **k: json.dumps({"response": {"success": True, "matches": []}}))
    monkeypatch.setattr(understat, "get_text", lambda *a, **k: json.dumps({"matches": MATCHES[:1]}))
    assert len(understat.fetch_player_matches("8260", use_cache=False)) == 1


def test_incremental_refresh_rule():
    from fpl_engine.pipeline import _needs_refresh
    assert _needs_refresh(None, "2026-08-21T19:00:00Z")          # nothing held yet
    assert not _needs_refresh("2026-05-24", None)                 # FPL has no match
    assert not _needs_refresh("2026-05-24", "2026-05-24T14:00:00Z")   # up to date
    assert not _needs_refresh("2026-05-24", "2026-05-10T14:00:00Z")   # pre-season
    assert _needs_refresh("2026-05-24", "2026-08-21T19:00:00Z")       # played since
