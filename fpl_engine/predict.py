"""Run the pre-trained OpenFPL ensemble on a samples dataframe.

This is a module-level refactor of the logic in ``play.ipynb`` so it can be
called from the CLI and the pipeline. It is deterministic and, given
``data/samples.csv``, reproduces ``data/predictions.csv`` exactly.
"""
from __future__ import annotations

import os

import joblib
import numpy as np
import pandas as pd

from . import config

_NUM_CVS = 5
_POSITIONS = ["GK", "DEF", "MID", "FWD", "AM"]
_METADATA = ["season", "gw", "position", "player", "team", "opponent", "home"]


def load_models(models_dir: str | None = None):
    """Load the OpenFPL ensembles, scalers and per-position feature lists."""
    models_dir = models_dir or config.MODELS_DIR
    models = {cv: {pos: [] for pos in _POSITIONS} for cv in range(1, _NUM_CVS + 1)}
    for cv in models:
        for pos in models[cv]:
            search_dir = os.path.join(models_dir, f"cv{cv}_{pos}")
            with open(os.path.join(search_dir, "search.txt")) as fh:
                log = fh.read()
            top = [x.split(" ")[0] for x in
                   log.split("The population is:")[-1].split("Candidate ")[1:]]
            for cand in top:
                cand_dir = os.path.join(search_dir, cand)
                fname = os.listdir(cand_dir)[0]
                models[cv][pos].append(joblib.load(os.path.join(cand_dir, fname)))
    xscaler = joblib.load(os.path.join(models_dir, "xscaler.save"))
    yscaler = joblib.load(os.path.join(models_dir, "yscaler.save"))
    features = joblib.load(os.path.join(models_dir, "features.save"))
    return models, xscaler, yscaler, features


def predict(samples_df: pd.DataFrame, bundle=None) -> pd.DataFrame:
    """Return a dataframe of metadata + ensemble ``prediction`` per player."""
    models, xscaler, yscaler, features = bundle or load_models()
    xfeatures = list(xscaler.feature_names_in_)

    out = pd.DataFrame(columns=_METADATA + ["prediction"])
    for pos in _POSITIONS:
        pos_df = samples_df[samples_df["position"] == pos]
        if pos_df.empty:
            continue
        data = pos_df[xfeatures].to_numpy()
        scaled = np.nan_to_num(
            xscaler.transform(np.nan_to_num(data).astype("float32"))
        ).astype("float32")
        idx = [xfeatures.index(f) for f in features[pos]]
        scaled = scaled[:, idx]

        preds = []
        for cv in models:
            for model in models[cv][pos]:
                p = model.predict(scaled)
                p = yscaler.inverse_transform(p.reshape(-1, 1)).reshape(-1)
                preds.append(p)
        ensemble = np.median(preds, axis=0)
        block = pos_df[_METADATA].copy()
        block["prediction"] = ensemble
        out = pd.concat([out, block], ignore_index=True)
    return out
