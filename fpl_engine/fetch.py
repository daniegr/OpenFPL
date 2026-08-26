"""Fetch the latest retrained models published by the GitHub Actions refresh.

The scheduled workflow (``.github/workflows/fpl-predict.yml``) retrains the
per-position models twice a week and publishes ``models/retrained`` as
``retrained.zip`` on a rolling GitHub Release (tag ``models-latest``). Pulling
that asset is how a local install picks up fresh weights without training
itself; ``predict/optimise --blend auto`` and the web app then blend them with
OpenFPL. Release assets on a public repo need no token.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import urllib.request
import zipfile

from . import config

DEFAULT_REPO = os.environ.get("FPL_MODELS_REPO", "KoalaaDev/OpenFPL")
DEFAULT_TAG = os.environ.get("FPL_MODELS_TAG", "models-latest")
ASSET = "retrained.zip"


def release_asset_url(repo: str = DEFAULT_REPO, tag: str = DEFAULT_TAG,
                      asset: str = ASSET) -> str:
    return f"https://github.com/{repo}/releases/download/{tag}/{asset}"


def install_zip(data: bytes, dest: str) -> dict:
    """Unpack a retrained-models zip into ``dest`` (replacing what is there).

    Accepts archives rooted at the files or at a single top-level directory.
    Returns the bundle's ``meta.json``.
    """
    tmp = dest.rstrip("\\/") + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        if not any(n.split("/")[-1] == "meta.json" for n in names):
            shutil.rmtree(tmp, ignore_errors=True)
            raise ValueError("archive has no meta.json — not a retrained-models bundle")
        z.extractall(tmp)
    entries = os.listdir(tmp)
    src = tmp if "meta.json" in entries else os.path.join(tmp, entries[0])
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    shutil.move(src, dest)
    shutil.rmtree(tmp, ignore_errors=True)
    with open(os.path.join(dest, "meta.json"), encoding="utf-8") as fh:
        return json.load(fh)


def fetch_retrained(repo: str = DEFAULT_REPO, tag: str = DEFAULT_TAG,
                    dest: str | None = None, timeout: int = 180) -> dict:
    """Download and install the latest published retrained models. Raises on
    any failure (callers that want best-effort behaviour catch it)."""
    dest = dest or os.path.join(config.MODELS_DIR, "retrained")
    url = release_asset_url(repo, tag)
    req = urllib.request.Request(url, headers={"User-Agent": "fpl-engine/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    meta = install_zip(data, dest)
    meta["source"] = url
    return meta


def local_meta(dest: str | None = None) -> dict | None:
    """meta.json of the installed retrained models, or None."""
    dest = dest or os.path.join(config.MODELS_DIR, "retrained")
    p = os.path.join(dest, "meta.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)
