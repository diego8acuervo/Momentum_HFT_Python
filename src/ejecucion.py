# -*- coding: utf-8 -*-
"""
Created on Fri May  5 15:57:58 2017
Ejecucion.py
@author: Diego Ochoa
"""

import datetime
from datetime import timedelta, timezone
import hmac
import json
import math
import time
import threading
#Modulos deprecados IBpy
# from ib.ext.Contract import Contract
# from ib.ext.Order import Order
# from ib.opt import ibConnection, message
from Eventos import EventoCalce, EventoOrden
import pandas as pd

from binance import Client
from binance_spot import BinanceSpotTrader
from binance_perp import BinancePerpetualTrader
from bitget_perp import BitgetPerpetualTrader

from abc import ABCMeta, abstractmethod
import datetime
try:
    import Queue as queue
except ImportError:
    import queue

from Eventos import EventoCalce, EventoOrden


class AdminEjecucion(object):
    """
    Administra la interacción entre un conjunto de objetos orden generados por 
    un portafolio y el conjunto de objetos calce que ocurren en el mercado
    sea en simulación o en tiempo real,

    El administrador puede operar con subclases de datos simulados
    (backtesting) o con datos en tiempo real de proveedores de precio en línea
    """

    __metaclass__ = ABCMeta

    @abstractmethod
    def ejecutar_orden(self, evento):
        """
        Toma un evento orden y lo ejecuta produciendo automáticamente un 
        evento Calce que entra a la fila (Queue)
        Parametros:
        evento - Contiene un evento orden con información de la misma.
        """
        raise NotImplementedError("Debería implementar  ejecutar_orden()")


class EjecutorSimulado(AdminEjecucion):
    """
    El ejecutor simulado simplemente convierte todos los objetos Orden 
    en su equivalente objeto Calce automaticamente sin latencia 
    slippage o razones de calce.

    Esto permite una primera simulación sin inconvenientes de una estrategia,
    antes de implementación antes de simular una ejecución más sofisticada.
    """
    
    def __init__(self, eventos):
        """
        Inicializa el ejecutor alistando la secuencia de eventos internamente.

        Parameters:
        eventos - La Fila de objetos evento.
        """
        self.eventos = eventos

    def ejecutar_orden(self, evento):
        """
        Simplemente Convierte orden en Calce sin ninguna consideración adicional.

        Parametros:
        evento - Contiene un objeto Evento con información.
        """
        if evento.type == 'ORDEN':
            print('...Orden  %s  recibida' % evento.direccion)
            calce_evento = EventoCalce(
                datetime.datetime.utcnow(), evento.nemo,
                'SMART', evento.cantidad, evento.direccion, evento.precio
            )
            print('Calzado orden %s' % calce_evento.direccion)
            self.eventos.put(calce_evento)


class _SliceTask:
    """A single scheduled limit-order slice. Comparable by fire_time for heapq."""
    __slots__ = (
        'fire_time', 'nemo', 'bolsa', 'direccion', 'slice_qty',
        'signal_type', 'batch_id', 'slice_idx', 'offset_bps',
        'base_price', 'cancelled',
    )

    def __init__(self, fire_time, nemo, bolsa, direccion, slice_qty,
                 signal_type, batch_id, slice_idx, offset_bps, base_price):
        self.fire_time   = fire_time
        self.nemo        = nemo
        self.bolsa       = bolsa
        self.direccion   = direccion
        self.slice_qty   = slice_qty
        self.signal_type = signal_type
        self.batch_id    = batch_id
        self.slice_idx   = slice_idx
        self.offset_bps  = offset_bps
        self.base_price  = base_price
        self.cancelled   = False

    def __lt__(self, other):
        return self.fire_time < other.fire_time


class BatchLimitOrderScheduler:
    """
    Splits an entry EventoOrden into N equal limit-order slices sent at
    regular intervals (batch_interval_s).  Exits (FUERA) bypass this path
    and remain as market orders for fast execution.

    Parameters
    ----------
    place_slice_fn : callable(nemo, bolsa, side, qty, price, signal_type)
        Sends a single LMT slice to the exchange.
    cancel_all_fn  : callable(nemo)
        Cancels all open exchange orders for a symbol.
    """

    def __init__(self, place_slice_fn, cancel_all_fn):
        self._place_slice = place_slice_fn
        self._cancel_all  = cancel_all_fn

        self._heap      = []           # heapq of _SliceTask
        self._heap_lock = threading.Lock()
        self._wakeup    = threading.Event()
        self._shutdown  = False

        self._active_batches: dict = {}   # batch_id → list[_SliceTask]
        self._batch_lock = threading.Lock()

        self._thread = threading.Thread(
            target=self._scheduler_loop,
            name="BatchLimitScheduler",
            daemon=True,
        )
        self._thread.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def schedule(self, evento) -> str:
        """
        Decompose an entry EventoOrden into N _SliceTasks and enqueue them.
        Uses evento.precio as the base_price for passive-limit computation.
        Returns batch_id for reference.
        """
        n          = getattr(evento, 'batch_n', 1)
        interval   = getattr(evento, 'batch_interval_s', 300)
        offset_bps = getattr(evento, 'limit_offset_bps', 2)
        base_price = getattr(evento, 'precio', None)

        if not base_price or base_price <= 0:
            print(f"[BATCH] No valid base price for {evento.nemo} — falling back to MKT")
            return None

        slice_qty = evento.cantidad / n
        batch_id  = f"{evento.nemo}_{int(time.time())}"
        now       = time.time()
        tasks     = []

        for i in range(n):
            task = _SliceTask(
                fire_time   = now + i * interval,
                nemo        = evento.nemo,
                bolsa       = evento.bolsa,
                direccion   = evento.direccion,
                slice_qty   = slice_qty,
                signal_type = evento.signal_type,
                batch_id    = batch_id,
                slice_idx   = i,
                offset_bps  = offset_bps,
                base_price  = base_price,
            )
            tasks.append(task)

        with self._heap_lock:
            for task in tasks:
                import heapq
                heapq.heappush(self._heap, task)

        with self._batch_lock:
            self._active_batches[batch_id] = tasks

        self._wakeup.set()
        print(f"[BATCH] Scheduled {n} slices for {evento.nemo} "
              f"total_qty={evento.cantidad:.6f} interval={interval}s "
              f"offset={offset_bps}bps base={base_price:.5f}")
        return batch_id

    def cancel_pair(self, lista_nemos: list) -> int:
        """
        Cancel all pending scheduled slices for the given symbols and
        cancel any open limit orders on the exchange.
        Returns count of slices cancelled.
        """
        cancelled = 0
        with self._batch_lock:
            for batch_id, tasks in list(self._active_batches.items()):
                for task in tasks:
                    if task.nemo in lista_nemos and not task.cancelled:
                        task.cancelled = True
                        cancelled += 1
                if all(t.cancelled for t in tasks):
                    del self._active_batches[batch_id]

        for nemo in lista_nemos:
            try:
                self._cancel_all(nemo)
            except Exception as e:
                print(f"[BATCH] Warning: cancel_all for {nemo} raised: {e}")

        if cancelled:
            print(f"[BATCH] Cancelled {cancelled} pending slices for {lista_nemos}")
        return cancelled

    def shutdown(self):
        self._shutdown = True
        self._wakeup.set()
        self._thread.join(timeout=5)

    # ------------------------------------------------------------------
    # Internal scheduler loop
    # ------------------------------------------------------------------

    def _scheduler_loop(self):
        import heapq
        while not self._shutdown:
            self._wakeup.clear()
            sleep_for = 60.0

            with self._heap_lock:
                if self._heap:
                    sleep_for = max(0.0, self._heap[0].fire_time - time.time())

            if sleep_for > 0:
                self._wakeup.wait(timeout=min(sleep_for, 60.0))
                if self._shutdown:
                    break

            while True:
                task = None
                with self._heap_lock:
                    if self._heap and self._heap[0].fire_time <= time.time():
                        task = heapq.heappop(self._heap)
                if task is None:
                    break
                if task.cancelled:
                    print(f"[BATCH] Slice {task.slice_idx} for {task.nemo} was cancelled — skipping.")
                    continue
                try:
                    self._place_task(task)
                except Exception as e:
                    print(f"[BATCH] Error placing slice {task.slice_idx} for {task.nemo}: {e}")

    def _place_task(self, task: '_SliceTask'):
        from decimal import Decimal, ROUND_DOWN, ROUND_UP
        
        offset = task.offset_bps / 10_000.0
        
        # Use Decimal for precise arithmetic to avoid floating-point errors
        base_price_decimal = Decimal(str(task.base_price))
        offset_decimal = Decimal(str(offset))
        
        if task.direccion.lower() == 'buy':
            # For BUY (maker passive): price = base * (1 - offset) → round DOWN
            limit_price_decimal = base_price_decimal * (Decimal('1.0') - offset_decimal)
            # Round to a reasonable number of decimals (8 should cover most cases)
            limit_price_decimal = limit_price_decimal.quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
        else:
            # For SELL (maker passive): price = base * (1 + offset) → round UP
            limit_price_decimal = base_price_decimal * (Decimal('1.0') + offset_decimal)
            # Round to a reasonable number of decimals (8 should cover most cases)
            limit_price_decimal = limit_price_decimal.quantize(Decimal('0.00000001'), rounding=ROUND_UP)
        
        limit_price = float(limit_price_decimal)

        print(f"[BATCH] Slice {task.slice_idx + 1} {task.nemo} "
              f"{task.direccion.upper()} {task.slice_qty:.6f} "
              f"@ {limit_price:.8f} (base={task.base_price:.5f}, {task.offset_bps}bps)")

        self._place_slice(
            nemo        = task.nemo,
            bolsa       = task.bolsa,
            side        = task.direccion,
            quantity    = task.slice_qty,
            price       = limit_price,
            signal_type = task.signal_type,
        )


