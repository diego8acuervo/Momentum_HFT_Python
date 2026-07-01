# -*- coding: utf-8 -*-
"""
MomentumPortfolio.py
--------------------
Weight-based portfolio for the momentum strategy.
Extends AQMPortHFT to reuse position tracking, fill processing,
equity curve and position sync. Overrides signal-to-order conversion
for weight-based rebalancing.
"""

import datetime
import numpy as np
from Eventos import EventoOrden
from PortAQMHFT import AQMPortHFT


class MomentumPortfolio(AQMPortHFT):
    """Portfolio that converts target-weight signals into rebalance orders.

    Inherits from AQMPortHFT:
      - posiciones_actuales, todas_posiciones, cuenta_actual, cuenta
      - actualiza_calce(), actualiza_tiempo()
      - sync_positions_from_exchange()
      - curva_equity_dataframe(), output_resumen_estadisticas()
    """

    def __init__(self, velas, eventos, fecha_inicial, capital_inicial=50000,
                 estrategia=None, order_logger=None, lista_bolsas=None,
                 batch_n=1, batch_interval_s=300, limit_offset_bps=2,
                 min_rebalance_threshold=0.005):
        super().__init__(
            velas, eventos, fecha_inicial, capital_inicial,
            estrategia, order_logger, lista_bolsas,
            batch_n, batch_interval_s, limit_offset_bps,
        )
        self.min_rebalance_threshold = min_rebalance_threshold

    # ------------------------------------------------------------------
    # Override: weight-based signal → order conversion
    # ------------------------------------------------------------------

    def actualiza_senal(self, evento):
        """Handle LARGO/CORTO/FUERA signals with weight-based sizing.

        The strategy sets ``evento.fuerza`` to the target weight for
        LARGO/CORTO signals (positive = long, negative = short) and 0
        for FUERA.
        """
        if evento.type != 'SEÑAL':
            return

        nemo = evento.nemo
        target_weight = evento.fuerza
        tipo = evento.tipo_senal

        price = self._current_price(nemo)
        if price is None or price <= 0:
            print(f"[MomentumPortfolio] No price for {nemo} — skipping order")
            return

        portfolio_value = self._portfolio_value()
        if portfolio_value <= 0:
            return

        current_qty = self.posiciones_actuales.get(nemo, 0)

        if tipo == 'FUERA':
            # Close entire position
            if abs(current_qty) < self.DUST_THRESHOLD:
                return
            direccion = 'sell' if current_qty > 0 else 'buy'
            cantidad = abs(current_qty)
            signal_type = 'FUERA'
        else:
            # Compute target quantity from weight
            target_notional = target_weight * portfolio_value
            target_qty = target_notional / price
            qty_delta = target_qty - current_qty

            if abs(qty_delta * price) < 5.0:
                return

            if qty_delta > 0:
                direccion = 'buy'
            else:
                direccion = 'sell'
            cantidad = abs(qty_delta)
            signal_type = 'REBALANCE'

        bolsa = self.lista_bolsas[0] if self.lista_bolsas else 'BINANCEFTS'

        orden = EventoOrden(
            nemo=nemo,
            tipo_orden='MKT',
            cantidad=cantidad,
            direccion=direccion,
            precio=price,
            bolsa=bolsa,
            timestamp=evento.datetime,
            signal_type=signal_type,
            batch_n=self.batch_n if signal_type != 'FUERA' else 1,
            batch_interval_s=self.batch_interval_s,
            limit_offset_bps=self.limit_offset_bps,
        )

        if self.estrategia:
            self.estrategia.actualiza_orden_pendiente(nemo, direccion)

        if self.order_logger:
            try:
                self.order_logger.log_order(
                    orden, signal_type=signal_type,
                    cash_available=self.cuenta_actual['Caja'],
                    price_used=price, final_quantity=cantidad,
                )
            except Exception:
                pass

        self.eventos.put(orden)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _current_price(self, nemo):
        """WebSocket price, falling back to strategy's cached REST price."""
        try:
            p = self.velas.get_valor_ultima_vela(nemo, 'close')
            if p is not None:
                return float(p)
        except Exception:
            pass
        # Fallback: strategy's price cache from daily REST data
        if self.estrategia and hasattr(self.estrategia, '_price_cache'):
            cached = self.estrategia._price_cache.get(nemo)
            if cached:
                return cached[0]
        return None

    def _portfolio_value(self):
        """Cash + mark-to-market value of all positions."""
        total = self.cuenta_actual.get('Caja', 0)
        for nemo in self.lista_nemos:
            qty = self.posiciones_actuales.get(nemo, 0)
            price = self._current_price(nemo)
            if price and qty:
                total += qty * price
        return total
