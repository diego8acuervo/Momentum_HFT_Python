#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Binance Spot Trading Module

This module provides a clean interface for Binance Spot trading,
extracted from the original traderPerp class for better separation of concerns.

Key Features:
- Spot order placement (market, limit)
- Order monitoring and cancellation
- Account balance queries
- Pre-flight order validation
- Circuit breaker pattern
- Comprehensive logging

Author: Diego Ochoa
Date: December 2025
"""

import os
import time
import traceback
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class BinanceSpotTrader:
    """
    Handles order execution for Binance Spot market.
    
    This class is responsible for:
    - Order placement (market, limit)
    - Order modification and cancellation
    - Account balance queries
    - Trade history
    - Pre-flight validation
    - Circuit breaker pattern
    """
    
    def __init__(self, lista_nemos: List[str]):
        """
        Initialize Binance Spot trader.
        
        Args:
            lista_nemos (list): List of trading symbols (e.g., ['LINK', 'SOL'])
        """
        self.lista_nemos = lista_nemos
        self.base_token = 'USDT'  # Quote currency for all pairs
        
        # Load API credentials
        self.api_key = self.load_binance_key()
        self.api_secret = self.load_binance_secret()

        if not self.api_key or not self.api_secret:
            raise ValueError("Binance API credentials not found in environment variables")
        
        # Create client
        self.client = self._create_client()
        
        # Order tracking
        self.client_order_map = {}
        
        # Circuit breaker
        self.api_health = {
            'status': 'HEALTHY',
            'last_error': None,
            'error_count': 0,
            'last_success': time.time()
        }
        self.circuit_breaker_threshold = 5
        self.circuit_breaker_timeout = 60
        
        # Logging files
        self.orders_log_file = "binance_spot_orders.csv"
        self.fills_log_file = "binance_spot_fills.csv"
        self._initialize_log_files()
        
        print(f"[INIT] BinanceSpotTrader initialized")
    
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
        """Create Binance client."""
        return Client(
            self.api_key,
            self.api_secret,
            requests_params={'timeout': 30}
        )
    
    def _initialize_log_files(self):
        """Initialize CSV log files for orders and fills."""
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
                'quantity', 'price', 'commission'
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
            timestamp = datetime.now(timezone.utc).isoformat()
            
            order_data = {
                'timestamp': timestamp,
                'symbol': symbol,
                'exchange': 'BINANCE_SPOT',
                'order_type': order_type,
                'side': side,
                'quantity': quantity,
                'price': price if price else 'N/A',
                'order_id': order_id if order_id else 'N/A',
                'client_order_id': client_order_id if client_order_id else 'N/A',
                'status': status
            }
            
            order_df = pd.DataFrame([order_data])
            order_df.to_csv(self.orders_log_file, mode='a', header=False, index=False)
            
            print(f"[LOG] SPOT order: {order_type} {side} {quantity} @ {price if price else 'MARKET'}")
            
        except Exception as e:
            print(f"[ERROR] Failed to log order: {e}")
    
    def _log_fill_event(self, symbol: str, order_id: str, side: str,
                       quantity: float, price: float, commission: float):
        """Log fill event to CSV file."""
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            
            fill_data = {
                'timestamp': timestamp,
                'symbol': symbol,
                'exchange': 'BINANCE_SPOT',
                'order_id': order_id,
                'side': side,
                'quantity': quantity,
                'price': price,
                'commission': commission
            }
            
            fill_df = pd.DataFrame([fill_data])
            fill_df.to_csv(self.fills_log_file, mode='a', header=False, index=False)
            
            print(f"[LOG] SPOT fill: {side} {quantity} @ {price}")
            
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
                print(f"[CIRCUIT BREAKER] SPOT circuit HALF_OPEN, testing...")
                return True
            else:
                remaining = self.circuit_breaker_timeout - time_since_error
                print(f"[CIRCUIT BREAKER] SPOT circuit OPEN ({remaining:.0f}s remaining)")
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
                print(f"⚠️  [CIRCUIT BREAKER] SPOT circuit OPENED after {health['error_count']} errors")
    
    def record_api_success(self):
        """Record successful API call and reset circuit breaker."""
        health = self.api_health
        
        if health['status'] in ['CIRCUIT_OPEN', 'HALF_OPEN']:
            print(f"✅ [CIRCUIT BREAKER] SPOT circuit CLOSED - API recovered")
        
        health['status'] = 'HEALTHY'
        health['error_count'] = 0
        health['last_success'] = time.time()
    
    # ========== HELPER METHODS ==========
    
    def get_symbol(self, nemo: str = None) -> str:
        """
        Get formatted symbol for Binance Spot API.
        
        Args:
            nemo: Base asset symbol (e.g., 'LINK', 'SOL')
                  If None, uses first symbol in lista_nemos
        
        Returns:
            str: Binance symbol format (e.g., 'LINKUSDT', 'SOLUSDT')
        """
        if nemo is None:
            nemo = self.lista_nemos[0]
        return f"{nemo.upper()}{self.base_token}"
    
    # ========== ACCOUNT & BALANCE ==========
    
    def get_balance(self) -> pd.DataFrame:
        """Get Binance Spot account balance as DataFrame."""
        try:
            account_info = self.client.get_account()
            balances = account_info['balances']
            
            # Filter for relevant currencies
            d = {}
            for balance in balances:
                asset = balance['asset']
                if asset in self.lista_nemos:
                    d[asset] = {
                        'free': float(balance['free']),
                        'locked': float(balance['locked']),
                        'total': float(balance['free']) + float(balance['locked'])
                    }
            
            # Add zero balances for missing currencies
            for nemo in self.lista_nemos:
                if nemo not in d:
                    d[nemo] = {'free': 0.0, 'locked': 0.0, 'total': 0.0}
            
            self.record_api_success()
            return pd.DataFrame.from_dict(d, orient='index')
            
        except Exception as e:
            print(f"[ERROR] Failed to get balance: {e}")
            self.record_api_error(str(e))
            
            # Return zero balances as fallback
            d = {}
            for nemo in self.lista_nemos:
                d[nemo] = {'free': 0.0, 'locked': 0.0, 'total': 0.0}
            return pd.DataFrame.from_dict(d, orient='index')
    
    # ========== ORDER PLACEMENT ==========
    
    def place_market_order(self, side: str, quantity: float,
                          strategy_id: Optional[str] = None,
                          nemo: Optional[str] = None) -> Optional[Dict]:
        """
        Place market order on Binance Spot.
        
        Args:
            side (str): 'BUY' or 'SELL'
            quantity (float): Order quantity
            strategy_id (str): Optional strategy identifier
            nemo (str): Base asset symbol (e.g., 'LINK', 'SOL'). 
                       If None, uses first symbol in lista_nemos.
        
        Returns:
            dict: Order response
        """
        # Check circuit breaker
        if not self.check_api_health():
            print(f"[CIRCUIT BREAKER] Market order blocked")
            return None
        
        try:
            symbol = self.get_symbol(nemo)
            
            # Generate client order ID
            timestamp = int(time.time() * 1000)
            client_order_id = f"{strategy_id}_{side}_{timestamp}" if strategy_id else f"mkt_{timestamp}"
            
            # Log order placement
            self._log_order_placement(
                symbol=symbol,
                order_type='MARKET',
                side=side.upper(),
                quantity=quantity,
                price=None,
                order_id=None,
                client_order_id=client_order_id,
                status='SENDING'
            )
            
            print(f"[ORDER SPOT] Placing MARKET {side} {quantity} {symbol}")
            
            order = self.client.create_order(
                symbol=symbol,
                side=side.upper(),
                type='MARKET',
                quantity=quantity,
                newClientOrderId=client_order_id
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
            
            # Log acceptance
            self._log_order_placement(
                symbol=symbol,
                order_type='MARKET',
                side=side.upper(),
                quantity=quantity,
                price=None,
                order_id=str(order['orderId']),
                client_order_id=client_order_id,
                status='ACCEPTED'
            )
            
            print(f"[ORDER SPOT] ✅ Market order placed: OrderID={order['orderId']}")
            
            return order
            
        except BinanceAPIException as e:
            print(f"[ERROR] Market order failed: {e}")
            self.record_api_error(str(e))
            
            # Log rejection
            self._log_order_placement(
                symbol=self.get_symbol(),
                order_type='MARKET',
                side=side.upper(),
                quantity=quantity,
                price=None,
                order_id=None,
                client_order_id=client_order_id if 'client_order_id' in locals() else None,
                status=f'REJECTED: {str(e)}'
            )
            
            return None
    
    def place_limit_order(self, side: str, quantity: float, price: float,
                         time_in_force: str = 'GTC',
                         strategy_id: Optional[str] = None,
                         nemo: Optional[str] = None) -> Optional[Dict]:
        """
        Place limit order on Binance Spot.
        
        Args:
            side (str): 'BUY' or 'SELL'
            quantity (float): Order quantity
            price (float): Limit price
            time_in_force (str): 'GTC', 'IOC', 'FOK'
            strategy_id (str): Optional strategy identifier
            nemo (str): Base asset symbol (e.g., 'LINK', 'SOL').
                       If None, uses first symbol in lista_nemos.
        
        Returns:
            dict: Order response
        """
        # Check circuit breaker
        if not self.check_api_health():
            print(f"[CIRCUIT BREAKER] Limit order blocked")
            return None
        
        try:
            symbol = self.get_symbol(nemo)
            
            # Generate client order ID
            timestamp = int(time.time() * 1000)
            client_order_id = f"{strategy_id}_{side}_{timestamp}" if strategy_id else f"lmt_{timestamp}"
            
            # Log order placement
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
            
            print(f"[ORDER SPOT] Placing LIMIT {side} {quantity} {symbol} @ {price}")
            
            order = self.client.create_order(
                symbol=symbol,
                side=side.upper(),
                type='LIMIT',
                timeInForce=time_in_force,
                quantity=quantity,
                price=str(int(price)),
                newClientOrderId=client_order_id
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
            
            # Log acceptance
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
            
            print(f"[ORDER SPOT] ✅ Limit order placed: OrderID={order['orderId']}")
            
            return order
            
        except BinanceAPIException as e:
            print(f"[ERROR] Limit order failed: {e}")
            traceback.print_exc()
            self.record_api_error(str(e))
            
            # Log rejection
            self._log_order_placement(
                symbol=self.get_symbol(),
                order_type='LIMIT',
                side=side.upper(),
                quantity=quantity,
                price=price,
                order_id=None,
                client_order_id=client_order_id if 'client_order_id' in locals() else None,
                status=f'REJECTED: {str(e)}'
            )
            
            return None
    
    # ========== ORDER MONITORING ==========
    
    def get_open_orders(self, symbol: Optional[str] = None) -> pd.DataFrame:
        """
        Get open orders as DataFrame.
        
        Args:
            symbol: Trading pair symbol (e.g., 'LINKUSDT'). 
                   If None, gets orders for ALL symbols in lista_nemos.
        
        Returns:
            DataFrame with open orders
        """
        try:
            all_orders = []
            
            if symbol is not None:
                # Get orders for specific symbol
                orders = self.client.get_open_orders(symbol=symbol)
                all_orders.extend(orders)
            else:
                # Get orders for all symbols in lista_nemos
                for nemo in self.lista_nemos:
                    formatted_symbol = self.get_symbol(nemo)
                    try:
                        orders = self.client.get_open_orders(symbol=formatted_symbol)
                        all_orders.extend(orders)
                    except BinanceAPIException as e:
                        print(f"[WARNING] Failed to get orders for {formatted_symbol}: {e}")
                        continue
            
            if all_orders:
                df_orders = pd.DataFrame(all_orders)
                df_orders.set_index('orderId', inplace=True)
                df_orders['exchange'] = 'BINANCE_SPOT'
                
                self.record_api_success()
                return df_orders
            else:
                return pd.DataFrame()
            
        except BinanceAPIException as e:
            print(f"[ERROR] Failed to get open orders: {e}")
            traceback.print_exc()
            self.record_api_error(str(e))
            return pd.DataFrame()
    
    def cancel_order(self, order_id: int, symbol: Optional[str] = None) -> Optional[Dict]:
        """Cancel a specific order."""
        try:
            if symbol is None:
                symbol = self.get_symbol()
            
            result = self.client.cancel_order(symbol=symbol, orderId=order_id)
            
            print(f"[CANCEL SPOT] ✅ Order {order_id} cancelled")
            
            self.record_api_success()
            return result
            
        except BinanceAPIException as e:
            print(f"[ERROR] Failed to cancel order: {e}")
            self.record_api_error(str(e))
            return None
    
    def cancel_order_by_client_id(self, client_order_id: str, symbol: Optional[str] = None) -> Optional[Dict]:
        """Cancel order using custom client ID."""
        try:
            if symbol is None:
                symbol = self.get_symbol()
            
            result = self.client.cancel_order(
                symbol=symbol,
                origClientOrderId=client_order_id
            )
            
            print(f"[CANCEL SPOT] ✅ Order {client_order_id} cancelled")
            
            # Remove from mapping
            self.client_order_map.pop(client_order_id, None)
            
            self.record_api_success()
            return result
            
        except BinanceAPIException as e:
            print(f"[ERROR] Failed to cancel order: {e}")
            self.record_api_error(str(e))
            return None
    
    def cancel_all_orders(self, symbol: Optional[str] = None) -> Optional[Dict]:
        """Cancel all open orders for a symbol."""
        try:
            if symbol is None:
                symbol = self.get_symbol()
            
            # Binance Spot doesn't have cancel_all_orders endpoint
            # Cancel orders one by one
            open_orders = self.get_open_orders(symbol)
            
            results = []
            for order_id in open_orders.index:
                result = self.cancel_order(order_id, symbol)
                if result:
                    results.append(result)
            
            print(f"[CANCEL SPOT] ✅ Cancelled {len(results)} orders for {symbol}")
            
            return {'cancelled_count': len(results), 'orders': results}
            
        except Exception as e:
            print(f"[ERROR] Failed to cancel all orders: {e}")
            self.record_api_error(str(e))
            return None
    
    # ========== TRADE HISTORY ==========
    
    def get_trades(self, symbol: Optional[str] = None, limit: int = 50) -> pd.DataFrame:
        """
        Get recent trades as DataFrame.
        
        Args:
            symbol: Trading pair symbol (e.g., 'LINKUSDT'). 
                   If None, gets trades for ALL symbols in lista_nemos.
            limit: Max trades per symbol
        
        Returns:
            DataFrame with trade history
        """
        try:
            all_trades = []
            
            if symbol is not None:
                # Get trades for specific symbol
                symbols_to_query = [symbol]
            else:
                # Get trades for all symbols in lista_nemos
                symbols_to_query = [self.get_symbol(nemo) for nemo in self.lista_nemos]
            
            for sym in symbols_to_query:
                try:
                    trades = self.client.get_my_trades(symbol=sym, limit=limit)
                    
                    for trade in trades:
                        # Parse time and ensure it's timezone-aware (UTC)
                        trade_time = pd.to_datetime(trade['time'], unit='ms')
                        if trade_time.tzinfo is None:
                            trade_time = trade_time.tz_localize('UTC')
                        
                        all_trades.append({
                            'time': trade_time,
                            'symbol': sym,
                            'id': trade['id'],
                            'orderId': trade['orderId'],
                            'price': float(trade['price']),
                            'qty': float(trade['qty']),
                            'quoteQty': float(trade['quoteQty']),
                            'commission': float(trade.get('commission', 0)),
                            'commissionAsset': trade.get('commissionAsset', ''),
                            'side': 'BUY' if trade['isBuyer'] else 'SELL',
                            'isMaker': trade.get('isMaker', False)
                        })
                except BinanceAPIException as e:
                    print(f"[WARNING] Failed to get trades for {sym}: {e}")
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
