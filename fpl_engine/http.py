"""Small, dependency-light HTTP helper: retries, backoff, on-disk caching.

Uses ``requests`` when available (nicer), otherwise falls back to urllib so the
pipeline still runs on a bare Python install. All network access in the project
goes through here so rate-limiting and caching live in one place.
"""
from __future__ import annotations

import hashlib
import os
import time
import urllib.request
from datetime import datetime, timezone

from . import config

try:  # optional, nicer client
    import requests  # type: ignore
except Exception:  # pragma: no cover - fallback path
    requests = None

USER_AGENT = "fpl-engine/0.1 (+https://github.com/daniegr/OpenFPL)"
_LAST_CALL: dict[str, float] = {}
MIN_INTERVAL_S = 0.6  # be polite to free endpoints


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _throttle(host: str) -> None:
    last = _LAST_CALL.get(host, 0.0)
    wait = MIN_INTERVAL_S - (time.monotonic() - last)
    if wait > 0:
        time.sleep(wait)
    _LAST_CALL[host] = time.monotonic()


def _cache_path(url: str) -> str:
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    return os.path.join(config.CACHE_DIR, f"{key}.cache")


def get_text(url: str, *, use_cache: bool = True, retries: int = 4,
             timeout: int = 60) -> str:
    """GET a URL as text, with on-disk caching and exponential backoff retries."""
    cache = _cache_path(url)
    if use_cache and os.path.exists(cache):
        with open(cache, "r", encoding="utf-8") as fh:
            return fh.read()

    host = url.split("/")[2]
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            _throttle(host)
            if requests is not None:
                resp = requests.get(url, headers={"User-Agent": USER_AGENT},
                                    timeout=timeout)
                resp.raise_for_status()
                text = resp.text
            else:  # pragma: no cover
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    text = r.read().decode("utf-8", "replace")
            if use_cache:
                with open(cache, "w", encoding="utf-8") as fh:
                    fh.write(text)
            return text
        except Exception as e:  # noqa: BLE001 - retry all transient errors
            last_err = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"GET failed after {retries} attempts: {url}") from last_err
