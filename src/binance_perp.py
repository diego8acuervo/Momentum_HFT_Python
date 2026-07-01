#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Binance Perpetuals – Standard USD-M Futures (fapi) Trading Module

This module provides a clean interface for Binance standard USD-M Futures
(perpetuals) trading, implementing order placement, position management,
and account monitoring.

Standard Futures accounts use the /fapi/v1/* REST endpoints and a dedicated
WebSocket stream (wss://fstream.binance.com/ws/<listenKey>).

Key Features:
- USD-M Futures order placement via fapi (market, limit, batch)
- Position management and tracking
- Leverage configuration
- Pre-flight order validation
- Circuit breaker pattern
- Order deduplication
- Comprehensive logging

References:
- https://developers.binance.com/docs/derivatives/usds-margined-futures/trade
- https://developers.binance.com/docs/derivatives/usds-margined-futures/user-data-streams

Author: Diego Ochoa
Date: December 2025  (fapi migration: May 2026)
"""

import os
import time
import json
import hmac
import hashlib
import threading
import traceback
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException
from dotenv import load_dotenv

# Import EventoCalce for immediate fill processing
try:
    from Eventos import EventoCalce
except ImportError:
    EventoCalce = None  # Graceful degradation if not available
    print("[WARNING] EventoCalce not available - immediate fill processing disabled")

# Load environment variables from .env file
load_dotenv()


class BinancePerpetualTrader:
    """
    Handles order execution for Binance USD-M Futures (Perpetuals).
    
    This class is responsible for:
    - Order placement (market, limit, batch)
    - Order modification and cancellation
    - Position tracking
    - Account balance queries
    - Pre-flight validation
    - Circuit breaker pattern
    """

    # ── Global REST rate limiter (shared across all instances in this process) ──
    _rest_lock = threading.Lock()
    _last_rest_call = 0.0
    _MIN_REST_INTERVAL = 2.0  # at most 1 REST call every 2 seconds

    @classmethod
    def _rate_limit_wait(cls):
        """Block until at least _MIN_REST_INTERVAL seconds since last REST call."""
        with cls._rest_lock:
            now = time.time()
            elapsed = now - cls._last_rest_call
            if elapsed < cls._MIN_REST_INTERVAL:
                time.sleep(cls._MIN_REST_INTERVAL - elapsed)
            cls._last_rest_call = time.time()
    
    def __init__(self, lista_nemos: List[str], testnet: bool = False, eventos=None):
        """
        Initialize Binance Futures trader.

        Args:
            lista_nemos (list): List of trading symbols (e.g., ['BTC', 'USDT'])
            testnet (bool): Use testnet (True) or live account (False)
            eventos: Event queue for immediate fill notifications (optional)
        """
        self.lista_nemos = lista_nemos
        self.testnet = testnet
        self.eventos = eventos  # Store event queue for immediate fill processing

        # Load API credentials
        if testnet:
            self.api_key = self.load_testnet_binance_key()
            self.api_secret = self.load_testnet_binance_secret()
        else:
            self.api_key = self.load_binance_key()
            self.api_secret = self.load_binance_secret()

        if not self.api_key or not self.api_secret:
            raise ValueError("API credentials not found in environment variables")
        
        # Create client
        self.client = self._create_client()
        self._consecutive_conn_errors = 0   # reconnect watchdog counter

        # Configuration
        self.default_leverage = 1  # Conservative default (1x = same as spot)
        self.position_side = 'BOTH'  # One-way mode (not hedge mode)
        
        # Exchange info cache (for validation)
        self._exchange_info = None
        self._exchange_info_timestamp = None
        self._exchange_info_ttl = 3600  # Cache for 1 hour
        
        # Order tracking
        self.client_order_map = {}  # Map client order IDs to exchange order IDs
        
        # Circuit breaker
        self.api_health = {
            'status': 'HEALTHY',
            'last_error': None,
            'error_count': 0,
            'last_success': time.time()
        }
        self.circuit_breaker_threshold = 5
        self.circuit_breaker_timeout = 60  # 1 minute timeout
        
        # Logging files
        self.orders_log_file = "binance_perp_orders.csv"
        self.fills_log_file = "binance_perp_fills.csv"
        self._initialize_log_files()
        
        # ========== PERFORMANCE MONITORING ==========
        self.fill_metrics = {
            'immediate_fills': 0,          # Fills processed from order response
            'websocket_fills': 0,          # Fills processed from WebSocket
            'polling_fills': 0,            # Fills processed from polling (backup)
            'total_fills': 0,              # Total fills across all methods
            'avg_immediate_latency_ms': 0, # Average latency for immediate fills
            'avg_websocket_latency_ms': 0, # Average latency for WebSocket fills
            'fill_latencies': [],          # List of (method, latency_ms, timestamp)
        }
        
        # ========== WEBSOCKET USER DATA STREAM ==========
        self.user_data_ws = None
        self.user_data_ws_thread = None
        self.user_data_listen_key = None
        self.user_data_keepalive_thread = None
        self.ws_is_running = False
        
        # ========== FILL DEDUPLICATION ==========
        # Tracks processed fill IDs (order_id + trade_id) to prevent duplicates
        # between immediate fills (order response) and WebSocket fills
        self._processed_fill_ids = set()
        # Tracks order IDs that were already processed via immediate fill
        self._immediate_fill_order_ids = set()
        
        print(f"[INIT] BinancePerpetualTrader initialized ({'TESTNET' if testnet else 'LIVE'})")
        
        # Load exchange info for validation
        self._load_exchange_info()
        
        # Start WebSocket User Data Stream for real-time fills
        if self.eventos:
            self._start_user_data_stream()


    def load_testnet_binance_key(self):
        """Load API key from environment variable. Default: BINANCE_TESTNET_API_KEY"""
        import os
        api_key = os.environ.get("BINANCE_TESTNET_API_KEY")
        if api_key is None:
            raise ValueError("API key not found. Please set the BINANCE_TESTNET_API_KEY environment variable.")
        return api_key

    def load_testnet_binance_secret(self):
        """Load API secret from environment variable. Default: BINANCE_API_SECRET"""
        import os
        api_secret = os.environ.get("BINANCE_TESTNET_SECRET_KEY")
        if api_secret is None:
            raise ValueError("API secret not found. Please set the BINANCE_TESTNET_SECRET_KEY environment variable.")
        return api_secret

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


    def _create_client(self) -> Client:
        """Create Binance client with testnet support and connection-resilience.
        
        Mounts a urllib3 Retry adapter on the underlying requests.Session so that
        stale TCP connections (RemoteDisconnected / ConnectionAborted) are retried
        automatically on a fresh socket, instead of propagating as hard exceptions.
        """
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        client = Client(
            self.api_key,
            self.api_secret,
            testnet=self.testnet,
            requests_params={'timeout': 30}
        )

        # ── Retry adapter ────────────────────────────────────────────────
        # Retry up to 3 times on connection-level errors (stale keep-alive
        # sockets, remote disconnects).  We do NOT retry on POST to avoid
        # duplicate order submissions.
        retry_strategy = Retry(
            total=3,                          # max retries per request
            backoff_factor=0.5,               # wait 0.5s, 1s, 2s between retries
            status_forcelist=[500, 502, 503, 504],  # also retry on server errors
            allowed_methods=["GET", "DELETE"],       # NEVER retry POST (orders)
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=4,   # keep a small pool of warm connections
            pool_maxsize=8,
            pool_block=False,
        )
        client.session.mount("https://", adapter)
        client.session.mount("http://",  adapter)
        # ─────────────────────────────────────────────────────────────────

        if self.testnet:
            client.API_URL = 'https://testnet.binancefuture.com'
            client.FUTURES_URL = 'https://testnet.binancefuture.com'
            print("[TESTNET] Using Binance Futures Testnet")

        return client

    def _reconnect_client(self):
        """Recreate the Binance client when the underlying TCP session is stale.

        Called after a RemoteDisconnected / ConnectionAborted error to replace
        the broken requests.Session with a fresh one carrying the same Retry
        adapter.  Safe to call from the polling thread because python-binance
        makes no use of the old session after reassignment.
        """
        print("[RECONNECT] Stale connection detected — rebuilding Binance client session")
        try:
            old_session = getattr(self.client, 'session', None)
            if old_session:
                try:
                    old_session.close()
                except Exception:
                    pass
            self.client = self._create_client()
            self._consecutive_conn_errors = 0
            print("[RECONNECT] ✅ Binance client session rebuilt successfully")
        except Exception as exc:
            print(f"[RECONNECT] ❌ Failed to rebuild client: {exc}")

    def _initialize_log_files(self):
        """Initialize CSV log files for orders and fills if they don't exist."""
        # Initialize orders log
        if not os.path.exists(self.orders_log_file):
            orders_df = pd.DataFrame(columns=[
                'timestamp', 'symbol', 'exchange', 'order_type', 'side', 
                'quantity', 'price', 'order_id', 'client_order_id', 'status'
            ])
            orders_df.to_csv(self.orders_log_file, index=False)
            print(f"[LOG] Created orders log file: {self.orders_log_file}")
        
        # Initialize fills log
        if not os.path.exists(self.fills_log_file):
            fills_df = pd.DataFrame(columns=[
                'timestamp', 'symbol', 'exchange', 'order_id', 'side',
                'quantity', 'price', 'commission', 'position_side', 'realized_pnl'
            ])
            fills_df.to_csv(self.fills_log_file, index=False)
            print(f"[LOG] Created fills log file: {self.fills_log_file}")
    
    def _log_order_placement(self, symbol: str, order_type: str, side: str, 
                           quantity: float, price: Optional[float] = None, 
                           order_id: Optional[str] = None, 
                           client_order_id: Optional[str] = None,
                           status: str = 'SENT'):
        """Log order placement to CSV file."""
        try:
            from datetime import datetime as dt, timezone as tz
            timestamp = dt.now(tz.utc).isoformat()
            
            order_data = {
                'timestamp': timestamp,
                'symbol': symbol,
                'exchange': 'BINANCE_PERP',
                'order_type': order_type,
                'side': side,
                'quantity': quantity,
                'price': price if price else 'N/A',
                'order_id': order_id if order_id else 'N/A',
                'client_order_id': client_order_id if client_order_id else 'N/A',
                'status': status
            }
            
            # Append to CSV
            order_df = pd.DataFrame([order_data])
            order_df.to_csv(self.orders_log_file, mode='a', header=False, index=False)
            
            print(f"[LOG] PERP order: {order_type} {side} {quantity} @ {price if price else 'MARKET'}")
            
        except Exception as e:
            print(f"[ERROR] Failed to log order: {e}")
    
    def _log_fill_event(self, symbol: str, order_id: str, side: str, 
                       quantity: float, price: float, commission: float,
                       position_side: str = 'BOTH', realized_pnl: float = 0.0):
        """Log fill event to CSV file."""
        try:
            from datetime import datetime as dt, timezone as tz
            timestamp = dt.now(tz.utc).isoformat()
            
            fill_data = {
                'timestamp': timestamp,
                'symbol': symbol,
                'exchange': 'BINANCE_PERP',
                'order_id': order_id,
                'side': side,
                'quantity': quantity,
                'price': price,
                'commission': commission,
                'position_side': position_side,
                'realized_pnl': realized_pnl
            }
            
            # Append to CSV
            fill_df = pd.DataFrame([fill_data])
            fill_df.to_csv(self.fills_log_file, mode='a', header=False, index=False)
            
            print(f"[LOG] PERP fill: {side} {quantity} @ {price}, PnL: {realized_pnl:.2f}")
            
        except Exception as e:
            print(f"[ERROR] Failed to log fill: {e}")
    
    # ========== CIRCUIT BREAKER ==========
    
    def check_api_health(self) -> bool:
        """Check if API is healthy before sending orders."""
        health = self.api_health
        current_time = time.time()
        
        if health['status'] == 'CIRCUIT_OPEN':
            time_since_error = current_time - health['last_error']
            if time_since_error > self.circuit_breaker_timeout:
                health['status'] = 'HALF_OPEN'
                print(f"[CIRCUIT BREAKER] PERP circuit HALF_OPEN, testing...")
                return True
            else:
                remaining = self.circuit_breaker_timeout - time_since_error
                print(f"[CIRCUIT BREAKER] PERP circuit OPEN ({remaining:.0f}s remaining)")
                return False
        
        return True
    
    def record_api_error(self, error: str):
        """Record API error and potentially open circuit breaker."""
        health = self.api_health
        health['error_count'] += 1
        health['last_error'] = time.time()
        
        if health['error_count'] >= self.circuit_breaker_threshold:
            if health['status'] != 'CIRCUIT_OPEN':
                health['status'] = 'CIRCUIT_OPEN'
                print(f"⚠️  [CIRCUIT BREAKER] PERP circuit OPENED after {health['error_count']} errors")
                print(f"⚠️  [CIRCUIT BREAKER] Last error: {error}")
    
    def record_api_success(self):
        """Record successful API call and reset circuit breaker."""
        health = self.api_health
        
        if health['status'] in ['CIRCUIT_OPEN', 'HALF_OPEN']:
            print(f"✅ [CIRCUIT BREAKER] PERP circuit CLOSED - API recovered")
        
        health['status'] = 'HEALTHY'
        health['error_count'] = 0
        health['last_success'] = time.time()
    
    # ========== EXCHANGE INFO & VALIDATION ==========
    
    def _load_exchange_info(self):
        """Load and cache exchange trading rules."""
        try:
            current_time = time.time()
            
            # Check if cache is still valid
            if (self._exchange_info is not None and 
                self._exchange_info_timestamp is not None and
                current_time - self._exchange_info_timestamp < self._exchange_info_ttl):
                return
            
            # Fetch fresh exchange info
            self._exchange_info = self.client.futures_exchange_info()
            self._exchange_info_timestamp = current_time
            print("[INIT] Exchange info cached for validation")
            
        except Exception as e:
            print(f"[WARNING] Could not load exchange info: {e}")
            self._exchange_info = None
    
    def get_symbol(self, nemo: Optional[str] = None) -> str:
        """
        Get formatted symbol for Binance Futures.
        
        Args:
            nemo (str, optional): Base asset symbol (e.g., 'LINK', 'AVAX').
                                 If None, uses first symbol from lista_nemos.
        
        Returns:
            str: Formatted symbol with USDT as quote currency (e.g., 'LINKUSDT')
        """
        if nemo is None:
            nemo = self.lista_nemos[0]
        return nemo + 'USDT'
    
    def format_quantity(self, symbol: str, quantity: float) -> float:
        """
        Format quantity to match exchange precision requirements.
        
        Args:
            symbol: Trading pair symbol (e.g., 'LINKUSDT')
            quantity: Raw quantity value
            
        Returns:
            Formatted quantity rounded to the correct step size
        """
        from decimal import Decimal, ROUND_DOWN
        
        print(f"[DEBUG FORMAT_QTY] Input: {symbol} quantity={quantity:.15f}")
        
        # Refresh exchange info if needed
        self._load_exchange_info()
        
        if not self._exchange_info:
            # Fallback: round to 2 decimals if exchange info unavailable
            print(f"[DEBUG FORMAT_QTY] ⚠️  Exchange info not available, using fallback (2 decimals)")
            result = round(quantity, 2)
            print(f"[DEBUG FORMAT_QTY] Output: {result:.15f}")
            return result
        
        # Find symbol rules
        symbol_info = None
        for s in self._exchange_info['symbols']:
            if s['symbol'] == symbol:
                symbol_info = s
                break
        
        if not symbol_info:
            # Fallback: round to 2 decimals
            print(f"[DEBUG FORMAT_QTY] ⚠️  Symbol {symbol} not found in exchange info, using fallback (2 decimals)")
            result = round(quantity, 2)
            print(f"[DEBUG FORMAT_QTY] Output: {result:.15f}")
            return result
        
        # Get LOT_SIZE filter
        filters = {f['filterType']: f for f in symbol_info['filters']}
        lot_size_filter = filters.get('LOT_SIZE')
        
        if not lot_size_filter:
            # Fallback: use quantityPrecision
            precision = symbol_info.get('quantityPrecision', 2)
            print(f"[DEBUG FORMAT_QTY] ⚠️  LOT_SIZE filter not found, using quantityPrecision={precision}")
            result = round(quantity, precision)
            print(f"[DEBUG FORMAT_QTY] Output: {result:.15f}")
            return result
        
        step_size = Decimal(lot_size_filter['stepSize'])
        min_qty = Decimal(lot_size_filter['minQty'])
        
        print(f"[DEBUG FORMAT_QTY] ✓ Found LOT_SIZE: stepSize={step_size}, minQty={min_qty}")
        
        # Convert to Decimal for precise rounding
        qty_decimal = Decimal(str(quantity))
        
        # Round down to nearest step_size multiple
        # Formula: floor((quantity - min_qty) / step_size) * step_size + min_qty
        steps = ((qty_decimal - min_qty) / step_size).quantize(Decimal('1'), rounding=ROUND_DOWN)
        formatted_qty = steps * step_size + min_qty
        
        result = float(formatted_qty)
        print(f"[DEBUG FORMAT_QTY] Calculation: ({qty_decimal} - {min_qty}) / {step_size} = {steps} steps")
        print(f"[DEBUG FORMAT_QTY] Output: {result:.15f} (formatted from {quantity:.15f})")
        
        return result
    
    def _format_price_for_symbol(self, price: float, symbol: str) -> float:
        """
        Format price according to symbol's tick size to avoid
        floating-point rounding errors that cause validation failures.
        
        Args:
            price (float): Price to format
            symbol (str): Symbol (e.g., 'XLMUSDT')
        
        Returns:
            float: Price rounded to tick size precision
        """
        from decimal import Decimal, ROUND_DOWN

        # Make sure exchange_info is loaded (cached for 1h)
        self._load_exchange_info()
        if not self._exchange_info:
            return price  # No exchange info → return as-is

        # _exchange_info['symbols'] is a list, not a dict
        symbol_info = None
        for s in self._exchange_info.get('symbols', []):
            if s.get('symbol') == symbol:
                symbol_info = s
                break
        if not symbol_info:
            return price

        filters = {f['filterType']: f for f in symbol_info.get('filters', [])}
        price_filter = filters.get('PRICE_FILTER')
        
        if not price_filter:
            return price
        
        tick_size = float(price_filter['tickSize'])
        if tick_size <= 0:
            return price
        
        # Use Decimal for precise rounding to tick size
        tick_size_decimal = Decimal(str(tick_size))
        price_decimal = Decimal(str(price))
        
        # Quantize to tick size (round down to be conservative)
        formatted = float(price_decimal.quantize(
            tick_size_decimal, rounding=ROUND_DOWN))
        
        return formatted
    
    def validate_order(self, symbol: str, side: str, quantity: float, 
                      price: Optional[float] = None, order_type: str = 'LIMIT') -> Tuple[bool, Optional[str]]:
        """
        Validate order parameters before sending to exchange.
        
        Returns:
            tuple: (is_valid: bool, error_message: str or None)
        """
        # Refresh exchange info if needed
        self._load_exchange_info()
        
        if not self._exchange_info:
            return False, "Exchange info not available"
        
        # Find symbol rules
        symbol_info = None
        for s in self._exchange_info['symbols']:
            if s['symbol'] == symbol:
                symbol_info = s
                break
        
        if not symbol_info:
            return False, f"Symbol {symbol} not found on exchange"
        
        # Check filters
        filters = {f['filterType']: f for f in symbol_info['filters']}
        
        # 1. PRICE_FILTER (for limit orders)
        if order_type == 'LIMIT' and price is not None:
            price_filter = filters.get('PRICE_FILTER')
            if price_filter:
                min_price = float(price_filter['minPrice'])
                max_price = float(price_filter['maxPrice'])
                tick_size = float(price_filter['tickSize'])
                
                if price < min_price:
                    return False, f"Price {price} below minimum {min_price}"
                if price > max_price:
                    return False, f"Price {price} above maximum {max_price}"
                
                # Check tick size using Decimal to avoid float modulo precision bugs
                if tick_size > 0:
                    from decimal import Decimal
                    _p   = Decimal(str(price))
                    _mn  = Decimal(str(min_price))
                    _ts  = Decimal(str(tick_size))
                    remainder = float((_p - _mn) % _ts)
                    if remainder > float(_ts) * 0.01:  # Allow 1% tolerance
                        return False, f"Price {price} not multiple of tick size {tick_size}"

        # 2. LOT_SIZE
        lot_size_filter = filters.get('LOT_SIZE')
        if lot_size_filter:
            min_qty = float(lot_size_filter['minQty'])
            max_qty = float(lot_size_filter['maxQty'])
            step_size = float(lot_size_filter['stepSize'])

            if quantity < min_qty:
                return False, f"Quantity {quantity} below minimum {min_qty}"
            if quantity > max_qty:
                return False, f"Quantity {quantity} above maximum {max_qty}"

            # Check step size using Decimal to avoid float modulo precision bugs
            if step_size > 0:
                from decimal import Decimal
                _q  = Decimal(str(quantity))
                _mq = Decimal(str(min_qty))
                _ss = Decimal(str(step_size))
                remainder = float((_q - _mq) % _ss)
                if remainder > float(_ss) * 0.01:  # Allow 1% tolerance
                    return False, f"Quantity {quantity} not multiple of step size {step_size}"
        
        # 3. MIN_NOTIONAL (minimum trade value)
        min_notional_filter = filters.get('MIN_NOTIONAL')
        if min_notional_filter and price is not None:
            min_notional = float(min_notional_filter['notional'])
            trade_value = quantity * price
            
            if trade_value < min_notional:
                return False, f"Trade value {trade_value} below minimum {min_notional}"
        
        # All checks passed
        return True, None
    
    # ========== ACCOUNT & POSITION MANAGEMENT ==========
    
    def get_account_info(self) -> Optional[Dict]:
        """Get USD-M Futures account information (GET /fapi/v2/account)."""
        self._rate_limit_wait()
        try:
            account = self.client.futures_account()

            # Standard futures account nests balances inside 'assets' list
            assets = account.get('assets', [])
            usdt = next((a for a in assets if a.get('asset') == 'USDT'), {})
            wallet_balance = float(usdt.get('walletBalance', 0))
            unrealized_pnl = float(usdt.get('unrealizedProfit', 0))
            available = wallet_balance - float(usdt.get('initialMargin', 0))

            print(f"[ACCOUNT PERP] Wallet: {wallet_balance:.2f} USDT")
            print(f"[ACCOUNT PERP] Available: {available:.2f} USDT")
            print(f"[ACCOUNT PERP] Unrealized PnL: {unrealized_pnl:.2f} USDT")

            self.record_api_success()
            return account

        except BinanceAPIException as e:
            print(f"[ERROR] Failed to get account info: {e}")
            self.record_api_error(str(e))
            return None
    
    def get_balance(self) -> pd.DataFrame:
        """Get USD-M Futures balance as DataFrame (GET /fapi/v2/balance)."""
        self._rate_limit_wait()
        try:
            assets = self.client.futures_account_balance()

            balance_dict = {}
            for asset in assets:
                currency = asset.get('asset', '')
                if currency in self.lista_nemos:
                    total = float(asset.get('balance', 0))
                    avail = float(asset.get('availableBalance', 0))
                    balance_dict[currency] = {
                        'free':   avail,
                        'locked': max(0.0, total - avail),
                        'total':  total,
                    }

            # Add zero balances for missing currencies
            for nemo in self.lista_nemos:
                if nemo not in balance_dict:
                    balance_dict[nemo] = {'free': 0.0, 'locked': 0.0, 'total': 0.0}

            self.record_api_success()
            return pd.DataFrame.from_dict(balance_dict, orient='index')

        except Exception as e:
            print(f"[ERROR] Failed to get balance: {e}")
            self.record_api_error(str(e))

            # Return zero balances as fallback
            d = {}
            for nemo in self.lista_nemos:
                d[nemo] = {'free': 0.0, 'locked': 0.0, 'total': 0.0}
            return pd.DataFrame.from_dict(d, orient='index')
    
    def get_position_info(self, symbol: Optional[str] = None) -> Optional[List[Dict]]:
        """Get current position information (GET /fapi/v2/positionRisk)."""
        self._rate_limit_wait()
        try:
            if symbol is None:
                symbol = self.get_symbol()

            positions = self.client.futures_position_information(symbol=symbol)

            # Filter to non-zero positions
            active_positions = [
                p for p in positions if float(p['positionAmt']) != 0
            ]

            if active_positions:
                for pos in active_positions:
                    print(
                        f"[POSITION PERP] {pos['symbol']}: "
                        f"{pos['positionAmt']} @ {pos['entryPrice']}"
                    )
                    print(
                        f"                Unrealized PnL: "
                        f"{pos['unRealizedProfit']} USDT"
                    )
            else:
                print(f"[POSITION PERP] No open positions for {symbol}")

            self.record_api_success()
            return positions

        except BinanceAPIException as e:
            print(f"[ERROR] Failed to get position info: {e}")
            self.record_api_error(str(e))
            return None
    
    def set_leverage(self, leverage: int, symbol: Optional[str] = None) -> Optional[Dict]:
        """
        Set leverage for a symbol (POST /fapi/v1/leverage).

        Args:
            leverage (int): Leverage (1-125)
            symbol (str): Trading symbol
        """
        self._rate_limit_wait()
        try:
            if symbol is None:
                symbol = self.get_symbol()

            result = self.client.futures_change_leverage(
                symbol=symbol,
                leverage=leverage
            )

            print(f"[LEVERAGE] {symbol} leverage set to {leverage}x")
            self.default_leverage = leverage

            self.record_api_success()
            return result

        except BinanceAPIException as e:
            print(f"[ERROR] Failed to set leverage: {e}")
            self.record_api_error(str(e))
            return None

    def set_margin_type(self, margin_type: str = 'CROSSED',
                        symbol: Optional[str] = None) -> None:
        """
        Portfolio Margin accounts always use CROSS margin for UM futures.
        This method is kept for interface compatibility but is a no-op.
        """
        print(
            "[MARGIN] Portfolio Margin accounts use CROSS margin by default. "
            "set_margin_type() is a no-op for PM accounts."
        )
    
    # ========== ORDER PLACEMENT ==========
    
    def place_market_order(self, side: str, quantity: float, 
                          reduce_only: bool = False,
                          strategy_id: Optional[str] = None,
                          nemo: Optional[str] = None) -> Optional[Dict]:
        """
        Place market order on Binance Futures.
        
        Args:
            side (str): 'BUY' or 'SELL'
            quantity (float): Order quantity
            reduce_only (bool): Only reduce position (don't increase)
            strategy_id (str): Optional strategy identifier
            nemo (str): Base asset symbol (e.g., 'LINK'). If None, uses first from lista_nemos
        
        Returns:
            dict: Order response
        """
        # Check circuit breaker
        if not self.check_api_health():
            print(f"[CIRCUIT BREAKER] Market order blocked")
            return None
        
        self._rate_limit_wait()
        try:
            symbol = self.get_symbol(nemo)
            
            print(f"\n{'='*80}")
            print(f"[DEBUG ORDER] === PLACE MARKET ORDER CALLED ===")
            print(f"[DEBUG ORDER] Input Parameters:")
            print(f"[DEBUG ORDER]   - symbol: {symbol}")
            print(f"[DEBUG ORDER]   - side: {side}")
            print(f"[DEBUG ORDER]   - quantity (RAW): {quantity:.15f}")
            print(f"[DEBUG ORDER]   - reduce_only: {reduce_only}")
            print(f"[DEBUG ORDER]   - strategy_id: {strategy_id}")
            print(f"[DEBUG ORDER]   - nemo: {nemo}")
            
            # Format quantity to match exchange precision requirements
            formatted_quantity = self.format_quantity(symbol, quantity)
            print(f"[DEBUG ORDER] Quantity after format_quantity(): {formatted_quantity:.15f}")
            print(f"[DEBUG ORDER] Precision change: {quantity:.15f} → {formatted_quantity:.15f}")
            
            # Generate client order ID
            timestamp = int(time.time() * 1000)
            client_order_id = f"{strategy_id}_{side}_{timestamp}" if strategy_id else f"mkt_{timestamp}"
            
            # Log order placement BEFORE sending
            self._log_order_placement(
                symbol=symbol,
                order_type='MARKET',
                side=side.upper(),
                quantity=formatted_quantity,
                price=None,
                order_id=None,
                client_order_id=client_order_id,
                status='SENDING'
            )
            
            print(f"[DEBUG ORDER] === SENDING TO BINANCE FAPI ===")
            print(f"[DEBUG ORDER] API Call Parameters:")
            print(f"[DEBUG ORDER]   - symbol: {symbol}")
            print(f"[DEBUG ORDER]   - side: {side.upper()}")
            print(f"[DEBUG ORDER]   - type: MARKET")
            print(f"[DEBUG ORDER]   - quantity: {formatted_quantity} (type: {type(formatted_quantity)})")
            print(f"[DEBUG ORDER]   - positionSide: {self.position_side}")
            print(f"[DEBUG ORDER]   - reduceOnly: {reduce_only}")
            print(f"[DEBUG ORDER]   - newClientOrderId: {client_order_id}")
            
            # Standard USD-M Futures order → POST /fapi/v1/order
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side.upper(),
                type='MARKET',
                quantity=formatted_quantity,
                positionSide=self.position_side,
                reduceOnly=str(reduce_only).lower(),
                newClientOrderId=client_order_id,
                newOrderRespType='RESULT'  # Get fill info immediately
            )
            
            print(f"[DEBUG ORDER] === BINANCE API RESPONSE ===")
            print(f"[DEBUG ORDER] Full Response: {json.dumps(order, indent=2)}")
            print(f"{'='*80}\n")
            
            # Record API success
            self.record_api_success()
            
            # Store mapping
            if 'orderId' in order:
                self.client_order_map[client_order_id] = {
                    'exchange_order_id': order['orderId'],
                    'symbol': symbol,
                    'strategy_id': strategy_id
                }
            
            # Log order acceptance
            self._log_order_placement(
                symbol=symbol,
                order_type='MARKET',
                side=side.upper(),
                quantity=formatted_quantity,
                price=None,
                order_id=str(order['orderId']),
                client_order_id=client_order_id,
                status='ACCEPTED'
            )
            
            print(f"[ORDER PERP] ✅ Market order placed: OrderID={order['orderId']}")
            print(f"             Status: {order['status']}")
            print(f"             Filled: {order.get('executedQty', 'N/A')} @ {order.get('avgPrice', 'N/A')}")
            
            # ========== IMMEDIATE FILL PROCESSING ==========
            # If order filled immediately, create EventoCalce right away
            # This eliminates the 0-10s polling delay
            if order.get('status') == 'FILLED' and self.eventos and EventoCalce:
                try:
                    filled_qty = float(order.get('executedQty', 0))
                    avg_price = float(order.get('avgPrice', 0))
                    
                    # Mark this order as processed via immediate fill
                    # so WebSocket handler skips it (avoid double-counting)
                    self._immediate_fill_order_ids.add(order.get('orderId'))
                    
                    print(f"[IMMEDIATE FILL] ⚡ Creating EventoCalce for instant processing")
                    print(f"[IMMEDIATE FILL]    {nemo} {side.upper()} {filled_qty} @ {avg_price}")
                    
                    fill_event = EventoCalce(
                        iTiempo=datetime.now(timezone.utc),
                        nemo=nemo,  # Base asset (e.g., 'ETH', 'BTC')
                        bolsa='BINANCEFTS',
                        cantidad=filled_qty,
                        direccion=side.upper(),
                        precioCalce=avg_price,
                        comision=0.0  # Will be updated from trades endpoint if needed
                    )
                    
                    # Send to event queue with high priority if available
                    if hasattr(self.eventos, 'add_high_priority_event'):
                        self.eventos.add_high_priority_event(fill_event)
                        print(f"[IMMEDIATE FILL] ✅ Fill event sent to HIGH PRIORITY queue")
                    else:
                        self.eventos.put(fill_event)
                        print(f"[IMMEDIATE FILL] ✅ Fill event sent to event queue")
                    
                    # Record metrics for immediate fill
                    # Latency is ~0 since we're processing from the order response
                    self.record_immediate_fill(latency_ms=50)  # Approximate latency
                        
                except Exception as fill_error:
                    print(f"[IMMEDIATE FILL] ⚠️ Error creating immediate fill event: {fill_error}")
                    # Don't fail the order if fill event creation fails
                    # Polling thread will still catch it as backup
            
            # Log fill if immediately filled (for CSV logging)
            if order.get('status') == 'FILLED':
                self._log_fill_event(
                    symbol=symbol,
                    order_id=str(order['orderId']),
                    side=side.upper(),
                    quantity=float(order.get('executedQty', 0)),
                    price=float(order.get('avgPrice', 0)),
                    commission=0.0,  # Will be updated from trades endpoint
                    realized_pnl=float(order.get('realizedProfit', 0))
                )
            
            return order
            
        except BinanceAPIException as e:
            print(f"\n{'='*80}")
            print(f"[DEBUG ERROR] === BINANCE API EXCEPTION ===")
            print(f"[DEBUG ERROR] Exception Type: {type(e).__name__}")
            print(f"[DEBUG ERROR] Error Message: {str(e)}")
            print(f"[DEBUG ERROR] Error Code: {getattr(e, 'code', 'N/A')}")
            print(f"[DEBUG ERROR] Error Status Code: {getattr(e, 'status_code', 'N/A')}")
            print(f"[DEBUG ERROR] Full Exception: {repr(e)}")
            
            # Try to get the request that was sent
            if 'symbol' in locals():
                print(f"[DEBUG ERROR] Request Parameters:")
                print(f"[DEBUG ERROR]   - symbol: {symbol}")
                print(f"[DEBUG ERROR]   - side: {side.upper()}")
                print(f"[DEBUG ERROR]   - quantity sent: {formatted_quantity if 'formatted_quantity' in locals() else quantity}")
                print(f"[DEBUG ERROR]   - quantity original: {quantity:.15f}")
            print(f"{'='*80}\n")
            
            self.record_api_error(str(e))
            
            # Log rejection
            self._log_order_placement(
                symbol=self.get_symbol() if 'symbol' not in locals() else symbol,
                order_type='MARKET',
                side=side.upper(),
                quantity=formatted_quantity if 'formatted_quantity' in locals() else quantity,
                price=None,
                order_id=None,
                client_order_id=client_order_id if 'client_order_id' in locals() else None,
                status=f'REJECTED: {str(e)}'
            )
            
            return None
    
    def place_limit_order(self, side: str, quantity: float, price: float,
                         time_in_force: str = 'GTC', reduce_only: bool = False,
                         strategy_id: Optional[str] = None,
                         nemo: Optional[str] = None) -> Optional[Dict]:
        """
        Place limit order on Binance Futures.
        
        Args:
            side (str): 'BUY' or 'SELL'
            quantity (float): Order quantity
            price (float): Limit price
            time_in_force (str): 'GTC', 'IOC', 'FOK'
            reduce_only (bool): Only reduce position (don't increase)
            strategy_id (str): Optional strategy identifier
            nemo (str): Base asset symbol (e.g., 'LINK'). If None, uses first from lista_nemos
        
        Returns:
            dict: Order response
        """
        # Check circuit breaker
        if not self.check_api_health():
            print(f"[CIRCUIT BREAKER] Limit order blocked")
            return None
        
        self._rate_limit_wait()
        symbol = self.get_symbol(nemo)

        # Format price and quantity to match exchange rules before validation
        price    = self._format_price_for_symbol(price, symbol)
        quantity = self.format_quantity(symbol, quantity)

        # Validate order
        is_valid, error_msg = self.validate_order(symbol, side, quantity, price, 'LIMIT')
        if not is_valid:
            print(f"[VALIDATION FAILED] {error_msg}")
            return None
        
        try:
            # Generate client order ID
            timestamp = int(time.time() * 1000)
            client_order_id = f"{strategy_id}_{side}_{timestamp}" if strategy_id else f"lmt_{timestamp}"
            
            # Log order placement BEFORE sending
            self._log_order_placement(
                symbol=symbol,
                order_type='LIMIT',
                side=side.upper(),
                quantity=quantity,
                price=price,
                order_id=None,
                client_order_id=client_order_id,
                status='SENDING'
            )
            
            print(f"[ORDER PERP] Placing LIMIT {side} {quantity} {symbol} @ {price}")
            
            # Standard USD-M Futures order → POST /fapi/v1/order
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side.upper(),
                type='LIMIT',
                quantity=quantity,
                price=str(price),
                timeInForce=time_in_force,
                positionSide=self.position_side,
                reduceOnly=str(reduce_only).lower(),
                newClientOrderId=client_order_id,
                newOrderRespType='RESULT'
            )
            
            # Record API success
            self.record_api_success()
            
            # Store mapping
            if 'orderId' in order:
                self.client_order_map[client_order_id] = {
                    'exchange_order_id': order['orderId'],
                    'symbol': symbol,
                    'strategy_id': strategy_id
                }
            
            # Log order acceptance
            self._log_order_placement(
                symbol=symbol,
                order_type='LIMIT',
                side=side.upper(),
                quantity=quantity,
                price=price,
                order_id=str(order['orderId']),
                client_order_id=client_order_id,
                status='ACCEPTED'
            )
            
            print(f"[ORDER PERP] ✅ Limit order placed: OrderID={order['orderId']}")
            print(f"             Status: {order['status']}")
            
            return order
            
        except BinanceAPIException as e:
            print(f"[ERROR] Limit order failed: {e}")
            self.record_api_error(str(e))
            
            # Log rejection
            self._log_order_placement(
                symbol=symbol,
                order_type='LIMIT',
                side=side.upper(),
                quantity=quantity,
                price=price,
                order_id=None,
                client_order_id=client_order_id if 'client_order_id' in locals() else None,
                status=f'REJECTED: {str(e)}'
            )
            
            return None
    
    def place_batch_orders(self, orders_list: List[Dict]) -> Optional[List[Dict]]:
        """
        Place multiple orders atomically using Binance batch orders API.
        
        Args:
            orders_list (list): List of order dicts, max 5 orders per request
            
        Returns:
            list: List of order responses
        """
        # Check circuit breaker
        if not self.check_api_health():
            print(f"[CIRCUIT BREAKER] Batch orders blocked")
            return None
        
        self._rate_limit_wait()
        try:
            # Validate max 5 orders per batch
            if len(orders_list) > 5:
                raise ValueError("Binance supports max 5 orders per batch request")
            
            # Add timestamps and client order IDs
            timestamp = int(time.time() * 1000)
            for i, order in enumerate(orders_list):
                order['timestamp'] = timestamp
                order['newClientOrderId'] = f"batch_{timestamp}_{i}"
                order['positionSide'] = self.position_side
            
            # Log batch before sending
            print(f"[BATCH ORDER PERP] Sending {len(orders_list)} orders atomically")
            for order in orders_list:
                self._log_order_placement(
                    symbol=order['symbol'],
                    order_type=order['type'],
                    side=order['side'],
                    quantity=order['quantity'],
                    price=order.get('price'),
                    order_id=None,
                    client_order_id=order['newClientOrderId'],
                    status='SENDING_BATCH'
                )
            
            # Send batch order via standard USD-M Futures (POST /fapi/v1/batchOrders)
            response = self.client.futures_place_batch_order(
                batchOrders=json.dumps(orders_list)
            )
            
            # Record API success
            self.record_api_success()
            
            # Log acceptance
            print(f"[BATCH ORDER PERP] ✅ Batch accepted: {len(response)} orders")
            for order_resp in response:
                if 'orderId' in order_resp:
                    self._log_order_placement(
                        symbol=order_resp['symbol'],
                        order_type=order_resp['type'],
                        side=order_resp['side'],
                        quantity=order_resp['origQty'],
                        price=order_resp.get('price'),
                        order_id=str(order_resp['orderId']),
                        client_order_id=order_resp.get('clientOrderId'),
                        status='ACCEPTED'
                    )
            
            return response
            
        except Exception as e:
            print(f"[ERROR] Batch order failed: {e}")
            self.record_api_error(str(e))
            
            # Log rejection for all orders in batch
            for order in orders_list:
                self._log_order_placement(
                    symbol=order['symbol'],
                    order_type=order['type'],
                    side=order['side'],
                    quantity=order['quantity'],
                    price=order.get('price'),
                    order_id=None,
                    client_order_id=order.get('newClientOrderId', 'N/A'),
                    status=f'REJECTED_BATCH: {str(e)}'
                )
            
            return None
    
    # ========== ORDER MONITORING ==========
    
    def get_open_orders(self, symbol: Optional[str] = None) -> pd.DataFrame:
        """
        Get all open orders as DataFrame (compatible with spot interface).

        Handles stale TCP connections (RemoteDisconnected) by triggering a
        client session rebuild via _reconnect_client() before retrying once.
        """
        from requests.exceptions import ConnectionError as RequestsConnError
        self._rate_limit_wait()
        try:
            all_orders = []

            if symbol is None:
                symbols_to_query = [self.get_symbol(nemo) for nemo in self.lista_nemos]
            else:
                symbols_to_query = [symbol]

            for query_symbol in symbols_to_query:
                try:
                    orders = self.client.futures_get_open_orders(symbol=query_symbol)
                    if orders:
                        all_orders.extend(orders)
                    self._consecutive_conn_errors = 0   # reset on success
                except BinanceAPIException as e:
                    print(f"[ERROR] Failed to get orders for {query_symbol}: {e}")
                    continue
                except (RequestsConnError, OSError) as conn_err:
                    # Stale keep-alive socket — rebuild session and retry once
                    self._consecutive_conn_errors += 1
                    print(f"[CONN ERROR] {query_symbol}: {conn_err} "
                          f"(consecutive: {self._consecutive_conn_errors})")
                    self._reconnect_client()
                    import time as _time
                    _time.sleep(min(2 ** self._consecutive_conn_errors, 30))  # 2s, 4s, 8s … cap 30s
                    try:
                        orders = self.client.futures_get_open_orders(symbol=query_symbol)
                        if orders:
                            all_orders.extend(orders)
                        self._consecutive_conn_errors = 0
                    except Exception as retry_err:
                        print(f"[CONN ERROR] Retry also failed for {query_symbol}: {retry_err}")
                    continue
            
            if all_orders:
                df_orders = pd.DataFrame(all_orders)
                df_orders.set_index('orderId', inplace=True)
                df_orders['exchange'] = 'BINANCE_PERP'
                
                print(f"[ORDERS PERP] Found {len(all_orders)} open orders across {len(symbols_to_query)} symbols")
                for order in all_orders:
                    print(f"              {order['symbol']}: {order['side']} {order['origQty']} @ {order['price']} (ID: {order['orderId']})")
                
                self.record_api_success()
                return df_orders
            else:
                return pd.DataFrame()
            
        except BinanceAPIException as e:
            print(f"[ERROR] Failed to get open orders: {e}")
            self.record_api_error(str(e))
            return pd.DataFrame()
    
    def cancel_order(self, order_id: int, symbol: Optional[str] = None) -> Optional[Dict]:
        """Cancel a specific UM order (DELETE /papi/v1/um/order)."""
        self._rate_limit_wait()
        try:
            if symbol is None:
                symbol = self.get_symbol()

            result = self.client.futures_cancel_order(
                symbol=symbol,
                orderId=order_id
            )

            print(f"[CANCEL PERP] ✅ Order {order_id} cancelled")

            self.record_api_success()
            return result

        except BinanceAPIException as e:
            print(f"[ERROR] Failed to cancel order: {e}")
            self.record_api_error(str(e))
            return None

    def cancel_order_by_client_id(self, client_order_id: str,
                                  symbol: Optional[str] = None) -> Optional[Dict]:
        """Cancel UM order by client order ID (DELETE /fapi/v1/order)."""
        self._rate_limit_wait()
        try:
            if symbol is None:
                symbol = self.get_symbol()

            result = self.client.futures_cancel_order(
                symbol=symbol,
                origClientOrderId=client_order_id
            )

            print(f"[CANCEL PERP] ✅ Order {client_order_id} cancelled")

            # Remove from mapping
            self.client_order_map.pop(client_order_id, None)

            self.record_api_success()
            return result

        except BinanceAPIException as e:
            print(f"[ERROR] Failed to cancel order: {e}")
            self.record_api_error(str(e))
            return None

    def cancel_all_orders(self, symbol: Optional[str] = None) -> Optional[Dict]:
        """Cancel all open UM orders (DELETE /fapi/v1/allOpenOrders)."""
        self._rate_limit_wait()
        try:
            if symbol is None:
                symbol = self.get_symbol()

            result = self.client.futures_cancel_all_open_orders(symbol=symbol)

            print(f"[CANCEL PERP] ✅ All orders cancelled for {symbol}")

            self.record_api_success()
            return result

        except BinanceAPIException as e:
            print(f"[ERROR] Failed to cancel all orders: {e}")
            self.record_api_error(str(e))
            return None
    
    # ========== TRADE HISTORY ==========
    
    def get_trades(self, symbol: Optional[str] = None, limit: int = 50) -> pd.DataFrame:
        """
        Get recent trades as DataFrame (compatible with spot interface).
        
        Args:
            symbol (str, optional): Specific symbol to query. If None, queries all symbols
                                   in lista_nemos (e.g., ['LINK', 'AVAX'] → queries LINKUSDT and AVAXUSDT)
            limit (int): Max trades per symbol
        
        Returns:
            pd.DataFrame: All trades across queried symbols
        """
        self._rate_limit_wait()
        try:
            all_trades = []
            
            if symbol is None:
                # Query all symbols in lista_nemos (for pairs trading)
                symbols_to_query = [self.get_symbol(nemo) for nemo in self.lista_nemos]
            else:
                # Query specific symbol
                symbols_to_query = [symbol]
            
            for query_symbol in symbols_to_query:
                try:
                    # Futures UM trades → GET /fapi/v1/userTrades
                    trades = self.client.futures_account_trades(
                        symbol=query_symbol,
                        limit=limit
                    )
                    
                    if trades:
                        for trade in trades:
                            # Parse time and ensure it's timezone-aware (UTC)
                            trade_time = pd.to_datetime(trade['time'], unit='ms')
                            if trade_time.tzinfo is None:
                                trade_time = trade_time.tz_localize('UTC')
                            
                            all_trades.append({
                                'time': trade_time,
                                'id': trade['id'],
                                'orderId': trade['orderId'],
                                'symbol': query_symbol,  # Add symbol to track which pair
                                'price': float(trade['price']),
                                'qty': float(trade['qty']),
                                'quoteQty': float(trade['quoteQty']),
                                'commission': float(trade.get('commission', 0)),
                                'commissionAsset': trade.get('commissionAsset', ''),
                                'side': trade['side'],
                                'positionSide': trade.get('positionSide', 'BOTH'),
                                'realizedPnl': float(trade.get('realizedPnl', 0)),
                                'isMaker': trade.get('maker', False)
                            })
                except BinanceAPIException as e:
                    # Log but continue with other symbols
                    print(f"[ERROR] Failed to get trades for {query_symbol}: {e}")
                    continue
            
            if all_trades:
                trade_df = pd.DataFrame(all_trades)
                trade_df.set_index('time', inplace=True)
                
                self.record_api_success()
                return trade_df
            else:
                return pd.DataFrame()
            
        except BinanceAPIException as e:
            print(f"[ERROR] Failed to get trades: {e}")
            self.record_api_error(str(e))
            return pd.DataFrame()

    # ========== WEBSOCKET USER DATA STREAM ==========
    
    def _start_user_data_stream(self):
        """
        Start WebSocket User Data Stream for real-time order/fill notifications.
        
        This provides <100ms latency for fill notifications vs 0-10s for polling.
        """
        try:
            import websocket
            
            # Get listen key for user data stream
            self.user_data_listen_key = self._get_listen_key()
            if not self.user_data_listen_key:
                print("[WS USER DATA] ⚠️ Failed to get listen key - WebSocket disabled")
                return
            
            # Construct WebSocket URL
            # Portfolio Margin stream: wss://fstream.binance.com/pm/ws/<listenKey>
            if self.testnet:
                ws_base = "wss://fstream.binancefuture.com/pm/ws/"
            else:
                ws_base = "wss://fstream.binance.com/pm/ws/"

            ws_url = f"{ws_base}{self.user_data_listen_key}"
            
            print(f"[WS USER DATA] Connecting to User Data Stream...")
            
            def on_message(ws, message):
                self._handle_user_data_message(message)
            
            def on_error(ws, error):
                print(f"[WS USER DATA] ❌ Error: {error}")
            
            def on_close(ws, close_status_code, close_msg):
                print(f"[WS USER DATA] Connection closed: {close_status_code} - {close_msg}")
                self.ws_is_running = False
            
            def on_open(ws):
                print(f"[WS USER DATA] ✅ Connected to User Data Stream")
                self.ws_is_running = True
            
            self.user_data_ws = websocket.WebSocketApp(
                ws_url,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
                on_open=on_open
            )
            
            # Run WebSocket in background thread
            self.user_data_ws_thread = threading.Thread(
                target=self.user_data_ws.run_forever,
                daemon=True,
                name="BinanceUserDataStream"
            )
            self.user_data_ws_thread.start()
            
            # Start keep-alive thread (ping every 30 minutes)
            self._start_listen_key_keepalive()
            
        except ImportError:
            print("[WS USER DATA] ⚠️ websocket-client not installed - WebSocket disabled")
        except Exception as e:
            print(f"[WS USER DATA] ⚠️ Failed to start: {e}")
    
    def _get_listen_key(self) -> Optional[str]:
        """
        Get listen key for USD-M Futures user data stream.

        Uses POST /fapi/v1/listenKey.
        """
        self._rate_limit_wait()
        try:
            response = self.client.futures_stream_get_listen_key()
            if isinstance(response, dict):
                return response.get('listenKey')
            return response
        except Exception as e:
            print(f"[WS USER DATA] Failed to get listen key: {e}")
            return None

    def _start_listen_key_keepalive(self):
        """Keep USD-M Futures listen key alive (PUT /fapi/v1/listenKey)."""
        def keepalive_loop():
            while self.ws_is_running:
                time.sleep(30 * 60)  # 30 minutes
                try:
                    self.client.futures_stream_keepalive(self.user_data_listen_key)
                except Exception as e:
                    print(f"[WS USER DATA] Keep-alive failed: {e}")

        self.user_data_keepalive_thread = threading.Thread(
            target=keepalive_loop,
            daemon=True,
            name="BinanceListenKeyKeepalive"
        )
        self.user_data_keepalive_thread.start()
    
    def _handle_user_data_message(self, message: str):
        """
        Handle incoming user data stream messages.
        
        Event types:
        - ORDER_TRADE_UPDATE: Order status/fill updates
        - ACCOUNT_UPDATE: Position/balance changes
        """
        try:
            data = json.loads(message)
            event_type = data.get('e')
            
            if event_type == 'ORDER_TRADE_UPDATE':
                self._process_order_update(data)
            elif event_type == 'ACCOUNT_UPDATE':
                # Can be used for position tracking if needed
                pass
                
        except Exception as e:
            print(f"[WS USER DATA] Error processing message: {e}")
    
    def _process_order_update(self, data: dict):
        """
        Process ORDER_TRADE_UPDATE events from WebSocket.
        
        Creates EventoCalce for fills with minimal latency.
        
        IMPORTANT: The User Data Stream broadcasts ALL account fills, not just
        fills for the symbols this process is trading. We MUST filter by
        lista_nemos to avoid cross-contamination when running multiple pairs.
        
        Also deduplicates against immediate fills already processed from the
        order response (place_market_order).
        """
        try:
            order_data = data.get('o', {})
            
            order_status = order_data.get('X')  # Order status
            exec_type = order_data.get('x')      # Execution type
            
            # Only process TRADE (fill) events
            if exec_type == 'TRADE':
                symbol = order_data.get('s', '')  # e.g., 'ETHUSDT'
                nemo = symbol.replace('USDT', '') if symbol else None
                side = order_data.get('S', '')    # BUY or SELL
                filled_qty = float(order_data.get('l', 0))  # Last filled quantity
                fill_price = float(order_data.get('L', 0))  # Last fill price
                order_id = order_data.get('i')    # Order ID
                trade_id = order_data.get('t')    # Trade ID (unique per partial fill)
                
                # Record latency
                event_time = data.get('E', 0)  # Event timestamp ms
                latency_ms = time.time() * 1000 - event_time
                
                # ── FILTER 1: Only process fills for symbols THIS process trades ──
                if nemo not in self.lista_nemos:
                    print(f"[WS FILL] ⏭️ Ignoring fill for {nemo} (not in {self.lista_nemos})")
                    return
                
                # ── FILTER 2: Deduplicate against immediate fills and WS retransmissions ──
                # Use order_id + trade_id as unique key for each partial fill
                dedup_key = f"{order_id}_{trade_id}"
                if dedup_key in self._processed_fill_ids:
                    print(f"[WS FILL] ⏭️ Duplicate fill skipped: {nemo} {side} {filled_qty} (order={order_id}, trade={trade_id})")
                    return
                self._processed_fill_ids.add(dedup_key)
                
                # Trim dedup set to avoid memory growth (keep last 500)
                if len(self._processed_fill_ids) > 500:
                    # Convert to list, keep last 250, convert back
                    self._processed_fill_ids = set(list(self._processed_fill_ids)[-250:])
                
                print(f"[WS FILL] ⚡ Real-time fill detected!")
                print(f"[WS FILL]    {nemo} {side} {filled_qty} @ {fill_price}")
                print(f"[WS FILL]    Latency: {latency_ms:.0f}ms")
                
                # ── FILTER 3: Skip if immediate fill already sent for this order ──
                if order_id in self._immediate_fill_order_ids:
                    print(f"[WS FILL] ⏭️ Skipping WS fill — already processed via immediate fill (order={order_id})")
                    return
                
                # Update metrics
                self.fill_metrics['websocket_fills'] += 1
                self.fill_metrics['total_fills'] += 1
                self.fill_metrics['fill_latencies'].append(
                    ('websocket', latency_ms, datetime.now(timezone.utc))
                )
                self._update_avg_latency('websocket', latency_ms)
                
                # Create EventoCalce if we have eventos queue
                if self.eventos and EventoCalce and nemo:
                    fill_event = EventoCalce(
                        iTiempo=datetime.now(timezone.utc),
                        nemo=nemo,
                        bolsa='BINANCEFTS',
                        cantidad=filled_qty,
                        direccion=side,
                        precioCalce=fill_price,
                        comision=float(order_data.get('n', 0))  # Commission
                    )
                    
                    if hasattr(self.eventos, 'add_high_priority_event'):
                        self.eventos.add_high_priority_event(fill_event)
                    else:
                        self.eventos.put(fill_event)
                    
                    print(f"[WS FILL] ✅ EventoCalce sent to queue")
                    
        except Exception as e:
            print(f"[WS FILL] Error processing order update: {e}")
    
    def stop_user_data_stream(self):
        """Stop the WebSocket user data stream."""
        self.ws_is_running = False
        if self.user_data_ws:
            self.user_data_ws.close()
            print("[WS USER DATA] Stopped")
    
    # ========== PERFORMANCE METRICS ==========
    
    def _update_avg_latency(self, method: str, latency_ms: float):
        """Update rolling average latency for a fill method."""
        if method == 'immediate':
            current = self.fill_metrics['avg_immediate_latency_ms']
            count = self.fill_metrics['immediate_fills']
        elif method == 'websocket':
            current = self.fill_metrics['avg_websocket_latency_ms']
            count = self.fill_metrics['websocket_fills']
        else:
            return
        
        # Rolling average
        new_avg = ((current * (count - 1)) + latency_ms) / count if count > 0 else latency_ms
        
        if method == 'immediate':
            self.fill_metrics['avg_immediate_latency_ms'] = new_avg
        else:
            self.fill_metrics['avg_websocket_latency_ms'] = new_avg
    
    def record_immediate_fill(self, latency_ms: float):
        """Record metrics for an immediate fill (from order response)."""
        self.fill_metrics['immediate_fills'] += 1
        self.fill_metrics['total_fills'] += 1
        self.fill_metrics['fill_latencies'].append(
            ('immediate', latency_ms, datetime.now(timezone.utc))
        )
        self._update_avg_latency('immediate', latency_ms)
    
    def record_polling_fill(self):
        """Record metrics for a polling-detected fill."""
        self.fill_metrics['polling_fills'] += 1
        self.fill_metrics['total_fills'] += 1
    
    def get_fill_metrics(self) -> dict:
        """
        Get current fill processing metrics.
        
        Returns:
            dict: Fill metrics including counts and latencies
        """
        metrics = self.fill_metrics.copy()
        
        # Calculate percentages
        total = metrics['total_fills']
        if total > 0:
            metrics['immediate_pct'] = (metrics['immediate_fills'] / total) * 100
            metrics['websocket_pct'] = (metrics['websocket_fills'] / total) * 100
            metrics['polling_pct'] = (metrics['polling_fills'] / total) * 100
        else:
            metrics['immediate_pct'] = 0
            metrics['websocket_pct'] = 0
            metrics['polling_pct'] = 0
        
        # Remove raw latency list from output (can be large)
        del metrics['fill_latencies']
        
        return metrics
    
    def print_fill_metrics(self):
        """Print formatted fill metrics report."""
        metrics = self.get_fill_metrics()
        
        print("\n" + "="*60)
        print("📊 FILL PROCESSING METRICS")
        print("="*60)
        print(f"Total Fills: {metrics['total_fills']}")
        print(f"")
        print(f"By Method:")
        print(f"  Immediate (order response): {metrics['immediate_fills']:>4} ({metrics['immediate_pct']:.1f}%)")
        print(f"  WebSocket (real-time):      {metrics['websocket_fills']:>4} ({metrics['websocket_pct']:.1f}%)")
        print(f"  Polling (backup):           {metrics['polling_fills']:>4} ({metrics['polling_pct']:.1f}%)")
        print(f"")
        print(f"Average Latency:")
        print(f"  Immediate: {metrics['avg_immediate_latency_ms']:.0f}ms")
        print(f"  WebSocket: {metrics['avg_websocket_latency_ms']:.0f}ms")
        print("="*60 + "\n")


# ========== TEST SUITE ==========

def run_tests(testnet: bool = True):
    """
    Run comprehensive tests for Binance Futures trading.
    
    Args:
        testnet (bool): Use testnet (True) or live account (False)
    """
    print("="*80)
    print(f"BINANCE FUTURES TEST SUITE ({'TESTNET' if testnet else 'LIVE ACCOUNT'})")
    print("="*80)
    print("\n⚠️  WARNING: This will place REAL orders on the exchange!")
    print("Make sure you're using TESTNET for testing.\n")
    
    # Initialize trader
    trader = BinancePerpetualTrader(['BTC', 'USDT'], testnet=testnet)
    
    # Test 1: Account Info
    print("\n" + "="*80)
    print("TEST 1: Get Account Info")
    print("="*80)
    account = trader.get_account_info()
    
    # Test 2: Get Balance (DataFrame interface)
    print("\n" + "="*80)
    print("TEST 2: Get Balance (DataFrame)")
    print("="*80)
    balance_df = trader.get_balance()
    print(balance_df)
    
    # Test 3: Set Leverage
    print("\n" + "="*80)
    print("TEST 3: Set Leverage to 2x")
    print("="*80)
    trader.set_leverage(leverage=2)
    
    # Test 4: Set Margin Type
    print("\n" + "="*80)
    print("TEST 4: Set Margin Type to CROSSED")
    print("="*80)
    trader.set_margin_type(margin_type='CROSSED')
    
    # Test 5: Get Position Info
    print("\n" + "="*80)
    print("TEST 5: Get Position Info")
    print("="*80)
    positions = trader.get_position_info()
    
    # Test 6: Order Validation
    print("\n" + "="*80)
    print("TEST 6: Order Validation")
    print("="*80)
    is_valid, error = trader.validate_order('BTCUSDT', 'BUY', 0.001, 50000, 'LIMIT')
    print(f"Validation result: {is_valid}, Error: {error}")
    
    # Test 7: Place Small Market Order (TESTNET ONLY)
    if testnet:
        print("\n" + "="*80)
        print("TEST 7: Place Small Market BUY Order (0.001 BTC)")
        print("="*80)
        
        order = trader.place_market_order('BUY', 0.001, strategy_id='TEST')
        
        if order:
            print(f"\n✅ Order placed successfully!")
            print(f"   Order ID: {order['orderId']}")
            print(f"   Status: {order['status']}")
            print(f"   Filled Qty: {order.get('executedQty', 'N/A')}")
            print(f"   Avg Price: {order.get('avgPrice', 'N/A')}")
            
            # Wait 2 seconds
            time.sleep(2)
            
            # Test 8: Get Position (should show new position)
            print("\n" + "="*80)
            print("TEST 8: Verify Position After Order")
            print("="*80)
            trader.get_position_info()
    
    # Test 9: Get Open Orders
    print("\n" + "="*80)
    print("TEST 9: Get Open Orders (DataFrame)")
    print("="*80)
    open_orders_df = trader.get_open_orders()
    if not open_orders_df.empty:
        print(open_orders_df[['symbol', 'side', 'type', 'origQty', 'price', 'status']])
    
    # Test 10: Get Trade History
    print("\n" + "="*80)
    print("TEST 10: Get Trade History (DataFrame)")
    print("="*80)
    trades_df = trader.get_trades(limit=10)
    if not trades_df.empty:
        print(trades_df[['orderId', 'side', 'qty', 'price', 'commission', 'realizedPnl']])
    
    print("\n" + "="*80)
    print("ALL TESTS COMPLETED")
    print("="*80)


if __name__ == "__main__":
    # Run tests on TESTNET (safe)
    run_tests(testnet=True)
