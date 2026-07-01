# -*- coding: utf-8 -*-
"""
MomentumLiveMonitor.py
----------------------
Momentum-specific live monitor. Writes JSON state with
target/current weights, per-asset positions, and aggregate
portfolio metrics.
"""

import datetime
from LiveMonitor import LiveMonitor


class MomentumLiveMonitor(LiveMonitor):
    """LiveMonitor variant for the multi-asset momentum strategy."""

    JSON_FILENAME = "live_state_momentum.json"

    def __init__(self, admin_datos, estrategia, portafolio,
                 admin_ejecucion, lista_nemos, **kwargs):
        super().__init__(
            admin_datos, estrategia, portafolio,
            admin_ejecucion, lista_nemos, **kwargs,
        )
        self._pair = "MOMENTUM"

    # ------------------------------------------------------------------
    # Overrides
    # ------------------------------------------------------------------

    def _build_snapshot(self):
        ts = datetime.datetime.utcnow().isoformat() + "Z"
        return {
            "timestamp": ts,
            "strategy_type": "MOMENTUM",
            "pair": self.nemos,
            "market_data": self._market_data(),
            "positions": self._positions(),
            "strategy": self._strategy_state(),
            "account": self._account(),
            "weights": self._weights(),
            "circuit_breaker": self._circuit_breaker_state(),
        }

    def _strategy_state(self):
        strat = self.strat
        tw = getattr(strat, 'target_weights', {})
        return {
            "variant": getattr(strat, 'variant', '?'),
            "short_window": getattr(strat, 'short_window', None),
            "long_window": getattr(strat, 'long_window', None),
            "last_signal_date": str(getattr(strat, 'last_signal_date', None)),
            "data_ready": getattr(strat, '_data_ready', False),
            "n_long": sum(1 for w in tw.values() if w > 0),
            "n_short": sum(1 for w in tw.values() if w < 0),
            "n_flat": sum(1 for w in tw.values() if w == 0),
            "gross_exposure": round(sum(abs(w) for w in tw.values()), 6),
            "net_exposure": round(sum(tw.values()), 6),
        }

    def _weights(self):
        """Per-asset target vs current weight breakdown."""
        strat = self.strat
        tw = getattr(strat, 'target_weights', {})
        pv = self._compute_portfolio_value()

        rows = {}
        for nemo in self.nemos:
            qty = self.port.posiciones_actuales.get(nemo, 0)
            price = self._safe_price(nemo)
            pos_value = qty * price if price else 0
            current_w = pos_value / pv if pv else 0
            target_w = tw.get(nemo, 0)

            rows[nemo] = {
                "target_weight": round(target_w, 6),
                "current_weight": round(current_w, 6),
                "weight_delta": round(target_w - current_w, 6),
                "direction": (
                    "LONG" if target_w > 0.001
                    else "SHORT" if target_w < -0.001
                    else "FLAT"
                ),
                "position_value": round(pos_value, 2),
            }
        return rows

    def _compute_portfolio_value(self):
        total = self.port.cuenta_actual.get('Caja', 0)
        for nemo in self.nemos:
            qty = self.port.posiciones_actuales.get(nemo, 0)
            price = self._safe_price(nemo)
            if price and qty:
                total += qty * price
        return total

    def _safe_price(self, nemo):
        try:
            p = self.datos.get_valor_ultima_vela(nemo, 'close')
            if p is not None:
                return float(p)
        except Exception:
            pass
        if hasattr(self.strat, '_price_cache'):
            cached = self.strat._price_cache.get(nemo)
            if cached:
                return cached[0]
        return None

    # ------------------------------------------------------------------
    # CLI output
    # ------------------------------------------------------------------

    def _print_cli(self, snap):
        ts = snap["timestamp"][:19]
        strat = snap["strategy"]
        acct = snap["account"]
        weights = snap.get("weights", {})

        n_long = strat.get("n_long", 0)
        n_short = strat.get("n_short", 0)
        gross = strat.get("gross_exposure", 0)

        total_pnl = 0.0
        for nemo in self.nemos:
            ps = snap["positions"].get(nemo, {})
            pnl = ps.get("unrealized_pnl")
            if pnl is not None:
                total_pnl += pnl

        equity = (acct.get("total_equity") or 0)

        print()
        print("┌" + "─" * 68 + "┐")
        print(f"│  📡 MOMENTUM MONITOR  {ts}  "
              f"{strat.get('variant', '?').upper()}  "
              f"S={strat.get('short_window')} L={strat.get('long_window')}")
        print(f"│  Long={n_long}  Short={n_short}  "
              f"Gross={gross:.3f}  uPnL=${total_pnl:+,.2f}  "
              f"Equity=${equity:,.0f}")
        print("├" + "─" * 68 + "┤")

        # Top movers (largest |weight_delta|)
        sorted_w = sorted(weights.items(),
                          key=lambda kv: abs(kv[1].get("weight_delta", 0)),
                          reverse=True)
        for nemo, w in sorted_w[:6]:
            tw = w.get("target_weight", 0)
            cw = w.get("current_weight", 0)
            dw = w.get("weight_delta", 0)
            d = w.get("direction", "FLAT")
            print(f"│  {nemo:>6}  target={tw:+.4f}  "
                  f"current={cw:+.4f}  delta={dw:+.4f}  {d}")

        if len(sorted_w) > 6:
            print(f"│  ... and {len(sorted_w) - 6} more assets")

        print("└" + "─" * 68 + "┘")
