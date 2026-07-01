# -*- coding: utf-8 -*-
"""
AQM_Momentum_Live.py
--------------------
CLI entry point for the multi-asset momentum live trading strategy.
Mirrors AQM_MR_Live.py but wires MomentumStrategy / MomentumPortfolio
/ MomentumTrading instead of the pairs-trading components.
"""

import datetime
import argparse

from MomentumStrategy import MomentumStrategy, DEFAULT_UNIVERSE
from MomentumPortfolio import MomentumPortfolio
from MomentumTrading import MomentumTrading
from Datos import BinanceData
from ejecucion import traderPerp
from account_manager import activate_account, validate_all, available_accounts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Momentum Strategy Live — daily rebalance across a crypto universe."
    )

    # Universe
    parser.add_argument(
        "--tokens", type=str, default=None,
        help="Comma-separated token list. Defaults to 26-asset universe.",
    )

    # Capital & sizing
    parser.add_argument("--capital", type=float, default=100000,
                        help="Total portfolio capital (default 100000).")

    # Strategy params
    parser.add_argument("--variant", type=str, default="turtle",
                        choices=["turtle", "tanh"],
                        help="Weight variant: 'turtle' (inv-ATR) or 'tanh' (equal-weight).")
    parser.add_argument("--short-window", type=int, default=5,
                        help="Momentum short lookback in days (default 5).")
    parser.add_argument("--long-window", type=int, default=30,
                        help="Momentum long lookback in days (default 30).")
    parser.add_argument("--max-weight", type=float, default=0.10,
                        help="Per-asset weight cap (default 0.10).")
    parser.add_argument("--stop-loss", type=float, default=-0.15,
                        help="Yesterday return filter threshold (default -0.15).")

    # Rebalance
    parser.add_argument("--rebalance-utc", type=int, default=0,
                        help="UTC hour for daily rebalance (default 0).")
    parser.add_argument("--min-rebalance", type=float, default=0.005,
                        help="Min weight change to trigger order (default 0.005).")

    # Execution
    parser.add_argument("--batch-n", type=int, default=3,
                        help="Limit order slices per order (default 3).")
    parser.add_argument("--batch-interval", type=int, default=600,
                        help="Seconds between slices (default 600).")
    parser.add_argument("--limit-offset-bps", type=int, default=2,
                        help="Passive limit offset in bps (default 2).")

    # Exchange & account
    parser.add_argument("--exchange", type=str, default="binance",
                        choices=["binance", "bitget"],
                        help="Execution exchange (default binance).")
    parser.add_argument("--account", type=str, default="binance_momentum",
                        choices=available_accounts(),
                        help="Named account preset (default: binance_momentum). "
                             "Overrides --exchange/--testnet/--paper.")
    parser.add_argument("--testnet", action="store_true", default=False)
    parser.add_argument("--paper", action="store_true", default=False)

    # State & logging
    parser.add_argument("--state-file", type=str, default=None,
                        help="JSON state filename (default live_state_momentum.json).")
    parser.add_argument("--gcp", action="store_true", default=False)
    parser.add_argument("--gcp-project", type=str, default=None)
    parser.add_argument("--gcp-dataset", type=str, default="trading")
    parser.add_argument("--interval", type=str, default="1d",
                        help="Candle interval for WebSocket stream (default 1d).")

    args = parser.parse_args()

    # ── Resolve account ────────────────────────────────
    validate_all()
    cfg = activate_account(args.account)
    args.exchange = cfg.exchange
    args.testnet = cfg.testnet
    args.paper = cfg.paper
    print(f"📌 Account: {cfg.label} "
          f"(exchange={cfg.exchange}, testnet={cfg.testnet}, paper={cfg.paper})")

    # ── Resolve universe ───────────────────────────────
    if args.tokens:
        lista_nemos = [t.strip().upper() for t in args.tokens.split(",")]
    else:
        lista_nemos = list(DEFAULT_UNIVERSE)

    print(f"📌 Universe: {len(lista_nemos)} tokens")

    # ── Resolve exchange ───────────────────────────────
    if args.exchange == "bitget":
        lista_bolsas = ['BITGETFTS']
        mode = "PAPER 📄" if args.paper else "LIVE 🔴"
        print(f"📌 Exchange: BITGET — {mode}")
    else:
        lista_bolsas = ['BINANCEFTS']
        if args.paper:
            print("⚠️  --paper only supported with --exchange=bitget. Ignored.")
        mode = "TESTNET 🧪" if args.testnet else "LIVE 🔴"
        print(f"📌 Exchange: BINANCE — {mode}")

    lista_libros = ['PERP'] * len(lista_nemos)

    # ── BQ logger ──────────────────────────────────────
    bq_logger = None
    if args.gcp:
        import os as _os
        from bq_logger import BQLogger
        gcp_project = args.gcp_project or _os.environ.get(
            "GCP_PROJECT_ID", "aqm-trading-prod")
        bq_logger = BQLogger(
            project_id=gcp_project,
            dataset=args.gcp_dataset,
            fallback_dir="outputs",
        )
        print(f"📊 BigQuery: {gcp_project}.{args.gcp_dataset}")

    account_label = args.account if args.account else args.exchange

    print(f"\n🚀 Launching Momentum Strategy ({args.variant.upper()}) "
          f"with ${args.capital:,.0f} capital")
    print(f"   Short={args.short_window}d  Long={args.long_window}d  "
          f"MaxWeight={args.max_weight}  StopLoss={args.stop_loss}")
    print(f"   Rebalance at {args.rebalance_utc:02d}:00 UTC  "
          f"MinDelta={args.min_rebalance}")
    print(f"   Batch={args.batch_n}×{args.batch_interval}s  "
          f"Offset={args.limit_offset_bps}bps\n")

    trader = MomentumTrading(
        lista_nemos,
        lista_bolsas=lista_bolsas,
        lista_libros=lista_libros,
        capital_inicial=args.capital,
        heartbeat=86400,
        fecha_inicial=datetime.datetime.now() - datetime.timedelta(days=1),
        admin_datos=BinanceData,
        admin_ejecucion=traderPerp,
        portafolio=MomentumPortfolio,
        estrategia=MomentumStrategy,
        interval=args.interval,
        testnet=args.testnet,
        state_file=args.state_file,
        batch_n=args.batch_n,
        batch_interval_s=args.batch_interval,
        limit_offset_bps=args.limit_offset_bps,
        paper_trading=args.paper,
        bq_logger=bq_logger,
        account=account_label,
        rebalance_hour_utc=args.rebalance_utc,
        min_rebalance_threshold=args.min_rebalance,
        variant=args.variant,
        short_window=args.short_window,
        long_window=args.long_window,
        max_weight=args.max_weight,
        stop_loss_pct=args.stop_loss,
    )

    trader._corre_aplicacion_trading()
