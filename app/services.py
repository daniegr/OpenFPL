"""Data services for the web app.

Bridges fpl_engine (SQLite + models + optimiser) and the HTTP layer:
  * live FPL bootstrap / fixtures (in-memory TTL cache — ownership, FDR,
    shirt/photo codes, injury news)
  * point-in-time projections for a horizon, cached on disk per (season, gw)
  * chip-aware solve orchestration
  * draft persistence (one JSON document)
"""
from __future__ import annotations

import json
import os
import threading
import time

import pandas as pd

from fpl_engine import config, db, manager, predict as predict_mod
from fpl_engine.http import get_text
from fpl_engine.optimise import chips, project
from fpl_engine.pipeline import next_gw, resolve_blend

from . import jobs

WEB_CACHE = os.path.join(config.DATA_DIR, "web_cache")
PROJ_PATH = os.path.join(WEB_CACHE, "projections.json")
DRAFTS_PATH = os.path.join(WEB_CACHE, "drafts.json")
MYTEAM_PATH = os.path.join(WEB_CACHE, "my_team.json")

FPL_BASE = "https://fantasy.premierleague.com/api"
_TTL = 600.0

_mem: dict[str, tuple[float, object]] = {}
_bundle = None
_bundle_lock = threading.Lock()
_proj_lock = threading.Lock()


# --------------------------------------------------------------------------
# live FPL endpoints (memory-cached)
# --------------------------------------------------------------------------

def _live_json(path: str):
    now = time.monotonic()
    hit = _mem.get(path)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    data = json.loads(get_text(f"{FPL_BASE}/{path}", use_cache=False))
    _mem[path] = (now, data)
    return data


def bootstrap() -> dict:
    return _live_json("bootstrap-static/")


def live_fixtures() -> list:
    return _live_json("fixtures/")


def players_payload() -> dict:
    """Static player + team info merged from live bootstrap (ownership, codes,
    injury status) and the local DB (canonical position/price used by models)."""
    bs = bootstrap()
    teams = {t["id"]: {"id": t["id"], "name": t["name"], "short": t["short_name"],
                       "code": t["code"]} for t in bs["teams"]}
    pos_map = config.ELEMENT_TYPE_TO_POSITION
    players = []
    for e in bs["elements"]:
        pos = pos_map.get(e["element_type"])
        if pos is None:
            continue
        players.append({
            "id": e["id"], "web_name": e["web_name"],
            "name": f"{e['first_name']} {e['second_name']}",
            "team_id": e["team"], "position": pos,
            "price": e["now_cost"] / 10.0,
            "own": float(e.get("selected_by_percent") or 0.0),
            "status": e.get("status"), "news": e.get("news") or "",
            "code": e.get("code"),
            "chance": e.get("chance_of_playing_next_round"),
        })
    events = [{"id": ev["id"], "name": ev["name"],
               "deadline": ev["deadline_time"],
               "finished": ev["finished"], "is_next": ev["is_next"]}
              for ev in bs["events"]]
    return {"players": players, "teams": teams, "events": events}


def fixtures_payload() -> dict:
    """Team-centric fixture grid with FPL difficulty ratings (for the heatmap)."""
    fx = live_fixtures()
    grid: dict[int, dict[int, list]] = {}
    for f in fx:
        gw = f.get("event")
        if gw is None:
            continue
        h, a = f["team_h"], f["team_a"]
        grid.setdefault(h, {}).setdefault(gw, []).append(
            {"opp": a, "home": True, "fdr": f.get("team_h_difficulty"),
             "kickoff": f.get("kickoff_time"), "finished": f.get("finished")})
        grid.setdefault(a, {}).setdefault(gw, []).append(
            {"opp": h, "home": False, "fdr": f.get("team_a_difficulty"),
             "kickoff": f.get("kickoff_time"), "finished": f.get("finished")})
    return {"grid": {str(t): {str(g): v for g, v in gws.items()}
                     for t, gws in grid.items()}}


def entry_payload(entry_id: int) -> dict:
    try:
        info = manager.fetch_entry(entry_id)
    except Exception:
        return {"entry_id": entry_id, "exists": False}
    state = squad_state(entry_id)
    out = {"entry_id": entry_id, "exists": True,
           "team_name": info.get("name"),
           "player_name": f"{info.get('player_first_name', '')} "
                          f"{info.get('player_last_name', '')}".strip(),
           "overall_points": info.get("summary_overall_points"),
           "overall_rank": info.get("summary_overall_rank"),
           "squad": None, "squad_source": None}
    if state is not None:
        out.update({"squad": state["squad"], "bank": state["bank"],
                    "free_transfers": state["free_transfers"],
                    "picks_gw": state.get("gw"),
                    "squad_source": state.get("source", "public")})
    return out


