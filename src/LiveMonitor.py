# -*- coding: utf-8 -*-
"""
LiveMonitor.py
--------------
Read-only observer that periodically snapshots the live trading
state and:
  1. Prints a compact dashboard to the terminal (CLI).
  2. Writes a JSON file that a Jupyter notebook can poll.

Design goals:
  - ZERO changes to Estrategia, Datos, PortAQMHFT, ejecucion.
  - Only reads public attributes / existing methods.
  - Called once per tick from trading.py's main loop.

Usage in trading.py:
    from LiveMonitor import LiveMonitor
    monitor = LiveMonitor(admin_datos, estrategia, portafolio,
                          admin_ejecucion, lista_nemos)
    # inside the while-loop:
    monitor.tick()          # respects its own refresh interval
"""

import json
import os
import time
import datetime
import numpy as np


class LiveMonitor:
    """
    Periodically snapshots bid/ask, positions, PnL and z-score
    from the live trading objects (read-only).
    """

    # ── configurable defaults ──────────────────────────
    REFRESH_INTERVAL = 60       # seconds between snapshots
    JSON_FILENAME = "live_state.json"

    def __init__(
        self,
        admin_datos,
        estrategia,
        portafolio,
        admin_ejecucion,
        lista_nemos,
        outputs_dir=None,
        refresh_interval=None,
        cli_print=True,
        json_filename=None,
        bq_logger=None,
        account=None,
    ):
        self.datos = admin_datos
        self.strat = estrategia
        self.port = portafolio
        self.ejec = admin_ejecucion
        self.nemos = list(lista_nemos)

        if refresh_interval is not None:
            self.REFRESH_INTERVAL = refresh_interval

        # Allow custom JSON filename (e.g. live_state_XRP_DOGE.json)
        if json_filename is not None:
            self.JSON_FILENAME = json_filename

        # Resolve output path (notebooks/ by default)
        if outputs_dir is None:
            src_dir = os.path.dirname(os.path.abspath(__file__))
            self.out_dir = os.path.join(src_dir, "..", "notebooks")
        else:
            self.out_dir = outputs_dir
        os.makedirs(self.out_dir, exist_ok=True)

        self._json_path = os.path.join(
            self.out_dir, self.JSON_FILENAME
        )
        self._last_tick = 0
        self._cli_print = cli_print
        self._snapshot_count = 0
        self.bq_logger = bq_logger
        self.account = account
        self._pair = f"{lista_nemos[0]}_{lista_nemos[1]}" if len(lista_nemos) >= 2 else str(lista_nemos)

        # ── Position cache to avoid hammering exchange REST API ──
        # get_position_info() is expensive (1 REST call per symbol).
        # Cache results and only refresh every _POS_CACHE_TTL seconds.
        self._POS_CACHE_TTL = 120  # seconds – refresh positions at most every 2 min
        self._pos_cache = {}       # {nemo: position_dict}
        self._pos_cache_ts = 0     # last refresh timestamp

    # ── public API ─────────────────────────────────────
    def tick(self):
        """Call from the trading loop. Snapshots only when
        REFRESH_INTERVAL has elapsed."""
        now = time.time()
        if now - self._last_tick < self.REFRESH_INTERVAL:
            return
        self._last_tick = now
        try:
            snap = self._build_snapshot()
            self._write_json(snap)
            if self._cli_print:
                self._print_cli(snap)
            self._snapshot_count += 1
            self._log_to_bq(snap)
        except Exception as e:
            print(f"⚠️  [LiveMonitor] error: {e}")

    # ── snapshot builder ───────────────────────────────
    def _build_snapshot(self):
        ts = datetime.datetime.utcnow().isoformat() + "Z"
        snap = {
            "timestamp": ts,
            "pair": self.nemos,
            "market_data": self._market_data(),
            "positions": self._positions(),
            "strategy": self._strategy_state(),
            "account": self._account(),
            "circuit_breaker": self._circuit_breaker_state(),
        }
        return snap

    def _circuit_breaker_state(self):
        """Read pair circuit breaker status from the execution handler."""
        try:
            pcb = getattr(self.ejec, '_pair_circuit_breaker', None)
            if pcb is None:
                return {"is_open": False}
            return {
                "is_open": pcb.get('is_open', False),
                "tripped_at": pcb.get('tripped_at', None),
                "filled_leg": pcb.get('filled_leg', None),
                "error": pcb.get('error', None),
            }
        except Exception:
            return {"is_open": False}

    def _log_to_bq(self, snap):
        """Write flattened snapshot to BigQuery if bq_logger is set."""
        if not self.bq_logger:
            return
        try:
            strat = snap.get("strategy", {})
            acct = snap.get("account", {})
            cb = snap.get("circuit_breaker", {})
            positions = snap.get("positions", {})

            n0, n1 = self.nemos[0], self.nemos[1]
            p0 = positions.get(n0, {})
            p1 = positions.get(n1, {})

            self.bq_logger.log("live_state", {
                "timestamp": snap["timestamp"],
                "account": self.account or "",
                "pair": self._pair,
                "z_score": strat.get("z_score"),
                "is_cointegrated": strat.get("is_cointegrated", False),
                "hedge_ratio": strat.get("hedge_ratio"),
                "position_direction": (
                    "LARGO" if strat.get("largo_spread") else
                    "CORTO" if strat.get("corto_spread") else "FLAT"
                ),
                "slow_layer_age_s": strat.get("slow_layer_age_s"),
                "asset1_qty": p0.get("quantity", 0),
                "asset2_qty": p1.get("quantity", 0),
                "asset1_mid": p0.get("mid", 0),
                "asset2_mid": p1.get("mid", 0),
                "asset1_upnl": p0.get("unrealized_pnl", 0),
                "asset2_upnl": p1.get("unrealized_pnl", 0),
                "circuit_breaker_open": cb.get("is_open", False),
                "eg_pvalue": strat.get("eg_pvalue"),
                "adf_pvalue": strat.get("adf_pvalue"),
                "spread_mean": strat.get("spread_mean"),
                "spread_std": strat.get("spread_std"),
            })

            self.bq_logger.log("pnl_snapshots", {
                "timestamp": snap["timestamp"],
                "account": self.account or "",
                "pair": self._pair,
                "cash": acct.get("cash", 0),
                "total_equity": acct.get("total", 0),
                "commission_total": acct.get("commission", 0),
                "unrealized_pnl": sum(
                    (positions.get(n, {}).get("unrealized_pnl") or 0)
                    for n in self.nemos
                ),
                "realized_pnl": acct.get("realized_pnl", 0),
            })

            if cb.get("is_open"):
                self.bq_logger.log("alerts", {
                    "timestamp": snap["timestamp"],
                    "account": self.account or "",
                    "pair": self._pair,
                    "alert_type": "CIRCUIT_BREAKER",
                    "severity": "CRITICAL",
                    "message": f"Circuit breaker open: {cb.get('error', 'unknown')}",
                })
        except Exception as e:
            print(f"⚠️  [LiveMonitor] BQ log error: {e}")

    # ── data collectors (all read-only) ────────────────
    def _market_data(self):
        """Bid/ask, mid, spread, last close per symbol."""
        rows = {}
        for nemo in self.nemos:
            sym = f"{nemo}USDT"
            bid, bid_qty = (None, None)
            ask, ask_qty = (None, None)
            mid = None
            spread_info = None

            # Try OrderBook (WebSocket L2)
            if hasattr(self.datos, 'get_best_bid'):
                bid, bid_qty = self.datos.get_best_bid(sym)
                ask, ask_qty = self.datos.get_best_ask(sym)
            if hasattr(self.datos, 'get_mid_price'):
                mid = self.datos.get_mid_price(sym)
            if hasattr(self.datos, 'get_spread'):
                spread_info = self.datos.get_spread(sym)

            # Fallback: last close from candle buffer
            last_close = None
            if hasattr(self.datos, 'get_valor_ultima_vela'):
                try:
                    last_close = self.datos.get_valor_ultima_vela(
                        nemo, "close"
                    )
                except Exception:
                    pass

            # If no L2, derive mid from last close
            if mid is None and last_close is not None:
                mid = float(last_close)

            rows[nemo] = {
                "bid": self._f(bid),
                "bid_qty": self._f(bid_qty),
                "ask": self._f(ask),
                "ask_qty": self._f(ask_qty),
                "mid": self._f(mid),
                "spread_bps": (
                    round(spread_info["percentage"] * 100, 2)
                    if spread_info else None
                ),
                "last_close": self._f(last_close),
            }
        return rows

    def _positions(self):
        """Current positions + exchange PnL per symbol.

        Uses a time-based cache (_POS_CACHE_TTL) so that the
        expensive get_position_info REST call is made at most
        once every _POS_CACHE_TTL seconds, regardless of how
        often tick() fires.
        """
        now = time.time()
        need_refresh = (now - self._pos_cache_ts) >= self._POS_CACHE_TTL

        if need_refresh:
            # Pick whichever exchange handler is available
            handler = None
            if hasattr(self.ejec, 'bitget_handler') and self.ejec.bitget_handler:
                handler = self.ejec.bitget_handler
            elif hasattr(self.ejec, 'binance_handler') and self.ejec.binance_handler:
                handler = self.ejec.binance_handler

            if handler:
                for nemo in self.nemos:
                    try:
                        infos = handler.get_position_info(
                            symbol=f"{nemo}USDT"
                        )
                        if infos:
                            for p in infos:
                                amt = float(
                                    p.get('positionAmt', 0)
                                )
                                if amt != 0:
                                    # Both handlers normalise to these keys
                                    upnl = float(
                                        p.get('unrealizedProfit',
                                              p.get('unRealizedProfit', 0))
                                    )
                                    self._pos_cache[nemo] = {
                                        "entry": float(
                                            p.get('entryPrice', 0)
                                        ),
                                        "upnl": upnl,
                                        "mark": float(
                                            p.get('markPrice', 0)
                                        ),
                                    }
                                    break
                            else:
                                self._pos_cache[nemo] = {}
                        else:
                            self._pos_cache[nemo] = {}
                    except Exception:
                        pass
            self._pos_cache_ts = now

        # Build rows from cache (always cheap)
        rows = {}
        for nemo in self.nemos:
            pos_qty = self.port.posiciones_actuales.get(nemo, 0)
            cached = self._pos_cache.get(nemo, {})

            rows[nemo] = {
                "qty": self._f(pos_qty),
                "entry_price": self._f(cached.get("entry")),
                "mark_price": self._f(cached.get("mark")),
                "unrealized_pnl": self._f(cached.get("upnl")),
            }
        return rows

    def _strategy_state(self):
        """Slow layer params, z-score, flags."""
        sp = self.strat.slow_params
        return {
            "largo_spread": self.strat.largoMdo,
            "corto_spread": self.strat.cortoMdo,
            "hedge_ratio": self._f(sp.get("hedge_ratio")),
            "spread_mean": self._f(sp.get("spread_mean")),
            "spread_std": self._f(sp.get("spread_std")),
            "is_cointegrated": sp.get("is_cointegrated", False),
            "adf_pvalue": self._f(sp.get("adf_pvalue")),
            "eg_pvalue": self._f(sp.get("eg_pvalue")),
            "slow_layer_age_s": (
                round(time.time() - sp["last_update"], 0)
                if sp["last_update"] else None
            ),
            "z_score": self._current_zscore(),
            "ordenes_pendientes": {
                k: v for k, v in
                self.strat.ordenes_pendientes.items()
            },
        }

    def _current_zscore(self):
        """Recompute z from latest 1-min closes + slow params."""
        sp = self.strat.slow_params
        hr = sp.get("hedge_ratio")
        mu = sp.get("spread_mean")
        sigma = sp.get("spread_std")
        if hr is None or mu is None or sigma is None:
            return None
        if sigma == 0:
            return None
        try:
            y = self.datos.get_valor_ultima_vela(
                self.nemos[0], "close"
            )
            x = self.datos.get_valor_ultima_vela(
                self.nemos[1], "close"
            )
            if y is None or x is None:
                return None
            y, x = float(y), float(x)
            if y <= 0 or x <= 0:
                return None
            spread = np.log(y) - hr * np.log(x)
            z = (spread - mu) / sigma
            return round(float(z), 4)
        except Exception:
            return None

    def _account(self):
        """Cash, commissions, total equity."""
        ca = self.port.cuenta_actual
        return {
            "cash": self._f(ca.get("Caja")),
            "commission": self._f(ca.get("comision")),
            "total_equity": self._f(ca.get("total")),
        }

    # ── output: JSON ───────────────────────────────────
    def _write_json(self, snap):
        tmp = self._json_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(snap, f, indent=2, default=str)
        os.replace(tmp, self._json_path)

    # ── output: CLI ────────────────────────────────────
    def _print_cli(self, snap):
        """Compact multi-section dashboard."""
        ts = snap["timestamp"][:19]
        z = snap["strategy"]["z_score"]
        z_str = f"{z:+.3f}" if z is not None else "  n/a"
        coint = "✅" if snap["strategy"]["is_cointegrated"] else "❌"

        # Position direction
        if snap["strategy"]["largo_spread"]:
            pos_dir = "LARGO spread"
        elif snap["strategy"]["corto_spread"]:
            pos_dir = "CORTO spread"
        else:
            pos_dir = "FLAT"

        hr = snap["strategy"]["hedge_ratio"]
        hr_str = f"{hr:.4f}" if hr is not None else "n/a"

        print()
        print("┌" + "─" * 68 + "┐")
        print(
            f"│  📡 LIVE MONITOR  {ts}  "
            f"z={z_str}  {coint}  HR={hr_str}"
        )
        print("├" + "─" * 68 + "┤")

        # Market data row
        for nemo in self.nemos:
            md = snap["market_data"][nemo]
            bid = md["bid"] or "—"
            ask = md["ask"] or "—"
            last = md["last_close"] or "—"
            sp = md["spread_bps"]
            sp_str = f"{sp:.1f}bp" if sp is not None else "—"
            print(
                f"│  {nemo:>6}  "
                f"Bid {bid:>12}  "
                f"Ask {ask:>12}  "
                f"Last {last:>12}  "
                f"Spr {sp_str:>7}"
            )

        print("├" + "─" * 68 + "┤")

        # Positions row
        total_pnl = 0.0
        for nemo in self.nemos:
            ps = snap["positions"][nemo]
            qty = ps["qty"] or 0
            entry = ps["entry_price"]
            pnl = ps["unrealized_pnl"]
            pnl_str = f"${pnl:+.2f}" if pnl is not None else "—"
            entry_str = f"{entry:.4f}" if entry is not None else "—"
            if pnl is not None:
                total_pnl += pnl
            print(
                f"│  {nemo:>6}  "
                f"Pos {qty:>10}  "
                f"Entry {entry_str:>12}  "
                f"uPnL {pnl_str:>10}"
            )

        print("├" + "─" * 68 + "┤")

        # Account summary
        acct = snap["account"]
        cash = acct["cash"] or 0
        equity = acct["total_equity"] or 0
        comm = acct["commission"] or 0
        print(
            f"│  💰 Cash ${cash:,.2f}  "
            f"Equity ${equity:,.2f}  "
            f"Comm ${comm:,.2f}  "
            f"uPnL ${total_pnl:+.2f}  "
            f"Dir: {pos_dir}"
        )
        print("└" + "─" * 68 + "┘")

    # ── helpers ────────────────────────────────────────
    @staticmethod
    def _f(val):
        """Safely convert to float or return None."""
        if val is None:
            return None
        try:
            v = float(val)
            if np.isnan(v) or np.isinf(v):
                return None
            return v
        except (ValueError, TypeError):
            return None
