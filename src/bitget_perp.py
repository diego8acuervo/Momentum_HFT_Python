#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bitget Perpetuals (USDT-M Futures) Trading Module — UTA V3 API

Drop-in adapter that replaces the Bitso integration in ejecucion.py.
Mirrors the interface of BinancePerpetualTrader so the rest of the system
(trading.py, Estrategia.py, PortAQMHFT.py) requires zero changes.

API Reference (UTA v3):
  Place order : POST /api/v3/trade/place-order
  Cancel order: POST /api/v3/trade/cancel-order
  Cancel all  : POST /api/v3/trade/cancel-symbol-order
  Open orders : GET  /api/v3/trade/unfilled-orders
  Fills       : GET  /api/v3/trade/fills
  Positions   : GET  /api/v3/position/current-position
  Account     : GET  /api/v3/account/assets
  Settings    : GET  /api/v3/account/settings
  Leverage    : POST /api/v3/account/set-leverage
  Instruments : GET  /api/v3/public/instruments
  Market data : (v2 public endpoints — candles, orderbook)

Authentication:
  prehash = str(timestamp_ms) + METHOD + requestPath + body
  sign    = base64( HMAC-SHA256(prehash, secret) )
  Headers : ACCESS-KEY, ACCESS-SIGN, ACCESS-TIMESTAMP, ACCESS-PASSPHRASE

