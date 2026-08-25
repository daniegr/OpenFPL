"""Command-line interface for the fpl_engine data pipeline.

Examples
--------
    python -m fpl_engine init-db
    python -m fpl_engine pull                 # FPL live + vaastav backfill -> SQLite
    python -m fpl_engine backfill             # historical seasons only
    python -m fpl_engine build --gw 1         # build point-in-time samples
    python -m fpl_engine predict --gw 1       # end-to-end OpenFPL predictions
    python -m fpl_engine run --gw 1           # pull + build + predict in one go
"""
from __future__ import annotations

import argparse
import sys
import warnings

warnings.filterwarnings("ignore")  # silence sklearn version-mismatch warnings

from . import config, db
from .ingest import vaastav


def _print_df(df, n=40):
    import pandas as pd
    with pd.option_context("display.max_rows", n, "display.width", 200):
        print(df.head(n).to_string(index=False))


def cmd_init_db(args):
    db.init_db(args.db)
    print(f"Initialised SQLite database at {args.db or config.DB_PATH}")


def cmd_pull(args):
    from .pipeline import pull
    with db.session(args.db) as conn:
        db.init_db(args.db)
        summary = pull(conn, season=args.season, use_cache=args.cache,
                       history=not args.no_history, backfill=not args.no_backfill,
                       with_understat=args.understat)
    print("Pull complete:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


def cmd_backfill(args):
    with db.session(args.db) as conn:
        db.init_db(args.db)
        out = vaastav.ingest_seasons(conn, args.seasons or None, use_cache=args.cache)
    for row in out:
        print(" ", row)


def cmd_build(args):
    from .pipeline import build
    db.init_db(args.db)
    with db.session(args.db) as conn:
        df = build(conn, args.gw, season=args.season, store=not args.no_store)
    print(f"Built {len(df)} samples for {args.season or config.CURRENT_SEASON} "
          f"GW{args.gw}")
    if args.out:
        df.to_csv(args.out, index=False)
        print(f"Wrote {args.out}")


def cmd_train(args):
    from . import train
    db.init_db(args.db)
    with db.session(args.db) as conn:
        meta = train.train(conn, seasons=args.seasons or None,
                           valid_season=args.valid_season, gw_step=args.gw_step,
                           device=args.device)
    print("Retraining complete. Forward-in-time validation "
          f"(held-out {meta['valid_season']}), device={meta['device']}:")
    for pos, m in meta["metrics"].items():
        print(f"  {pos}: {m}")
    print(f"\nSaved to {train.RETRAINED_DIR}. Use it via --blend, e.g. "
          f"`predict --gw 1 --blend auto`.")


def cmd_predict(args):
    from .pipeline import next_gw, predict_gw
    db.init_db(args.db)
    with db.session(args.db) as conn:
        gw = args.gw if args.gw is not None else next_gw(
            conn, args.season or config.CURRENT_SEASON)
        preds = predict_gw(conn, gw, season=args.season, blend=args.blend)
    _print_df(preds, args.top)
    if args.out:
        preds.to_csv(args.out, index=False)
        print(f"Wrote {args.out}")


def cmd_optimise(args):
    from .pipeline import optimise_squad
    from .manager import DEFAULT_ENTRY
    entry = args.entry if args.entry is not None else DEFAULT_ENTRY
    db.init_db(args.db)
    with db.session(args.db) as conn:
        result = optimise_squad(
            conn, entry_id=entry, season=args.season, horizon=args.horizon,
            budget=args.budget, decay=args.decay,
            max_transfers_per_gw=args.max_transfers, time_limit=args.time_limit,
            use_cache=args.cache, blend=args.blend)
    plan = result["plan"]
    print(f"Entry {result['entry_id']} | mode: {result['mode']} | "
          f"horizon GW{result['gws'][0]}–{result['gws'][-1]}")
    print(f"State: {result['state']}")
    print("=" * 64)
    print(plan.summary())
    print("=" * 64)
    first = plan.per_gw[0]
    print(f"\nRecommended squad for GW{first['gw']} "
          f"(captain: {first['captain']}):")
    for pos in ("GK", "DEF", "MID", "FWD"):
        line = [f"{n}{'*' if n in first['xi'] else ''} ({e})"
                for n, pp, e in first["squad"] if pp == pos]
        print(f"  {pos}: " + ", ".join(line))
    print("  (* = starting XI)")


def cmd_backtest(args):
    from . import backtest
    db.init_db(args.db)
    with db.session(args.db) as conn:
        report = backtest.run(conn, args.backtest_season, gws=args.gws or None,
                              openfpl_every=args.openfpl_every,
                              retrain_minutes=args.retrain_minutes,
                              with_openfpl=not args.no_openfpl)
    print(f"\nBacktest {report['season']} "
          f"(minutes-model holdout acc {report['minutes_holdout_accuracy']}):")
    cols = ["spearman", "p_at_20", "captain", "captain_best", "rmse", "gws"]
    print(f"{'model':<10}" + "".join(f"{c:>14}" for c in cols))
    for name, m in sorted(report["summary"].items()):
        print(f"{name:<10}" + "".join(f"{m.get(c, float('nan')):>14}" for c in cols))
    if report.get("blend"):
        b = report["blend"]
        print(f"\nBlend fit: w(xpts)={b['weight']}  "
              f"eval spearman openfpl={b['eval_spearman']['openfpl']:.4f} "
              f"xpts={b['eval_spearman']['xpts']:.4f} "
              f"blend={b['eval_spearman']['blend']:.4f}")
        print("Saved to models/xpts/blend.json (used automatically by predict/web).")


def cmd_run(args):
    from .pipeline import pull, predict_gw
    with db.session(args.db) as conn:
        db.init_db(args.db)
        pull(conn, season=args.season, use_cache=args.cache,
             history=not args.no_history, backfill=not args.no_backfill,
             with_understat=args.understat)
        preds = predict_gw(conn, args.gw, season=args.season)
    _print_df(preds, args.top)
    if args.out:
        preds.to_csv(args.out, index=False)
        print(f"Wrote {args.out}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="fpl_engine",
                                description="Free, automatic FPL data pipeline -> SQLite -> OpenFPL")
    p.add_argument("--db", help="SQLite path (default data/fpl.sqlite or $FPL_DB_PATH)")
    p.add_argument("--season", help=f"season (default {config.CURRENT_SEASON})")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init-db", help="create the SQLite schema")
    sp.set_defaults(func=cmd_init_db)

    sp = sub.add_parser("pull", help="pull FPL live + historical into SQLite")
    sp.add_argument("--cache", action="store_true",
                    help="cache the static historical backfill (live data stays fresh)")
    sp.add_argument("--no-history", action="store_true", help="skip per-player history")
    sp.add_argument("--no-backfill", action="store_true", help="skip vaastav backfill")
    sp.add_argument("--understat", action="store_true", help="also pull Understat if available")
    sp.set_defaults(func=cmd_pull)

    sp = sub.add_parser("backfill", help="historical seasons only (vaastav)")
    sp.add_argument("--cache", action="store_true")
    sp.add_argument("--seasons", nargs="*", help="e.g. 2023-24 2024-25")
    sp.set_defaults(func=cmd_backfill)

    sp = sub.add_parser("build", help="build point-in-time samples for a gw")
    sp.add_argument("--gw", type=int, required=True)
    sp.add_argument("--no-store", action="store_true")
    sp.add_argument("--out", help="write samples CSV")
    sp.set_defaults(func=cmd_build)

    sp = sub.add_parser("train", help="retrain per-position models on the feature store (GPU-aware)")
    sp.add_argument("--seasons", nargs="*", help="seasons to train on (default backfill set)")
    sp.add_argument("--valid-season", help="held-out season for forward validation")
    sp.add_argument("--gw-step", type=int, default=1,
                    help="subsample gameweeks for a faster run (e.g. 2)")
    sp.add_argument("--device", help="cuda | cpu (default: auto-detect)")
    sp.set_defaults(func=cmd_train)

    sp = sub.add_parser("predict", help="end-to-end predictions for a gw")
    sp.add_argument("--gw", type=int, default=None,
                    help="gameweek (default: next scheduled)")
    sp.add_argument("--top", type=int, default=40, help="rows to print")
    sp.add_argument("--out", help="write predictions CSV")
    sp.add_argument("--blend", default=None,
                    help="blend retrained model: 'auto', or a weight 0..1 (needs `train` first)")
    sp.set_defaults(func=cmd_predict)

    sp = sub.add_parser("optimise", help="suggest transfers / build a squad for an FPL entry")
    sp.add_argument("--entry", type=int, default=None,
                    help="FPL entry (squad) id; defaults to 883566")
    sp.add_argument("--horizon", type=int, default=5, help="gameweeks to plan over")
    sp.add_argument("--budget", type=float, default=100.0)
    sp.add_argument("--decay", type=float, default=0.85)
    sp.add_argument("--max-transfers", type=int, default=3,
                    help="cap transfers per gameweek (bounds the search)")
    sp.add_argument("--time-limit", type=int, default=40, help="solver seconds")
    sp.add_argument("--cache", action="store_true")
    sp.add_argument("--blend", default=None,
                    help="blend retrained model: 'auto', or a weight 0..1 (needs `train` first)")
    sp.set_defaults(func=cmd_optimise)

    sp = sub.add_parser("backtest", help="replay past gameweeks: xpts vs OpenFPL vs baselines")
    sp.add_argument("--backtest-season", default="2025-26",
                    help="season to replay (default 2025-26)")
    sp.add_argument("--gws", nargs="*", type=int, help="specific gameweeks only")
    sp.add_argument("--openfpl-every", type=int, default=4,
                    help="run the (slow) OpenFPL ensemble every Nth gw")
    sp.add_argument("--no-openfpl", action="store_true",
                    help="skip the OpenFPL comparison entirely")
    sp.add_argument("--retrain-minutes", action="store_true",
                    help="force retraining the minutes classifier")
    sp.set_defaults(func=cmd_backtest)

    sp = sub.add_parser("run", help="pull + build + predict")
    sp.add_argument("--gw", type=int, required=True)
    sp.add_argument("--cache", action="store_true")
    sp.add_argument("--no-history", action="store_true")
    sp.add_argument("--no-backfill", action="store_true")
    sp.add_argument("--understat", action="store_true")
    sp.add_argument("--top", type=int, default=40)
    sp.add_argument("--out", help="write predictions CSV")
    sp.set_defaults(func=cmd_run)

    args = p.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
