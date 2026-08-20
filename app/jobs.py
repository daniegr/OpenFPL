"""Tiny in-process background-job manager for long-running work (solves,
projection builds, data pulls). One thread per job; polled via the API."""
from __future__ import annotations

import threading
import traceback
import uuid
from datetime import datetime, timezone

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def start(kind: str, target, *args, **kwargs) -> str:
    """Run ``target(job_id, *args, **kwargs)`` in a thread; return job id."""
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {"id": job_id, "kind": kind, "status": "running",
                         "progress": [], "pct": 0.0, "result": None,
                         "error": None, "started_at": _now()}

    def _run():
        try:
            result = target(job_id, *args, **kwargs)
            with _lock:
                _jobs[job_id].update(status="done", result=result, pct=1.0)
        except Exception as exc:  # noqa: BLE001 - reported to the client
            with _lock:
                _jobs[job_id].update(status="error",
                                     error=f"{exc}\n{traceback.format_exc(limit=3)}")

    threading.Thread(target=_run, daemon=True).start()
    return job_id


def progress(job_id: str, msg: str, pct: float | None = None) -> None:
    with _lock:
        j = _jobs.get(job_id)
        if j is None:
            return
        j["progress"].append({"t": _now(), "msg": msg})
        if pct is not None:
            j["pct"] = max(j["pct"], min(1.0, pct))


def get(job_id: str) -> dict | None:
    with _lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None


def running(kind: str | None = None) -> list[dict]:
    with _lock:
        return [dict(j) for j in _jobs.values()
                if j["status"] == "running" and (kind is None or j["kind"] == kind)]
