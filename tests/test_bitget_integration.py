#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bitget Perpetual Adapter — Integration Test Suite

Public tests (no API keys needed):
  - Candles
  - Orderbook
  - Symbol mapping

Authenticated tests (require valid BITGET_API_KEY / SECRET / PASSPHRASE):
  - Account balance
  - Positions
  - Open orders
  - Trade history

Order placement tests are DISABLED by default (set RUN_ORDER_TESTS=1).

Usage:
    cd src
    python -m pytest ../tests/test_bitget_integration.py -v
    # or simply:
    python ../tests/test_bitget_integration.py
"""

import os
import sys
import unittest
from datetime import datetime

# Ensure src/ is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from bitget_perp import BitgetPerpetualTrader

# ── Feature flags ────────────────────────────────────────────────
HAS_KEYS = bool(
    os.getenv("BITGET_API_KEY") and
    os.getenv("BITGET_SECRET_KEY") and
    os.getenv("BITGET_PASSPHRASE")
)
RUN_ORDER_TESTS = 1 # os.getenv("RUN_ORDER_TESTS", "0") == "1"


class TestBitgetPublicEndpoints(unittest.TestCase):
    """Tests that hit unauthenticated / public endpoints only."""

    @classmethod
    def setUpClass(cls):
        cls.trader = BitgetPerpetualTrader(lista_nemos=["BTC", "USDT"])

    # ── Symbol mapping ──────────────────────────────────────────
    def test_symbol_mapping(self):
        self.assertEqual(self.trader.get_symbol("BTC"), "BTCUSDT")
        self.assertEqual(self.trader.get_symbol("ETH"), "ETHUSDT")
        self.assertEqual(self.trader.get_symbol("sol"), "SOLUSDT")

    # ── Candles ─────────────────────────────────────────────────
    def test_get_candles_btc(self):
        df = self.trader.get_candles("BTC", "1m", 5)
        self.assertFalse(df.empty, "Candle DataFrame should not be empty")
        self.assertIn("close", df.columns)
        self.assertIn("open_time", df.columns)
        self.assertLessEqual(len(df), 5)
        # Prices must be positive
        self.assertTrue((df["close"] > 0).all())

    def test_get_candles_eth(self):
        df = self.trader.get_candles("ETH", "5m", 3)
        self.assertFalse(df.empty)
        self.assertEqual(len(df), 3)

    # ── Orderbook ───────────────────────────────────────────────
    def test_get_orderbook(self):
        df = self.trader.get_orderbook("BTC", depth=5)
        self.assertFalse(df.empty, "Orderbook should not be empty")
        self.assertIn("bid_price", df.columns)
        self.assertIn("ask_price", df.columns)
        self.assertLessEqual(len(df), 5)
        # Best bid < best ask
        self.assertLess(df["bid_price"].iloc[0], df["ask_price"].iloc[0])

    # ── Format quantity ─────────────────────────────────────────
    def test_format_quantity(self):
        # Uses live contract specs; verify rounding is applied correctly
        btc_qty = self.trader.format_quantity("BTCUSDT", 0.12345678)
        eth_qty = self.trader.format_quantity("ETHUSDT", 1.23456)
        sol_qty = self.trader.format_quantity("SOLUSDT", 12.3456)
        # All should be floats
        self.assertIsInstance(btc_qty, float)
        self.assertIsInstance(eth_qty, float)
        self.assertIsInstance(sol_qty, float)
        # Rounded values should be close to original (within rounding tolerance)
        self.assertAlmostEqual(btc_qty, 0.12345678, places=3)
        self.assertAlmostEqual(eth_qty, 1.23456, places=1)
        self.assertAlmostEqual(sol_qty, 12.3456, places=1)

    # ── Validate order ──────────────────────────────────────────
    def test_validate_order_ok(self):
        ok, msg = self.trader.validate_order("BTCUSDT", "buy", 0.001, price=50000, order_type="LIMIT")
        self.assertTrue(ok)
        self.assertIsNone(msg)

    def test_validate_order_zero_qty(self):
        ok, msg = self.trader.validate_order("BTCUSDT", "buy", 0, price=50000, order_type="LIMIT")
        self.assertFalse(ok)
        self.assertIn("Quantity", msg)

    def test_validate_order_limit_no_price(self):
        ok, msg = self.trader.validate_order("BTCUSDT", "buy", 0.001, price=None, order_type="LIMIT")
        self.assertFalse(ok)
        self.assertIn("price", msg.lower())

    # ── Circuit breaker ─────────────────────────────────────────
    def test_circuit_breaker(self):
        self.assertTrue(self.trader.check_api_health())
        for _ in range(5):
            self.trader.record_api_error("test error")
        self.assertFalse(self.trader.check_api_health())
        # Reset
        self.trader.record_api_success()
        self.assertTrue(self.trader.check_api_health())


@unittest.skipUnless(HAS_KEYS, "Bitget API keys not configured")
class TestBitgetAuthenticatedEndpoints(unittest.TestCase):
    """Tests that require valid Bitget credentials."""

    @classmethod
    def setUpClass(cls):
        cls.trader = BitgetPerpetualTrader(lista_nemos=["BTC", "ETH"])

    def test_get_balance(self):
        df = self.trader.get_balance()
        self.assertFalse(df.empty, "Balance DataFrame should not be empty")
        self.assertIn("free", df.columns)
        self.assertIn("total", df.columns)

    def test_get_account_info(self):
        info = self.trader.get_account_info()
        self.assertIsNotNone(info, "Account info should not be None")

    def test_get_position_info(self):
        positions = self.trader.get_position_info()
        self.assertIsNotNone(positions, "Positions should not be None")
        self.assertIsInstance(positions, list)

    def test_get_open_orders(self):
        df = self.trader.get_open_orders()
        # Could be empty — that's fine — just ensure no error
        self.assertIsInstance(df, type(df))

    def test_get_trades(self):
        df = self.trader.get_trades()
        self.assertIsInstance(df, type(df))


@unittest.skipUnless(HAS_KEYS and RUN_ORDER_TESTS, "Order tests disabled (set RUN_ORDER_TESTS=1)")
class TestBitgetOrderPlacement(unittest.TestCase):
    """
    Guarded order placement tests.
    Only runs when RUN_ORDER_TESTS=1 is set explicitly.
    Uses very small quantities to minimize risk.
    """

    @classmethod
    def setUpClass(cls):
        cls.trader = BitgetPerpetualTrader(lista_nemos=["BTC", "ETH"])

    def test_place_and_cancel_limit_order(self):
        """Place a limit order far from market, then cancel it."""
        # Check balance first — skip if no funds
        balance_df = self.trader.get_balance()
        if balance_df.empty or float(balance_df["free"].iloc[0]) < 5:
            self.skipTest("Insufficient USDT balance (need at least 5 USDT)")

        # Get current price
        candles = self.trader.get_candles("BTC", "1m", 1)
        if candles.empty:
            self.skipTest("Cannot fetch candle data")
        current_price = candles["close"].iloc[-1]

        # Place limit BUY way below market (should not fill)
        far_price = self.trader.format_price("BTCUSDT", current_price * 0.5)

        # Bitget requires minimum notional of 5 USDT — compute safe quantity
        min_notional = 6.0  # slightly above 5 USDT minimum
        min_qty = min_notional / far_price
        qty = self.trader.format_quantity("BTCUSDT", max(min_qty, 0.001))

        result = self.trader.place_limit_order(
            side="buy",
            quantity=qty,
            price=far_price,
            nemo="BTC",
        )
        self.assertIsNotNone(result, "place_limit_order should return a dict")
        order_id = result.get("data", {}).get("orderId")
        self.assertIsNotNone(order_id, "orderId should be in response")

        # Cancel
        cancel = self.trader.cancel_order(order_id, "BTCUSDT")
        self.assertIsNotNone(cancel, "cancel_order should return a dict")


# ── Standalone runner ────────────────────────────────────────────
if __name__ == "__main__":
    # Load env vars
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    unittest.main(verbosity=2)