Author: Diego Ochoa
Date: February 2026 (updated April 2026 for UTA V3)
"""

import os
import time
import json
import hmac
import hashlib
import base64
import threading
import traceback
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
from dotenv import load_dotenv

# Import EventoCalce for immediate fill processing
try:
    from Eventos import EventoCalce
except ImportError:
    EventoCalce = None
    print("[WARNING] EventoCalce not available — immediate fill processing disabled")

load_dotenv()

# ============================================================================
# Constants
# ============================================================================
BITGET_API_URL = "https://api.bitget.com"
BITGET_TESTNET_URL = os.getenv("BITGET_TESTNET_URL", BITGET_API_URL)  # Bitget has no public testnet; override if available

PRODUCT_TYPE = "USDT-FUTURES"  # default product type for USDT-margined perps
MARGIN_COIN = "USDT"

# Granularity mapping: system interval → Bitget granularity
INTERVAL_MAP = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1H",
    "1H": "1H",
    "2h": "2H",
    "4h": "4H",
    "6h": "6H",
    "12h": "12H",
    "1d": "1D",
    "1D": "1D",
}


class BitgetPerpetualTrader:
    """
    Handles order execution, market data and position tracking for
    Bitget USDT-M Futures (Perpetuals).

    Public interface deliberately mirrors BinancePerpetualTrader so that
    `ejecucion.traderPerp` can delegate to it in exactly the same way.
    """

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------
    def __init__(
        self,
        lista_nemos: List[str],
        testnet: bool = False,
        eventos=None,
        paper_trading: bool = False,
    ):
        """
        Args:
            lista_nemos:    e.g. ['BTC', 'ETH']  — base coins
            testnet:        legacy placeholder (Bitget has no public testnet URL)
            eventos:        event queue for EventoCalce fill notifications
            paper_trading:  True → use Bitget paper trading account (BITGET_PAPER_*
                            credentials from .env). All orders execute in Bitget's
                            simulated environment against real market prices.
                            Obtain keys from: Bitget dashboard →
                            Trading → Paper Trading → API Management.
        """
        self.lista_nemos   = lista_nemos
        self.testnet       = testnet
        self.paper_trading = paper_trading
        self.eventos       = eventos
        self.base_url      = BITGET_TESTNET_URL if testnet else BITGET_API_URL

        # Credentials — paper trading uses separate keys from the Bitget demo account
        if paper_trading:
            self.api_key    = os.getenv("BITGET_PAPER_API_KEY", "")
            self.api_secret = os.getenv("BITGET_PAPER_SECRET_KEY", "")
            self.passphrase = os.getenv("BITGET_PAPER_PASSPHRASE", "")
            cred_label = "PAPER"
        else:
            self.api_key    = os.getenv("BITGET_API_KEY", "")
            self.api_secret = os.getenv("BITGET_SECRET_KEY", "")
            self.passphrase = os.getenv("BITGET_PASSPHRASE", "")
            cred_label = "LIVE"

        if not self.api_key or not self.api_secret or not self.passphrase:
            print(f"[BITGET] ⚠️  {cred_label} API credentials missing — "
                  "authenticated endpoints will fail")

        # Session with keep-alive
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

        # Position mode — detect from account (hedge_mode or one_way_mode)
        self.position_mode = self._detect_position_mode()

        # Circuit breaker
        self.api_health: Dict = {
            "status": "HEALTHY",
            "last_error": None,
            "error_count": 0,
            "last_success": time.time(),
        }
        self.circuit_breaker_threshold = 5
        self.circuit_breaker_timeout = 60

        # Log files
        self.orders_log_file = "bitget_perp_orders.csv"
        self.fills_log_file = "bitget_perp_fills.csv"
        self._initialize_log_files()

        # Fill metrics (mirrors BinancePerpetualTrader)
        self.fill_metrics = {
            "immediate_fills": 0,
            "polling_fills": 0,
            "avg_latency_ms": 0.0,
        }

        mode_tag = "📄 PAPER TRADING" if paper_trading else "🔴 LIVE"
        print(f"[BITGET] ✅ BitgetPerpetualTrader initialized — mode: {mode_tag}")

    # ------------------------------------------------------------------
    # Authentication helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _sign(prehash: str, secret: str) -> str:
        """HMAC-SHA256 → base64 as required by Bitget v2 API."""
        mac = hmac.new(
            secret.encode("utf-8"),
            prehash.encode("utf-8"),
            digestmod=hashlib.sha256,
        )
        return base64.b64encode(mac.digest()).decode("utf-8")

    def _auth_headers(self, method: str, request_path: str, body: str = "") -> Dict:
        """Build authenticated headers for a Bitget request."""
        timestamp = str(int(time.time() * 1000))
        prehash = timestamp + method.upper() + request_path + body
        sign = self._sign(prehash, self.api_secret)
        return {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": sign,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
            "locale": "en-US",
        }

    # ------------------------------------------------------------------
    # Position-mode detection
    # ------------------------------------------------------------------
    def _detect_position_mode(self) -> str:
        """
        Query Bitget account to determine if the account uses
        'hedge_mode' or 'one_way_mode' for positions.

        Falls back to 'one_way_mode' if credentials are missing or
        the request fails.
        """
        if not self.api_key or not self.api_secret:
            return "one_way_mode"
        try:
            ts = str(int(time.time() * 1000))
            path = "/api/v3/account/settings"
            prehash = ts + "GET" + path
            sign = self._sign(prehash, self.api_secret)
            headers = {
                "ACCESS-KEY": self.api_key,
                "ACCESS-SIGN": sign,
                "ACCESS-TIMESTAMP": ts,
                "ACCESS-PASSPHRASE": self.passphrase,
                "Content-Type": "application/json",
                "locale": "en-US",
            }
            resp = self.session.get(
                self.base_url + path,
                headers=headers,
                timeout=10,
            )
            data = resp.json()
            settings = data.get("data", {})
            pos_mode = settings.get("holdMode", "one_way_mode")
            print(f"[BITGET] 📋 Position mode detected: {pos_mode}")
            return pos_mode
        except Exception as e:
            print(f"[BITGET] ⚠️ Could not detect position mode ({e}) — defaulting to one_way_mode")
            return "one_way_mode"

    @property
    def is_hedge_mode(self) -> bool:
        """True if the account is using hedge (dual-side) position mode."""
        return self.position_mode == "hedge_mode"

    # ------------------------------------------------------------------
    # Low-level HTTP helpers (with retry / circuit-breaker)
    # ------------------------------------------------------------------
    def _public_get(self, path: str, params: Optional[Dict] = None, timeout: int = 10) -> Dict:
        """Unauthenticated GET."""
        url = self.base_url + path
        try:
            resp = self.session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "00000":
                raise ValueError(f"Bitget error {data.get('code')}: {data.get('msg')}")
            self.record_api_success()
            return data
        except Exception as e:
            self.record_api_error(str(e))
            raise

    def _auth_get(self, path: str, params: Optional[Dict] = None, timeout: int = 10) -> Dict:
        """Authenticated GET (params appended to query string for signing)."""
        qs = ""
        if params:
            pairs = sorted(params.items())
            qs = "?" + "&".join(f"{k}={v}" for k, v in pairs)
        full_path = path + qs
        headers = self._auth_headers("GET", full_path)
        url = self.base_url + full_path
        try:
            resp = self.session.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "00000":
                raise ValueError(f"Bitget error {data.get('code')}: {data.get('msg')}")
            self.record_api_success()
            return data
        except Exception as e:
            self.record_api_error(str(e))
            raise

    def _auth_post(self, path: str, body_dict: Dict, timeout: int = 10) -> Dict:
        """Authenticated POST."""
        body_json = json.dumps(body_dict)
        headers = self._auth_headers("POST", path, body_json)
        url = self.base_url + path
        try:
            resp = self.session.post(url, headers=headers, data=body_json, timeout=timeout)
            # Parse body BEFORE raise_for_status so we can log the real error
            try:
                data = resp.json()
            except Exception:
                data = {}
            if not resp.ok:
                err_code = data.get("code", resp.status_code)
                err_msg = data.get("msg", resp.text[:300])
                raise ValueError(f"Bitget HTTP {resp.status_code} — code={err_code}: {err_msg}")
            if data.get("code") != "00000":
                raise ValueError(f"Bitget error {data.get('code')}: {data.get('msg')}")
            self.record_api_success()
            return data
        except Exception as e:
            self.record_api_error(str(e))
            raise

    # ------------------------------------------------------------------
    # Symbol helpers & contract specs
    # ------------------------------------------------------------------
    def get_symbol(self, nemo: Optional[str] = None) -> str:
        """Map internal nemo → Bitget symbol.  e.g. 'BTC' → 'BTCUSDT'."""
        if nemo:
            return f"{nemo.upper()}USDT"
        return f"{self.lista_nemos[0].upper()}USDT"

    def _get_contract_spec(self, symbol: str) -> Dict:
        """
        Fetch & cache contract spec from /api/v3/market/instruments.
        Returns dict with pricePrecision, quantityPrecision, etc.
        """
        if not hasattr(self, "_contract_cache"):
            self._contract_cache: Dict[str, Dict] = {}
        if symbol in self._contract_cache:
            return self._contract_cache[symbol]
        try:
            data = self._public_get(
                "/api/v3/market/instruments",
                {"category": PRODUCT_TYPE, "symbol": symbol},
            )
            contracts = data.get("data", [])
            if contracts:
                spec = contracts[0]
                self._contract_cache[symbol] = spec
                return spec
        except Exception as e:
            print(f"[BITGET] ⚠️ Could not fetch contract spec for {symbol}: {e}")
        # Fallback defaults
        return {"pricePrecision": "2", "quantityPrecision": "4"}

    def format_quantity(self, symbol: str, quantity: float) -> float:
        """Round quantity to exchange-specified decimal places."""
        spec = self._get_contract_spec(symbol)
        decimals = int(spec.get("quantityPrecision",
                        spec.get("volumePlace", "4")))
        return round(quantity, decimals)

    def format_price(self, symbol: str, price: float) -> float:
        """Round price to exchange-specified decimal places."""
        spec = self._get_contract_spec(symbol)
        decimals = int(spec.get("pricePrecision",
                        spec.get("pricePlace", "2")))
        return round(price, decimals)

    # ------------------------------------------------------------------
    # MARKET DATA  (public, no auth)
    # ------------------------------------------------------------------
    def get_candles(
        self,
        nemo: str,
        granularity: str = "1m",
        limit: int = 100,
    ) -> pd.DataFrame:
        """
        GET /api/v2/mix/market/candles

        Returns DataFrame with columns:
            open_time, open, high, low, close, volume, quote_volume
        — same shape as BinanceData candles.
        """
        symbol = self.get_symbol(nemo)
        gran = INTERVAL_MAP.get(granularity, granularity)
        params = {
            "symbol": symbol,
            "productType": PRODUCT_TYPE,
            "granularity": gran,
            "limit": str(limit),
        }
        data = self._public_get("/api/v2/mix/market/candles", params)
        rows = []
        for r in data.get("data", []):
            ts_ms = int(r[0])
            rows.append({
                "open_time": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
                "quote_volume": float(r[6]) if len(r) > 6 else 0.0,
            })
        df = pd.DataFrame(rows)
        if not df.empty:
            df.sort_values("open_time", inplace=True)
            df.reset_index(drop=True, inplace=True)
        return df

    def get_orderbook(self, nemo: str, depth: int = 5) -> pd.DataFrame:
        """
        GET /api/v2/mix/market/merge-depth

        Returns DataFrame with columns:
            bid_price, bid_size, ask_price, ask_size
        — same shape expected by the existing get_bitso_lob / get_binance_lob.
        """
        symbol = self.get_symbol(nemo)
        limit_str = str(min(depth, 50))
        params = {
            "symbol": symbol,
            "productType": PRODUCT_TYPE,
            "limit": limit_str,
        }
        data = self._public_get("/api/v2/mix/market/merge-depth", params)
        inner = data.get("data", {})
        bids = inner.get("bids", [])
        asks = inner.get("asks", [])
        max_rows = max(len(bids), len(asks))
        rows = []
        for i in range(max_rows):
            row: Dict = {}
            if i < len(bids):
                row["bid_price"] = float(bids[i][0])
                row["bid_size"] = float(bids[i][1])
            else:
                row["bid_price"] = None
                row["bid_size"] = None
            if i < len(asks):
                row["ask_price"] = float(asks[i][0])
                row["ask_size"] = float(asks[i][1])
            else:
                row["ask_price"] = None
                row["ask_size"] = None
            rows.append(row)
        return pd.DataFrame(rows).head(depth)

    # ------------------------------------------------------------------
    # ACCOUNT / POSITIONS  (authenticated)
    # ------------------------------------------------------------------
    def get_account_info(self) -> Optional[Dict]:
        """
        GET /api/v3/account/assets

        Returns the raw response dict.
        """
        try:
            data = self._auth_get("/api/v3/account/assets")
            assets = data.get("data", {}).get("list", [])
            return assets[0] if assets else None
        except Exception as e:
            print(f"[BITGET] ❌ get_account_info error: {e}")
            return None

    def get_balance(self) -> pd.DataFrame:
        """
        Return account balance as DataFrame with columns:
            free (available), locked, total (accountEquity)
        Indexed by margin coin.
        """
        try:
            data = self._auth_get("/api/v3/account/assets")
            assets = data.get("data", {}).get("list", [])
            rows = {}
            for acc in assets:
                coin = acc.get("coin", "USDT")
                rows[coin] = {
                    "free": float(acc.get("available", 0)),
                    "locked": float(acc.get("frozen", 0)),
                    "total": float(acc.get("equity", 0)),
                }
            df = pd.DataFrame.from_dict(rows, orient="index")
            return df
        except Exception as e:
            print(f"[BITGET] ❌ get_balance error: {e}")
            return pd.DataFrame()

    def get_position_info(self, symbol: Optional[str] = None) -> Optional[List[Dict]]:
        """
        GET /api/v3/position/current-position?category=USDT-FUTURES

        Returns list of position dicts compatible with BinancePerpetualTrader:
            symbol, positionAmt, entryPrice, unrealizedProfit, leverage, marginType
        """
        try:
            params: Dict = {"category": PRODUCT_TYPE}
            if symbol:
                params["symbol"] = symbol if "USDT" in symbol.upper() else self.get_symbol(symbol)
            data = self._auth_get("/api/v3/position/current-position", params)
            positions_raw = data.get("data", {}).get("list", [])

            normalized: List[Dict] = []
            for pos in positions_raw:
                bg_symbol = pos.get("symbol", "")
                # Filter by nemo if requested
                if symbol and bg_symbol.upper() != symbol.upper():
                    continue
                hold_side = pos.get("posSide", "long")
                total = float(pos.get("total", 0))
                # Convention: short positions are negative
                signed_qty = total if hold_side == "long" else -total
                normalized.append({
                    "symbol": bg_symbol,
                    "positionAmt": signed_qty,
                    "entryPrice": float(pos.get("avgPrice", 0)),
                    "unrealizedProfit": float(pos.get("unrealisedPnl", 0)),
                    "leverage": int(pos.get("leverage", 1)),
                    "marginType": pos.get("marginMode", "crossed"),
                    # Bitget-specific extras
                    "holdSide": hold_side,
                    "available": float(pos.get("available", 0)),
                    "locked": float(pos.get("frozen", 0)),
                    "markPrice": float(pos.get("markPrice", 0)),
                    "liquidationPrice": pos.get("liquidationPrice", "0"),
                    "breakEvenPrice": pos.get("breakEvenPrice", "0"),
                    "achievedProfits": float(pos.get("curRealisedPnl", 0)),
                })
            return normalized
        except Exception as e:
            print(f"[BITGET] ❌ get_position_info error: {e}")
            traceback.print_exc()
            return None

    def set_leverage(self, leverage: int, symbol: Optional[str] = None) -> Optional[Dict]:
        """Set leverage (Bitget UTA v3 API)."""
        sym = self.get_symbol(symbol) if symbol else self.get_symbol()
        try:
            body = {
                "symbol": sym,
                "productType": PRODUCT_TYPE,
                "leverage": str(leverage),
            }
            return self._auth_post("/api/v3/account/set-leverage", body)
        except Exception as e:
            print(f"[BITGET] ❌ set_leverage error: {e}")
            return None

    def set_margin_type(self, margin_type: str = "crossed", symbol: Optional[str] = None) -> Optional[Dict]:
        """UTA only supports cross margin — no-op."""
        print("[BITGET] ℹ️ UTA account always uses cross margin — skipping set_margin_type")
        return None

    # ------------------------------------------------------------------
    # ORDER PLACEMENT
    # ------------------------------------------------------------------
    def place_market_order(
        self,
        side: str,
        quantity: float,
        reduce_only: bool = False,
        strategy_id: Optional[str] = None,
        nemo: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        POST /api/v3/trade/place-order  (market)

        Args:
            side: 'buy' or 'sell'
            quantity: base-coin amount
            reduce_only: if True, only reduces position
            strategy_id: optional custom tag
            nemo: base symbol (e.g. 'BTC')
        Returns:
            Bitget response dict with orderId
        """
        symbol = self.get_symbol(nemo)
        qty = self.format_quantity(symbol, abs(quantity))
        if qty <= 0:
            print(f"[BITGET] ⚠️ Skipping zero-quantity order for {symbol}")
            return None

        body: Dict = {
            "category": PRODUCT_TYPE,
            "symbol": symbol,
            "qty": str(qty),
            "side": side.lower(),
            "orderType": "market",
        }
        # Hedge mode requires posSide; one-way mode uses reduceOnly
        if self.is_hedge_mode:
            if reduce_only:
                body["posSide"] = "short" if side.lower() == "buy" else "long"
            else:
                body["posSide"] = "long" if side.lower() == "buy" else "short"
        else:
            if reduce_only:
                body["reduceOnly"] = "yes"
        if strategy_id:
            body["clientOid"] = f"{strategy_id}_{int(time.time()*1000)}"

        self._log_order_placement(symbol, "MARKET", side.upper(), qty)

        try:
            result = self._auth_post("/api/v3/trade/place-order", body)
            order_data = result.get("data", {})
            order_id = order_data.get("orderId", "N/A")
            print(f"[BITGET] ✅ Market order placed: {side.upper()} {qty} {symbol} → orderId={order_id}")
            self._log_order_placement(symbol, "MARKET", side.upper(), qty, order_id=order_id, status="ACCEPTED")
            return result
        except Exception as e:
            print(f"[BITGET] ❌ place_market_order error: {e}")
            self._log_order_placement(symbol, "MARKET", side.upper(), qty, status=f"REJECTED: {e}")
            return None

    def place_limit_order(
        self,
        side: str,
        quantity: float,
        price: float,
        time_in_force: str = "gtc",
        reduce_only: bool = False,
        strategy_id: Optional[str] = None,
        nemo: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        POST /api/v3/trade/place-order  (limit)
        """
        symbol = self.get_symbol(nemo)
        qty = self.format_quantity(symbol, abs(quantity))
        px = self.format_price(symbol, price)
        if qty <= 0:
            print(f"[BITGET] ⚠️ Skipping zero-quantity order for {symbol}")
            return None

        body: Dict = {
            "category": PRODUCT_TYPE,
            "symbol": symbol,
            "qty": str(qty),
            "price": str(px),
            "side": side.lower(),
            "orderType": "limit",
            "timeInForce": time_in_force.lower(),
        }
        # Hedge mode requires posSide; one-way mode uses reduceOnly
        if self.is_hedge_mode:
            if reduce_only:
                body["posSide"] = "short" if side.lower() == "buy" else "long"
            else:
                body["posSide"] = "long" if side.lower() == "buy" else "short"
        else:
            if reduce_only:
                body["reduceOnly"] = "yes"
        if strategy_id:
            body["clientOid"] = f"{strategy_id}_{int(time.time()*1000)}"

        self._log_order_placement(symbol, "LIMIT", side.upper(), qty, price=px)

        try:
            result = self._auth_post("/api/v3/trade/place-order", body)
            order_data = result.get("data", {})
            order_id = order_data.get("orderId", "N/A")
            print(f"[BITGET] ✅ Limit order placed: {side.upper()} {qty} {symbol} @ {px} → orderId={order_id}")
            self._log_order_placement(symbol, "LIMIT", side.upper(), qty, price=px, order_id=order_id, status="ACCEPTED")
            return result
        except Exception as e:
            print(f"[BITGET] ❌ place_limit_order error: {e}")
            self._log_order_placement(symbol, "LIMIT", side.upper(), qty, price=px, status=f"REJECTED: {e}")
            return None

    # ------------------------------------------------------------------
    # ORDER MANAGEMENT
    # ------------------------------------------------------------------
    def get_open_orders(self, symbol: Optional[str] = None) -> pd.DataFrame:
        """
        GET /api/v3/trade/unfilled-orders

        Returns DataFrame indexed by orderId with standard columns:
            symbol, side, type, quantity, price, status, time, exchange
        """
        try:
            params: Dict = {"category": PRODUCT_TYPE}
            if symbol:
                params["symbol"] = symbol if "USDT" in symbol.upper() else self.get_symbol(symbol)
            data = self._auth_get("/api/v3/trade/unfilled-orders", params)
            orders_list = data.get("data", {}).get("list", [])
            if not orders_list:
                return pd.DataFrame()

            rows = []
            for o in orders_list:
                rows.append({
                    "order_id": o.get("orderId"),
                    "symbol": o.get("symbol"),
                    "side": o.get("side"),
                    "type": o.get("orderType"),
                    "quantity": float(o.get("qty", 0)),
                    "price": float(o.get("price", 0)),
                    "status": o.get("status"),
                    "time": o.get("createdTime"),
                    "exchange": "BITGET",
                })
            df = pd.DataFrame(rows)
            if not df.empty and "order_id" in df.columns:
                df.set_index("order_id", inplace=True)
            return df
        except Exception as e:
            print(f"[BITGET] ❌ get_open_orders error: {e}")
            return pd.DataFrame()

    def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> Optional[Dict]:
        """
        POST /api/v3/trade/cancel-order
        """
        body = {
            "orderId": str(order_id),
            "category": PRODUCT_TYPE,
        }
        try:
            result = self._auth_post("/api/v3/trade/cancel-order", body)
            print(f"[BITGET] ✅ Order cancelled: {order_id}")
            return result
        except Exception as e:
            print(f"[BITGET] ❌ cancel_order error: {e}")
            return None

    def cancel_all_orders(self, symbol: Optional[str] = None) -> Optional[Dict]:
        """Cancel all open orders via POST /api/v3/trade/cancel-symbol-order."""
        body: Dict = {"category": PRODUCT_TYPE}
        if symbol:
            sym = symbol if "USDT" in symbol.upper() else self.get_symbol(symbol)
            body["symbol"] = sym
        try:
            result = self._auth_post("/api/v3/trade/cancel-symbol-order", body)
            print(f"[BITGET] ✅ All orders cancelled")
            return result
        except Exception as e:
            print(f"[BITGET] ❌ cancel_all_orders error: {e}")
            return None

    # ------------------------------------------------------------------
    # TRADE HISTORY
    # ------------------------------------------------------------------
    def get_trades(self, symbol: Optional[str] = None, limit: int = 50) -> pd.DataFrame:
        """
        GET /api/v3/trade/fills  (authenticated)

        Returns DataFrame with columns compatible with binance_perp.get_trades().
        """
        try:
            params: Dict = {"category": PRODUCT_TYPE, "limit": str(limit)}
            if symbol:
                params["symbol"] = symbol if "USDT" in symbol.upper() else self.get_symbol(symbol)
            data = self._auth_get("/api/v3/trade/fills", params)
            fills = data.get("data", {}).get("list", [])
            if not fills:
                return pd.DataFrame()
            rows = []
            for f in fills:
                # V3: fee is in feeDetail array
                fee_detail = f.get("feeDetail", [])
                fee = float(fee_detail[0].get("fee", 0)) if fee_detail else 0.0
                fee_coin = fee_detail[0].get("feeCoin", "USDT") if fee_detail else "USDT"
                rows.append({
                    "orderId": f.get("orderId"),
                    "symbol": f.get("symbol"),
                    "side": f.get("side"),
                    "price": float(f.get("execPrice", 0)),
                    "qty": float(f.get("execQty", 0)),
                    "commission": fee,
                    "commissionAsset": fee_coin,
                    "time": datetime.fromtimestamp(
                        int(f.get("createdTime", 0)) / 1000,
                        tz=timezone.utc
                    ) if f.get("createdTime") else None,
                    "exchange": "BITGET",
                })
            df = pd.DataFrame(rows)
            if not df.empty and "time" in df.columns:
                df.set_index("time", inplace=True)
            return df
        except Exception as e:
            print(f"[BITGET] ❌ get_trades error: {e}")
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # CIRCUIT BREAKER
    # ------------------------------------------------------------------
    def check_api_health(self) -> bool:
        health = self.api_health
        now = time.time()
        if health["status"] == "CIRCUIT_OPEN":
            if now - (health["last_error"] or 0) > self.circuit_breaker_timeout:
                health["status"] = "HALF_OPEN"
                return True
            return False
        return True

    def record_api_error(self, error: str):
        h = self.api_health
        h["error_count"] += 1
        h["last_error"] = time.time()
        if h["error_count"] >= self.circuit_breaker_threshold:
            if h["status"] != "CIRCUIT_OPEN":
                h["status"] = "CIRCUIT_OPEN"
                print(f"[BITGET] ⚠️ Circuit OPENED after {h['error_count']} errors — last: {error}")

    def record_api_success(self):
        h = self.api_health
        h["error_count"] = 0
        h["last_success"] = time.time()
        if h["status"] in ("CIRCUIT_OPEN", "HALF_OPEN"):
            h["status"] = "HEALTHY"
            print("[BITGET] ✅ Circuit breaker reset → HEALTHY")

    # ------------------------------------------------------------------
    # LOGGING
    # ------------------------------------------------------------------
    def _initialize_log_files(self):
        for fname, cols in [
            (self.orders_log_file, ["timestamp", "symbol", "exchange", "order_type", "side", "quantity", "price", "order_id", "status"]),
            (self.fills_log_file, ["timestamp", "symbol", "exchange", "order_id", "side", "quantity", "price", "commission", "response_type"]),
        ]:
            if not os.path.exists(fname):
                pd.DataFrame(columns=cols).to_csv(fname, index=False)
                print(f"[BITGET] Created log: {fname}")

    def _log_order_placement(
        self,
        symbol: str,
        order_type: str,
        side: str,
        quantity: float,
        price: Optional[float] = None,
        order_id: Optional[str] = None,
        status: str = "SENDING",
    ):
        try:
            row = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol,
                "exchange": "BITGET",
                "order_type": order_type,
                "side": side,
                "quantity": quantity,
                "price": price if price else "N/A",
                "order_id": order_id if order_id else "N/A",
                "status": status,
            }
            pd.DataFrame([row]).to_csv(self.orders_log_file, mode="a", header=False, index=False)
        except Exception as e:
            print(f"[BITGET] ⚠️ log error: {e}")

    def _log_fill_event(self, symbol, order_id, side, quantity, price, commission, response_type="FILL"):
        try:
            row = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol,
                "exchange": "BITGET",
                "order_id": order_id,
                "side": side,
                "quantity": quantity,
                "price": price,
                "commission": commission,
                "response_type": response_type,
            }
            pd.DataFrame([row]).to_csv(self.fills_log_file, mode="a", header=False, index=False)
        except Exception as e:
            print(f"[BITGET] ⚠️ log error: {e}")

    # ------------------------------------------------------------------
    # FILL METRICS  (compatible with binance_perp interface)
    # ------------------------------------------------------------------
    def record_immediate_fill(self, latency_ms: float):
        self.fill_metrics["immediate_fills"] += 1
        n = self.fill_metrics["immediate_fills"]
        self.fill_metrics["avg_latency_ms"] = (
            self.fill_metrics["avg_latency_ms"] * (n - 1) + latency_ms
        ) / n

    def record_polling_fill(self):
        self.fill_metrics["polling_fills"] += 1

    def get_fill_metrics(self) -> dict:
        return self.fill_metrics.copy()

    def print_fill_metrics(self):
        m = self.fill_metrics
        print(f"\n{'='*50}")
        print("📊 BITGET FILL METRICS")
        print(f"{'='*50}")
        print(f"  Immediate fills : {m['immediate_fills']}")
        print(f"  Polling fills   : {m['polling_fills']}")
        print(f"  Avg latency     : {m['avg_latency_ms']:.1f} ms")
        print(f"{'='*50}\n")

    # ------------------------------------------------------------------
    # VALIDATE ORDER  (pre-flight)
    # ------------------------------------------------------------------
    def validate_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: Optional[float] = None,
        order_type: str = "LIMIT",
    ) -> Tuple[bool, Optional[str]]:
        """Basic pre-flight validation."""
        if quantity <= 0:
            return False, "Quantity must be > 0"
        if order_type.upper() == "LIMIT" and (price is None or price <= 0):
            return False, "Limit orders require a positive price"
        if side.lower() not in ("buy", "sell"):
            return False, f"Invalid side: {side}"
        return True, None


# ============================================================================
# Standalone quick-test
# ============================================================================
if __name__ == "__main__":
    print("=" * 70)
    print("BITGET PERPETUAL TRADER — Quick Smoke Test")
    print("=" * 70)

    trader = BitgetPerpetualTrader(lista_nemos=["BTC", "ETH"])

    print("\n1) Candles (BTC, 1m, 5 bars)")
    try:
        candles = trader.get_candles("BTC", "1m", 5)
        print(candles)
    except Exception as e:
        print(f"   FAILED: {e}")

    print("\n2) Order book (BTC, depth=3)")
    try:
        ob = trader.get_orderbook("BTC", 3)
        print(ob)
    except Exception as e:
        print(f"   FAILED: {e}")

    print("\n3) Account balance (requires keys)")
    try:
        bal = trader.get_balance()
        print(bal)
    except Exception as e:
        print(f"   SKIPPED/FAILED: {e}")

    print("\n4) All positions (requires keys)")
    try:
        pos = trader.get_position_info()
        print(pos)
    except Exception as e:
        print(f"   SKIPPED/FAILED: {e}")

    print("\nDone.")
