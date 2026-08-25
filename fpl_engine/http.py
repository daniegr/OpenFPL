"""Small, dependency-light HTTP helper: retries, backoff, on-disk caching.

Uses ``requests`` when available (nicer), otherwise falls back to urllib so the
pipeline still runs on a bare Python install. All network access in the project
goes through here so rate-limiting and caching live in one place.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
import urllib.error
import urllib.parse
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
# The official FPL API is a large CDN-backed service; its own site fires
# dozens of these calls per page, so a short interval is still polite.
HOST_INTERVAL_S = {"fantasy.premierleague.com": 0.15}


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_THROTTLE_LOCK = threading.Lock()


def _throttle(host: str) -> None:
    """Pace request *starts* per host; safe under concurrent callers."""
    interval = HOST_INTERVAL_S.get(host, MIN_INTERVAL_S)
    while True:
        with _THROTTLE_LOCK:
            now = time.monotonic()
            wait = _LAST_CALL.get(host, 0.0) + interval - now
            if wait <= 0:
                _LAST_CALL[host] = now
                return
        time.sleep(wait)


def _permanent_http_error(exc: Exception) -> bool:
    """True for definitive client errors (404 …) that retrying cannot fix.

    429 (rate limited) and all 5xx stay retryable.
    """
    status = None
    if requests is not None and isinstance(exc, requests.HTTPError):
        status = getattr(exc.response, "status_code", None)
    elif isinstance(exc, urllib.error.HTTPError):
        status = exc.code
    return status is not None and 400 <= status < 500 and status != 429


def _cache_path(url: str) -> str:
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    return os.path.join(config.CACHE_DIR, f"{key}.cache")


def get_text(url: str, *, use_cache: bool = True, retries: int = 4,
             timeout: int = 60, headers: dict | None = None) -> str:
    """GET a URL as text, with on-disk caching and exponential backoff retries.

    ``headers`` are merged over the default User-Agent (some JSON endpoints,
    e.g. Understat's, answer only to ``X-Requested-With: XMLHttpRequest``).
    """
    return _request("GET", url, None, use_cache=use_cache, retries=retries,
                    timeout=timeout, headers=headers)


def post_text(url: str, data: dict, *, use_cache: bool = True, retries: int = 4,
              timeout: int = 60, headers: dict | None = None) -> str:
    """POST a form and return the body as text (cached on url + form data)."""
    return _request("POST", url, data, use_cache=use_cache, retries=retries,
                    timeout=timeout, headers=headers)


def _request(method: str, url: str, data: dict | None, *, use_cache: bool,
             retries: int, timeout: int, headers: dict | None) -> str:
    form = urllib.parse.urlencode(sorted((data or {}).items()))
    cache = _cache_path(url if method == "GET" else f"{url}?{form}")
    if use_cache and os.path.exists(cache):
        with open(cache, "r", encoding="utf-8") as fh:
            return fh.read()

    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    host = url.split("/")[2]
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            _throttle(host)
            if requests is not None:
                resp = (requests.get(url, headers=hdrs, timeout=timeout)
                        if method == "GET" else
                        requests.post(url, data=data, headers=hdrs, timeout=timeout))
                resp.raise_for_status()
                text = resp.text
            else:  # pragma: no cover
                body = form.encode() if method == "POST" else None
                req = urllib.request.Request(url, data=body, headers=hdrs,
                                             method=method)
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    text = r.read().decode("utf-8", "replace")
            if use_cache:
                with open(cache, "w", encoding="utf-8") as fh:
                    fh.write(text)
            return text
        except Exception as e:  # noqa: BLE001 - retry transient errors
            last_err = e
            if _permanent_http_error(e):
                raise RuntimeError(f"{method} failed ({e}): {url}") from e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"{method} failed after {retries} attempts: {url}") from last_err
