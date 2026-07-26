"""Tests for the optional retraining + blending module."""
import numpy as np
import pytest

from fpl_engine import train


def test_detect_device_returns_valid_string():
    assert train.detect_device() in ("cpu", "cuda") or ":" in train.detect_device()


def test_stratified_rmse_bins():
    y_true = np.array([0, 1, 3, 6])
    y_pred = np.array([0, 1, 3, 6])
    m = train._stratified_rmse(y_true, y_pred)
    assert m["rmse_all"] == 0.0
    assert set(m) >= {"rmse_Zeros", "rmse_Blanks", "rmse_Tickers", "rmse_Haulers"}


def test_season_blend_weight_grows_then_caps(conn):
    # No current-season data -> weight 0 (trust the pretrained models).
    assert train.season_blend_weight(conn, "2099-00") == 0.0
    # Seed some current-season 'fpl' gameweeks and check it rises and caps.
    from fpl_engine import db
    rows = [{"season": "2099-00", "gw": g, "source": "fpl", "player_id": 1,
             "fixture_id": g, "total_points": 5} for g in range(1, 11)]
    db.upsert(conn, "player_gw", rows)
    conn.commit()
    w = train.season_blend_weight(conn, "2099-00", cap=0.5, ramp=20)
    assert 0 < w < 0.5           # 10/20 * 0.5 = 0.25
    w_full = train.season_blend_weight(conn, "2099-00", cap=0.5, ramp=5)
    assert w_full == 0.5         # capped


def test_resolve_blend_off_by_default(conn):
    from fpl_engine.pipeline import resolve_blend
    assert resolve_blend(conn, "2099-00", None) == (None, 0.0)
    assert resolve_blend(conn, "2099-00", 0) == (None, 0.0)


class _StubModel:
    def __init__(self, value):
        self.value = value

    def predict(self, X):
        return np.full(X.shape[0], self.value, dtype=float)


@pytest.mark.skipif(
    not __import__("os").path.exists(
        __import__("os").path.join(
            __import__("fpl_engine").config.MODELS_DIR, "xscaler.save")),
    reason="OpenFPL model artefacts not present")
def test_blend_zero_matches_openfpl_and_one_uses_retrained():
    import os
    import pandas as pd
    from fpl_engine import config, predict
    samples = pd.read_csv(os.path.join(config.DATA_DIR, "samples.csv"))
    bundle = predict.load_models()
    base = predict.predict(samples, bundle=bundle)
    stub = {p: _StubModel(99.0) for p in ("GK", "DEF", "MID", "FWD")}

    b0 = predict.predict(samples, bundle=bundle, retrained=stub, blend=0.0)
    assert np.allclose(base["prediction"].to_numpy(dtype=float),
                       b0["prediction"].to_numpy(dtype=float))

    b1 = predict.predict(samples, bundle=bundle, retrained=stub, blend=1.0)
    # Every non-AM row should now equal the stub's constant.
    non_am = b1[b1["position"] != "AM"]
    assert np.allclose(non_am["prediction"].to_numpy(dtype=float), 99.0)