# --------------------------------------------------------------------------
# "my team": the squad the planner/solver should start from.
#
# Before a gameweek deadline passes, FPL's public API does not expose an
# entry's picks (they are private), so pre-season / pre-deadline squads can
# only come from (a) the authenticated my-team endpoint using the user's own
# browser session cookie, or (b) a manually entered squad. Both are stored in
# one local file and take precedence over the public picks endpoints.
# --------------------------------------------------------------------------

def load_my_team() -> dict | None:
    if os.path.exists(MYTEAM_PATH):
        with open(MYTEAM_PATH, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_my_team(doc: dict | None) -> dict | None:
    os.makedirs(WEB_CACHE, exist_ok=True)
    if doc is None:
        if os.path.exists(MYTEAM_PATH):
            os.remove(MYTEAM_PATH)
        return None
    doc = {"entry_id": doc.get("entry_id"),
           "squad": doc["squad"],                 # [{element, selling_price, ...}]
           "bank": float(doc.get("bank") or 0.0),
           "free_transfers": int(doc.get("free_transfers") or 1),
           "source": doc.get("source", "manual"),
           "saved_at": time.time()}
    tmp = MYTEAM_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    os.replace(tmp, MYTEAM_PATH)
    return doc


def import_my_team_with_cookie(entry_id: int, cookie: str) -> dict:
    """Fetch the authenticated my-team endpoint with the user's own FPL
    browser session cookie (never stored — only the resulting squad is)."""
    import urllib.request
    req = urllib.request.Request(
        f"{FPL_BASE}/my-team/{entry_id}/",
        headers={"Cookie": cookie.strip(),
                 "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode())
    if "picks" not in data:
        raise ValueError("response has no picks — cookie rejected?")
    squad = [{
        "element": p["element"],
        "selling_price": p.get("selling_price", 0) / 10.0,
        "purchase_price": p.get("purchase_price", 0) / 10.0,
        "is_captain": bool(p.get("is_captain")),
        "is_vice": bool(p.get("is_vice_captain")),
        "multiplier": p.get("multiplier", 1),
    } for p in data["picks"]]
    tr = data.get("transfers", {}) or {}
    limit = tr.get("limit")
    made = tr.get("made", 0) or 0
    return save_my_team({
        "entry_id": entry_id, "squad": squad,
        "bank": (tr.get("bank", 0) or 0) / 10.0,
        "free_transfers": max(0, (limit or 1) - made) if limit is not None else 1,
        "source": "fpl-login"})


def squad_state(entry_id: int) -> dict | None:
    """Current squad for an entry: public picks if available (post-deadline,
    authoritative), else the locally saved my-team (cookie import or manual)."""
    state = None
    try:
        state = manager.current_squad(entry_id)
    except Exception:
        state = None
    if state is not None:
        state["source"] = "public"
        return state
    mine = load_my_team()
    if mine and (mine.get("entry_id") in (None, entry_id)):
        return {"entry_id": entry_id, "name": None, "gw": None,
                "bank": mine["bank"], "squad": mine["squad"],
                "free_transfers": mine["free_transfers"],
                "source": mine.get("source", "manual")}
    return None


# --------------------------------------------------------------------------
# projections (disk-cached per season+gw)
# --------------------------------------------------------------------------

def _load_proj_cache() -> dict:
    if os.path.exists(PROJ_PATH):
        with open(PROJ_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"season": config.CURRENT_SEASON, "gws": {}, "players": {}}


def _save_proj_cache(cache: dict) -> None:
    os.makedirs(WEB_CACHE, exist_ok=True)
    tmp = PROJ_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    os.replace(tmp, PROJ_PATH)


def _get_bundle():
    global _bundle
    with _bundle_lock:
        if _bundle is None:
            _bundle = predict_mod.load_models()
        return _bundle


def projections_payload() -> dict:
    cache = _load_proj_cache()
    return cache


def build_projections(job_id: str | None, gws: list[int], *,
                      force: bool = False, blend=None) -> dict:
    """Compute and cache per-player projections for each gw in ``gws``.

    Serialised under a lock so concurrent solves don't duplicate model runs.
    """
    season = config.CURRENT_SEASON
    with _proj_lock:
        cache = _load_proj_cache()
        if cache.get("season") != season:
            cache = {"season": season, "gws": {}, "players": {}}
        todo = [g for g in gws if force or str(g) not in cache["gws"]]
        if not todo:
            return cache
        conn = db.connect(config.DB_PATH)
        try:
            bundle = _get_bundle()
            retrained, weight = resolve_blend(conn, season, blend)
            for i, g in enumerate(todo):
                if job_id:
                    jobs.progress(job_id, f"Projecting GW{g} "
                                  f"({i + 1}/{len(todo)})…",
                                  pct=0.05 + 0.6 * (i / max(1, len(todo))))
                df = project.horizon_projections(
                    conn, season, [g], bundle=bundle, retrained=retrained,
                    blend=weight)
                if df.empty:
                    continue
                for r in df.itertuples():
                    pid = str(int(r.player_id))
                    rec = cache["players"].setdefault(pid, {
                        "player_id": int(r.player_id), "player": r.player,
                        "position": r.position, "team_id": int(r.team_id),
                        "team": r.team, "price": float(r.price),
                        "available": float(r.available), "ep": {}})
                    rec["price"] = float(r.price)
                    rec["available"] = float(r.available)
                    rec["ep"][str(g)] = round(float(getattr(r, f"ep_gw{g}")), 3)
                cache["gws"][str(g)] = {"built_at": time.time()}
            cache["updated_at"] = time.time()
            _save_proj_cache(cache)
        finally:
            conn.close()
        return cache


def _proj_frame(cache: dict, gws: list[int], decay: float) -> pd.DataFrame:
    rows = []
    for rec in cache["players"].values():
        eps = {g: rec["ep"].get(str(g)) for g in gws}
        if all(v is None for v in eps.values()):
            continue
        row = {"player_id": rec["player_id"], "player": rec["player"],
               "position": rec["position"], "team_id": rec["team_id"],
               "team": rec["team"], "price": rec["price"],
               "available": rec["available"]}
        total = 0.0
        for i, g in enumerate(gws):
            v = eps[g] or 0.0
            row[f"ep_gw{g}"] = v
            total += (decay ** i) * v
        row["ep_total"] = total
        rows.append(row)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# solve orchestration
# --------------------------------------------------------------------------

def run_solve(job_id: str, params: dict) -> dict:
    season = config.CURRENT_SEASON
    conn = db.connect(config.DB_PATH)
    try:
        start = next_gw(conn, season)
        solve_from = int(params.get("solve_from") or start)
        horizon = int(params.get("horizon") or 5)
        scheduled = [r["gw"] for r in conn.execute(
            "SELECT DISTINCT gw FROM fixture WHERE season=? AND gw>=? AND gw "
            "IS NOT NULL ORDER BY gw", (season, max(start, solve_from)))]
        gws = scheduled[:horizon] or [start]
    finally:
        conn.close()

    jobs.progress(job_id, f"Planning horizon GW{gws[0]}–GW{gws[-1]}", pct=0.02)
    cache = build_projections(job_id, gws, blend=params.get("blend"))
    decay = float(params.get("decay") or 0.85)
    proj = _proj_frame(cache, gws, decay)
    if proj.empty:
        raise RuntimeError("No projections available — run a data pull first.")

    jobs.progress(job_id, "Fetching entry state…", pct=0.68)
    entry_id = params.get("entry")
    initial, bank, fts = None, float(params.get("budget") or 100.0), 1
    entry_state = None
    if entry_id:
        entry_state = squad_state(int(entry_id))
        if entry_state is not None:
            initial = {p["element"]: p["selling_price"]
                       for p in entry_state["squad"]}
            bank = entry_state["bank"]
            fts = entry_state["free_transfers"]
    if params.get("free_transfers") is not None:
        fts = int(params["free_transfers"])

    locked = set(params.get("locked") or [])
    avoid = set(params.get("avoid") or [])
    must_keep = set(initial or {}) | locked
    proj_p = project.prune(
        proj, keep_per_position=int(params.get("keep_per_position") or 30),
        must_keep=must_keep)

    chip_gws: dict[str, list[int]] = {}
    chip_force: dict[str, int] = {}
    for c, cfg in (params.get("chips") or {}).items():
        if c not in chips.CHIPS or not cfg or not cfg.get("enabled"):
            continue
        allowed = cfg.get("gws") or gws
        chip_gws[c] = [g for g in allowed if g in gws]
        if cfg.get("force") and int(cfg["force"]) in gws:
            chip_force[c] = int(cfg["force"])

    jobs.progress(job_id, f"Solving MILP over {len(proj_p)} players × "
                  f"{len(gws)} GWs…", pct=0.72)
    plans = chips.optimise_with_chips(
        proj_p, gws,
        initial=initial, bank=bank, free_transfers=fts,
        budget=float(params.get("budget") or 100.0),
        decay=decay,
        hit_cost=float(params.get("hit_cost") or 4.0),
        bench_weight=float(params.get("bench_weight") or 0.1),
        ft_value=float(params.get("ft_value") or 1.5),
        max_transfers_per_gw=int(params.get("max_transfers") or 3),
        time_limit=int(params.get("time_limit") or 60),
        chip_gws=chip_gws, chip_force=chip_force,
        locked=locked, avoid=avoid,
        banned_clubs=set(params.get("banned_teams") or []),
        forced_in={int(g): v for g, v in (params.get("forced_in") or {}).items()},
        forced_out={int(g): v for g, v in (params.get("forced_out") or {}).items()},
        min_ft={int(g): int(v) for g, v in (params.get("min_ft") or {}).items()},
        n_plans=int(params.get("n_plans") or 1),
        on_progress=lambda m: jobs.progress(job_id, m),
    )
    if not plans:
        raise RuntimeError("Solver found no feasible plan — check constraints "
                           "(locked/avoid/banned may conflict).")
    return {
        "mode": "optimise-transfers" if initial else "build-from-scratch",
        "entry_id": entry_id, "gws": gws,
        "state": {"bank": bank, "free_transfers": fts,
                  "team_name": (entry_state or {}).get("name") if entry_state
                  else None},
        "plans": [{"objective": p.objective, "status": p.status,
                   "per_gw": p.per_gw} for p in plans],
    }


def run_pull(job_id: str, understat: bool = False) -> dict:
    from fpl_engine.pipeline import pull
    jobs.progress(job_id, "Pulling FPL live data + backfill…", pct=0.1)
    conn = db.connect(config.DB_PATH)
    try:
        db.init_db(config.DB_PATH)
        summary = pull(conn, use_cache=True, with_understat=understat)
    finally:
        conn.close()
    # prices/status changed -> projections stale
    if os.path.exists(PROJ_PATH):
        os.remove(PROJ_PATH)
    _mem.clear()
    jobs.progress(job_id, "Pull complete; projection cache invalidated.", pct=1.0)
    return {"summary": {k: str(v) for k, v in summary.items()}}


# --------------------------------------------------------------------------
# drafts
# --------------------------------------------------------------------------

def load_drafts() -> dict:
    if os.path.exists(DRAFTS_PATH):
        with open(DRAFTS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"drafts": [], "updated_at": None}


def save_drafts(doc: dict) -> dict:
    os.makedirs(WEB_CACHE, exist_ok=True)
    doc = {"drafts": doc.get("drafts") or [], "updated_at": time.time()}
    tmp = DRAFTS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    os.replace(tmp, DRAFTS_PATH)
    return doc


def status_payload() -> dict:
    season = config.CURRENT_SEASON
    conn = db.connect(config.DB_PATH)
    try:
        n_players = conn.execute(
            "SELECT COUNT(*) FROM player WHERE season=?", (season,)).fetchone()[0]
        gw = next_gw(conn, season)
        scheduled = [r["gw"] for r in conn.execute(
            "SELECT DISTINCT gw FROM fixture WHERE season=? AND gw IS NOT NULL "
            "ORDER BY gw", (season,))]
    finally:
        conn.close()
    cache = _load_proj_cache()
    return {"season": season, "next_gw": gw, "scheduled_gws": scheduled,
            "db_ready": bool(n_players),
            "projected_gws": sorted(int(g) for g in cache.get("gws", {})),
            "proj_updated_at": cache.get("updated_at"),
            "default_entry": manager.DEFAULT_ENTRY,
            "jobs_running": [j["kind"] for j in jobs.running()]}
