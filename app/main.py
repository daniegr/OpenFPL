"""FastAPI app: JSON API for the OpenFPL planner + static frontend serving.

Run with:  python -m app   (serves http://127.0.0.1:8410)
"""
from __future__ import annotations

import os
import warnings

warnings.filterwarnings("ignore")  # sklearn pickle version chatter

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from fpl_engine import config, db

from . import jobs, services

app = FastAPI(title="OpenFPL Planner", docs_url="/api/docs",
              openapi_url="/api/openapi.json")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"], allow_headers=["*"],
)


@app.on_event("startup")
def _ensure_db() -> None:
    db.init_db(config.DB_PATH)


# --- meta / data ----------------------------------------------------------

@app.get("/api/status")
def status():
    return services.status_payload()


@app.get("/api/players")
def players():
    return services.players_payload()


@app.get("/api/fixtures")
def fixtures():
    return services.fixtures_payload()


@app.get("/api/projections")
def projections():
    return services.projections_payload()


@app.post("/api/projections/build")
def projections_build(body: dict):
    gws = body.get("gws")
    if not gws:
        raise HTTPException(400, "gws required")
    if jobs.running("projections") or jobs.running("solve"):
        raise HTTPException(409, "a projection/solve job is already running")
    job_id = jobs.start("projections", services.build_projections,
                        [int(g) for g in gws], force=bool(body.get("force")),
                        blend=body.get("blend"))
    return {"job_id": job_id}


@app.post("/api/pull")
def pull(body: dict | None = None):
    if jobs.running():
        raise HTTPException(409, "another job is already running")
    job_id = jobs.start("pull", services.run_pull,
                        bool((body or {}).get("understat")))
    return {"job_id": job_id}


@app.get("/api/entry/{entry_id}")
def entry(entry_id: int):
    return services.entry_payload(entry_id)


# --- my team (pre-deadline squads are private to the public API) ----------

@app.get("/api/myteam")
def myteam_get():
    doc = services.load_my_team()
    return doc or {"squad": None}


@app.put("/api/myteam")
def myteam_put(body: dict):
    squad = body.get("squad") or []
    if len(squad) != 15:
        raise HTTPException(400, f"a squad needs 15 players, got {len(squad)}")
    return services.save_my_team(body)


@app.delete("/api/myteam")
def myteam_delete():
    services.save_my_team(None)
    return {"squad": None}


@app.post("/api/myteam/import")
def myteam_import(body: dict):
    cookie = (body.get("cookie") or "").strip()
    entry_id = int(body.get("entry") or 0)
    if not cookie or not entry_id:
        raise HTTPException(400, "cookie and entry required")
    try:
        return services.import_my_team_with_cookie(entry_id, cookie)
    except Exception as exc:  # surface the reason (401, no picks, …)
        raise HTTPException(400, f"import failed: {exc}")


# --- solver ---------------------------------------------------------------

@app.post("/api/solve")
def solve(body: dict):
    if jobs.running("solve"):
        raise HTTPException(409, "a solve is already running")
    job_id = jobs.start("solve", services.run_solve, body or {})
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    j = jobs.get(job_id)
    if j is None:
        raise HTTPException(404, "unknown job")
    return j


# --- drafts ---------------------------------------------------------------

@app.get("/api/drafts")
def drafts_get():
    return services.load_drafts()


@app.put("/api/drafts")
def drafts_put(body: dict):
    return services.save_drafts(body or {})


# --- static frontend (vite build output) ----------------------------------

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