class traderPerp(AdminEjecucion):
    # Class-level variable to track monitoring thread (shared across all instances)
    _monitoring_thread = None
    _monitoring_lock = threading.Lock()

    def __init__(self, eventos, lista_nemos, lista_bolsas, market_type='SPOT',
                 testnet:bool=False, paper_trading:bool=False):
            """
            Orchestrator for multi-exchange order execution.
            
            Args:
                eventos: Event queue for EventoOrden and EventoCalce
                lista_nemos: List of trading symbols (e.g., ['BTC', 'USDT'])
                lista_bolsas: List of exchanges (e.g., ['BINANCE', 'BITSO'])
                market_type: Market type for Binance ('SPOT' or 'PERP')
                            Default: 'SPOT' for backward compatibility
            """
            
            self.eventos=eventos
            self.lista_nemos=lista_nemos
            self.lista_bolsas=lista_bolsas
            self.market_type   = market_type.upper()
            self.testnet       = testnet
            self.paper_trading = paper_trading
            # Initialize market-specific handlers
            self.binance_handler = None
            if 'BINANCE' in lista_bolsas or 'BINANCEFTS' in lista_bolsas:
                if self.market_type == 'PERP':
                    # Use perpetuals handler with eventos queue for immediate fill processing
                    self.binance_handler = BinancePerpetualTrader(
                        lista_nemos=lista_nemos,
                        testnet=self.testnet,
                        eventos=self.eventos  # Pass eventos queue for immediate fills
                    )
                    print("[INIT] ✅ Binance PERPETUALS handler initialized (with immediate fill processing)")
                else:
                    # Use spot handler (default for backward compatibility)
                    self.binance_handler = BinanceSpotTrader(
                        lista_nemos=lista_nemos
                    )
                    print("[INIT] ✅ Binance SPOT handler initialized")

            # ── Bitget handler ────────────────────────────────
            self.bitget_handler = None
            if 'BITGET' in lista_bolsas or 'BITGETFTS' in lista_bolsas:
                self.bitget_handler = BitgetPerpetualTrader(
                    lista_nemos=lista_nemos,
                    testnet=self.testnet,
                    eventos=self.eventos,
                    paper_trading=self.paper_trading,
                )
                mode = "PAPER TRADING" if self.paper_trading else "LIVE"
                print(f"[INIT] ✅ Bitget PERPETUALS handler initialized — {mode}")

            self.binance_fills = []
            self.bitget_fills = []
            self.fill_dict = {
                'BINANCE': self.binance_fills,
                'BITGET': self.bitget_fills,
            }
            #Conexion a Binance Estas conexiones vienen de la version anterior
            # Remover luego de pruebas
            # self.binance_api_key = self.load_binance_key()
            # self.binance_api_secret = self.load_binance_secret()
            # self.taker= self.create_binance_conn()
            
            #Conexion a CoinAPI
            self.coinApiKey=self.load_coinAPI_key()
            self.orderIds = {}
            
            # Initialize logging files
            self.orders_log_file = "orders_log.csv"
            self.fills_log_file = "fills_log.csv"
            self._initialize_log_files()
            
            # Initialize API health monitoring and circuit breaker
            self.api_health = {
                'BINANCE': {
                    'status': 'HEALTHY',
                    'last_error': None,
                    'error_count': 0,
                    'last_success': time.time()
                },
                'BINANCEFTS': {
                    'status': 'HEALTHY',
                    'last_error': None,
                    'error_count': 0,
                    'last_success': time.time()
                },
                'BITGET': {
                    'status': 'HEALTHY',
                    'last_error': None,
                    'error_count': 0,
                    'last_success': time.time()
                },
                'BITGETFTS': {
                    'status': 'HEALTHY',
                    'last_error': None,
                    'error_count': 0,
                    'last_success': time.time()
                },
                'BYBIT': {
                    'status': 'HEALTHY',
                    'last_error': None,
                    'error_count': 0,
                    'last_success': time.time()
                },
                'OKX': {
                    'status': 'HEALTHY',
                    'last_error': None,
                    'error_count': 0,
                    'last_success': time.time()
                }
            }
            self.circuit_breaker_threshold = 5  # Consecutive errors before circuit opens
            self.circuit_breaker_timeout = 5  # Seconds before retry attempt
            
            # Order deduplication and rate limiting
            self.recent_orders = {}  # Track recent order requests
            self.order_dedup_window = 10  # Seconds - prevent duplicate orders
            self.order_max_age = 30  # Seconds - reject stale orders
            self.MAX_ORDER_AGE_SECONDS = 3600  # 60 minutes - auto-cancel stale open orders

            # ========== PAIR CIRCUIT BREAKER ==========
            # Tracks the last successfully filled leg so we can unwind it if the
            # second leg is rejected by the exchange (e.g., -2027 max position,
            # -4005 max quantity).  When tripped, blocks ALL new signals for this
            # pair until manual intervention resets it.
            self._pair_circuit_breaker = {
                'is_open': False,            # True → all new signals blocked
                'tripped_at': None,          # datetime when breaker tripped
                'failed_nemo': None,         # Symbol of the leg that was REJECTED
                'failed_error': None,        # Error message from exchange
                'filled_leg': None,          # EventoOrden of the leg that DID fill
                'unwind_sent': False,        # True once we've queued the reverse order
                'alert_file': 'outputs/PAIR_CIRCUIT_BREAKER_ALERT.txt',
            }
            # Temporary storage for the last successfully executed order in a pair
            self._last_filled_leg = None

            # Rate limiting - Multi-exchange support
            self.order_rate_limit = {
                'BINANCE': {'last_order': 0, 'min_interval': 0.75},     # 2 orders/sec max
                'BINANCEFTS': {'last_order': 0, 'min_interval': 0.75},  # 2 orders/sec max
                'BITGET': {'last_order': 0, 'min_interval': 0.75},   # 2 orders/sec max
                'BITGETFTS': {'last_order': 0, 'min_interval': 0.75},
                'BYBIT': {'last_order': 0, 'min_interval': 0.75},       # 2 orders/sec max
                'OKX': {'last_order': 0, 'min_interval': 0.75}          # 2 orders/sec max
            }
            
            # Start unified order monitoring ONLY if not already running (singleton pattern)
            # Only monitor exchanges that are configured in lista_bolsas
            with traderPerp._monitoring_lock:
                if traderPerp._monitoring_thread is None or not traderPerp._monitoring_thread.is_alive():
                    # Check if any exchanges need monitoring
                    exchanges_to_monitor = [ex for ex in ['BINANCE', 'BINANCEFTS', 'BITGET', 'BITGETFTS'] if ex in lista_bolsas]
                    
                    if exchanges_to_monitor:
                        print(f"[INIT] Starting order monitoring for: {', '.join(exchanges_to_monitor)}")
                        # 60s polling interval to avoid Binance IP rate-limit bans.
                        # Each cycle does ~4 REST calls (open_orders + trades × 2 symbols).
                        # With N subprocesses sharing the same IP, lower intervals
                        # quickly exceed the 2400 req/min weight budget.
                        traderPerp._monitoring_thread = self.monitor_orders_with_polling(check_interval=60)
                        print("[INIT] Order monitoring active (polling every 60 seconds)")
                    else:
                        print("[INIT] No exchanges to monitor, skipping monitoring thread")
                else:
                    print("[INIT] Order monitoring already running, skipping duplicate thread start")

            # ── Batch limit-order scheduler ───────────────────────────────────
            # Holds a single daemon thread and a heapq of pending slices.
            # batch_n=1 (default) means no slicing — scheduler heap stays empty.
            self.batch_scheduler = BatchLimitOrderScheduler(
                place_slice_fn = self._place_lmt_slice,
                cancel_all_fn  = self._cancel_all_for_scheduler,
            )
            print("[INIT] Batch limit-order scheduler initialized (batch_n=1 → MKT passthrough)")

    # ── Batch scheduler helpers ───────────────────────────────────────────────

    def _cancel_all_for_scheduler(self, nemo: str):
        """Cancel all open exchange limit orders for nemo. Called by BatchLimitOrderScheduler."""
        if self.bitget_handler:
            try:
                symbol = self.bitget_handler.get_symbol(nemo)
                self.bitget_handler.cancel_all_orders(symbol=symbol)
            except Exception as e:
                print(f"[BATCH CANCEL] Bitget cancel_all for {nemo}: {e}")
        if self.binance_handler:
            try:
                symbol = self.binance_handler.get_symbol(nemo)
                self.binance_handler.cancel_all_orders(symbol=symbol)
            except Exception as e:
                print(f"[BATCH CANCEL] Binance cancel_all for {nemo}: {e}")

    def _place_lmt_slice(self, nemo: str, bolsa: str, side: str,
                         quantity: float, price: float, signal_type: str):
        """
        Build a minimal LMT EventoOrden (batch_n=1 to prevent recursion) and
        route it through ejecutar_orden() to reuse all safety checks.
        """
        import datetime as _dt
        from datetime import timezone as _tz
        slice_ev = EventoOrden(
            nemo        = nemo,
            tipo_orden  = 'LMT',
            cantidad    = quantity,
            direccion   = side,
            precio      = price,
            bolsa       = bolsa,
            timestamp   = _dt.datetime.now(_tz.utc),
            signal_type = signal_type,
            batch_n     = 1,   # prevents re-entry into batch path
        )
        self.ejecutar_orden(slice_ev)

    def _initialize_log_files(self):
        """Initialize CSV log files for orders and fills if they don't exist"""
        import os
        
        # Initialize orders log
        if not os.path.exists(self.orders_log_file):
            orders_df = pd.DataFrame(columns=[
                'timestamp', 'symbol', 'exchange', 'order_type', 'side', 
                'quantity', 'price', 'order_id', 'status'
            ])
            orders_df.to_csv(self.orders_log_file, index=False)
            print(f"[LOG] Created orders log file: {self.orders_log_file}")
        
        # Initialize fills log
        if not os.path.exists(self.fills_log_file):
            fills_df = pd.DataFrame(columns=[
                'timestamp', 'symbol', 'exchange', 'order_id', 'side',
                'quantity', 'price', 'commission', 'response_type'
            ])
            fills_df.to_csv(self.fills_log_file, index=False)
            print(f"[LOG] Created fills log file: {self.fills_log_file}")

    def is_tracked_symbol(self, nemo: str) -> bool:
        """
        Check if a symbol is being tracked by this execution handler.
        
        Used to filter out fills for symbols that are not in lista_nemos,
        preventing KeyError in portfolio position tracking.
        
        Args:
            nemo: Symbol to check (e.g., 'SOL', 'ETH', 'BTC')
            
        Returns:
            bool: True if symbol is tracked, False otherwise
        """
        if nemo is None:
            return False
        return nemo in self.lista_nemos
    
    def _log_order_placement(self, symbol, exchange, order_type, side, quantity, price=None, order_id=None, status='SENT'):
        """
        Log order placement to CSV file.
        
        Args:
            symbol (str): Trading symbol
            exchange (str): 'BINANCE_PERP' or 'BINANCE_SPOT'
            order_type (str): 'MARKET' or 'LIMIT'
            side (str): 'BUY' or 'SELL'
            quantity (float): Order quantity
            price (float, optional): Limit price (None for market orders)
            order_id (str, optional): Order ID from exchange response
            status (str): Order status ('SENT', 'ACCEPTED', 'REJECTED', etc.)
        """
        try:
            timestamp = datetime.datetime.now(timezone.utc).isoformat()
            
            order_data = {
                'timestamp': timestamp,
                'symbol': symbol,
                'exchange': exchange,
                'order_type': order_type,
                'side': side,
                'quantity': quantity,
                'price': price if price else 'N/A',
                'order_id': order_id if order_id else 'N/A',
                'status': status
            }
            
            # Append to CSV
            order_df = pd.DataFrame([order_data])
            order_df.to_csv(self.orders_log_file, mode='a', header=False, index=False)
            
            print(f"[LOG] Order logged: {exchange} {order_type} {side} {quantity} @ {price if price else 'MARKET'}")
            
        except Exception as e:
            print(f"[ERROR] Failed to log order: {e}")
    
    def _log_fill_event(self, symbol, exchange, order_id, side, quantity, price, commission, response_type='FILL'):
        """
        Log fill event to CSV file.
        
        Args:
            symbol (str): Trading symbol
            exchange (str): 'BINANCE' or 'BITSO'
            order_id (str): Order ID
            side (str): 'BUY' or 'SELL'
            quantity (float): Filled quantity
            price (float): Fill price
            commission (float): Commission paid
            response_type (str): 'FILL', 'PARTIAL_FILL', 'CANCELED', etc.
        """
        try:
            timestamp = datetime.datetime.now(timezone.utc).isoformat()
            
            fill_data = {
                'timestamp': timestamp,
                'symbol': symbol,
                'exchange': exchange,
                'order_id': order_id,
                'side': side,
                'quantity': quantity,
                'price': price,
                'commission': commission,
                'response_type': response_type
            }
            
            # Append to CSV
            fill_df = pd.DataFrame([fill_data])
            fill_df.to_csv(self.fills_log_file, mode='a', header=False, index=False)
            
            print(f"[LOG] Fill logged: {exchange} {response_type} {side} {quantity} @ {price}")
            
        except Exception as e:
            print(f"[ERROR] Failed to log fill: {e}")

    def check_api_health(self, exchange):
        """
        Check if exchange API is healthy before sending orders.
        Implements circuit breaker pattern.
        
        Args:
            exchange (str): Exchange identifier (e.g., 'BINANCE', 'BINANCEFTS', 'BITSO', 'BYBIT', 'OKX')
            
        Returns:
            bool: True if API is healthy, False if circuit is open
        """
        # Defensive fallback: if exchange not in health dict, create default entry
        if exchange not in self.api_health:
            print(f"[WARNING] Exchange '{exchange}' not in api_health dict, adding with default HEALTHY status")
            self.api_health[exchange] = {
                'status': 'HEALTHY',
                'last_error': None,
                'error_count': 0,
                'last_success': time.time()
            }
        
        health = self.api_health[exchange]
        current_time = time.time()
        
        if health['status'] == 'CIRCUIT_OPEN':
            # Check if timeout has elapsed
            time_since_error = current_time - health['last_error']
            if time_since_error > self.circuit_breaker_timeout:
                health['status'] = 'HALF_OPEN'  # Try one request
                print(f"[CIRCUIT BREAKER] {exchange} circuit HALF_OPEN after {time_since_error:.0f}s, testing...")
                return True
            else:
                remaining = self.circuit_breaker_timeout - time_since_error
                print(f"[CIRCUIT BREAKER] {exchange} circuit OPEN, blocking orders ({remaining:.0f}s remaining)")
                return False
        
        return True    
    
    def record_api_error(self, exchange, error):
        """
        Record API error and potentially open circuit breaker.
        
        Args:
            exchange (str): Exchange identifier (e.g., 'BINANCE', 'BINANCEFTS', 'BITSO', 'BYBIT', 'OKX')
            error: Error message or exception
        """
        # Defensive fallback: if exchange not in health dict, create default entry
        if exchange not in self.api_health:
            print(f"[WARNING] Exchange '{exchange}' not in api_health dict, adding with default HEALTHY status")
            self.api_health[exchange] = {
                'status': 'HEALTHY',
                'last_error': None,
                'error_count': 0,
                'last_success': time.time()
            }
        
        health = self.api_health[exchange]
        health['error_count'] += 1
        health['last_error'] = time.time()
        
        if health['error_count'] >= self.circuit_breaker_threshold:
            if health['status'] != 'CIRCUIT_OPEN':
                health['status'] = 'CIRCUIT_OPEN'
                print(f"⚠️  [CIRCUIT BREAKER] {exchange} circuit OPENED after {health['error_count']} consecutive errors")
                print(f"⚠️  [CIRCUIT BREAKER] Last error: {error}")
                print(f"⚠️  [CIRCUIT BREAKER] Will retry in {self.circuit_breaker_timeout}s")
        else:
            print(f"[API ERROR] {exchange} error {health['error_count']}/{self.circuit_breaker_threshold}: {error}")
    
    def record_api_success(self, exchange):
        """
        Record successful API call and reset circuit breaker if needed.
        
        Args:
            exchange (str): Exchange identifier (e.g., 'BINANCE', 'BINANCEFTS', 'BITGET')
        """
        if exchange not in self.api_health:
            return
        health = self.api_health[exchange]
        health['error_count'] = 0
        health['last_success'] = time.time()
        if health['status'] in ('CIRCUIT_OPEN', 'HALF_OPEN'):
            health['status'] = 'HEALTHY'
            print(f"✅ [CIRCUIT BREAKER] {exchange} circuit reset → HEALTHY")

    def is_duplicate_order(self, side, quantity, price, exchange):
        """
        Check if order is a duplicate within the deduplication window.
        Prevents sending the same order multiple times.
        
        Args:
            side (str): 'BUY' or 'SELL'
            quantity (float): Order quantity
            price (float): Order price (None for market orders)
            exchange (str): 'BINANCE' or 'BITSO'
            
        Returns:
            bool: True if duplicate, False if unique
        """
        price_str = f"{price:.2f}" if price else "MARKET"
        order_key = f"{exchange}_{side}_{quantity}_{price_str}"
        current_time = time.time()
        
        if order_key in self.recent_orders:
            last_time = self.recent_orders[order_key]
            time_diff = current_time - last_time
            
            if time_diff < self.order_dedup_window:
                print(f"[DUPLICATE] Blocked duplicate order within {time_diff:.1f}s: {order_key}")
                return True
        
        # Record this order
        self.recent_orders[order_key] = current_time
        
        # Cleanup old entries (older than 2x dedup window)
        cleanup_threshold = current_time - (self.order_dedup_window * 2)
        self.recent_orders = {k: v for k, v in self.recent_orders.items() if v > cleanup_threshold}
        
        return False
    
    def check_rate_limit(self, exchange):
        """
        Check if order rate limit allows sending another order.
        Supports multiple exchanges: BINANCE, BINANCEFTS, BITSO, BYBIT, OKX.
        
        Args:
            exchange (str): Exchange identifier
            
        Returns:
            bool: True if rate limit allows order, False otherwise
        """
        # Defensive: Auto-create rate limit entry for unknown exchanges
        if exchange not in self.order_rate_limit:
            print(f"[WARNING] Exchange '{exchange}' not in order_rate_limit dict, adding with default settings...")
            self.order_rate_limit[exchange] = {'last_order': 0, 'min_interval': 0.75}
        
        rate_info = self.order_rate_limit[exchange]
        current_time = time.time()
        time_since_last = current_time - rate_info['last_order']
        
        if time_since_last < rate_info['min_interval']:
            wait_time = rate_info['min_interval'] - time_since_last
            print(f"[RATE LIMIT] {exchange} rate limit: wait {wait_time:.2f}s before next order")
            return False
        
        rate_info['last_order'] = current_time
        return True

    # ==================================================================
    # PAIR CIRCUIT BREAKER — second-leg failure protection
    # ==================================================================

    def is_pair_circuit_open(self) -> bool:
        """Return True if the pair circuit breaker has been tripped."""
        return self._pair_circuit_breaker['is_open']

    def _trip_pair_circuit_breaker(self, failed_evento, error_msg: str):
        """
        Trip the pair circuit breaker after the second leg is rejected.

        1. Records the failure details.
        2. Writes an alert file to outputs/ for manual review.
        3. Queues an UNWIND (reverse) order for the first leg that already
           filled so the account doesn't stay with a naked directional
           position.
        """
        import os
        pcb = self._pair_circuit_breaker
        pcb['is_open'] = True
        pcb['tripped_at'] = datetime.datetime.now(timezone.utc)
        pcb['failed_nemo'] = failed_evento.nemo
        pcb['failed_error'] = error_msg

        # ── ALERT: loud console output ──
        print("\n" + "🚨" * 30)
        print("🚨  PAIR CIRCUIT BREAKER TRIPPED  🚨")
        print(f"🚨  Second leg REJECTED: {failed_evento.nemo} "
              f"{failed_evento.direccion} {failed_evento.cantidad}")
        print(f"🚨  Error: {error_msg}")
        if pcb['filled_leg']:
            fl = pcb['filled_leg']
            print(f"🚨  First leg FILLED:  {fl.nemo} "
                  f"{fl.direccion} {fl.cantidad}")
        print("🚨  ALL NEW SIGNALS BLOCKED until manual reset.")
        print("🚨  Attempting automatic UNWIND of first leg...")
        print("🚨" * 30 + "\n")

        # ── ALERT FILE ──
        try:
            os.makedirs('outputs', exist_ok=True)
            alert_path = pcb['alert_file']
            with open(alert_path, 'a') as f:
                f.write("=" * 70 + "\n")
                f.write(f"PAIR CIRCUIT BREAKER TRIPPED  "
                        f"{pcb['tripped_at'].isoformat()}\n")
                f.write(f"Pair: {self.lista_nemos}\n")
                f.write(f"Failed leg: {failed_evento.nemo} "
                        f"{failed_evento.direccion} "
                        f"qty={failed_evento.cantidad}\n")
                f.write(f"Error: {error_msg}\n")
                if pcb['filled_leg']:
                    fl = pcb['filled_leg']
                    f.write(f"Filled leg: {fl.nemo} "
                            f"{fl.direccion} qty={fl.cantidad}\n")
                f.write("ACTION REQUIRED: Manually verify positions on "
                        "exchange and reset breaker.\n")
                f.write("=" * 70 + "\n\n")
            print(f"📄 Alert written to {alert_path}")
        except Exception as e:
            print(f"⚠️  Could not write alert file: {e}")

        # ── UNWIND the first leg ──
        self._send_unwind_order()

    def _send_unwind_order(self):
        """
        Queue a market order that reverses the first (already filled) leg.
        """
        pcb = self._pair_circuit_breaker
        filled = pcb.get('filled_leg')
        if filled is None:
            print("[UNWIND] ⚠️  No filled leg recorded — nothing to unwind.")
            return
        if pcb.get('unwind_sent'):
            print("[UNWIND] ⏭️  Unwind already sent, skipping duplicate.")
            return

        # Reverse direction
        reverse_dir = 'sell' if filled.direccion.lower() == 'buy' else 'buy'

        unwind_order = EventoOrden(
            nemo=filled.nemo,
            tipo_orden='MKT',
            cantidad=filled.cantidad,
            direccion=reverse_dir,
            precio=0,  # market order — price irrelevant
            bolsa=filled.bolsa,
            timestamp=datetime.datetime.now(timezone.utc),
            signal_type='FUERA',  # mark as closing to get reduce_only
        )

        # Put directly on event queue so the trading loop picks it up
        self.eventos.put(unwind_order)
        pcb['unwind_sent'] = True

        print(f"[UNWIND] ✅ Queued reverse order: "
              f"{filled.nemo} {reverse_dir.upper()} "
              f"{filled.cantidad} (reduce_only via FUERA)")

    def reset_pair_circuit_breaker(self):
        """
        Manually reset the pair circuit breaker after the operator has
        verified positions on the exchange.

        Call from the live_dashboard notebook or a maintenance script:
            admin_ejecucion.reset_pair_circuit_breaker()
        """
        pcb = self._pair_circuit_breaker
        was_open = pcb['is_open']
        pcb['is_open'] = False
        pcb['tripped_at'] = None
        pcb['failed_nemo'] = None
        pcb['failed_error'] = None
        pcb['filled_leg'] = None
        pcb['unwind_sent'] = False
        self._last_filled_leg = None
        if was_open:
            print("✅ Pair circuit breaker RESET — signals re-enabled.")
        else:
            print("ℹ️  Pair circuit breaker was not open.")

    def load_binance_key(self):
        """Load API key from environment variable. Default: BINANCE_API_KEY"""
        import os
        api_key = os.environ.get("BINANCE_API_KEY")
        if api_key is None:
            raise ValueError("API key not found. Please set the BINANCE_API_KEY environment variable.")
        return api_key
    
    def load_binance_secret(self):
        """Load API secret from environment variable. Default: BINANCE_API_SECRET"""
        import os
        api_secret = os.environ.get("BINANCE_SECRET_KEY")
        if api_secret is None:
            raise ValueError("API secret not found. Please set the BINANCE_SECRET_KEY environment variable.")
        return api_secret

    def load_bitso_key(self):
        """Load API key from environment variable. Default: BITSO_API_KEY"""
        import os
        api_key = os.environ.get("BITSO_API_KEY")
        if api_key is None:
            raise ValueError("API key not found. Please set the BITSO_API_KEY environment variable.")
        return api_key
    
    def load_bitso_secret(self):
        """Load API secret from environment variable. Default: BITSO_API_SECRET"""
        import os
        api_secret = os.environ.get("BITSO_SECRET_KEY")
        if api_secret is None:
            raise ValueError("API secret not found. Please set the BITSO_SECRET_KEY environment variable.")
        return api_secret

    def load_coinAPI_key(self):
        """Load API key from environment variable. Default: COINAPI_KEY"""
        import os
        api_key = os.environ.get("COINAPI_KEY")
        if api_key is None:
            raise ValueError("API key not found. Please set the COINAPI_KEY environment variable.")
        return api_key

    def generate_nonce_v2(self):
        """Generate a nonce for Bitso API requests (v2)"""
        return str(int(time.time() * 1000))

    def create_bitso_signed_headers(self, method, endpoint, body=""):
        """Create properly signed headers for Bitso API requests"""
        nonce = self.generate_nonce_v2()
        
        # Convert body to JSON string if it's a dict
        if isinstance(body, dict):
            body_str = json.dumps(body, separators=(',', ':'))
        else:
            body_str = str(body) if body else ""
            
        # Create message: nonce + HTTP method + request path + JSON payload
        message = nonce + method + endpoint + body_str
        
        # Create HMAC signature
        signature = hmac.new(
            self.bitso_api_secret.encode(), 
            message.encode(), 
            hashlib.sha256
        ).hexdigest()
        
        # Return properly formatted headers
        return {
            'Authorization': f'Bitso {self.bitso_api_key}:{nonce}:{signature}',
            'Content-Type': 'application/json'
        }

    def get_binance_symbol(self):
        """Get the trading symbol for Binance based on the list of nemos."""
        return self.lista_nemos[0] + self.lista_nemos[1]

    # ------------------------------------------------------------------
    # Minimum Notional Guard (Option C)
    # ------------------------------------------------------------------
    # Minimum notional thresholds by exchange (USDT)
    MIN_NOTIONAL = {
        'BINANCE': 20.0,
        'BINANCEFTS': 20.0,
        'BITGET': 5.0,
        'BITGETFTS': 5.0,
    }

    def _enforce_min_notional(self, evento) -> str:
        """
        Validate and enforce minimum notional value before sending an order.

        If notional (qty × price) is below the exchange minimum, the quantity
        is scaled UP to the minimum.  If it still can't meet the threshold
        after scaling (e.g., price is 0), the order is rejected.

        Args:
            evento: EventoOrden — modified in-place if quantity needs scaling.

        Returns:
            'OK'   — order meets or was scaled to minimum notional.
            'SKIP' — order cannot meet minimum and should be dropped.
        """
        min_notional = self.MIN_NOTIONAL.get(evento.bolsa, 20.0)

        # --- Obtain current mark / last price for the asset ---------------
        current_price = 0.0
        try:
            if evento.bolsa in ('BINANCE', 'BINANCEFTS') and self.binance_handler:
                symbol = self.binance_handler.get_symbol(evento.nemo)
                positions = self.binance_handler.get_position_info(symbol=symbol)
                if positions:
                    current_price = abs(float(positions[0].get('markPrice', 0)))
                # Fallback: use the order's own price field
                if current_price == 0 and hasattr(evento, 'precio') and evento.precio:
                    current_price = float(evento.precio)

            elif evento.bolsa in ('BITGET', 'BITGETFTS') and self.bitget_handler:
                # Quick price from last candle
                candles = self.bitget_handler.get_candles(evento.nemo, '1m', 1)
                if not candles.empty:
                    current_price = float(candles['close'].iloc[-1])
                if current_price == 0 and hasattr(evento, 'precio') and evento.precio:
                    current_price = float(evento.precio)
            else:
                # Unknown exchange — use order price as-is
                if hasattr(evento, 'precio') and evento.precio:
                    current_price = float(evento.precio)
        except Exception as e:
            print(f"[NOTIONAL] ⚠️ Could not fetch price for {evento.nemo}: {e}")
            if hasattr(evento, 'precio') and evento.precio:
                current_price = float(evento.precio)

        if current_price <= 0:
            print(f"[NOTIONAL] ⚠️ No price available for {evento.nemo} — cannot validate notional")
            return 'OK'  # Let the exchange decide

        # --- Compute and validate notional ---------------------------------
        qty = abs(evento.cantidad)
        notional = qty * current_price

        if notional >= min_notional:
            return 'OK'

        # --- Scale quantity up to minimum notional -------------------------
        min_qty = min_notional / current_price
        # Round UP to the exchange step size so we don't under-shoot
        if evento.bolsa in ('BINANCE', 'BINANCEFTS') and self.binance_handler:
            symbol = self.binance_handler.get_symbol(evento.nemo)
            step = self._get_binance_step_size(symbol)
            if step and step > 0:
                min_qty = math.ceil(min_qty / step) * step
            else:
                min_qty = round(min_qty, 4)
            min_qty = self.binance_handler.format_quantity(symbol, min_qty)
        elif evento.bolsa in ('BITGET', 'BITGETFTS') and self.bitget_handler:
            symbol = self.bitget_handler.get_symbol(evento.nemo)
            min_qty = float(self.bitget_handler.format_quantity(symbol, min_qty))
            # Ensure it doesn't round down below threshold
            if min_qty * current_price < min_notional:
                spec = self.bitget_handler._get_contract_spec(symbol)
                decimals = int(spec.get('volumePlace', '4'))
                step = 10 ** (-decimals)
                min_qty += step
                min_qty = round(min_qty, decimals)
        else:
            min_qty = round(min_qty, 4)

        new_notional = min_qty * current_price
        if new_notional < min_notional:
            print(f"[NOTIONAL] 🚫 Rejecting {evento.nemo} order: notional ${notional:.2f} < "
                  f"${min_notional} and cannot scale to minimum (price={current_price:.4f})")
            self._log_order_placement(
                symbol=evento.nemo,
                exchange=evento.bolsa,
                order_type=evento.tipo_orden,
                side=evento.direccion,
                quantity=qty,
                price=current_price,
                order_id=None,
                status='REJECTED_MIN_NOTIONAL',
            )
            return 'SKIP'

        # Apply the scaled-up quantity (preserve sign)
        sign = 1 if evento.cantidad >= 0 else -1
        old_qty = evento.cantidad
        evento.cantidad = sign * min_qty
        print(f"[NOTIONAL] ⬆️  Scaled {evento.nemo} qty: {abs(old_qty):.6f} → {min_qty:.6f} "
              f"(notional ${notional:.2f} → ${new_notional:.2f}, min=${min_notional})")
        return 'OK'

    def _get_binance_step_size(self, symbol: str) -> float:
        """Get LOT_SIZE step size from Binance exchange info cache."""
        try:
            self.binance_handler._load_exchange_info()
            if not self.binance_handler._exchange_info:
                return 0.0
            for s in self.binance_handler._exchange_info['symbols']:
                if s['symbol'] == symbol:
                    filters = {f['filterType']: f for f in s['filters']}
                    lot = filters.get('LOT_SIZE')
                    if lot:
                        return float(lot['stepSize'])
            return 0.0
        except Exception:
            return 0.0

    def ejecutar_orden(self, evento):
        """
        Ejecuta una orden en función del evento recibido y la bolsa a la que se dirige.
        Includes safety checks: order age, circuit breaker, deduplication, rate limiting.
        Also implements pair circuit breaker: if the second leg of a pair trade
        is rejected, the first leg is automatically unwound.
        """
        if evento.type == 'ORDEN':
            # CHECK 0: Pair Circuit Breaker — block all new entry signals
            # (but allow FUERA / unwind orders through so we can close positions)
            is_unwind = (hasattr(evento, 'signal_type')
                         and evento.signal_type == 'FUERA')
            if self.is_pair_circuit_open() and not is_unwind:
                pcb = self._pair_circuit_breaker
                print(f"🚨 [PAIR BREAKER] Order BLOCKED: "
                      f"{evento.nemo} {evento.direccion} "
                      f"{evento.cantidad} — breaker tripped at "
                      f"{pcb['tripped_at']}")
                self._log_order_placement(
                    symbol=evento.nemo,
                    exchange=evento.bolsa,
                    order_type=evento.tipo_orden,
                    side=evento.direccion,
                    quantity=evento.cantidad,
                    price=getattr(evento, 'precio', None),
                    order_id=None,
                    status='BLOCKED_PAIR_CIRCUIT_BREAKER'
                )
                return None

            # CHECK 1: Order Age - Reject stale orders
            if hasattr(evento, 'timeStamp'):
                order_age = (datetime.datetime.now(timezone.utc) - evento.timeStamp).total_seconds()
                if order_age > self.order_max_age:
                    print(f"[STALE ORDER] Rejected: order is {order_age:.1f}s old (max: {self.order_max_age}s)")
                    self._log_order_placement(
                        symbol=evento.nemo,
                        exchange=evento.bolsa,
                        order_type=evento.tipo_orden,
                        side=evento.direccion,
                        quantity=evento.cantidad,
                        price=evento.precio if hasattr(evento, 'precio') else None,
                        order_id=None,
                        status='REJECTED_STALE'
                    )
                    return None
            
            # CHECK 2: Circuit Breaker - Block if API is down
            if not self.check_api_health(evento.bolsa):
                print(f"[CIRCUIT BREAKER] Order blocked: {evento.bolsa} API circuit is OPEN")
                self._log_order_placement(
                    symbol=evento.nemo,
                    exchange=evento.bolsa,
                    order_type=evento.tipo_orden,
                    side=evento.direccion,
                    quantity=evento.cantidad,
                    price=evento.precio if hasattr(evento, 'precio') else None,
                    order_id=None,
                    status='BLOCKED_CIRCUIT_BREAKER'
                )
                return None
            
            # CHECK 3: Deduplication - Prevent duplicate orders
            price = evento.precio if hasattr(evento, 'precio') and evento.tipo_orden == 'LMT' else None
            if self.is_duplicate_order(evento.direccion, evento.cantidad, price, evento.bolsa):
                self._log_order_placement(
                    symbol=evento.nemo,
                    exchange=evento.bolsa,
                    order_type=evento.tipo_orden,
                    side=evento.direccion,
                    quantity=evento.cantidad,
                    price=price,
                    order_id=None,
                    status='REJECTED_DUPLICATE'
                )
                return None
            
            # CHECK 4: Rate Limiting - Prevent order floods
            if not self.check_rate_limit(evento.bolsa):
                print(f"[RATE LIMIT] Order delayed: {evento.bolsa} rate limit exceeded")
                # Don't reject, just delay slightly
                time.sleep(0.2)
            
            # CHECK 5: Minimum Notional Guard
            # Prevents asymmetric entries where one leg is rejected for insufficient notional.
            # Binance Futures requires >= 20 USDT notional; Bitget requires >= 5 USDT.
            # Closing orders (FUERA/reduce_only) are exempt from min notional on the exchange.
            is_closing = hasattr(evento, 'signal_type') and evento.signal_type == 'FUERA'
            if not is_closing:
                notional_result = self._enforce_min_notional(evento)
                if notional_result == 'SKIP':
                    return None  # order rejected — logged inside helper

            # ── On FUERA: cancel pending batch slices + open limit orders ────
            if is_closing:
                self.batch_scheduler.cancel_pair(self.lista_nemos)

            # ── BATCH intercept: entry orders with batch_n > 1 ───────────────
            # Routes to BatchLimitOrderScheduler instead of immediate execution.
            # FUERA / unwind orders always bypass this path (is_closing guard above).
            _batch_n  = getattr(evento, 'batch_n', 1)
            _is_entry = getattr(evento, 'signal_type', None) in ('LARGO', 'CORTO')
            if _batch_n > 1 and _is_entry:
                batch_id = self.batch_scheduler.schedule(evento)
                if batch_id:
                    return 'BATCH_SCHEDULED'
                # If schedule() returned None (no valid price), fall through to MKT

            print(f'Orden recibida en OMS: {evento.tipo_orden}-{evento.direccion} {evento.cantidad} {evento.nemo} @ {evento.precio} en {evento.bolsa}')
        
        if evento.bolsa in ['BINANCE', 'BINANCEFTS']:
            if not self.binance_handler:
                print(f"[ERROR] No Binance handler initialized")
                return None
            
            # ✅ REDUCE_ONLY LOGIC: Use reduce_only=True for closing orders (FUERA signals)
            # BUT only if there's actually a position to reduce on the exchange
            is_closing_order = hasattr(evento, 'signal_type') and evento.signal_type == 'FUERA'
            use_reduce_only = False  # Default: don't use reduce_only
            
            if is_closing_order:
                # Verify there's actually a position to close on the exchange
                # FIX A: Use the exchange's exact position quantity to avoid dust residuals
                try:
                    symbol = self.binance_handler.get_symbol(evento.nemo)
                    positions = self.binance_handler.get_position_info(symbol=symbol)
                    if positions is not None and len(positions) > 0:
                        # Check if there's a non-zero position for this symbol
                        for pos in positions:
                            pos_amt = float(pos.get('positionAmt', 0))
                            if abs(pos_amt) > 0:
                                use_reduce_only = True
                                # ✅ FIX A: Override order quantity with exchange's exact position
                                # This avoids the dust residual caused by format_quantity() rounding
                                original_qty = evento.cantidad
                                evento.cantidad = abs(pos_amt)
                                print(f"[REDUCE_ONLY] ✅ Position exists ({pos_amt}) - using reduce_only=True")
                                print(f"[REDUCE_ONLY] 🔧 Quantity overridden: {original_qty} → {abs(pos_amt)} (exchange exact)")
                                break
                        if not use_reduce_only:
                            print(f"[REDUCE_ONLY] ⚠️ No position to close for {symbol} - reduce_only=False")
                    else:
                        print(f"[REDUCE_ONLY] ⚠️ No positions found - reduce_only=False")
                except Exception as e:
                    print(f"[REDUCE_ONLY] ⚠️ Error checking positions: {e} - defaulting to reduce_only=False")
            
            if evento.tipo_orden == 'MKT':
                _strat_id = getattr(evento, 'signal_type', None) or 'PAIRS_TRADING'
                result = self.binance_handler.place_market_order(
                    side=evento.direccion,
                    quantity=evento.cantidad,
                    reduce_only=use_reduce_only,
                    strategy_id=_strat_id,
                    nemo=evento.nemo
                )
                # ── Pair circuit breaker tracking ──
                _is_rebalance = getattr(evento, 'signal_type', None) == 'REBALANCE'
                if not is_closing_order and not _is_rebalance:
                    if result is not None:
                        # Leg filled OK — store it in case the next
                        # leg fails and we need to unwind.
                        self._last_filled_leg = evento
                        self._pair_circuit_breaker['filled_leg'] = evento
                    else:
                        # Leg REJECTED — if we have a previously-filled
                        # leg, this is a second-leg failure → trip breaker.
                        if self._last_filled_leg is not None:
                            self._trip_pair_circuit_breaker(
                                evento,
                                "Binance rejected second leg "
                                "(place_market_order returned None)"
                            )
                        # Clear the tracking regardless
                        self._last_filled_leg = None
                else:
                    # Closing / unwind orders don't participate in
                    # pair tracking — clear after execution.
                    self._last_filled_leg = None
                return result
            
            elif evento.tipo_orden == 'LMT':
                _strat_id = getattr(evento, 'signal_type', None) or 'PAIRS_TRADING'
                return self.binance_handler.place_limit_order(
                    side=evento.direccion,
                    quantity=evento.cantidad,
                    price=evento.precio,
                    strategy_id=_strat_id,
                    nemo=evento.nemo
                )

        # ── BITGET PERPETUALS ROUTING ────────────────────────────
        elif evento.bolsa in ('BITGET', 'BITGETFTS'):
            if not self.bitget_handler:
                print(f"[ERROR] No Bitget handler initialized")
                return None

            # REDUCE_ONLY LOGIC: same pattern as Binance
            is_closing_order = hasattr(evento, 'signal_type') and evento.signal_type == 'FUERA'
            use_reduce_only = False

            if is_closing_order:
                try:
                    symbol = self.bitget_handler.get_symbol(evento.nemo)
                    positions = self.bitget_handler.get_position_info(symbol=symbol)
                    if positions:
                        for pos in positions:
                            pos_amt = float(pos.get('positionAmt', 0))
                            if abs(pos_amt) > 0:
                                use_reduce_only = True
                                # ✅ FIX A: Override order quantity with exchange's exact position
                                original_qty = evento.cantidad
                                evento.cantidad = abs(pos_amt)
                                print(f"[REDUCE_ONLY] ✅ Bitget position exists ({pos_amt}) — using reduce_only=True")
                                print(f"[REDUCE_ONLY] 🔧 Quantity overridden: {original_qty} → {abs(pos_amt)} (exchange exact)")
                                break
                    if not use_reduce_only:
                        print(f"[REDUCE_ONLY] ⚠️ No Bitget position to close — reduce_only=False")
                except Exception as e:
                    print(f"[REDUCE_ONLY] ⚠️ Error checking Bitget positions: {e}")

            if evento.tipo_orden == 'MKT':
                _strat_id = getattr(evento, 'signal_type', None) or 'PAIRS_TRADING'
                result = self.bitget_handler.place_market_order(
                    side=evento.direccion,
                    quantity=evento.cantidad,
                    reduce_only=use_reduce_only,
                    strategy_id=_strat_id,
                    nemo=evento.nemo,
                )
                # ── Pair circuit breaker tracking (Bitget) ──
                _is_rebalance = getattr(evento, 'signal_type', None) == 'REBALANCE'
                if not is_closing_order and not _is_rebalance:
                    if result is not None:
                        self._last_filled_leg = evento
                        self._pair_circuit_breaker['filled_leg'] = evento
                    else:
                        if self._last_filled_leg is not None:
                            self._trip_pair_circuit_breaker(
                                evento,
                                "Bitget rejected second leg "
                                "(place_market_order returned None)"
                            )
                        self._last_filled_leg = None
                else:
                    self._last_filled_leg = None
                return result

            elif evento.tipo_orden == 'LMT':
                _strat_id = getattr(evento, 'signal_type', None) or 'PAIRS_TRADING'
                return self.bitget_handler.place_limit_order(
                    side=evento.direccion,
                    quantity=evento.cantidad,
                    price=evento.precio,
                    reduce_only=use_reduce_only,
                    strategy_id=_strat_id,
                    nemo=evento.nemo,
                )
    
    def place_market_order_binance(self, symbol, side, quantity):
        """
        Place market order on Binance (delegates to spot or perp handler).
        
        Args:
            symbol: Trading symbol (legacy parameter, now uses self.lista_nemos)
            side: 'buy' or 'sell'
            quantity: Order quantity
        """
        if not self.binance_handler:
            print(f"[ERROR] No Binance handler initialized")
            return None
        
        return self.binance_handler.place_market_order(
            side=side,
            quantity=quantity,
            strategy_id='TRADING'
        )

    # Bitácora de trades Binance
    def get_binance_trades(self):
        """Get trade history from Binance (delegates to spot or perp handler)."""
        if not self.binance_handler:
            print(f"[ERROR] No Binance handler initialized")
            return pd.DataFrame()
        
        return self.binance_handler.get_trades()

    # Lanza orden Maker en Binance
    def place_limit_order_binance(self, side, quantity, price):
        """ Place limit order on Binance (delegates to spot or perp handler).
        
        Args:
            side: 'buy' or 'sell'
            quantity: Order quantity
            price: Limit price
        """
        if not self.binance_handler:
            print(f"[ERROR] No Binance handler initialized")
            return None
        
        return self.binance_handler.place_limit_order(
            side=side,
            quantity=quantity,
            price=price,
            strategy_id='TRADING'
        )

    # Check  all order status in Binance
    def check_order_status_binance(self):
        """Get open orders from Binance (delegates to spot or perp handler)."""
        if not self.binance_handler:
            print(f"[ERROR] No Binance handler initialized")
            return pd.DataFrame()
        
        return self.binance_handler.get_open_orders()
        
    # Handler for Binance balance
    def get_balance_binance(self) -> pd.DataFrame:
        """Get Binance balance (delegates to spot or perp handler)."""
        if not self.binance_handler:
            print(f"[ERROR] No Binance handler initialized")
            return pd.DataFrame()

        return self.binance_handler.get_balance()

    def get_position_info(self, symbol: str = None):
        """
        Get current position information from Binance.
        
        Args:
            symbol: Trading symbol (e.g., 'LINKUSDT'). If None, uses default.
            
        Returns:
            List of position dictionaries or None on error.
        """
        if not self.binance_handler:
            print(f"[ERROR] No Binance handler initialized")
            return None
        
        if hasattr(self.binance_handler, 'get_position_info'):
            return self.binance_handler.get_position_info(symbol=symbol)
        else:
            print(f"[ERROR] Binance handler does not support get_position_info")
            return None

    def get_balance_bitso(self) -> pd.DataFrame:
        """Get Bitso account balances as DataFrame. DEPRECATED — kept for backward compatibility."""
        print("[DEPRECATED] get_balance_bitso() — Bitso has been replaced by Bitget. Use get_balance_bitget().")
        return pd.DataFrame()

    def get_balance_bitget(self) -> pd.DataFrame:
        """Get Bitget account balances as DataFrame (delegates to bitget_handler)."""
        if not self.bitget_handler:
            print(f"[ERROR] No Bitget handler initialized")
            return pd.DataFrame()
        return self.bitget_handler.get_balance()
        
    # Get total balance in USDT across both exchanges
    def get_total_balance(self) -> pd.DataFrame:
        """Get total balance across Binance + Bitget as DataFrame with 'free' column."""
        try:
            # Get Binance balances
            binance_balance = self.get_balance_binance()
            
            # Get Bitget balances  
            bitget_balance = self.get_balance_bitget()
            
            # Combine balances
            total_balance = {}
            for nemo in self.lista_nemos:
                binance_free = 0.0
                bitget_free = 0.0
                
                if not binance_balance.empty and nemo in binance_balance.index:
                    binance_free = float(binance_balance.loc[nemo, 'free'])
                
                if not bitget_balance.empty and nemo in bitget_balance.index:
                    bitget_free = float(bitget_balance.loc[nemo, 'free'])
                
                total_balance[nemo] = {
                    'free': binance_free + bitget_free,
                    'locked': 0.0,  # Simplified for now
                    'total': binance_free + bitget_free
                }
            
            df = pd.DataFrame.from_dict(total_balance, orient='index')
            #print(f"Total combined balance: {df}")
            return df
            
        except Exception as e:
            print(f"Error getting total balance: {e}")
            # Return zero balances as fallback
            d = {}
            for nemo in self.lista_nemos:
                d[nemo] = {'free': 0.0, 'locked': 0.0, 'total': 0.0}
            return pd.DataFrame.from_dict(d, orient='index')

    def get_monthly_pnl_summary(self, start_date=None, end_date=None, initial_capital_cop=None, initial_capital_usdt=None):
        """
        Calculate monthly P&L summary across both exchanges.
        
        Based on:
        - Binance API: GET /api/v3/myTrades (returns all trades for a symbol)
        - Bitso API: GET /v3/user_trades/ (returns trade history)
        
        Args:
            start_date (str or datetime): Start date for the period (default: 30 days ago)
            end_date (str or datetime): End date for the period (default: now)
            initial_capital_cop (float): Starting COP balance (if None, uses current balance - net trades)
            initial_capital_usdt (float): Starting USDT balance (if None, uses current balance - net trades)
            
        Returns:
            dict: Summary with:
                - total_pnl_cop: Total P&L in COP
                - total_pnl_usdt: Total P&L in USDT  
                - net_change_cop: Net change in COP balance
                - net_change_usdt: Net change in USDT balance
                - total_fees_cop: Total fees paid in COP
                - total_fees_usdt: Total fees paid in USDT
                - trade_count: Number of trades executed
                - binance_trades: DataFrame of Binance trades
                - bitso_trades: DataFrame of Bitso trades
                - current_balances: Current account balances
                - period_start: Period start date
                - period_end: Period end date
        """
        # Set default date range (last 30 days)
        if end_date is None:
            end_date = datetime.datetime.now(timezone.utc)
        else:
            end_date = pd.to_datetime(end_date)
            if end_date.tzinfo is None:
                end_date = end_date.tz_localize('UTC')
                
        if start_date is None:
            start_date = end_date - timedelta(days=30)
        else:
            start_date = pd.to_datetime(start_date)
            if start_date.tzinfo is None:
                start_date = start_date.tz_localize('UTC')
        
        print(f"\n{'='*70}")
        print(f"MONTHLY P&L REPORT: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
        print(f"{'='*70}\n")
        
        # Get current balances
        current_balances = self.get_total_balance()
        current_cop = current_balances.loc['COP', 'total'] if 'COP' in current_balances.index else 0.0
        current_usdt = current_balances.loc['USDT', 'total'] if 'USDT' in current_balances.index else 0.0
        
        print(f"📊 Current Balances:")
        print(f"   USDT: {current_usdt:,.2f}")
        print(f"   COP:  {current_cop:,.2f}\n")
        
        # Get trades from both exchanges
        binance_trades = self.get_binance_trades()
        bitget_trades = self.get_bitget_trades()
        
        # Filter trades by date range
        if not binance_trades.empty:
            binance_trades = binance_trades[(binance_trades.index >= start_date) & 
                                           (binance_trades.index <= end_date)]
        
        if not bitget_trades.empty:
            bitget_trades = bitget_trades[(bitget_trades.index >= start_date) & 
                                       (bitget_trades.index <= end_date)]
        
        # Calculate metrics
        binance_trade_count = len(binance_trades) if not binance_trades.empty else 0
        bitget_trade_count = len(bitget_trades) if not bitget_trades.empty else 0
        total_trades = binance_trade_count + bitget_trade_count
        
        print(f"📈 Trade Activity:")
        print(f"   Binance: {binance_trade_count} trades")
        print(f"   Bitget:  {bitget_trade_count} trades")
        print(f"   Total:   {total_trades} trades\n")
        
        # Calculate fees
        binance_fees_usdt = 0.0
        binance_fees_cop = 0.0
        
        if not binance_trades.empty:
            for idx, trade in binance_trades.iterrows():
                fee = trade['commission']
                asset = trade['commissionAsset']
                if asset == 'USDT':
                    binance_fees_usdt += fee
                elif asset == 'COP':
                    binance_fees_cop += fee
                else:
                    # Convert to USDT equivalent if other asset
                    binance_fees_usdt += fee  # Simplified assumption
        
        bitget_fees_usdt = bitget_trades['commission'].sum() if not bitget_trades.empty else 0.0
        
        total_fees_usdt = binance_fees_usdt + abs(bitget_fees_usdt)
        total_fees_cop = binance_fees_cop
        
        print(f"💰 Fees Paid:")
        print(f"   USDT: {total_fees_usdt:,.4f}")
        print(f"   COP:  {total_fees_cop:,.2f}\n")
        
        # Calculate net traded amounts
        net_usdt_traded = 0.0
        net_cop_traded = 0.0
        
        if not binance_trades.empty:
            for idx, trade in binance_trades.iterrows():
                qty = trade['qty']
                quote_qty = trade['quoteQty']
                side = trade['side']
                
                if side == 'BUY':
                    net_usdt_traded += qty  # Bought USDT
                    net_cop_traded -= quote_qty  # Spent COP
                else:  # SELL
                    net_usdt_traded -= qty  # Sold USDT
                    net_cop_traded += quote_qty  # Received COP
        
        if not bitget_trades.empty:
            for idx, trade in bitget_trades.iterrows():
                qty = float(trade.get('qty', 0))
                price = float(trade.get('price', 0))
                side = str(trade.get('side', '')).upper()
                
                if side == 'BUY':
                    net_usdt_traded += qty
                else:
                    net_usdt_traded -= qty
        
        print(f"📊 Net Traded Amounts (excluding fees):")
        print(f"   USDT: {net_usdt_traded:+,.4f}")
        print(f"   COP:  {net_cop_traded:+,.2f}\n")
        
        # Calculate beginning balances if not provided
        if initial_capital_usdt is None:
            initial_capital_usdt = current_usdt - net_usdt_traded
            
        if initial_capital_cop is None:
            initial_capital_cop = current_cop - net_cop_traded
        
        print(f"📅 Beginning Balances (calculated):")
        print(f"   USDT: {initial_capital_usdt:,.2f}")
        print(f"   COP:  {initial_capital_cop:,.2f}\n")
        
        # Calculate net changes
        net_change_usdt = current_usdt - initial_capital_usdt
        net_change_cop = current_cop - initial_capital_cop
        
        print(f"📈 Net Change:")
        print(f"   USDT: {net_change_usdt:+,.4f} ({(net_change_usdt/initial_capital_usdt*100) if initial_capital_usdt != 0 else 0:.2f}%)")
        print(f"   COP:  {net_change_cop:+,.2f} ({(net_change_cop/initial_capital_cop*100) if initial_capital_cop != 0 else 0:.2f}%)\n")
        
        # Calculate P&L (including fees)
        total_pnl_usdt = net_change_usdt
        total_pnl_cop = net_change_cop
        
        print(f"{'='*70}")
        print(f"💵 TOTAL P&L:")
        print(f"   USDT: {total_pnl_usdt:+,.4f}")
        print(f"   COP:  {total_pnl_cop:+,.2f}")
        print(f"{'='*70}\n")
        
        return {
            'period_start': start_date,
            'period_end': end_date,
            'initial_capital_usdt': initial_capital_usdt,
            'initial_capital_cop': initial_capital_cop,
            'current_usdt': current_usdt,
            'current_cop': current_cop,
            'net_change_usdt': net_change_usdt,
            'net_change_cop': net_change_cop,
            'total_pnl_usdt': total_pnl_usdt,
            'total_pnl_cop': total_pnl_cop,
            'total_fees_usdt': total_fees_usdt,
            'total_fees_cop': total_fees_cop,
            'trade_count': total_trades,
            'binance_trade_count': binance_trade_count,
            'bitget_trade_count': bitget_trade_count,
            'binance_trades': binance_trades,
            'bitget_trades': bitget_trades,
            'current_balances': current_balances,
            'net_usdt_traded': net_usdt_traded,
            'net_cop_traded': net_cop_traded
        }

    # Check  all order status in Bitso
    def check_order_status_bitso(self):
        """Get open orders from Bitso and return as DataFrame indexed by order ID"""
        try:
            url = f"https://api.bitso.com/v3/open_orders/"
            headers = self.create_bitso_signed_headers('GET', '/v3/open_orders/')
            response = requests.get(url, headers=headers, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                
                # Check if response is successful
                if not data.get('success', False):
                    print(f"Bitso API returned error: {data.get('error', 'Unknown error')}")
                    return pd.DataFrame()
                
                # Get orders list
                orders_list = data.get('payload', [])
                
                # Handle case where orders is a list directly (no nested 'orders' key)
                if isinstance(orders_list, list):
                    if len(orders_list) == 0:
                        print("No open orders on Bitso.")
                        return pd.DataFrame()
                    orders_df = pd.DataFrame(orders_list)
                # Handle case where orders is nested under 'orders' key
                elif isinstance(orders_list, dict) and 'orders' in orders_list:
                    if len(orders_list['orders']) == 0:
                        print("No open orders on Bitso.")
                        return pd.DataFrame()
                    orders_df = pd.DataFrame(orders_list['orders'])
                else:
                    print(f"Unexpected Bitso response format: {type(orders_list)}")
                    return pd.DataFrame()
                
                # Set index and add exchange column
                if not orders_df.empty and 'oid' in orders_df.columns:
                    orders_df.set_index('oid', inplace=True)
                    orders_df['exchange'] = 'BITSO'
                    #print(f"Found {len(orders_df)} open orders on Bitso")
                    return orders_df
                else:
                    print("No open orders on Bitso.")
                    return pd.DataFrame()
            else:
                print(f"Error checking Bitso order status: {response.status_code}")
                print(f"Response: {response.text}")
                return pd.DataFrame()
                
        except Exception as e:
            print(f"Exception in check_order_status_bitso: {e}")
            import traceback
            traceback.print_exc()
            return pd.DataFrame()

    def check_order_status_bitget(self):
        """Get open orders from Bitget and return as DataFrame indexed by order ID."""
        if not self.bitget_handler:
            print(f"[ERROR] No Bitget handler initialized")
            return pd.DataFrame()
        return self.bitget_handler.get_open_orders()

    def get_bitget_trades(self):
        """Get trade history from Bitget (delegates to bitget_handler)."""
        if not self.bitget_handler:
            print(f"[ERROR] No Bitget handler initialized")
            return pd.DataFrame()
        return self.bitget_handler.get_trades()

    def get_bitget_lob(self, nemo: str = None, depth: int = 5) -> pd.DataFrame:
        """Get order book for Bitget exchange (delegates to bitget_handler)."""
        if not self.bitget_handler:
            print(f"[ERROR] No Bitget handler initialized")
            return pd.DataFrame()
        target_nemo = nemo if nemo else self.lista_nemos[0]
        return self.bitget_handler.get_orderbook(target_nemo, depth)

    def get_bitget_position_info(self, symbol: str = None):
        """Get current position information from Bitget."""
        if not self.bitget_handler:
            print(f"[ERROR] No Bitget handler initialized")
            return None
        return self.bitget_handler.get_position_info(symbol)

    # Función para colocar una orden límite en Bitso
    def place_limit_order_bitso(self, side, quantity, price):
        """Place a limit order on Bitso with proper authentication"""
        import json
        
        url = "https://api.bitso.com/v3/orders"  # No trailing slash
        book = f"{self.lista_nemos[0].lower()}_{self.lista_nemos[1].lower()}"
        
        # Create request body
        body_dict = {
            "book": book,
            "side": side,
            "type": "limit",
            "major": str(quantity),
            "price": str(price)

        }
        
        # Log order placement BEFORE sending
        self._log_order_placement(
            symbol=book,
            exchange='BITSO',
            order_type='LIMIT',
            side=side.upper(),
            quantity=quantity,
            price=price,
            order_id=None,
            status='SENDING'
        )
        
        # Convert to JSON string with exact formatting
        body_json = json.dumps(body_dict, separators=(',', ':'))
        
        # Create proper signed headers for POST request
        headers = self.create_bitso_signed_headers('POST', '/v3/orders', body_dict)
        
        # Send request with data parameter (not json parameter)
        response = requests.post(url, headers=headers, data=body_json, timeout=10)
        if response.status_code == 200:
            order = response.json()
            print(f"Limit order placed: {order}")
            
            # Record API success
            self.record_api_success('BITSO')
            
            # Log order acceptance with order ID
            if order and 'payload' in order:
                order_id = order['payload'].get('oid', 'N/A')
                self._log_order_placement(
                    symbol=book,
                    exchange='BITSO',
                    order_type='LIMIT',
                    side=side.upper(),
                    quantity=quantity,
                    price=price,
                    order_id=str(order_id),
                    status='ACCEPTED'
                )
            
            #self.monitor_bitso_orders()
            return order
        else:
            print(f"Error placing limit order: {response.status_code}, {response.text}")
            # Record API error
            self.record_api_error('BITSO', f"HTTP {response.status_code}: {response.text}")
            # Log rejection
            self._log_order_placement(
                symbol=book,
                exchange='BITSO',
                order_type='LIMIT',
                side=side.upper(),
                quantity=quantity,
                price=price,
                order_id=None,
                status=f'REJECTED: {response.status_code}'
            )
            return None

    # Función para colocar una orden de mercado en Bitso
    def place_market_order_bitso(self, side, quantity):
        """Place a market order on Bitso with proper authentication"""
        import json
        
        url = "https://api.bitso.com/v3/orders"  # No trailing slash
        book = f"{self.lista_nemos[0].lower()}_{self.lista_nemos[1].lower()}"
        
        # Create request body
        body_dict = {
            "book": book,
            "side": side,
            "type": "market",
            "major": str(quantity)
        }
        
        # Log order placement BEFORE sending
        self._log_order_placement(
            symbol=book,
            exchange='BITSO',
            order_type='MARKET',
            side=side.upper(),
            quantity=quantity,
            price=None,
            order_id=None,
            status='SENDING'
        )
        
        # Convert to JSON string with exact formatting
        body_json = json.dumps(body_dict, separators=(',', ':'))
        
        # Create proper signed headers for POST request
        headers = self.create_bitso_signed_headers('POST', '/v3/orders', body_dict)
        
        # Send request with data parameter (not json parameter)
        response = requests.post(url, headers=headers, data=body_json, timeout=10)
        if response.status_code == 200:
            order = response.json()
            print(f"Market order placed: {order}")
            
            # Record API success
            self.record_api_success('BITSO')
            
            # Log order acceptance with order ID
            if order and 'payload' in order:
                order_id = order['payload'].get('oid', 'N/A')
                self._log_order_placement(
                    symbol=book,
                    exchange='BITSO',
                    order_type='MARKET',
                    side=side.upper(),
                    quantity=quantity,
                    price=None,
                    order_id=str(order_id),
                    status='ACCEPTED'
                )
            
            return order
        else:
            print(f"Error placing market order: {response.status_code}, {response.text}")
            # Record API error
            self.record_api_error('BITSO', f"HTTP {response.status_code}: {response.text}")
            # Log rejection
            self._log_order_placement(
                symbol=book,
                exchange='BITSO',
                order_type='MARKET',
                side=side.upper(),
                quantity=quantity,
                price=None,
                order_id=None,
                status=f'REJECTED: {response.status_code}'
            )
            return None

    # Función para obtener detalles de una orden específica en Binance
    def get_order(self,symbol, orderId):
        try:
            order = self.taker.get_order(symbol=symbol, orderId=orderId)
            return order
        except Exception as e:
            print(f"Error getting order: {e}")
            return None
        
    # Función para revisar el estado de una orden
    def check_order_status(self,symbol, orderId):
        try:
            order = self.get_order(symbol=symbol, orderId=orderId)
            print(f"Order status: {order}")
            return order
        except Exception as e:
            print(f"Error checking order status: {e}")
            return None

    # Función para cancelar una orden (detecta automáticamente el exchange)
    def cancel_order(self, symbol=None, orderId=None, exchange=None):
        """
        Cancel an order on the appropriate exchange.
        
        Args:
            symbol (str): Trading symbol (required for Binance)
            orderId (str): Order ID
            exchange (str): Exchange name ('BINANCE' or 'BITSO')
        """
        try:
            if exchange in ('BITGET', 'BITGETFTS'):
                if not self.bitget_handler:
                    print(f"[ERROR] No Bitget handler initialized")
                    return None
                bg_symbol = self.bitget_handler.get_symbol(symbol) if symbol and 'USDT' not in symbol.upper() else symbol
                result = self.bitget_handler.cancel_order(orderId, bg_symbol)
                print(f"Order cancelled on Bitget: {result}")
                return result
            elif exchange == 'BITSO':
                # DEPRECATED: Bitso has been replaced by Bitget
                print(f"[DEPRECATED] cancel_order for BITSO — use BITGET")
                return None
            elif exchange == 'BINANCE':
                # Delegate to handler
                if not self.binance_handler:
                    print(f"[ERROR] No Binance handler initialized")
                    return None
                
                result = self.binance_handler.cancel_order(orderId, symbol)
                print(f"Order cancelled on Binance: {result}")
                return result
            else:
                # Fallback to Binance for backward compatibility
                print(f"Warning: exchange not specified, defaulting to Binance")
                if not symbol:
                    print(f"Error: symbol is required for Binance order cancellation")
                    return None
        except Exception as e:
            print(f"Error cancelling order: {e}")
            
            # Record API error for circuit breaker tracking
            if exchange:
                self.record_api_error(exchange, f"Cancel exception: {str(e)}")
            elif symbol:  # Fallback to Binance
                self.record_api_error('BINANCE', f"Cancel exception: {str(e)}")
            
            return None

    # Funcion para modificar el precio de una orden abierta
    def modify_order_price(self,symbol, orderId, new_price):

        try:
            # Cancelar la orden existente
            cancel_result = self.cancel_order(symbol, orderId)
            if cancel_result:
                # Recolocar la orden con el nuevo precio
                new_order = self.taker.create_order(
                    symbol=symbol,
                    side=cancel_result['side'],
                    type='LIMIT',
                    timeInForce='GTC',
                    quantity=cancel_result['origQty'],
                    price=new_price
                )
                print(f"Order modified: {new_order}")
                return new_order
            else:
                print("Failed to cancel the existing order.")
                return None
        except Exception as e:
            print(f"Error modifying order price: {e}")
            return None
                
    # Consolida órdenes abiertas en Binance y Bitget
    def check_all_order_status(self):
        # print("Checking Binance orders:")
        binance_symbol = self.get_binance_symbol()
        binance_orders = self.check_order_status_binance()
        # print("Checking Bitget orders:")
        bitget_orders = self.check_order_status_bitget()
        return binance_orders, bitget_orders
    
    # Combina todas las ordenes en un solo DF
    def get_all_open_orders(self, symbol=None):
        """
        Get all open orders from both exchanges as a unified DataFrame.
        
        Args:
            symbol (str, optional): Trading symbol for Binance orders (not required, will use default)
            
        Returns:
            pandas.DataFrame: Unified DataFrame with all open orders indexed by order_id
        """
        try:
            # Get orders from both exchanges - always check Binance
            binance_df = self.check_order_status_binance()
            bitget_df = self.check_order_status_bitget()
            
            # If both DataFrames are empty, return empty DataFrame
            if binance_df.empty and bitget_df.empty:
                print("No open orders found.")
                return None
            
            # Combine DataFrames
            all_orders = []
            
            if not binance_df.empty:
                all_orders.append(binance_df)
            
            if not bitget_df.empty:
                all_orders.append(bitget_df)
            
            # Concatenate all orders
            unified_df = pd.concat(all_orders, ignore_index=False)
            
            # Ensure consistent column order and types
            standard_columns = ['symbol', 'side', 'type', 'quantity', 'price', 'status', 'time', 'exchange']
            for col in standard_columns:
                if col not in unified_df.columns:
                    unified_df[col] = None
            
            return unified_df[standard_columns]
            
        except Exception as e:
            print(f"Error getting all open orders: {e}")
            return pd.DataFrame()

    # Bitácora de trades Bitso
    def get_bitso_trades(self):
        """Get trade history from Bitso."""
        url = "https://api.bitso.com/v3/user_trades/"
        headers = self.create_bitso_signed_headers('GET', '/v3/user_trades/')
        
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                
                # Debug: Check response structure
                if not isinstance(data, dict):
                    print(f"[ERROR] Bitso trades response is not a dict, got: {type(data)}")
                    return pd.DataFrame()
                
                if 'payload' not in data:
                    print(f"[ERROR] No 'payload' in Bitso response. Keys: {data.keys()}")
                    return pd.DataFrame()
                
                payload = data['payload']
                
                # Handle two possible response formats:
                # 1. payload is a list (trades directly)
                # 2. payload is a dict with 'trades' key
                if isinstance(payload, list):
                    trades = payload
                elif isinstance(payload, dict):
                    if 'trades' not in payload:
                        print(f"[ERROR] No 'trades' in payload dict. Keys: {payload.keys()}")
                        return pd.DataFrame()
                    trades = payload['trades']
                else:
                    print(f"[ERROR] payload is neither list nor dict, got: {type(payload)}")
                    return pd.DataFrame()
                
                if not isinstance(trades, list):
                    print(f"[ERROR] trades is not a list, got: {type(trades)}")
                    return pd.DataFrame()
                
                # If no trades, return empty DataFrame
                if len(trades) == 0:
                    return pd.DataFrame()
                
                # Debug: Check first trade structure (only print once)
                # if trades:
                #     print(f"[DEBUG] First trade keys: {trades[0].keys()}")
                
                trades_list = []
                for trade in trades:
                    try:
                        # Parse created_at and ensure it's timezone-aware (UTC)
                        created_at = trade.get('created_at')
                        if created_at:
                            trade_time = pd.to_datetime(created_at)
                            # Make sure it's UTC timezone-aware
                            if trade_time.tzinfo is None:
                                trade_time = trade_time.tz_localize('UTC')
                        else:
                            trade_time = pd.Timestamp.now(tz='UTC')
                        
                        trades_list.append({
                            'time': trade_time,
                            'tradeId': trade.get('tid', trade.get('trade_id', 'unknown')),
                            'orderId': trade.get('oid', trade.get('order_id', 'unknown')),
                            'price': float(trade.get('price', 0)),
                            'amount': float(trade.get('major', trade.get('amount', 0))),
                            'fee': float(trade.get('fees_amount', trade.get('fee', 0))),
                            'side': trade.get('side', trade.get('maker_side', 'unknown'))
                        })
                    except Exception as e:
                        timeStamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                        print(f"{timeStamp}[ERROR] Failed to parse trade: {e}")
                        print(f"{timeStamp} [ERROR] Trade data: {trade}")
                        continue
                
                if not trades_list:
                    print("[WARNING] No valid trades parsed")
                    return pd.DataFrame()
                
                trade_df = pd.DataFrame(trades_list)
                trade_df.set_index('time', inplace=True)
                return trade_df
            else:
                print(f"[ERROR] Bitso trades API error: {response.status_code}")
                try:
                    print(f"[ERROR] Response: {response.json()}")
                except:
                    print(f"[ERROR] Response text: {response.text[:500]}")
                return pd.DataFrame()
        
        except Exception as e:
            print(f"[ERROR] Exception in get_bitso_trades: {e}")
            import traceback
            traceback.print_exc()
            return pd.DataFrame()

    # Monitor de ordenes abiertas Bitso con EventoCalce
    def monitor_bitso_orders(self, interval=60):
        """
        Monitor open Bitso orders and create EventoCalce for fills.
        Similar to Binance WebSocket monitoring but using REST API polling.
        
        Args:
            interval (int): Seconds between status checks
            
        Returns:
            threading.Thread: The monitoring thread
        """
        def monitor():
            previous_orders = {}
            base_asset = self.lista_nemos[0]  # USDT
            
            while True:
                try:
                    # Get open orders
                    open_orders = self.check_order_status_bitso()   
                    
                    if not open_orders.empty:
                        for order_id, order in open_orders.iterrows():
                            # Calculate filled quantity
                            filled_qty = float(order.get('original_amount', 0)) - float(order.get('unfilled_amount', 0))
                            total_qty = float(order.get('original_amount', 0))
                            status = order.get('status')
                            side = order.get('side', '').upper()
                            price = float(order.get('price', 0))
                            
                            # Initialize tracking
                            if order_id not in previous_orders:
                                previous_orders[order_id] = {'filled_qty': 0}
                            
                            # Detect new fills
                            prev_filled = previous_orders[order_id]['filled_qty']
                            new_fill_qty = filled_qty - prev_filled
                            
                            if new_fill_qty > 0:
                                # Create EventoCalce for new fill
                                fill_event = EventoCalce(
                                    iTiempo=datetime.utcnow(),
                                    nemo=base_asset,
                                    bolsa='BITSO',
                                    cantidad=new_fill_qty,
                                    direccion=side,
                                    precioCalce=price,
                                    comision=0.0
                                )
                                
                                # Send to HIGH PRIORITY queue
                                if hasattr(self.eventos, 'add_high_priority_event'):
                                    self.eventos.add_high_priority_event(fill_event)
                                    print(f"⚡ HIGH PRIORITY: BITSO {side} {new_fill_qty} @ {price}")
                                else:
                                    self.eventos.put(fill_event)
                                
                                print(f"[FILL EVENT] BITSO {side} {new_fill_qty} @ {price}")
                                previous_orders[order_id]['filled_qty'] = filled_qty
                            
                            # Clean up completed orders
                            if status in ['complete', 'cancelled']:
                                previous_orders.pop(order_id, None)
                    
                    # Clean up stale tracking
                    current_ids = set(open_orders.index) if not open_orders.empty else set()
                    for order_id in list(previous_orders.keys()):
                        if order_id not in current_ids:
                            previous_orders.pop(order_id, None)
                            
                except Exception as e:
                    print(f"[ERROR] Bitso monitoring: {e}")
                time.sleep(interval)
        
        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        # print(f"[MONITOR] Bitso order monitor started (interval: {interval}s)")
        return thread

    def _create_fill_event(self, symbol, exchange, quantity, direction, fill_cost, commission):
        """
        Create and queue a fill event when an order is executed.
        
        Args:
            symbol (str): Trading symbol
            exchange (str): Exchange name
            quantity (float): Filled quantity
            direction (str): 'BUY' or 'SELL'
            fill_cost (float): Total cost of the fill
            commission (float): Commission paid
        """
        try:
            # Calculate average price per unit
            avg_price = fill_cost / quantity if quantity != 0 else 0
            
            # Log fill event to CSV
            self._log_fill_event(
                symbol=symbol,
                exchange=exchange,
                order_id='N/A',  # Order ID may not be available in this context
                side=direction.upper(),
                quantity=quantity,
                price=avg_price,
                commission=commission,
                response_type='FILL'
            )
            
            # Create EventoCalce with correct parameters matching the class definition
            fill_event = EventoCalce(
                iTiempo=datetime.utcnow(),
                nemo=symbol,
                bolsa=exchange,
                cantidad=quantity,
                direccion=direction.lower(),  # Ensure lowercase for consistency
                precioCalce=avg_price,
                comision=commission
            )
            
            # Add to high priority queue (fills are critical)
            if hasattr(self.eventos, 'add_high_priority_event'):
                self.eventos.add_high_priority_event(fill_event)
                print(f"⚡ HIGH PRIORITY: Fill event queued: {direction} {quantity} {symbol} @ {avg_price:.2f}")
            else:
                self.eventos.put(fill_event)
                print(f"📤 Fill event queued: {direction} {quantity} {symbol} @ {avg_price:.2f}")
            
        except Exception as e:
            print(f"❌ Error creating fill event: {e}")
            import traceback
            traceback.print_exc()

    # Alimenta bitàcora de transacciones
    def log_fill(self, order, exchange):
        """
        Log fill to in-memory lists and CSV file.
        
        Args:
            order (dict): Order data from exchange
            exchange (str): 'binance' or 'bitso'
        """
        if exchange == "binance":
            self.binance_fills.append(order)
        elif exchange == "bitget":
            self.bitget_fills.append(order)
        
        print(f"Logged fill for {exchange}: {order}")
        
        # Log to CSV if order contains necessary information
        try:
            if exchange == "binance":
                # Binance order structure
                self._log_fill_event(
                    symbol=order.get('symbol', 'UNKNOWN'),
                    exchange='BINANCE',
                    order_id=str(order.get('orderId', 'N/A')),
                    side=order.get('side', 'UNKNOWN'),
                    quantity=float(order.get('executedQty', 0)),
                    price=float(order.get('price', 0)),
                    commission=float(order.get('commission', 0)),
                    response_type=order.get('status', 'FILL')
                )
            elif exchange == "bitget":
                # Bitget order structure (from adapter)
                self._log_fill_event(
                    symbol=order.get('symbol', 'UNKNOWN'),
                    exchange='BITGET',
                    order_id=str(order.get('orderId', 'N/A')),
                    side=order.get('side', 'UNKNOWN').upper(),
                    quantity=float(order.get('size', order.get('qty', 0))),
                    price=float(order.get('price', 0)),
                    commission=float(order.get('fee', 0)),
                    response_type=order.get('status', 'FILL')
                )
        except Exception as e:
            print(f"[ERROR] Failed to log fill to CSV: {e}")

    # Cancela todas las órdenes abiertas para un símbolo dado        
    def cancel_all_orders(self):
        """Cancela todas las órdenes abiertas para un símbolo dado."""
        try:
            open_orders = self.get_all_open_orders()
            # Check if DataFrame is None or empty
            if open_orders is None or open_orders.empty:
                print("No open orders to cancel.")
                return
            
            # Iterate through DataFrame rows
            for idx, order in open_orders.iterrows():
                # Get order ID (use index which is the order ID)
                order_id = idx
                # Get exchange information from the order
                exchange = order.get('exchange', 'BINANCE')
                # Get symbol (needed for Binance, not for Bitso)
                symbol = order.get('symbol', 'USDTCOP')
                
                # Cancel order on the appropriate exchange
                cancel_result = self.cancel_order(symbol=symbol, orderId=order_id, exchange=exchange)
                print(f"Cancelled order {order_id} on {exchange}: {cancel_result}")
        except Exception as e:
            print(f"Error cancelling orders: {e}")
            import traceback
            traceback.print_exc()

    def get_mid_price_binance(self, symbol):
        """Obtiene el precio medio (mid price) actual de Binance para un símbolo dado."""
        try:
            ticker = self.taker.get_ticker(symbol=symbol)
            bid = float(ticker['bidPrice'])
            ask = float(ticker['askPrice'])
            mid_price = (bid + ask) / 2
            print(f"Mid price for {symbol} on Binance: {mid_price}")
            return mid_price
        except Exception as e:
            print(f"Error fetching mid price for {symbol}: {e}")
            return None
        
    def get_bitso_lob(self, depth: int = 1) -> pd.DataFrame:
        """ Get order book for Bitso exchange """
        import requests
        import json
        import pandas as pd
        print("Fetching order book for Bitso...")
        url = "https://api.bitso.com/v3/order_book/?book=usdt_cop"
        payload = {}
        headers = {}
        try:
            response = requests.request("GET", url, headers=headers, data=payload)
        except Exception as e:
            raise ValueError(f"Error fetching order book for Bitso: {e}")
        # Valida si la respuesta esta vacia o es un error
        if response.status_code != 200:
            raise ValueError(f"Error fetching order book for Bitso: {response.status_code} - {response.text}")
        else:
            data = json.loads(response.text)
            bids = pd.json_normalize(data, record_path=[['payload','bids']])
            bids.columns = ['bid_price', 'bid_size', 'bid_orders']
            asks = pd.json_normalize(data, record_path=[['payload','asks']])
            asks.columns = ['ask_price', 'ask_size', 'ask_orders']
            df_orderbook = pd.concat([bids, asks], axis=1)
            print("Order book for Bitso fetched.")
        return df_orderbook.head(depth)
    
    def get_binance_lob(self, depth: int = 1) -> pd.DataFrame:
        """ Get order book for Binance exchange """
        import requests
        import json
        import pandas as pd
        print("Fetching order book for Binance...")
        url = "https://api.binance.com/api/v3/depth?symbol=USDTCOP&limit=20"
        payload = {}
        headers = {}
        try:
            response = requests.request("GET", url, headers=headers, data=payload)
        except Exception as e:
            raise ValueError(f"Error fetching order book for Binance: {e}")
        # Valida si la respuesta esta vacia o es un error
        if response.status_code != 200:
            raise ValueError(f"Error fetching order book for Binance: {response.status_code} - {response.text}")
        else:
            data = json.loads(response.text)
            bids = pd.json_normalize(data, record_path=[['bids']])
            bids.columns = ['bid_price', 'bid_size']
            asks = pd.json_normalize(data, record_path=[['asks']])
            asks.columns = ['ask_price', 'ask_size']
            df_orderbook = pd.concat([bids, asks], axis=1)
            print("Order book for Binance fetched.")
        return df_orderbook.head(depth)

    # Bitacora combinada de trades Binance y Bitget
    def get_all_trades(self,symbol='USDTCOP'):
        """Get combined trade history from Binance and Bitget."""
        binance_trades = self.get_binance_trades()
        bitget_trades = self.get_bitget_trades()
        
        if not binance_trades.empty:
            binance_trades['exchange'] = 'Binance'
        if not bitget_trades.empty:
            bitget_trades['exchange'] = 'Bitget'
        
        combined_trades = pd.concat([binance_trades, bitget_trades])
        combined_trades.sort_index(inplace=True)
        
        return combined_trades
 
    # Start order monitor in background thread (non-blocking)
    def start_order_monitor_thread(self, order_ids, timeout=360):
        """
        Start order monitoring in a background thread (non-blocking).
        
        Args:
            order_ids (list): List of order IDs to monitor
            timeout (int): Maximum monitoring time
            
        Returns:
            threading.Thread: The monitoring thread
        """
        # Validate input
        if not order_ids:
            print("[ERROR] No order IDs provided to monitor thread")
            return None
        
        # Ensure order_ids is a list
        if not isinstance(order_ids, (list, tuple)):
            order_ids = [order_ids]
        
        def run_monitor():
            # Create new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                result = loop.run_until_complete(
                    self.monitor_multiple_orders(order_ids, timeout)
                )
                # print(f"[MONITOR] Background monitor result: {result}")
            except Exception as e:
                print(f"[ERROR] Monitor thread exception: {e}")
                import traceback
                traceback.print_exc()
            finally:
                loop.close()
        
        monitor_thread = threading.Thread(target=run_monitor, daemon=True)
        monitor_thread.start()
        # print(f"[MONITOR] Order monitor started in background thread for {len(order_ids)} orders")
        
        return monitor_thread

    def monitor_orders_with_polling(self, check_interval:int=30):
        """
        Monitor open orders from BOTH Binance and Bitso via REST API polling.
        Creates EventoCalce when fills occur on either exchange.
        
        Features:
        - Monitors both exchanges simultaneously
        - Detects partial and full fills
        - Sends EventoCalce to HIGH PRIORITY queue
        - Handles different response formats per exchange
        
        Args:
            check_interval (int): Seconds between status checks
            
        Returns:
            threading.Thread: Monitoring thread
        """
        def monitor_loop():
            # Track previous state to detect new fills
            # Key format: "EXCHANGE_orderid" to avoid conflicts
            previous_orders = {}
            cycle_count = 0
            
            # print(f"[POLLING] Started UNIFIED order monitoring (interval: {check_interval}s)")
            # print(f"[POLLING] Monitoring: BINANCE + BITSO")
            
            while True:
                try:
                    cycle_count += 1
                    # print(f"\n{'='*80}")
                    # print(f"[POLLING CYCLE #{cycle_count}] Starting check...")
                    # print(f"{'='*80}")
                    
                    # Initialize as empty DataFrames to avoid NameError
                    binance_orders = pd.DataFrame()
                    bitso_orders = pd.DataFrame()
                    
                    # ========== MONITOR BINANCE (ONLY IF CONFIGURED) ==========
                    if 'BINANCE' in self.lista_bolsas or 'BINANCEFTS' in self.lista_bolsas:
                        try:
                            binance_orders = self.check_order_status_binance()
                            
                            # print(f"[BINANCE POLL] Checking orders... Found {len(binance_orders) if not binance_orders.empty else 0} open orders")
                            
                            if not binance_orders.empty:
                                for order_id, order in binance_orders.iterrows():
                                    tracking_key = f"BINANCE_{order_id}"
                                    
                                    # Extract symbol from order and get base asset (remove USDT suffix)
                                    order_symbol = order.get('symbol', '')  # e.g., 'LINKUSDT' or 'AVAXUSDT'
                                    nemo = order_symbol.replace('USDT', '') if order_symbol else self.lista_nemos[0]
                                    
                                    # ✅ FILTER: Skip fills for symbols not in lista_nemos
                                    if not self.is_tracked_symbol(nemo):
                                        print(f"⚠️  [FILTER] Ignoring BINANCE order for non-tracked symbol: {nemo}")
                                        continue
                                    
                                    # Get filled quantity
                                    filled_qty = float(order.get('executedQty', 0))
                                    total_qty = float(order.get('origQty', 0))
                                    status = order.get('status')
                                    side = order.get('side')
                                    price = float(order.get('price', 0))
                                    
                                    # Debug: Show all order details
                                    print(f"[BINANCE ORDER] ID={order_id}, status={status}, filled={filled_qty}/{total_qty}, side={side}, price={price}")
                                    
                                    # Initialize tracking if new order
                                    is_new_order = tracking_key not in previous_orders
                                    if is_new_order:
                                        #print(f"[BINANCE NEW] First time seeing order {order_id}")
                                        previous_orders[tracking_key] = {
                                            'filled_qty': 0,
                                            'exchange': 'BINANCE',
                                            'side': side,
                                            'price': price,
                                            'nemo': nemo,  # Store nemo for cancellation events
                                            'first_seen': datetime.datetime.now(timezone.utc)  # Track order age
                                        }
                                
                                # Check order age and auto-cancel if too old
                                order_age = (datetime.datetime.now(timezone.utc) - previous_orders[tracking_key].get('first_seen', datetime.datetime.now(timezone.utc))).total_seconds()
                                if order_age > self.MAX_ORDER_AGE_SECONDS:
                                    print(f"[STALE ORDER] ⏰ BINANCE {side} order {order_id} is {order_age/60:.1f} minutes old - auto-cancelling")
                                    try:
                                        self.cancel_order(symbol=self.get_binance_symbol(), orderId=order_id, exchange='BINANCE')
                                        print(f"[STALE ORDER] ✅ Auto-cancelled BINANCE order {order_id}")
                                    except Exception as e:
                                        print(f"[STALE ORDER] ⚠️  Failed to cancel BINANCE order {order_id}: {e}")
                                    continue  # Skip further processing of this order
                                
                                # Detect NEW fills
                                prev_filled = previous_orders[tracking_key]['filled_qty']
                                new_fill_qty = filled_qty - prev_filled
                                
                                # print(f"[BINANCE FILL CHECK] Order {order_id}: prev_filled={prev_filled}, current_filled={filled_qty}, new_fill={new_fill_qty}")
                                
                                if new_fill_qty > 0:
                                    # NEW BINANCE FILL!
                                    # print(f"[BINANCE FILL DETECTED] ⚡ {side} {new_fill_qty} @ {price}")
                                    
                                    # Log fill to CSV
                                    self._log_fill_event(
                                        symbol=order_symbol,
                                        exchange='BINANCE',
                                        order_id=str(order_id),
                                        side=side,
                                        quantity=new_fill_qty,
                                        price=price,
                                        commission=0.0,  # Commission details may need separate query
                                        response_type='PARTIAL_FILL' if filled_qty < total_qty else 'FILL'
                                    )
                                    
                                    fill_event = EventoCalce(
                                        iTiempo=datetime.datetime.now(timezone.utc),
                                        nemo=nemo,  # Use extracted base asset (LINK or AVAX)
                                        bolsa='BINANCE',
                                        cantidad=new_fill_qty,
                                        direccion=side,
                                        precioCalce=price,
                                        comision=None
                                    )
                                    
                                    # Send to HIGH PRIORITY queue
                                    if hasattr(self.eventos, 'add_high_priority_event'):
                                        self.eventos.add_high_priority_event(fill_event)
                                        #print(f"[BINANCE FILL] ✅ Fill event sent to HIGH PRIORITY queue")
                                    else:
                                        self.eventos.put(fill_event)
                                        #print(f"[BINANCE FILL] ✅ Fill event sent to regular queue")

                                    print(f"[FILL EVENT][POLLING] BINANCE {side} {new_fill_qty} {nemo} @ {price} (total: {filled_qty}/{total_qty})")
                                    
                                    # Record polling fill in metrics
                                    if self.binance_handler and hasattr(self.binance_handler, 'record_polling_fill'):
                                        self.binance_handler.record_polling_fill()

                                    # Update tracked quantity
                                    previous_orders[tracking_key]['filled_qty'] = filled_qty
                                
                                # Remove completed orders and generate events for cancellations
                                if status in ['FILLED', 'CANCELED', 'EXPIRED', 'REJECTED']:
                                    if status == 'FILLED':
                                        print(f"[BINANCE COMPLETE] Order {order_id} fully filled - removing from tracking")
                                        pass
                                    else:
                                        # print(f"[BINANCE {status}] Order {order_id} - generating cancellation event")
                                        pass
                                        
                                        # Generate EventoCalce with quantity=0 to signal cancellation
                                        # This will reset the order_placed flags in livetest.py
                                        cancel_event = EventoCalce(
                                            iTiempo=datetime.datetime.now(timezone.utc),
                                            nemo=self.get_binance_symbol(),
                                            bolsa='BINANCE',
                                            cantidad=0,  # Zero quantity indicates cancellation
                                            direccion=side,
                                            precioCalce=price,
                                            comision=None,
                                            tipo="MAKER"
                                        )
                                        
                                        if hasattr(self.eventos, 'add_high_priority_event'):
                                            self.eventos.add_high_priority_event(cancel_event)
                                            print(f"[CANCEL EVENT] ⚡ BINANCE {side} order cancelled - event sent to HIGH PRIORITY queue")
                                        else:
                                            self.eventos.put(cancel_event)
                                    
                                    previous_orders.pop(tracking_key, None)
                        
                            # Check for orders that disappeared (filled/cancelled between polls)
                            # These won't be in binance_orders but are in previous_orders
                            current_binance_ids = set(binance_orders.index) if not binance_orders.empty else set()
                            tracked_binance_ids = {k.replace('BINANCE_', '') for k in previous_orders.keys() if k.startswith('BINANCE_')}
                            disappeared_ids = tracked_binance_ids - current_binance_ids
                            
                            # ALSO check my_trades for completed fills that disappeared from open_orders
                            # This catches fills that happened and were immediately removed from open orders
                            try:
                                binance_trades = self.get_binance_trades()
                                if binance_trades is not None and not binance_trades.empty:
                                    # Get trades from the last interval + 5 seconds buffer
                                    # Use timezone-aware datetime for comparison
                                    recent_cutoff = pd.Timestamp.now(tz='UTC') - timedelta(seconds=check_interval + 5)
                                    recent_trades = binance_trades[binance_trades.index > recent_cutoff]
                                    
                                    for trade_time, trade_row in recent_trades.iterrows():
                                        # trade_row is a pandas Series, access with [] or .get()
                                        trade_id = trade_row['id']
                                        tracking_key = f"BINANCE_TRADE_{trade_id}"
                                        
                                        # Skip if we already processed this trade
                                        if tracking_key in previous_orders:
                                            continue
                                        
                                        # New trade detected!
                                        # Extract symbol and get base asset (remove USDT suffix)
                                        trade_symbol = trade_row.get('symbol', '')  # e.g., 'LINKUSDT' or 'AVAXUSDT'
                                        nemo = trade_symbol.replace('USDT', '') if trade_symbol else self.lista_nemos[0]
                                        
                                        # ✅ FILTER: Skip fills for symbols not in lista_nemos
                                        if not self.is_tracked_symbol(nemo):
                                            print(f"⚠️  [FILTER] Ignoring BINANCE trade for non-tracked symbol: {nemo}")
                                            previous_orders[tracking_key] = {'processed': True}  # Mark as seen to avoid re-logging
                                            continue
                                        
                                        # Log fill to CSV
                                        self._log_fill_event(
                                            symbol=trade_symbol,
                                            exchange='BINANCE',
                                            order_id=str(trade_row['orderId']),
                                            side=str(trade_row['side']).upper(),
                                            quantity=float(trade_row['qty']),
                                            price=float(trade_row['price']),
                                            commission=float(trade_row['commission']),
                                            response_type='FILL'
                                        )
                                        
                                        fill_event = EventoCalce(
                                            iTiempo=trade_time,
                                            nemo=nemo,  # Use extracted base asset (LINK or AVAX)
                                            bolsa='BINANCE',
                                            cantidad=float(trade_row['qty']),
                                            direccion=str(trade_row['side']).upper(),
                                            precioCalce=float(trade_row['price']),
                                            comision=float(trade_row['commission']),
                                            tipo="TAKER" if trade_row.get('isMaker', False) == False else "MAKER"
                                        )
                                        
                                        # Send to HIGH PRIORITY queue
                                        if hasattr(self.eventos, 'add_high_priority_event'):
                                            self.eventos.add_high_priority_event(fill_event)
                                        else:
                                            self.eventos.put(fill_event)
                                        
                                        print(f"[FILL EVENT][TRADES] BINANCE {str(trade_row['side']).upper()} {trade_row['qty']} {nemo} @ {trade_row['price']} (order: {trade_row['orderId']})")
                                        
                                        # Mark this trade as processed
                                        previous_orders[tracking_key] = {'processed': True}
                            
                            except Exception as e:
                                print(f"[ERROR] Binance trades check: {e}")
                                import traceback
                                traceback.print_exc()
                        
                        except Exception as e:
                            print(f"[ERROR] Binance polling: {e}")
                            import traceback
                            traceback.print_exc()
                            # If this is a connection-level error and the handler
                            # has not already rebuilt its session, trigger reconnect
                            from requests.exceptions import ConnectionError as ReqCE
                            if isinstance(e, (ReqCE, OSError)):
                                if (self.binance_handler and
                                        hasattr(self.binance_handler, '_reconnect_client')):
                                    self.binance_handler._reconnect_client()
                    
                    # ========== MONITOR BITGET (ONLY IF CONFIGURED) ==========
                    # Bitget: poll open orders + trade history, emit EventoCalce on new fills
                    bitget_orders = pd.DataFrame()
                    if any(b in self.lista_bolsas for b in ('BITGET', 'BITGETFTS')):
                        try:
                            bitget_orders = self.check_order_status_bitget()

                            if bitget_orders is not None and not bitget_orders.empty:
                                for order_id, order in bitget_orders.iterrows():
                                    tracking_key = f"BITGET_{order_id}"

                                    # Extract nemo from symbol (e.g. 'BTCUSDT' → 'BTC')
                                    bg_symbol = order.get('symbol', '')
                                    nemo = bg_symbol.replace('USDT', '') if bg_symbol else self.lista_nemos[0]

                                    if not self.is_tracked_symbol(nemo):
                                        continue

                                    filled_qty = float(order.get('quantity', 0))  # Bitget open orders show remaining
                                    total_qty = filled_qty  # For open orders, quantity == remaining
                                    status = order.get('status', 'open')
                                    side = order.get('side', '').upper()
                                    price = float(order.get('price', 0))

                                    is_new_order = tracking_key not in previous_orders
                                    if is_new_order:
                                        previous_orders[tracking_key] = {
                                            'filled_qty': 0,
                                            'exchange': 'BITGET',
                                            'side': side,
                                            'price': price,
                                            'nemo': nemo,
                                            'first_seen': datetime.datetime.now(timezone.utc),
                                        }

                                    # Auto-cancel stale orders
                                    order_age = (datetime.datetime.now(timezone.utc) - previous_orders[tracking_key].get('first_seen', datetime.datetime.now(timezone.utc))).total_seconds()
                                    if order_age > self.MAX_ORDER_AGE_SECONDS:
                                        print(f"[STALE ORDER] ⏰ BITGET {side} order {order_id} is {order_age/60:.1f} min old — auto-cancelling")
                                        try:
                                            self.bitget_handler.cancel_order(str(order_id), bg_symbol)
                                            print(f"[STALE ORDER] ✅ Auto-cancelled BITGET order {order_id}")
                                        except Exception as e:
                                            print(f"[STALE ORDER] ⚠️  Failed to cancel BITGET order {order_id}: {e}")
                                        continue

                            # Also check recent trades from Bitget
                            try:
                                bg_trades = self.get_bitget_trades()
                                if bg_trades is not None and not bg_trades.empty:
                                    recent_cutoff = pd.Timestamp.now(tz='UTC') - timedelta(seconds=check_interval + 5)
                                    recent_trades = bg_trades[bg_trades.index > recent_cutoff]

                                    for trade_time, trade_row in recent_trades.iterrows():
                                        trade_oid = trade_row.get('orderId', '')
                                        tracking_key = f"BITGET_TRADE_{trade_oid}_{trade_time}"

                                        if tracking_key in previous_orders:
                                            continue

                                        trade_symbol = trade_row.get('symbol', '')
                                        trade_nemo = trade_symbol.replace('USDT', '') if trade_symbol else self.lista_nemos[0]

                                        if not self.is_tracked_symbol(trade_nemo):
                                            previous_orders[tracking_key] = {'processed': True}
                                            continue

                                        self._log_fill_event(
                                            symbol=trade_symbol,
                                            exchange='BITGET',
                                            order_id=str(trade_oid),
                                            side=str(trade_row.get('side', '')).upper(),
                                            quantity=float(trade_row.get('qty', 0)),
                                            price=float(trade_row.get('price', 0)),
                                            commission=float(trade_row.get('commission', 0)),
                                            response_type='FILL',
                                        )

                                        fill_event = EventoCalce(
                                            iTiempo=trade_time if isinstance(trade_time, datetime.datetime) else datetime.datetime.now(timezone.utc),
                                            nemo=trade_nemo,
                                            bolsa='BITGETFTS',
                                            cantidad=float(trade_row.get('qty', 0)),
                                            direccion=str(trade_row.get('side', '')).upper(),
                                            precioCalce=float(trade_row.get('price', 0)),
                                            comision=float(trade_row.get('commission', 0)),
                                            tipo="TAKER",
                                        )

                                        if hasattr(self.eventos, 'add_high_priority_event'):
                                            self.eventos.add_high_priority_event(fill_event)
                                        else:
                                            self.eventos.put(fill_event)

                                        print(f"[FILL EVENT][TRADES] BITGET {str(trade_row.get('side','')).upper()} {trade_row.get('qty',0)} {trade_nemo} @ {trade_row.get('price',0)} (order: {trade_oid})")
                                        previous_orders[tracking_key] = {'processed': True}

                            except Exception as e:
                                print(f"[ERROR] Bitget trades check: {e}")
                                import traceback
                                traceback.print_exc()

                        except Exception as e:
                            print(f"[ERROR] Bitget polling: {e}")
                            import traceback
                            traceback.print_exc()
                    
                    # Clean up stale order tracking AND generate cancellation events
                    # This handles cases where orders disappear (manual cancel, expiry, rejection, etc.)
                    binance_ids = set(f"BINANCE_{oid}" for oid in binance_orders.index) if binance_orders is not None and not binance_orders.empty else set()
                    bitget_ids = set(f"BITGET_{oid}" for oid in bitget_orders.index) if bitget_orders is not None and not bitget_orders.empty else set()
                    current_ids = binance_ids | bitget_ids
                    
                    # Also keep trade tracking keys (they should persist)
                    trade_keys = {k for k in previous_orders.keys() if '_TRADE_' in k}
                    current_ids = current_ids | trade_keys
                    
                    for tracking_key in list(previous_orders.keys()):
                        if tracking_key not in current_ids:
                            # Order disappeared - could be manual cancel, expiry, rejection, etc.
                            # Generate cancellation event to reset order_placed flags
                            order_info = previous_orders[tracking_key]
                            
                            # Skip if this is just a processed trade marker
                            if order_info.get('processed'):
                                previous_orders.pop(tracking_key, None)
                                continue
                            
                            # Generate cancellation event - use stored nemo or fallback
                            order_nemo = order_info.get('nemo', self.lista_nemos[0] if self.lista_nemos else 'UNKNOWN')
                            cancel_event = EventoCalce(
                                iTiempo=datetime.datetime.now(timezone.utc),
                                nemo=order_nemo,
                                bolsa=order_info.get('exchange', 'UNKNOWN'),
                                cantidad=0,  # Zero quantity indicates cancellation
                                direccion=order_info.get('side', 'UNKNOWN'),
                                precioCalce=order_info.get('price', 0),
                                comision=None,
                                tipo="MAKER"
                            )
                            
                            if hasattr(self.eventos, 'add_high_priority_event'):
                                self.eventos.add_high_priority_event(cancel_event)
                            else:
                                self.eventos.put(cancel_event)
                            
                            print(f"[DISAPPEARED] ⚠️  {order_info.get('exchange', 'UNKNOWN')} {order_info.get('side', 'UNKNOWN')} order disappeared from exchange - sending cancellation event to reset flags")
                            
                            previous_orders.pop(tracking_key, None)
                    
                except Exception as e:
                    print(f"[ERROR] Unified polling monitor: {e}")
                    import traceback
                    traceback.print_exc()
                
                # print(f"\n[POLLING CYCLE #{cycle_count}] Complete. Sleeping for {check_interval}s...")
                # print(f"{'='*80}\n")
                time.sleep(check_interval)
        
        # Start monitoring thread
        monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        monitor_thread.start()
        
        return monitor_thread

    def close_binance(self):
        """Cierra la conexión con Binance."""
        self.taker.close_connection()
        print("Binance connection closed.")

class traderSpot(AdminEjecucion):
    # Class-level variable to track monitoring thread (shared across all instances)
    _monitoring_thread = None
    _monitoring_lock = threading.Lock()



#class AdminEjecucionIB(AdminEjecucion):
#    """
#    Administra la ejecución de eventos  via Interactive Brokers
#    API, para usar en cuentas simuladas o .
#    """
#
#    def __init__(
#        self, eventos, order_routing="SMART", currency="USD"
#    ):
#        """
#        Initialises the IBExecutionHandler instance.
#
#        Parameters:
#        events - The Queue of Event objects.
#        """
#        self.eventos = eventos
#        self.order_routing = order_routing
#        self.currency = currency
#        self.fill_dict = {}
#
#        self.tws_conn = self.create_tws_connection()
#        self.order_id = self.create_initial_order_id()
#        self.register_handlers()
#
#    def _error_handler(self, msg):
#        """Handles the capturing of error messages"""
#        # Currently no error handling.
#        print("Server Error: %s" % msg)
#
#    def _reply_handler(self, msg):
#        """Handles of server replies"""
#        # Handle open order orderId processing
#        if msg.typeName == "openOrder" and \
#            msg.orderId == self.order_id and \
#            not self.fill_dict.has_key(msg.orderId):
#            self.create_fill_dict_entry(msg)
#        # Handle Fills
#        if msg.typeName == "orderStatus" and \
#            msg.status == "Filled" and \
#            self.fill_dict[msg.orderId]["filled"] == False:
#            self.create_fill(msg)
#            
#    def create_tws_connection(self):
#        """
#        Connect to the Trader Workstation (TWS) running on the
#        usual port of 7496, with a clientId of 100.
#        The clientId is chosen by us and we will need
#        separate IDs for both the execution connection and
#        market data connection, if the latter is used elsewhere.
#        """
#        tws_conn = ibConnection('',7497,1)
#        tws_conn.connect()
#        return tws_conn
#
#    def create_initial_order_id(self):
#        """
#        Creates the initial order ID used for Interactive
#        Brokers to keep track of submitted orders.
#        """
#        # There is scope for more logic here, but we
#        # will use "1" as the default for now.
#        return 1
#
#    def register_handlers(self):
#        """
#        Register the error and server reply
#        message handling functions.
#        """
#        # Assign the error handling function defined above
#        # to the TWS connection
#        self.tws_conn.register(self._error_handler, 'Error')
#
#        # Assign all of the server reply messages to the
#        # reply_handler function defined above
#        self.tws_conn.registerAll(self._reply_handler)
#
#    def create_contract(self, symbol, sec_type, exch, prim_exch, curr):
#        """Create a Contract object defining what will
#        be purchased, at which exchange and in which currency.
#
#        symbol - The ticker symbol for the contract
#        sec_type - The security type for the contract ('STK' is 'stock')
#        exch - The exchange to carry out the contract on
#        prim_exch - The primary exchange to carry out the contract on
#        curr - The currency in which to purchase the contract"""
#        contract = Contract()
#        contract.m_symbol = symbol
#        contract.m_secType = sec_type
#        contract.m_exchange = exch
#        contract.m_primaryExch = prim_exch
#        contract.m_currency = curr
#        return contract
#
#    def create_order(self, order_type, quantity, action):
#        """Create an Order object (Market/Limit) to go long/short.
#
#        order_type - 'MKT', 'LMT' for Market or Limit orders
#        quantity - Integral number of assets to order
#        action - 'BUY' or 'SELL'"""
#        order = Order()
#        order.m_orderType = order_type
#        order.m_totalQuantity = quantity
#        order.m_action = action
#        return order
#
#    def create_fill_dict_entry(self, msg):
#        """
#        Creates an entry in the Fill Dictionary that lists
#        orderIds and provides security information. This is
#        needed for the event-driven behaviour of the IB
#        server message behaviour.
#        """
#        self.fill_dict[msg.orderId] = {
#            "symbol": msg.contract.m_symbol,
#            "exchange": msg.contract.m_exchange,
#            "direction": msg.order.m_action,
#            "filled": False
#        }
#
#    def create_fill(self, msg):
#        """
#        Handles the creation of the FillEvent that will be
#        placed onto the events queue subsequent to an order
#        being filled.
#        """
#        fd = self.fill_dict[msg.orderId]
#
#        # Prepare the fill data
#        symbol = fd["symbol"]
#        exchange = fd["exchange"]
#        filled = msg.filled
#        direction = fd["direction"]
#        fill_cost = msg.avgFillPrice
#
#        # Create a fill event object
#        fill_event = EventoCalce(
#            datetime.datetime.utcnow(), symbol,
#            exchange, filled, direction, fill_cost
#        )
#
#        # Make sure that multiple messages don't create
#        # additional fills.
#        self.fill_dict[msg.orderId]["filled"] = True
#
#        # Place the fill event onto the event queue
#        self.events.put(fill_event)
#
#    def execute_order(self, event):
#        """
#        Creates the necessary InteractiveBrokers order object
#        and submits it to IB via their API.
#
#        The results are then queried in order to generate a
#        corresponding Fill object, which is placed back on
#        the event queue.
#
#        Parameters:
#        event - Contains an Event object with order information.
#        """
#        if event.type == 'ORDEN':
#            # Prepare the parameters for the asset order
#            asset = event.nemo
#            asset_type = event.instrumento
#            order_type = event.tipo_orden
#            quantity = event.cantidad
#            direction = event.direccion
#
#            # Create the Interactive Brokers contract via the
#            # passed Order event
#            ib_contract = self.create_contract(
#                asset, asset_type, self.order_routing,
#                self.order_routing, self.currency
#            )
#
#            # Create the Interactive Brokers order via the
#            # passed Order event
#            ib_order = self.create_order(
#                order_type, quantity, direction
#            )
#
#            # Use the connection to the send the order to IB
#            self.tws_conn.placeOrder(
#                self.order_id, ib_contract, ib_order
#            )
#
#            # NOTE: This following line is crucial.
#            # It ensures the order goes through!
#            time.sleep(1)
#
#            # Increment the order ID for this session
#            self.order_id += 1
#
