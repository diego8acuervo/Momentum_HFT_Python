# -*- coding: utf-8 -*-
"""
Created on Fri Mar  9 14:48:03 2018

@author: Diego Ochoa
"""


import datetime
import pprint   #Pretty Print.
try:
    import Queue as queue
except ImportError:
    import queue
import time
import signal
import sys
try:
    import pylab as plt
except ImportError:
    plt = None
from TradeLogger import TradeLogger
from SignalOrderLogger import SignalOrderLogger
from LiveMonitor import LiveMonitor

class LiveTrading(object):
    """
    Contiene la lógica para simular una estrategia en vivo usando datos en 
    tiempo real.
    
    """

    def __init__(
        self, lista_nemos,lista_bolsas,lista_libros, capital_inicial,
        heartbeat, fecha_inicial, admin_datos,
        admin_ejecucion, portafolio, estrategia, interval='1m',
        testnet:bool=False, state_file:str=None,
        batch_n:int=1, batch_interval_s:int=300, limit_offset_bps:int=2,
        paper_trading:bool=False,
        bq_logger=None, account:str=None,
    ):
        """
        Inicializa el Livetest.

        Parametros:
        lista_nemos - Lista con los strings de los nemotécnicos.
        capital_inicial - El capital de arranque.
        heartbeat - Tiempo de espera en segundos antes de actualizar datos.
        fecha_inicial - El momento de inicio de la estrategia.
        admin_datos - (Class) Tipo de Administrador de datos (CSV,SQL,Histórico o en vivo...).
        admin_ejecucion - (Class) Administra las órdenes y calces para los trades.
        portafolio - (Class) Posiciones actuales y previas para evaluar PYG y performance.
        estrategia - (Class) Genera señales basados en eventos de mercado.
        interval - Candle interval string ('1m', '5m', '1h', etc.) for data provider.
        state_file - Custom JSON filename for LiveMonitor (e.g. 'live_state_XRP_DOGE.json').
        """
   
        self.lista_nemos = lista_nemos
        self.lista_bolsas=lista_bolsas
        self.lista_libros =lista_libros
        self.market_type=lista_libros[0]#'PERP'  # Tipo de mercado: SPOT, PERP, FUTURES
        self.testnet       = testnet
        self.paper_trading = paper_trading
        self.capital_inicial = capital_inicial
        self.heartbeat = heartbeat
        self.interval = interval  # Store interval separately from heartbeat
        self.state_file = state_file  # Custom JSON for LiveMonitor
        self.batch_n          = batch_n
        self.batch_interval_s = batch_interval_s
        self.limit_offset_bps = limit_offset_bps
        self.fecha_inicial = fecha_inicial
        self.admin_datos_cls = admin_datos
        self.admin_ejecucion_cls = admin_ejecucion
        self.portafolio_cls = portafolio
        self.estrategia_cls = estrategia
        self.bq_logger = bq_logger
        self.account = account

        self.eventos = queue.Queue()
        
        self.senales = 0
        self.ordenes = 0
        self.calces = 0
        self.num_estratgs = 1
        
        # Shutdown flags for graceful exit
        self.is_running = True
        self.shutdown_requested = False
        
        # Initialize trade logger
        self.trade_logger = TradeLogger(
            lista_nemos, bq_logger=self.bq_logger, account=self.account
        )

        # Initialize signal and order logger
        self.signal_order_logger = SignalOrderLogger(
            lista_nemos, bq_logger=self.bq_logger, account=self.account
        )
       
        self._generar_instancias_trading()

    def _generar_instancias_trading(self):
        """
        Genera las instancias de los objetos necesarios para ejecutar el 
        BT a partir de sus tipos de clase.
        """
        print(
            "Creando AdminDatos, Estrategia, Portafolio y Ejecución"
        )
        self.admin_datos = self.admin_datos_cls(self.eventos, self.lista_nemos, interval=self.interval, testnet=self.testnet)
        self.estrategia = self.estrategia_cls(self.admin_datos, self.eventos, signal_logger=self.signal_order_logger)
        self.portafolio = self.portafolio_cls(self.admin_datos, self.eventos, self.fecha_inicial,
                                            self.capital_inicial, self.estrategia, order_logger=self.signal_order_logger,
                                            lista_bolsas=self.lista_bolsas,
                                            batch_n=self.batch_n,
                                            batch_interval_s=self.batch_interval_s,
                                            limit_offset_bps=self.limit_offset_bps)
        
        # ✅ Link strategy to portfolio for position synchronization
        if hasattr(self.estrategia, 'set_portafolio'):
            self.estrategia.set_portafolio(self.portafolio)
        
        self.admin_ejecucion = self.admin_ejecucion_cls(
            self.eventos, self.lista_nemos, self.lista_bolsas,
            self.market_type, self.testnet,
            paper_trading=self.paper_trading,
        )

        # ✅ Live dashboard monitor (read-only observer)
        self.live_monitor = LiveMonitor(
            self.admin_datos,
            self.estrategia,
            self.portafolio,
            self.admin_ejecucion,
            self.lista_nemos,
            json_filename=self.state_file,
            bq_logger=self.bq_logger,
            account=self.account,
        )

    def _setup_signal_handlers(self):
        """
        Setup signal handlers for graceful shutdown.
        Handles Ctrl+C (SIGINT) and termination signals (SIGTERM).
        """
        def signal_handler(signum, frame):
            signal_name = 'SIGINT' if signum == signal.SIGINT else 'SIGTERM'
            print(f"\n\n🛑 Received {signal_name} - Initiating graceful shutdown...")
            print("⏳ Please wait while we close positions and save data...")
            self.shutdown_requested = True
            self.is_running = False
        
        # Register signal handlers
        signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
        signal.signal(signal.SIGTERM, signal_handler)  # Termination signal
        print("✅ Signal handlers registered for graceful shutdown")

    def _cleanup(self):
        """
        Cleanup resources before shutdown.
        - Disconnect WebSocket
        - Close any pending orders
        - Save final state
        """
        print("\n🧹 Cleaning up resources...")
        
        # Disconnect WebSocket
        if hasattr(self.admin_datos, 'disconnect_websocket'):
            try:
                print("📡 Disconnecting WebSocket...")
                self.admin_datos.disconnect_websocket()
                print("✅ WebSocket disconnected")
            except Exception as e:
                print(f"⚠️ Error disconnecting WebSocket: {e}")
        
        # Close any pending orders (optional - implement if needed)
        # if hasattr(self.admin_ejecucion, 'cancel_all_orders'):
        #     try:
        #         print("🚫 Cancelling pending orders...")
        #         self.admin_ejecucion.cancel_all_orders()
        #         print("✅ Orders cancelled")
        #     except Exception as e:
        #         print(f"⚠️ Error cancelling orders: {e}")
        
        # Flush BigQuery logger
        if self.bq_logger:
            try:
                print("📊 Flushing BigQuery logger...")
                self.bq_logger.shutdown()
                print("✅ BigQuery logger flushed")
            except Exception as e:
                print(f"⚠️ Error flushing BQ logger: {e}")

        print("✅ Cleanup completed")
    
    def trade(self):
        """
        Inicia la alimentación de datos y la negociación en vivo.
        
        Uses HYBRID APPROACH:
        - Heartbeat-driven signal generation (consistent frequency)
        - WebSocket event processing (real-time updates)
        - Data freshness validation
        - WebSocket health monitoring
        - Graceful shutdown handling
        """   
        # Setup signal handlers for graceful shutdown
        self._setup_signal_handlers()
        
        # Inicializa velas para evaluar cointegración y spread.
        self.admin_datos.set_initial_candles()
        
        # Connect WebSocket if available (CoinApiDs)
        if hasattr(self.admin_datos, 'connect_websocket'):
            self.admin_datos.connect_websocket()
            print("✅ WebSocket connected for real-time data")
        
        last_signal_check = time.time()
        last_position_sync = time.time()  # ✅ Track last position sync time
        last_slow_layer = 0  # ✅ Force immediate slow-layer on first tick
        POSITION_SYNC_INTERVAL = 600  # ✅ Sync with exchange every 10 minutes
        
        # Dict maps heartbeat values to seconds
        freq_dict = {
            '1m': 60,
            '5m': 300,
            '15m': 900,
            '1HRS': 3600,
            '1DAY': 86400
        }
        signal_interval = freq_dict.get(self.heartbeat, 60)  # Signal generation frequency

        print(f"🚀 Starting live trading loop (heartbeat: {self.heartbeat})")
        print(f"📊 Signal generation interval: {signal_interval}")
        print(f"💡 Press Ctrl+C to stop gracefully\n")

        # Determina que hacer con el siguiente evento que llegue
        while self.is_running:
            # Check if shutdown was requested
            if self.shutdown_requested:
                print("🛑 Shutdown requested, exiting trading loop...")
                break
            
            current_time = time.time()
            
            # ✅ HEARTBEAT-DRIVEN SIGNAL GENERATION
            # Ensures consistent signal calculation frequency
            if current_time - last_signal_check >= signal_interval:
                self._check_and_generate_signals()
                last_signal_check = current_time
            
            # ✅ FIX D: PERIODIC POSITION SYNC WITH EXCHANGE
            # Corrects portfolio drift (dust residuals, partial fills, manual changes)
            if current_time - last_position_sync >= POSITION_SYNC_INTERVAL:
                try:
                    # Use whichever handler is available (Bitget or Binance)
                    handler = None
                    if hasattr(self, 'admin_ejecucion'):
                        handler = getattr(self.admin_ejecucion, 'bitget_handler', None) or getattr(self.admin_ejecucion, 'binance_handler', None)
                    if handler:
                        changes = self.portafolio.sync_positions_from_exchange(handler)
                        if changes:
                            print(f"🔄 [POSITION SYNC] Corrected {len(changes)} positions: {changes}")
                        else:
                            print(f"🔄 [POSITION SYNC] All positions in sync with exchange")
                    else:
                        print(f"⚠️ [POSITION SYNC] No exchange handler available, skipping sync")
                except Exception as e:
                    print(f"❌ [POSITION SYNC] Error during sync: {e}")
                last_position_sync = current_time
            
            # ✅ SLOW LAYER: Proactively refresh daily OLS params
            # The strategy also self-refreshes inside calcular_senal_pares(),
            # but this ensures the slow layer is warm even during quiet periods.
            # Add the random offset so different processes don't all hit the
            # API at exactly the same time.
            slow_interval = getattr(
                self.estrategia, 'SLOW_LAYER_INTERVAL', 21600
            )
            slow_offset = getattr(
                self.estrategia, '_slow_layer_offset', 0
            )
            if current_time - last_slow_layer >= (slow_interval + slow_offset):
                try:
                    self.estrategia._slow_layer_update()
                    print("🔬 [SLOW LAYER] Proactive refresh done")
                except Exception as e:
                    print(f"⚠️ [SLOW LAYER] Error: {e}")
                last_slow_layer = current_time

            # ✅ LIVE MONITOR: Periodic dashboard snapshot
            self.live_monitor.tick()

            # ✅ PROCESS WEBSOCKET EVENTS
            # Process events with timeout to prevent blocking
            try:
                evento = self.eventos.get(timeout=0.5)  # 500ms timeout
            except queue.Empty:
                # Check WebSocket health during idle time
                if hasattr(self.admin_datos, 'is_running'):
                    if not self.admin_datos.is_running:
                        print("⚠️ WebSocket disconnected, attempting reconnection...")
                        if hasattr(self.admin_datos, 'reconnect_websocket'):
                            self.admin_datos.reconnect_websocket()
                continue
            except KeyboardInterrupt:
                # Handle Ctrl+C gracefully (in case signal handler doesn't catch it)
                print("\n🛑 KeyboardInterrupt received, shutting down...")
                self.shutdown_requested = True
                break
            else:
                if evento is not None:
                    if evento.type == 'MERCADO':
                        self.estrategia.calcular_senales(evento)
                        self.portafolio.actualiza_tiempo(evento)

                    elif evento.type == 'SEÑAL':
                        self.senales += 1
                        # CHECK: Pair circuit breaker — block new signals
                        if (hasattr(self.admin_ejecucion, '_pair_circuit_breaker')
                                and self.admin_ejecucion.is_pair_circuit_open()):
                            sig_type = getattr(evento, 'tipo_senal', '')
                            if sig_type != 'FUERA':
                                print(f"🚨 [PAIR BREAKER] Signal BLOCKED: "
                                      f"{evento.nemo} {sig_type}")
                                continue
                        self.portafolio.actualiza_senal(evento)
                        #self.portafolio.calcular_unidades(evento)

                    elif evento.type == 'ORDEN':
                        self.ordenes += 1
                        self.trade_logger.log_order(evento)  # Log order
                        result = self.admin_ejecucion.ejecutar_orden(evento)

                        # ── If the order was REJECTED, clear stale strategy flags ──
                        # When a pair leg fails, ordenes_pendientes and
                        # largoMdo/cortoMdo may already be set. If the fill
                        # never comes, those flags stick forever.
                        if result is None and hasattr(evento, 'nemo'):
                            sig = getattr(evento, 'signal_type', None)
                            if sig and sig != 'FUERA':
                                print(f"⚠️  [ORDER REJECTED] Clearing pending flag for {evento.nemo}")
                                self.estrategia.limpia_orden_pendiente(evento.nemo)
                                # Sync position flags with actual positions
                                # so stuck largoMdo/cortoMdo get corrected
                                if hasattr(self.estrategia, 'sync_position_flags'):
                                    self.estrategia.sync_position_flags()

                    elif evento.type == 'CALCE':
                        self.calces += 1
                        self.trade_logger.log_fill(evento)  # Log fill
                        self.portafolio.actualiza_calce(evento)
                        # Clear pending order in strategy
                        self.estrategia.limpia_orden_pendiente(evento.nemo)
    
    def _check_and_generate_signals(self):
        """
        Generate signals based on latest available data.
    
        ✅ ONLY PROCESSES CLOSED CANDLES (not incomplete ones)
        ✅ PREVENTS DUPLICATE EVENT GENERATION
        """
        from Eventos import EventoMdo
        import time
    
        # Initialize tracking dict if not exists
        if not hasattr(self, '_last_processed_candle_time'):
            self._last_processed_candle_time = {}
    
        for nemo in self.lista_nemos:
            # Check if we have data for this symbol
            if not hasattr(self.admin_datos, 'ultimo_dato_nemo'):
                continue
            
            if nemo not in self.admin_datos.ultimo_dato_nemo:
                print(f"⚠️ No data available for {nemo}")
                continue
        
            latest_candle = self.admin_datos.ultimo_dato_nemo[nemo]
        
            # ✅ CHECK 1: Get candle open_time (use open_time instead of close_time to avoid future timestamps)
            # CoinAPI sends close_time as the EXPECTED close time, which is in the future for streaming candles
            candle_open_time = latest_candle.get('open_time')
            if candle_open_time is None:
                # Fallback to close_time if open_time not available
                candle_open_time = latest_candle.get('close_time')
            if candle_open_time is None:
                continue
        
            # Convert to timestamp if datetime object
            if isinstance(candle_open_time, datetime.datetime):
                candle_timestamp = candle_open_time.timestamp()
            elif isinstance(candle_open_time, str):
                candle_timestamp = datetime.datetime.fromisoformat(candle_open_time.replace('Z', '+00:00')).timestamp()
            else:
                candle_timestamp = float(candle_open_time)
        
            # ✅ CHECK 2: Skip if we already processed this candle
            last_processed = self._last_processed_candle_time.get(nemo, 0)
            if candle_timestamp <= last_processed:
                # Same candle as before - don't generate duplicate event
                continue
        
            # ✅ CHECK 2.5: Reject future timestamps (clock drift protection)
            current_time = time.time()
            if candle_timestamp > current_time:
                time_diff = candle_timestamp - current_time
                candle_dt = datetime.datetime.fromtimestamp(candle_timestamp, tz=datetime.timezone.utc)
                print(f"⏰ Rejecting FUTURE candle for {nemo}: {candle_dt.strftime('%Y-%m-%d %H:%M:%S')} ({time_diff:.1f}s in the future)")
                continue
        
            # ✅ CHECK 3: Ensure candle is CLOSED (at least 5 seconds old)
            candle_age = current_time - candle_timestamp
        
            if candle_age < 0:
                # Future timestamp - should have been caught by CHECK 2.5, but defensive check
                candle_dt = datetime.datetime.fromtimestamp(candle_timestamp, tz=datetime.timezone.utc)
                print(f"⏰ Rejecting FUTURE candle for {nemo}: {candle_dt.strftime('%Y-%m-%d %H:%M:%S')} ({abs(candle_age):.1f}s in the future)")
                continue
            elif candle_age < 5:
                # Candle still forming - wait for it to close
                # print(f"⏳ {nemo} candle still forming (age: {candle_age:.1f}s)")
                continue
        
            # ✅ CHECK 4: Validate data freshness (< 2x heartbeat)
            last_update_time = 0
            if hasattr(self.admin_datos, 'last_message_time'):
                last_update_time = self.admin_datos.last_message_time.get(nemo, 0)
        
            data_age = current_time - last_update_time if last_update_time > 0 else float('inf')
            max_data_age = self.heartbeat * 2
    
            if data_age > max_data_age and last_update_time > 0: # type: ignore
                print(f"⚠️ Stale data for {nemo}: {data_age:.1f}s old (max: {max_data_age}s)")
                continue
    
            # ✅ ALL CHECKS PASSED - Generate event for NEW CLOSED candle
            try:
                evento = EventoMdo(nemo=nemo, **latest_candle)
                self.eventos.put(evento)
                
                # Update tracking
                self._last_processed_candle_time[nemo] = candle_timestamp
                
                # Format timestamp for display
                candle_dt = datetime.datetime.fromtimestamp(candle_timestamp, tz=datetime.timezone.utc)
                print(f"✅ Generated signal for {nemo} - NEW CLOSED candle at {candle_dt.strftime('%H:%M:%S')} (age: {candle_age:.1f}s)")
                
            except Exception as e:
                print(f"❌ Error generating signal for {nemo}: {e}")
                import traceback
                traceback.print_exc()
        
        # At the end of _check_and_generate_signals() - OUTSIDE the for loop
        if self._last_processed_candle_time:
            print(f"\n[CANDLE TRACKING] Last processed timestamps:")
            for nemo, ts in self._last_processed_candle_time.items():
                dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
                print(f"  {nemo}: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
            print()
    

    def _genera_performance(self):
        """
        Imprime el resultado del Backtest ( performance)
        Crea la curva de equity.
        """
        self.portafolio.curva_equity_dataframe()
        
        print("Creando estadísticas de resumen...")
        stats = self.portafolio.output_resumen_estadisticas()
        
        print("Creando curva de equity...")
        print(self.portafolio.curva_equity.tail(10))
        pprint.pprint(stats)

        print("Señales: %s" % self.senales)
        print("Ordenes: %s" % self.ordenes)
        print("Calces: %s" % self.calces)
        
        # Show deduplication statistics
        if hasattr(self.estrategia, 'get_deduplication_stats'):
            dedup_stats = self.estrategia.get_deduplication_stats()
            print("\n" + "="*60)
            print("📊 CANDLE DEDUPLICATION STATISTICS")
            print("="*60)
            print(f"Duplicate candles skipped: {dedup_stats['duplicates_skipped']}")
            print(f"Last processed candles:")
            for nemo, timestamp in dedup_stats['last_processed_candles'].items():
                print(f"  {nemo}: {timestamp}")
            print("="*60 + "\n")
        
        # Print fill processing metrics (if available)
        if hasattr(self.admin_ejecucion, 'binance_handler'):
            handler = self.admin_ejecucion.binance_handler
            if handler and hasattr(handler, 'print_fill_metrics'):
                handler.print_fill_metrics()
        
        # Export trade log to CSV
        print("\nExportando log de trades...")
        self.trade_logger.build_dataframe()
        self.trade_logger.print_summary()
        self.trade_logger.export_to_csv()
        
        # Export signal and order logs to CSV
        print("\nExportando log de señales y órdenes...")
        self.signal_order_logger.build_dataframes()
        self.signal_order_logger.print_summary()
        self.signal_order_logger.export_to_csv()

    def _corre_aplicacion_trading(self):
        """
        Simula el backtes y genera el desempeño de la estrategia.
        Includes graceful shutdown handling.
        """
        try:
            print("Iniciando Ciclo de trading...")
            self.trade()
        except KeyboardInterrupt:
            print("\n\n🛑 KeyboardInterrupt received in main loop")
            self.shutdown_requested = True
        except Exception as e:
            print(f"❌ Error en Ciclo de trading: {e}")
            import traceback
            traceback.print_exc()
        finally:
            print("\n" + "="*60)
            print("🏁 Trading loop ended - Generating performance report...")
            print("="*60 + "\n")
            
            # Cleanup resources
            self._cleanup()
            
            # Generate performance report
            try:
                self._genera_performance()
            except Exception as e:
                print(f"⚠️ Error generating performance report: {e}")
                import traceback
                traceback.print_exc()
            
            print("\n" + "="*60)
            print("✅ Shutdown complete")
            print("="*60)
