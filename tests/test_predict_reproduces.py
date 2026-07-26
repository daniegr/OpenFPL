"""The refactored predictor must reproduce the committed reference predictions.

This is the deterministic anchor: given data/samples.csv, the module output
must equal data/predictions.csv exactly (up to float noise).
"""
import os
import warnings

import pandas as pd
import pytest

from fpl_engine import config, predict

warnings.filterwarnings("ignore")

KEYS = ["season", "gw", "position", "player", "team", "opponent", "home"]


@pytest.mark.skipif(
    not os.path.exists(os.path.join(config.MODELS_DIR, "xscaler.save")),
    reason="OpenFPL model artefacts not present",
)
def test_predict_reproduces_reference():
    samples = pd.read_csv(os.path.join(config.DATA_DIR, "samples.csv"))
    ref = pd.read_csv(os.path.join(config.DATA_DIR, "predictions.csv"))
    got = predict.predict(samples)
    merged = got.merge(ref, on=KEYS, suffixes=("_got", "_ref"))
    assert len(merged) == len(ref)
    assert (merged["prediction_got"] - merged["prediction_ref"]).abs().max() < 1e-4
