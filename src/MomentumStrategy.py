# -*- coding: utf-8 -*-
"""
MomentumStrategy.py
-------------------
Multi-asset momentum strategy for live trading.
Implements the tanh-momentum and Turtle N-Weight signals
from Momentum_Backtest.ipynb.
"""

import datetime
import time
import numpy as np
import pandas as pd

from Estrategia import Estrategia
from Eventos import EventoSenal


DEFAULT_UNIVERSE = [
    "AAVE", "AIXBT", "AVAX", "BCH", "BNB", "BTC", "COMP", "DOGE", "DOT",
    "DYDX", "EIGEN", "ENA", "ETH", "ETHFI", "FORM", "INJ", "JUP", "LTC",
    "NEAR", "PNUT", "RAY", "SOL", "SUI", "TRX", "UNI", "WIF",
]


class MomentumStrategy(Estrategia):
    """
    Daily momentum strategy with two variants:
      - 'tanh':   equal-weight normalized tanh(momentum), clipped to ±max_weight
      - 'turtle': inverse-ATR weighted tanh(momentum), normalized to gross=1.0

    Signal: tanh((price[t-short_w] - price[t-long_w]) / price[t-long_w])
    """

    REBALANCE_DATA_BARS = 60

    def __init__(self, velas, eventos, signal_logger=None,
                 variant='turtle', short_window=5, long_window=30,
                 max_weight=0.10, stop_loss_pct=-0.15, atr_period=20,
                 min_rebalance_threshold=0.005):
        self.velas = velas
        self.lista_nemos = list(velas.lista_nemos)
        self.eventos = eventos
        self.signal_logger = signal_logger

        self.variant = variant
        self.short_window = short_window
        self.long_window = long_window
        self.max_weight = max_weight
        self.stop_loss_pct = stop_loss_pct
        self.atr_period = atr_period
        self.min_rebalance_threshold = min_rebalance_threshold

        self.target_weights = {n: 0.0 for n in self.lista_nemos}
        self.last_signal_date = None
        self.portafolio = None

        self.df_prices = None
        self.df_high = None
        self.df_low = None
        self.df_atr = None

        self.ordenes_pendientes = {n: None for n in self.lista_nemos}
        self._price_cache = {}  # REST fallback: {nemo: (price, timestamp)}
        self._data_ready = False

    def set_portafolio(self, portafolio):
        self.portafolio = portafolio

    # ------------------------------------------------------------------
    # Data layer
    # ------------------------------------------------------------------

    def refresh_daily_data(self):
        """Fetch daily OHLCV for all assets via REST.
        Builds df_prices, df_high, df_low and (for turtle) df_atr."""
        limit = self.REBALANCE_DATA_BARS
        closes, highs, lows = {}, {}, {}
        skipped = []

        for nemo in self.lista_nemos:
            try:
                df = self.velas.get_perpetual_ohlcv(nemo, '1d', limit=limit)
                if df is None or df.empty or len(df) < self.long_window + 2:
                    skipped.append(nemo)
                    continue
                closes[nemo] = df['close'].astype(float)
                highs[nemo] = df['high'].astype(float)
                lows[nemo] = df['low'].astype(float)
            except Exception as e:
                print(f"[MomentumStrategy] Error fetching {nemo}: {e}")
                skipped.append(nemo)

        if skipped:
            print(f"[MomentumStrategy] Skipped {len(skipped)} assets: {skipped}")

        if not closes:
            print("[MomentumStrategy] No data fetched — cannot compute weights")
            self._data_ready = False
            return

        self.df_prices = pd.DataFrame(closes)
        self.df_high = pd.DataFrame(highs)
        self.df_low = pd.DataFrame(lows)

        if self.variant == 'turtle':
            self.df_atr = self._build_rolling_atr(
                self.df_high, self.df_low, self.df_prices, self.atr_period
            )

        # Cache latest close prices for REST fallback
        for col in self.df_prices.columns:
            last_price = self.df_prices[col].dropna().iloc[-1]
            if last_price > 0:
                self._price_cache[col] = (float(last_price), time.time())

        self._data_ready = True
        print(f"[MomentumStrategy] Daily data refreshed: "
              f"{len(self.df_prices.columns)} assets, {len(self.df_prices)} bars")

    @staticmethod
    def _build_rolling_atr(df_high, df_low, df_close, period=20):
        """Vectorised rolling ATR (causally shifted by 1 day).
        Matches build_rolling_atr from Momentum_Backtest.ipynb."""
        c_prev = df_close.shift(1)
        tr1 = df_high - df_low
        tr2 = (df_high - c_prev).abs()
        tr3 = (df_low - c_prev).abs()
        tr = pd.DataFrame(
            {col: pd.concat([tr1[col], tr2[col], tr3[col]], axis=1).max(axis=1)
             for col in df_high.columns}
        )
        atr = tr.rolling(period).mean()
        return atr.shift(1)

    # ------------------------------------------------------------------
    # Weight computation — mirrors Momentum_Backtest.ipynb exactly
    # ------------------------------------------------------------------

    def compute_weights(self):
        """Compute target weights for today. Returns {nemo: float}.

        Replicates run_momentum_backtest / run_turtle_backtest from the
        notebook. Uses the LAST ROW of the weight DataFrame (today's
        signal to be executed tomorrow), matching the backtest's shift(1).
        """
        if not self._data_ready or self.df_prices is None:
            print("[MomentumStrategy] Data not ready — skipping weight computation")
            return self.target_weights

        prices = self.df_prices
        sw, lw = self.short_window, self.long_window

        # Momentum indicator (identical to notebook)
        df_momo = (prices.shift(sw) - prices.shift(lw)) / prices.shift(lw)

        if self.variant == 'turtle':
            df_w = self._turtle_weights(df_momo, prices)
        else:
            df_w = self._tanh_weights(df_momo, prices)

        # Extract last valid row
        last_row = df_w.iloc[-1]
        self.target_weights = {n: float(last_row.get(n, 0.0)) for n in self.lista_nemos}
        self.last_signal_date = datetime.date.today()

        n_long = sum(1 for w in self.target_weights.values() if w > 0)
        n_short = sum(1 for w in self.target_weights.values() if w < 0)
        gross = sum(abs(w) for w in self.target_weights.values())
        print(f"[MomentumStrategy] Weights computed: {n_long} long, {n_short} short, "
              f"gross={gross:.4f}, variant={self.variant}")

        return self.target_weights

    def _tanh_weights(self, df_momo, prices):
        """Tanh-momentum with equal-weight normalisation + clip."""
        df_raw_w = np.tanh(df_momo)
        df_raw_w = df_raw_w.where(prices.notna(), 0).fillna(0)

        total_abs = df_raw_w.abs().sum(axis=1)
        df_w = df_raw_w.div(total_abs.replace(0, np.nan), axis=0).fillna(0)
        df_w = df_w.clip(lower=-self.max_weight, upper=self.max_weight)

        # Stop-loss filter
        df_returns = prices.pct_change()
        df_w = df_w.where(df_returns.shift(1) >= self.stop_loss_pct, 0)

        return df_w

    def _turtle_weights(self, df_momo, prices):
        """Turtle N-Weight: direction × inverse ATR, normalised."""
        df_dir = np.tanh(df_momo)
        df_dir = df_dir.where(prices.notna(), 0).fillna(0)

        atr = self.df_atr
        if atr is None:
            return self._tanh_weights(df_momo, prices)

        # Align columns
        common = prices.columns.intersection(atr.columns)
        df_n_pct = atr[common] / prices[common]
        df_inv_n = 1.0 / df_n_pct.replace(0, np.nan)
        df_inv_n = df_inv_n.fillna(0)

        df_raw_w = df_dir[common] * df_inv_n
        total_abs = df_raw_w.abs().sum(axis=1)
        df_w = df_raw_w.div(total_abs.replace(0, np.nan), axis=0).fillna(0)

        # Stop-loss filter
        df_returns = prices[common].pct_change()
        df_w = df_w.where(df_returns.shift(1) >= self.stop_loss_pct, 0)

        # Re-add any missing nemos as zero-weight columns
        for n in self.lista_nemos:
            if n not in df_w.columns:
                df_w[n] = 0.0

        return df_w

    # ------------------------------------------------------------------
    # Signal emission
    # ------------------------------------------------------------------

    def calcular_senales(self, evento=None):
        """Compare target weights vs current portfolio weights.
        Emit EventoSenal for each asset needing rebalancing."""
        if not self._data_ready:
            return

        if self.portafolio is None:
            print("[MomentumStrategy] No portfolio linked — cannot emit signals")
            return

        portfolio_value = self._get_portfolio_value()
        if portfolio_value <= 0:
            return

        now = datetime.datetime.utcnow()
        signals_emitted = 0

        for nemo in self.lista_nemos:
            target_w = self.target_weights.get(nemo, 0.0)

            current_qty = self.portafolio.posiciones_actuales.get(nemo, 0)
            price = self._get_price(nemo)
            if price is None or price <= 0:
                continue

            current_value = current_qty * price
            current_w = current_value / portfolio_value

            delta_w = target_w - current_w

            if abs(delta_w) < self.min_rebalance_threshold:
                continue

            if target_w == 0.0 and abs(current_w) > self.min_rebalance_threshold:
                tipo = 'FUERA'
            elif target_w > 0:
                tipo = 'LARGO'
            else:
                tipo = 'CORTO'

            senal = EventoSenal(
                id_estrategia=1,
                nemo=nemo,
                datetime=now,
                tipo_senal=tipo,
                fuerza=target_w,
            )
            self.eventos.put(senal)
            signals_emitted += 1

            if self.signal_logger:
                try:
                    self.signal_logger.log_signal(senal, z_score=None,
                                                   is_cointegrated=True)
                except Exception:
                    pass

        print(f"[MomentumStrategy] Emitted {signals_emitted} rebalance signals")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_portfolio_value(self):
        """Total portfolio value: cash + market value of positions."""
        if self.portafolio is None:
            return 0.0
        total = self.portafolio.cuenta_actual.get('Caja', 0)
        for nemo in self.lista_nemos:
            qty = self.portafolio.posiciones_actuales.get(nemo, 0)
            price = self._get_price(nemo)
            if price and qty:
                total += qty * price
        return total

    def _get_price(self, nemo):
        """Last close price for a symbol.
        Tries WebSocket buffer first, falls back to cached REST price."""
        try:
            p = self.velas.get_valor_ultima_vela(nemo, 'close')
            if p is not None:
                return float(p)
        except Exception:
            pass
        # Fallback: use price cached from refresh_daily_data()
        cached = self._price_cache.get(nemo)
        if cached:
            return cached[0]
        return None

    def limpia_orden_pendiente(self, nemo):
        if nemo in self.ordenes_pendientes:
            self.ordenes_pendientes[nemo] = None

    def actualiza_orden_pendiente(self, nemo, direction):
        if nemo in self.ordenes_pendientes:
            self.ordenes_pendientes[nemo] = direction
