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
    with db.session(args.db) as conn:
        df = build(conn, args.gw, season=args.season, store=not args.no_store)
    print(f"Built {len(df)} samples for {args.season or config.CURRENT_SEASON} "
          f"GW{args.gw}")
    if args.out:
        df.to_csv(args.out, index=False)
        print(f"Wrote {args.out}")


def cmd_predict(args):
    from .pipeline import predict_gw
    with db.session(args.db) as conn:
        preds = predict_gw(conn, args.gw, season=args.season)
    _print_df(preds, args.top)
    if args.out:
        preds.to_csv(args.out, index=False)
        print(f"Wrote {args.out}")


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
    sp.add_argument("--cache", action="store_true", help="use cached HTTP responses")
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

    sp = sub.add_parser("predict", help="end-to-end predictions for a gw")
    sp.add_argument("--gw", type=int, required=True)
    sp.add_argument("--top", type=int, default=40, help="rows to print")
    sp.add_argument("--out", help="write predictions CSV")
    sp.set_defaults(func=cmd_predict)

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
