# -*- coding: utf-8 -*-
"""
MomentumTrading.py
------------------
Orchestrator for the daily-rebalance momentum strategy.
Extends LiveTrading, replacing the heartbeat-driven signal loop
with a daily rebalance scheduler.
"""

import datetime
import time
try:
    import Queue as queue
except ImportError:
    import queue

from trading import LiveTrading
from MomentumLiveMonitor import MomentumLiveMonitor


class MomentumTrading(LiveTrading):
    """Daily-rebalance momentum orchestrator.

    Differences from LiveTrading:
      - No heartbeat signal generation (replaced by daily rebalance)
      - No slow layer refresh loop
      - Keeps: position sync (10 min), LiveMonitor (60 s), event queue,
        graceful shutdown, WebSocket for real-time prices
    """

    def __init__(
        self, lista_nemos, lista_bolsas, lista_libros, capital_inicial,
        heartbeat, fecha_inicial, admin_datos,
        admin_ejecucion, portafolio, estrategia,
        interval='1d', testnet=False, state_file=None,
        batch_n=1, batch_interval_s=300, limit_offset_bps=2,
        paper_trading=False, bq_logger=None, account=None,
        # Momentum-specific
        rebalance_hour_utc=0,
        min_rebalance_threshold=0.005,
        variant='turtle',
        short_window=5,
        long_window=30,
        max_weight=0.10,
        stop_loss_pct=-0.15,
        subscribe_orderbooks=False,
    ):
        self.rebalance_hour_utc = rebalance_hour_utc
        self.min_rebalance_threshold = min_rebalance_threshold
        self.variant = variant
        self.short_window = short_window
        self.long_window = long_window
        self.max_weight = max_weight
        self.stop_loss_pct = stop_loss_pct
        self._subscribe_orderbooks = subscribe_orderbooks

        super().__init__(
            lista_nemos, lista_bolsas, lista_libros, capital_inicial,
            heartbeat, fecha_inicial, admin_datos,
            admin_ejecucion, portafolio, estrategia,
            interval=interval, testnet=testnet, state_file=state_file,
            batch_n=batch_n, batch_interval_s=batch_interval_s,
            limit_offset_bps=limit_offset_bps,
            paper_trading=paper_trading,
            bq_logger=bq_logger, account=account,
        )

    # ------------------------------------------------------------------
    # Override component instantiation
    # ------------------------------------------------------------------

    def _generar_instancias_trading(self):
        """Instantiate momentum-specific components."""
        print("Creating MomentumStrategy, MomentumPortfolio, Execution, Monitor")

        # WebSocket uses 1m for real-time prices; daily OHLCV for signals
        # is fetched separately via REST in MomentumStrategy.refresh_daily_data()
        self.admin_datos = self.admin_datos_cls(
            self.eventos, self.lista_nemos,
            interval='1m', testnet=self.testnet,
            subscribe_orderbooks=self._subscribe_orderbooks,
        )

        self.estrategia = self.estrategia_cls(
            self.admin_datos, self.eventos,
            signal_logger=self.signal_order_logger,
            variant=self.variant,
            short_window=self.short_window,
            long_window=self.long_window,
            max_weight=self.max_weight,
            stop_loss_pct=self.stop_loss_pct,
            min_rebalance_threshold=self.min_rebalance_threshold,
        )

        self.portafolio = self.portafolio_cls(
            self.admin_datos, self.eventos,
            self.fecha_inicial, self.capital_inicial,
            self.estrategia,
            order_logger=self.signal_order_logger,
            lista_bolsas=self.lista_bolsas,
            batch_n=self.batch_n,
            batch_interval_s=self.batch_interval_s,
            limit_offset_bps=self.limit_offset_bps,
            min_rebalance_threshold=self.min_rebalance_threshold,
        )

        self.estrategia.set_portafolio(self.portafolio)

        self.admin_ejecucion = self.admin_ejecucion_cls(
            self.eventos, self.lista_nemos, self.lista_bolsas,
            self.market_type, self.testnet,
            paper_trading=self.paper_trading,
        )

        self.live_monitor = MomentumLiveMonitor(
            self.admin_datos,
            self.estrategia,
            self.portafolio,
            self.admin_ejecucion,
            self.lista_nemos,
            json_filename=self.state_file,
            bq_logger=self.bq_logger,
            account=self.account,
        )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def trade(self):
        """Main loop: daily rebalance + fill processing + position sync."""
        self._setup_signal_handlers()

        # Wait for WebSocket to start streaming prices
        print("[MomentumTrading] Waiting for WebSocket price data...")
        self._wait_for_prices(timeout=120)

        print("[MomentumTrading] Fetching initial daily data...")
        self.estrategia.refresh_daily_data()
        self.estrategia.compute_weights()

        # Execute initial rebalance immediately (don't wait until midnight)
        print("[MomentumTrading] Executing initial rebalance...")
        self.estrategia.calcular_senales()

        last_rebalance_date = datetime.datetime.utcnow().date()
        last_position_sync = time.time()
        POSITION_SYNC_INTERVAL = 600

        print(f"[MomentumTrading] Starting main loop "
              f"(next rebalance at {self.rebalance_hour_utc:02d}:00 UTC)")

        while self.is_running:
            if self.shutdown_requested:
                print("[MomentumTrading] Shutdown requested")
                break

            current_time = time.time()
            now_utc = datetime.datetime.utcnow()

            # --- Daily rebalance ---
            if (now_utc.hour == self.rebalance_hour_utc
                    and (last_rebalance_date is None
                         or now_utc.date() != last_rebalance_date)):
                self._execute_daily_rebalance()
                last_rebalance_date = now_utc.date()

            # --- Position sync (every 10 min) ---
            if current_time - last_position_sync >= POSITION_SYNC_INTERVAL:
                self._sync_positions()
                last_position_sync = current_time

            # --- LiveMonitor snapshot ---
            self.live_monitor.tick()

            # --- Process event queue ---
            try:
                evento = self.eventos.get(timeout=1.0)
            except queue.Empty:
                continue
            except KeyboardInterrupt:
                print("\n[MomentumTrading] KeyboardInterrupt")
                self.shutdown_requested = True
                break

            if evento is None:
                continue

            if evento.type == 'MERCADO':
                self.portafolio.actualiza_tiempo(evento)

            elif evento.type == 'SEÑAL':
                self.senales += 1
                self.portafolio.actualiza_senal(evento)

            elif evento.type == 'ORDEN':
                self.ordenes += 1
                self.trade_logger.log_order(evento)
                result = self.admin_ejecucion.ejecutar_orden(evento)
                if result is None and hasattr(evento, 'nemo'):
                    self.estrategia.limpia_orden_pendiente(evento.nemo)

            elif evento.type == 'CALCE':
                self.calces += 1
                self.trade_logger.log_fill(evento)
                self.portafolio.actualiza_calce(evento)
                self.estrategia.limpia_orden_pendiente(evento.nemo)

    # ------------------------------------------------------------------
    # Daily rebalance
    # ------------------------------------------------------------------

    def _execute_daily_rebalance(self):
        """Fetch fresh data, compute weights, emit rebalance signals."""
        print("\n" + "=" * 60)
        print(f"[REBALANCE] {datetime.datetime.utcnow().isoformat()} UTC")
        print("=" * 60)

        try:
            self.estrategia.refresh_daily_data()
            self.estrategia.compute_weights()
            self.estrategia.calcular_senales()
        except Exception as e:
            print(f"[REBALANCE] Error: {e}")
            import traceback
            traceback.print_exc()

    # ------------------------------------------------------------------
    # Position sync
    # ------------------------------------------------------------------

    def _wait_for_prices(self, timeout=120):
        """Block until at least one symbol has a WebSocket price update."""
        start = time.time()
        while time.time() - start < timeout:
            for nemo in self.lista_nemos:
                try:
                    p = self.admin_datos.get_valor_ultima_vela(nemo, 'close')
                    if p is not None:
                        print(f"[MomentumTrading] Price data available "
                              f"({nemo}={p}, waited {time.time()-start:.0f}s)")
                        return
                except Exception:
                    pass
            time.sleep(2)
        print(f"[MomentumTrading] WARNING: No WebSocket prices after {timeout}s "
              f"— will use REST fallback")

    def _sync_positions(self):
        """Periodic position sync with exchange."""
        try:
            handler = None
            if hasattr(self.admin_ejecucion, 'bitget_handler'):
                handler = getattr(self.admin_ejecucion, 'bitget_handler', None)
            if handler is None and hasattr(self.admin_ejecucion, 'binance_handler'):
                handler = getattr(self.admin_ejecucion, 'binance_handler', None)
            if handler:
                changes = self.portafolio.sync_positions_from_exchange(handler)
                if changes:
                    print(f"[POSITION SYNC] Corrected {len(changes)} positions")
        except Exception as e:
            print(f"[POSITION SYNC] Error: {e}")
