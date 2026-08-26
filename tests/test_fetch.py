"""Retrained-model bundle installer (release asset -> models/retrained)."""
import io
import json
import os
import tempfile
import zipfile

import pytest

from fpl_engine import fetch


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_install_zip_flat_and_nested(tmp_path):
    meta = json.dumps({"seasons": ["2024-25"], "valid_season": "2024-25", "trained_at": "t"})
    dest = str(tmp_path / "retrained")
    # flat archive
    m = fetch.install_zip(_zip({"meta.json": meta, "cv1_GK.joblib": b"x"}), dest)
    assert m["valid_season"] == "2024-25"
    assert os.path.exists(os.path.join(dest, "cv1_GK.joblib"))
    # nested under a top-level dir (how `zip -r retrained` packs it) replaces it
    m2 = fetch.install_zip(_zip({"retrained/meta.json": meta.replace('"trained_at": "t"', '"trained_at": "t2"'),
                                 "retrained/cv1_DEF.joblib": b"y"}), dest)
    assert m2["trained_at"] == "t2"
    assert os.path.exists(os.path.join(dest, "cv1_DEF.joblib"))
    assert not os.path.exists(os.path.join(dest, "cv1_GK.joblib"))   # replaced, not merged
    assert fetch.local_meta(dest)["trained_at"] == "t2"


def test_install_zip_rejects_foreign_archives(tmp_path):
    with pytest.raises(ValueError):
        fetch.install_zip(_zip({"readme.txt": b"nope"}), str(tmp_path / "r"))
    assert fetch.local_meta(str(tmp_path / "r")) is None


def test_release_url():
    assert fetch.release_asset_url("o/r", "models-latest") == \
        "https://github.com/o/r/releases/download/models-latest/retrained.zip"
