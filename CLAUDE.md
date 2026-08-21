# CLAUDE.md — fpl_engine data pipeline

Guidance for working on the automatic data pipeline that feeds OpenFPL.

## What this is

`fpl_engine/` is a free, automatic data pipeline that pulls Fantasy Premier
League data into a **local SQLite database** and builds the point-in-time
feature samples the pre-trained OpenFPL models consume — so predictions run
end-to-end with no hand-built `samples.csv`.

Store: one SQLite file (`data/fpl.sqlite`, override with `$FPL_DB_PATH`). No
server, no cloud, no API keys.

## Architecture (layers)

```
Free sources ─▶ ingest ─▶ SQLite ─▶ features (point-in-time) ─▶ OpenFPL models ─▶ predictions
```

| Module | Responsibility |
|---|---|
| `fpl_engine/db.py` | SQLite schema + connection/upsert helpers (the only store) |
| `fpl_engine/http.py` | cached, retrying, polite HTTP (requests or urllib) |
| `fpl_engine/ingest/fpl_api.py` | official FPL API (free): bootstrap, fixtures, per-player history |
| `fpl_engine/ingest/vaastav.py` | free historical backfill (cross-season form for early GWs) |
| `fpl_engine/ingest/understat.py` | best-effort Understat advanced stats (degrades gracefully) |
| `fpl_engine/resolve.py` | FPL↔Understat entity resolution (override table, fails loud) |
| `fpl_engine/scoring.py` | canonical FPL points calculator (YAML-driven) |
| `fpl_engine/features.py` | point-in-time 228-feature builder (exact OpenFPL columns) |
| `fpl_engine/predict.py` | OpenFPL ensemble inference (refactor of `play.ipynb`), optional blend |
| `fpl_engine/train.py` | optional GPU retrain of per-position models + blend weight |
| `fpl_engine/manager.py` | fetch an FPL entry (squad id): current squad, bank, FTs |
| `fpl_engine/optimise/project.py` | per-player projections across the horizon |
| `fpl_engine/optimise/milp.py` | multi-period squad/transfer/captain optimiser (PuLP+CBC) |
| `fpl_engine/pipeline.py` / `__main__.py` | orchestration + CLI |

## Non-negotiable engineering principles

1. **Point-in-time discipline.** Every feature uses only matches with
   `kickoff_utc < as_of` (the target GW's first kickoff). The builder filters
   physically; never relax this. This is the highest-risk failure mode.
2. **The scoring engine is the single source of truth.** All FPL points come
   from `scoring.points_from_events`, driven by `config/scoring_rules_*.yaml`.
3. **Forward-in-time validation only.** No random splits. Reconcile and backtest
   on point-in-time data.
4. **Data contracts / idempotency.** Ingestors `INSERT OR REPLACE` on stable
   keys; re-running never duplicates. `player_gw` is keyed per *fixture* so
   double-gameweeks are not silently collapsed.
5. **Fail loud on entity-resolution misses**; never silently drop players.
6. **No hardcoded scoring constants** outside the YAML rules file.

Current scoring rules: `config/scoring_rules_2026_27.yaml` (version `2026-27`).

## Adapting to new results

Two independent mechanisms:

1. **Form (inputs) update every week for free.** Re-running `pull` after a
   gameweek writes the new matches into `player_gw`/`team_match`; the next
   `build`/`predict` recomputes the trailing-window features from them. The
   frozen OpenFPL models then see fresh form. This is the primary adaptation and
   needs no retraining.
2. **Weights (optional) via `train.py`.** `python -m fpl_engine train` refits
   per-position XGBoost regressors on the point-in-time feature store (GPU auto-
   detected via `device`; override with `$FPL_DEVICE`), validates forward-in-time
   (holds out the latest season, reports stratified RMSE), and saves to
   `models/retrained/`. Inference blends them with OpenFPL:
   `predict/optimise --blend auto` weights the fresh model up as the new season
   accrues data (`season_blend_weight`); `--blend 0` (default) is pure OpenFPL.
   The retrained models reuse OpenFPL's scaler + per-position feature subset, so
   the blend is in one consistent space. Training MUST stay forward-in-time (no
   random splits, no look-ahead) — the frame is built with the same `as_of`
   builder used for prediction.

## Optimiser

`optimise/milp.py` is a multi-period mixed-integer program (PuLP + bundled CBC,
free) over a rolling horizon. It jointly chooses squad, starting XI, captain and
transfers per gameweek to maximise **discounted expected points net of the -4
hit cost**. Free transfers accrue (+1/gw, bankable to 5) and are modelled
explicitly, so the model decides whether a hit is worth it (a transfer is taken
only when its marginal XI gain over the displaced/benched player beats 4 points).
Constraints enforced as hard: £100m budget with the bank recursion, 2/5/5/3
squad, ≤3 per club, legal XI formation. Given an entry (squad) id it suggests
transfers from the current team; with no team yet (pre-season) it builds a fresh
squad from budget (initial selection is free). Default entry: `883566`.
Projections use current form applied to each horizon gameweek's fixture.

## Must stay green

* `scoring.points_without_defcon` reconciles **100%** of 2024-25 player-matches
  against actual FPL totals (the Phase-1 gate). Re-run before touching scoring.
* `features.build_samples` reproduces the FPL-sourced columns of
  `data/samples.csv` for windows 1/3/5/10 exactly, and window-38 within
  long-horizon tolerance (depends on how many backfill seasons are loaded).
* `predict.predict(samples.csv)` reproduces `data/predictions.csv` exactly.

Run: `python -m pytest tests/ -q`

## Known approximations (documented, not bugs)

* **`player relevant fpl points`** (5 columns): OpenFPL's exact definition is
  not reconstructable from this repo's artefacts, so a documented best-effort
  (`total_points - appearance_points`) is used. All other FPL columns match.
* **Understat** is ingested from its JSON endpoints (`getLeagueData/{league}/{year}`
  for every club's per-match xG/xGA/deep/PPDA in one call, `getPlayerData/{id}`
  for a player's per-match log across all seasons, `main/getPlayersStats/` for
  ids/names used in resolution; all need `X-Requested-With: XMLHttpRequest`).
  `pull --understat` (the web Data button has it on) covers the current and
  previous season; current-season players are fetched live, the rest cached.
  Understat features are NaN when it is unreachable; the models tolerate this
  via `np.nan_to_num`. To limit the damage, FPL's own (Opta) expected stats —
  stored per match in `player_gw.xg/xa/xgi/xgc` and `team_match.xg/xga` (team
  xG = summed player xG, xGA = opponent's) — stand in for the Understat
  metrics they map onto (`player xg/xa`, `team/opponent xg/xga`) whenever the
  Understat history is empty. Understat data takes precedence when present.
  Shots, key passes, xGChain/xGBuildup, deep and PPDA have no FPL equivalent
  and stay NaN.
* **Expected minutes** (`fpl_engine/minutes.py`) scale EP relative to the
  trailing-minutes baseline the features encode; across a season break a fit
  player is never down-weighted on stale absences (pre-season factor is in
  `[avail, 1.15·avail]`).
* **Clubs with no top-flight match log** (promoted) borrow a pooled prior from
  the previous season's relegated clubs for team/opponent features instead of
  NaN→0; clubs are matched across seasons by FPL's stable `team.code`.
* **League-rank / status-rank** columns are AM-only in OpenFPL and left NaN for
  player rows (matching the reference samples).

## Web app (FPL Review-style planner)

`app/` (FastAPI backend) + `web/` (React/Vite frontend) serve a local planner
UI on **http://127.0.0.1:8410** with four tabs: Planner (pitch + drafts),
Projections (per-GW model output table), Fixtures (FDR heatmap) and Solver
(chip-aware optimisation via `fpl_engine/optimise/chips.py` — a superset of
`milp.py` adding WC/FH/BB/TC chips, target/avoid/ban constraints and N
alternative plans; `milp.py` itself stays untouched).

```
python -m app                      # serve the built site (needs app/static)
cd web && npm install && npm run build   # rebuild frontend -> app/static
cd web && npm run dev              # frontend dev server (proxies /api to 8410)
```

Projections are cached per (season, gw) in `data/web_cache/projections.json`
(invalidated by a data pull); drafts persist in `data/web_cache/drafts.json`.
A solve builds any missing projection gameweeks first, so the first solve of a
session is slow (model inference per GW) and later ones are fast.

## Commands

```
python -m fpl_engine init-db
python -m fpl_engine pull            # FPL live + vaastav backfill -> SQLite (free)
python -m fpl_engine predict --gw 1        # end-to-end predictions
python -m fpl_engine run --gw 1            # pull + build + predict
python -m fpl_engine optimise --entry 883566 --horizon 5   # transfers / squad
python -m fpl_engine train                 # optional: retrain models (GPU-aware)
python -m fpl_engine predict --gw 1 --blend auto   # blend retrained + OpenFPL
python -m pytest tests/ -q
```
