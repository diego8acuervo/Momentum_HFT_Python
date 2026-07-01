# -*- coding: utf-8 -*-

"""

Created on Fri Jul 28 07:59:06 2017



@author: Diego Ochoa

"""
from __future__ import print_function
import datetime
from math import floor
import queue  # Python 3
import numpy as np
import pandas as pd
try:
    import matplotlib.pyplot as plt
except ImportError:
    plt = None
import os
from Eventos import EventoCalce, EventoOrden, EventoSenal
from performance import sharpe_ratio, max_drawdowns,kelly

#===================================================================================================
#===================================================================================================
class AQMPortHFT(object):
    """
    La clase portafolio administra las posiciones acutales y pasadas, con
    una resolución equivalente a una vela que puede ser de diverso tamaño
    (segundos, minutos,horas,día).

    El DataFrame de posiciones almacena un indice de tiempo de las cantidades
    y posiciones abiertas. 

    El DataFrame de Holdings almacena la caja y las tenencias totales a mer-
    cado para cada nemotécnico para un índice de tiempo en particular, así como 
    el porcentaje de cambio en el portafolio a través de todas las velas
    """

    def __init__(self, velas, eventos, fecha_inicial, capital_inicial=50000, estrategia=None, order_logger=None, lista_bolsas=None,
                 batch_n=1, batch_interval_s=300, limit_offset_bps=2):
        """
        Initialises the portfolio with bars and an event queue. 
        Also includes a starting datetime index and initial capital 
        (USD unless otherwise stated).

        Parameters:
        velas - El objeto de Admindatos con los datos de mercado actuales.
        eventos - El objeto Fila de eventos(queue).
        fecha_inicial - El tiempo de la vela de inicio del portafolio.
        capital_inicial - El capital inicial en USD.
        estrategia - Reference to strategy for pending order notifications.
        order_logger - SignalOrderLogger instance for debugging (optional).
        """
        self.velas = velas
        self.eventos = eventos
        self.lista_nemos = self.velas.lista_nemos
        self.lista_bolsas = lista_bolsas or []
        self.fecha_inicial = fecha_inicial
        self.fecha_final = datetime.datetime.now()
        self.capital_inicial = capital_inicial
        self.estrategia = estrategia
        self.order_logger = order_logger
       
        self.todas_posiciones = self.resumen_todas_posiciones()
        self.posiciones_actuales = dict( (k,v) for k, v in [(s, 0) for s in self.lista_nemos] )
        
        # ✅ Dust threshold: positions smaller than this are treated as zero
        # Prevents residual micro-positions from blocking new entries
        self.DUST_THRESHOLD = 0.001  # Configurable per instrument if needed
        self._primary_leg_notional = None  # Stores primary leg notional for hedge sizing

        # Batch limit-order configuration (batch_n=1 → standard MKT, backward-compatible)
        self.batch_n          = batch_n
        self.batch_interval_s = batch_interval_s
        self.limit_offset_bps = limit_offset_bps

        self.cuenta = self.resumen_cuenta()
        self.cuenta_actual = self.resumen_cuenta_actual()
        #self.precio_promedio=self.calcula_ppromedio()

    def resumen_todas_posiciones(self):
        """
        Resume las posiciones actuales y pasadas iniciando en la fecha_inicio.
        Crea un diccionario para cada nemo, lo pone en 0, y luego añade una
        clave de tiempo añadiendolo a una lista al final
        """
        d = dict( (k,v) for k, v in [(s, 0) for s in self.lista_nemos] )
        d['tiempo'] = self.fecha_inicial
        return [d]

    def resumen_cuenta(self):
        """
        Resume la lista de inventarios (Todas las tenencias) incluyendo
        la caja, un acumulado de las comisiones y el patrimonio
        total de la cuenta incluyendo Caja y posiciones. Los cortos son
        tratados con posiciones negativas desde  la fecha_inicio. Es insumo
        para el sistema de Manejo de Ordenes (SMO)
        
        """
        d = dict( (k,v) for k, v in [(s, 0.0) for s in self.lista_nemos] )
        d['tiempo'] = self.fecha_inicial
        d['Caja'] = self.capital_inicial
        d['comision'] = 0.0
        #d['Tamaño']=0.0
        d['total'] = self.capital_inicial
        return [d]

    def resumen_cuenta_actual(self):
        """
        Genera un diccionario que contiene el valor en el instante del portafolio
        en todos los nemos.
        """
        d = dict( (k,v) for k, v in [(s, 0.0) for s in self.lista_nemos] )
        d['Caja'] = self.capital_inicial
        d['comision'] = 0.0
        #d['Tamaño']=0.0
        d['total'] = self.capital_inicial
        return d

    def actualiza_tiempo(self, evento):
        """
        Añade un nuevo registro a la matriz de posiciones para la vela actual 
        de mercado, lo que implica que toda la información (OHLCV) es conocida

        Usa EventoMdo desde Eventos.
        
        Includes timestamp normalization for consistent equity curve indexing.
        """
        from datetime import timezone
        
        ultimo_tiempo = self.velas.get_tiempo_ultima_vela(self.lista_nemos[0])
        
        # ✅ NORMALIZE TIMESTAMP for consistent equity curve
        # Round to nearest minute (for 1m candles) and ensure UTC timezone
        if isinstance(ultimo_tiempo, datetime.datetime):
            # Round to minute (remove seconds and microseconds)
            normalized_time = ultimo_tiempo.replace(second=0, microsecond=0)
            
            # Ensure UTC timezone
            if normalized_time.tzinfo is None:
                normalized_time = normalized_time.replace(tzinfo=timezone.utc)
            
            ultimo_tiempo = normalized_time

        # ACTUALIZA LAS POSICIONES
        # ================
        dp = dict( (k,v) for k, v in [(s, 0) for s in self.lista_nemos] )
        dp['tiempo'] = ultimo_tiempo

        for s in self.lista_nemos:
            dp[s] = self.posiciones_actuales[s]

        # Agrega posiciones actuales
        self.todas_posiciones.append(dp)

        # ACTUALIZA LA CUENTA
        # ===============
        dh = dict( (k,v) for k, v in [(s, 0) for s in self.lista_nemos] )
        dh['tiempo'] = ultimo_tiempo
        dh['Caja'] = self.cuenta_actual['Caja']
        dh['comision'] = self.cuenta_actual['comision']
        dh['total'] = self.cuenta_actual['Caja']
        precio_close = self.velas.get_valor_ultima_vela(s, "close")
        dh['precio'] = precio_close if precio_close is not None else 0.0
        #dh['Tamaño']=self.velas.get_valor_ultima_vela(s,"N")
       

        for s in self.lista_nemos:
            # Aproxima el total Caja + Valor de mercado posiciones con ultima vela
            close_val = self.velas.get_valor_ultima_vela(s, "close")
            if close_val is None:
                close_val = 0.0
            valor_mercado = self.posiciones_actuales[s] * close_val
            dh[s] = valor_mercado
            dh['total'] += valor_mercado
        # Agrega la cuenta actual al resumen histórico
        self.cuenta.append(dh)
    # ======================
    # MANEJO DE CALCE/POSICIONES
    # ======================
    def actualiza_posiciones_calce(self, calce):
        """
        Toma un evento de calce de ordenes y actualiza la matriz de posiciones 
        para reflejar el cambio
        Parametros:
        calce - Objeto EventoCalce usado para actualizar.
        """
        # Safety guard: ignore fills for symbols this process doesn't trade
        if calce.nemo not in self.posiciones_actuales:
            print(f"⚠️  Fill without matching order: {calce.nemo} {calce.direccion}")
            return
        
        # Valida calce de compra o venta (case-insensitive)
        calce_dir = 0
        if calce.direccion.upper() == 'BUY':
            calce_dir = 1
        if calce.direccion.upper() == 'SELL':
            calce_dir = -1
        # Actualiza la lista de posiciones con las nuevas adquisiciones
        self.posiciones_actuales[calce.nemo] += calce_dir*calce.cantidad

    def actualiza_cuenta_calce(self, calce):
        """
        Actualiza los valores de la cuenta actual con el calce.

        Parametros:
        calce - Objeto EventoCalce usado para actualizar.
        """
        # Safety guard: ignore fills for symbols this process doesn't trade
        if calce.nemo not in self.cuenta_actual:
            return
        
        # Compra o venta? (case-insensitive)
        calce_dir = 0
        if calce.direccion.upper() == 'BUY':
            calce_dir = 1
        if calce.direccion.upper() == 'SELL':
            calce_dir = -1

        # Actualiza la cuenta con los nuevos valores
        costo_calce = self.velas.get_valor_ultima_vela(calce.nemo, "close")
        costo = calce_dir * costo_calce * calce.cantidad
        self.cuenta_actual[calce.nemo] += costo
        self.cuenta_actual['comision'] += calce.comision
        self.cuenta_actual['Caja'] -= (costo + calce.comision)
        self.cuenta_actual['total'] -= (costo + calce.comision)
        #self.cuenta_actual['P_Prom']=self.actualiza_ppromedio(calce)

    def actualiza_calce(self, evento):
        """
        Actualiza las posiciones de calce.
            
        """
        if evento.type == 'CALCE':
            self.actualiza_posiciones_calce(evento)
            self.actualiza_cuenta_calce(evento)
            
            # ✅ Clear pending order after fill
            if hasattr(self, 'estrategia') and self.estrategia:
                self.estrategia.limpia_orden_pendiente(evento.nemo)
            
            print(f"✅ Calce procesado: {evento.nemo} {evento.direccion} {evento.cantidad} unidades")
            print(f"   Posición actual {evento.nemo}: {self.posiciones_actuales[evento.nemo]}")
            print(f"   Caja actual: ${self.cuenta_actual['Caja']:.2f}")

    def sync_positions_from_exchange(self, binance_handler):
        """
        Synchronize portfolio positions with actual exchange positions.
        
        This method fetches current positions from Binance and updates
        posiciones_actuales to match reality. This handles:
        - Manual closes on exchange
        - Partial fills not properly tracked
        - Position drift over time
        - System restarts
        
        Args:
            binance_handler: BinancePerpetualTrader instance with get_position_info method
            
        Returns:
            dict: Dictionary of position changes {nemo: (old_pos, new_pos)}
        """
        changes = {}
        
        if binance_handler is None or not hasattr(binance_handler, 'get_position_info'):
            print("⚠️  Cannot sync positions: No binance_handler with get_position_info")
            return changes
        
        try:
            # Get all positions for tracked symbols
            for nemo in self.lista_nemos:
                symbol = f"{nemo}USDT"
                positions = binance_handler.get_position_info(symbol=symbol)
                
                if positions is None:
                    continue
                
                # Find position for this symbol
                exchange_qty = 0.0
                for pos in positions:
                    if pos.get('symbol') == symbol:
                        exchange_qty = float(pos.get('positionAmt', 0))
                        break
                
                # Compare with tracked position
                tracked_qty = self.posiciones_actuales.get(nemo, 0)
                
                if abs(exchange_qty - tracked_qty) > 1e-8:  # Significant difference
                    old_pos = tracked_qty
                    self.posiciones_actuales[nemo] = exchange_qty
                    changes[nemo] = (old_pos, exchange_qty)
                    print(f"🔄 Position synced {nemo}: {old_pos:.6f} → {exchange_qty:.6f}")
            
            if changes:
                print(f"✅ Position sync complete: {len(changes)} positions updated")
            
            return changes
            
        except Exception as e:
            print(f"❌ Position sync error: {e}")
            import traceback
            traceback.print_exc()
            return changes
#            
#    def actualiza_ppromedio(self,calce):
#         stock=floor(self.cuenta_actual[nemo]/self.velas.get_valor_ultima_vela(nemo, "Close"))
#         pstock=self.cuenta_actual['P_Prom']
#         nuevo=calce.cantidad
#         ppromedio=calce.precioCalce*(nuevo/(stock+nuevo))+pstock*(stock/(stock+nuevo))
#         return ppromedio
#                     
#     ========================
#     ADMINISTRA EL RIESGO ASUMIDO (SIZING)
#     ========================
#    Calcula las Unidades
    def calcular_unidades(self, senal: EventoSenal, hedgeRatio: float = 1, ref_quantity=None):
        """
        Calcula el número de unidades a operar basado en el ATR de 14 horas
        y el capital inicial del portafolio.

        Sizing model (per leg):
            risk_budget = RISK_PCT × capital          (e.g. 1 % → $400 per leg)
            DVO1        = ATR(14h) × price × √24      (daily dollar-vol of 1 unit)
            U           = risk_budget / DVO1

        NOTE: leverage is intentionally NOT included in DVO1.
        Volatility of 1 unit depends only on ATR and price; leverage
        belongs in the margin calculation, not in the sizing denominator.
        Including leverage in DVO1 would shrink positions by 20×, which
        is the bug that produced $12 notionals on a $40,000 account.

        RISK_PCT calibration:
        With RISK_PCT = 1 %, expected daily 1-σ P&L move ≈ $400 per leg
        on a $40k account, producing notionals of ~$50–60k (~1.4×
        leverage). RISK_PCT = 10 % was tested but produced 13.9×
        leverage and >$4k single-trade risk → reverted to 1 %.

        Pair-trade logic:
            Primary leg (fuerza == 1.0): sized by ATR, stores notional.
            Hedge   leg (fuerza == HR ):  qty = HR × primary_notional / price.
        """
        # ── Risk parameters ──────────────────────────────────────────────
        RISK_PCT = 0.01      # 1 % of capital per primary leg (~$400 daily 1σ)
        # Hard notional cap: primary leg may never exceed 30 % of capital.
        # This protects against DVO1 near-zero for sub-cent priced symbols
        # (e.g. PENGU, SHIB) where even the corrected formula can produce
        # outsized sizes if ATR is unusually compressed.
        MAX_NOTIONAL_PER_LEG = 0.30 * self.capital_inicial   # e.g. $12 000
        # ─────────────────────────────────────────────────────────────────

        if ref_quantity is not None:
            return ref_quantity          # external override

        nemo = senal.nemo
        fuerza = getattr(senal, 'fuerza', 1.0) or 1.0

        # ── Fetch hourly candles for ATR ──
        print(f"📊 Calculando unidades para {nemo}, frecuencia: {self.velas.interval}")
        candles = self.velas.get_perpetual_ohlcv(symbol=nemo, interval='1h', limit=1000)

        # ── Fallbacks when data is insufficient ──
        if candles is None or candles.empty:
            print(f"⚠️ No historical data for {nemo}, using {RISK_PCT*100:.0f}% fallback")
            p = self._get_last_price(nemo)
            if p and p > 0:
                U = (RISK_PCT * self.capital_inicial) / p
                print(f"🔢 Fallback {nemo}: {U:.1f} units @ ${p}")
                return U
            return 1.0

        if len(candles) < 15:
            print(f"⚠️ Only {len(candles)} candles for {nemo}, using {RISK_PCT*100:.0f}% fallback")
            p = float(candles['close'].iloc[-1])
            U = (RISK_PCT * self.capital_inicial) / p
            return U

        # ── ATR(14) on hourly candles ──
        high = candles['high'].astype(float)
        low = candles['low'].astype(float)
        close = candles['close'].astype(float)
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=14).mean().iloc[-1]

        p_mdo = close.iloc[-1]          # current price

        # ── DVO1: daily dollar-volatility of 1 unit ──────────────────────
        # ATR from OHLCV is already in dollar terms (price units), so
        # DVO1 = ATR(14h) × √24  to scale hourly bars to a daily figure.
        # Do NOT multiply by p_mdo again — ATR is not a dimensionless ratio.
        # Leverage is NOT included here (see docstring).
        DVO1 = atr * np.sqrt(24)

        if DVO1 <= 0:
            print(f"⚠️ DVO1 ≤ 0 for {nemo}, cannot size")
            return 1.0

        Z = self.capital_inicial
        U = (RISK_PCT * Z) / DVO1       # units such that daily P&L ≈ RISK_PCT × capital

        # ── Pair-trade hedge-ratio logic ──
        if abs(fuerza - 1.0) < 1e-9:
            # ── Primary leg ──
            raw_notional = U * p_mdo
            if raw_notional > MAX_NOTIONAL_PER_LEG:
                print(f"⚠️  SIZE CAP triggered for {nemo}: "
                      f"${raw_notional:,.2f} → ${MAX_NOTIONAL_PER_LEG:,.2f} "
                      f"(ATR={atr:.6f}, DVO1={DVO1:.6f})")
                U = MAX_NOTIONAL_PER_LEG / p_mdo
            self._primary_leg_notional = U * p_mdo
            print(f"🔢 PRIMARY {nemo}: {U:,.1f} units × ${p_mdo:.5f}"
                  f" = ${self._primary_leg_notional:,.2f} notional"
                  f" (ATR={atr:.6f}, DVO1={DVO1:.4f})")
        else:
            # ── Hedge leg ──
            primary_notional = getattr(self, '_primary_leg_notional', None)
            if primary_notional and p_mdo > 0:
                U = (fuerza * primary_notional) / p_mdo
                print(f"🔢 HEDGE  {nemo}: HR={fuerza:.4f}"
                      f" × ${primary_notional:,.2f}"
                      f" / ${p_mdo:.5f}"
                      f" = {U:,.1f} units"
                      f" (notional=${U * p_mdo:,.2f})")
            else:
                U = U * fuerza
                print(f"🔢 HEDGE  {nemo}: {U:,.1f} units"
                      f" (ATR fallback × fuerza={fuerza:.4f})")

        return U

    def _get_last_price(self, nemo):
        """Get the last close price for a nemo from live data."""
        try:
            return float(self.velas.get_valor_ultima_vela(nemo, "close"))
        except Exception:
            return None

    # ========================
    # GENERA LAS ÓRDENES
    # ========================

    def genera_orden(self, senal):
        """
        Radica una orden con una cantidad constante del objeto señal recibido.
        Evalúa si la senal es primaria o de cobertura y genera la orden usando 
        las cantidades de referencia y Hedge ratio

        Parameters:
        senal - La tupla que contiene la información de la senal.
        """
        orden = None

        nemo = senal.nemo
        direccion_senal = senal.tipo_senal
        fuerza = senal.fuerza
        signal_timestamp = senal.datetime  # Candle timestamp that triggered the signal
        
        # Map strategy signal types to order execution types
        # Strategy uses business logic: LARGO/CORTO/FUERA
        # Execution layer uses: buy/sell/FUERA
        if direccion_senal == 'LARGO':
            direccion = 'buy'
            print(f"📋 Señal '{direccion_senal}' → Orden '{direccion}' para {nemo}")
            cantidad_mdo = self.calcular_unidades(senal)
            cantidad_act = self.posiciones_actuales[nemo]
        elif direccion_senal == 'CORTO':
            direccion = 'sell'
            print(f"📋 Señal '{direccion_senal}' → Orden '{direccion}' para {nemo}")
            cantidad_mdo = self.calcular_unidades(senal)
            cantidad_act = self.posiciones_actuales[nemo]

        else: # !!! Por que funciona en Backtesting pero no en Real? Esta cerrando en el umbral o en la posicion CONTRARIA en BT?
            direccion = direccion_senal  # Keep 'FUERA', 'buy', 'sell' as-is
            if direccion_senal == 'FUERA':
                print(f"📋 Señal '{direccion_senal}' → Cerrando posición para {nemo}")
                cantidad_act = self.posiciones_actuales[nemo]
                cantidad_mdo = cantidad_act # Close entire position


        #p_promedio=self.cuenta_actual['P_Prom']
        
        # Get VWAP price - extract the actual price from the dict
        # p_mdo_dict = self.velas.get_vwap_price(nemo, cantidad_mdo, direccion) # VWAP disabled in backtest
        p_mdo = self.velas.get_valor_ultima_vela(nemo, "close") # Default to close price in backtest only
        
        ## Esta sección se desactiva en backtestin 
        # if p_mdo_dict and isinstance(p_mdo_dict, dict) and 'vwap' in p_mdo_dict:
        #     p_mdo = p_mdo_dict['vwap']
        #     print(f"✅ VWAP available for {nemo}: {p_mdo:.2f}")
        # else:
        #     # Fallback to current close price if VWAP not available
        #     print(f"⚠️  VWAP not available for {nemo}, attempting to get close price...")
        #     try:
        #         p_mdo = self.velas.get_valor_ultima_vela(nemo, "close")
        #         print(f"✅ Got close price for {nemo}: {p_mdo:.2f}")
        #     except (KeyError, Exception) as e:
        #         print(f"❌ ERROR: Could not get price for {nemo}: {e}")
        #         print(f"   Available symbols in ultimo_dato_nemo: {list(self.velas.ultimo_dato_nemo.keys())}")
        #         # Try to get from historical data as last resort
        #         try:
        #             candles = self.velas.get_perpetual_ohlcv(nemo, '1m', limit=1)
        #             p_mdo = candles['close'].iloc[-1]
        #             print(f"✅ Got price from historical data for {nemo}: {p_mdo:.2f}")
        #         except Exception as e2:
        #             print(f"❌ CRITICAL: Cannot get any price for {nemo}: {e2}")
        #             return None  # Cannot create order without price
        
        tipo_orden = 'MKT'
        
        # Obtener la bolsa del data provider (primera bolsa en la lista)
        bolsa = self.lista_bolsas[0] if self.lista_bolsas else 'BITGETFTS'

        if direccion == 'buy' and abs(cantidad_act) < self.DUST_THRESHOLD:
            # Entry order (LARGO) - signal_type tracks original signal for reduce_only logic
            # ✅ Dust tolerance: treats micro-residual positions as zero
            orden = EventoOrden(nemo, tipo_orden, cantidad_mdo, 'buy', p_mdo, bolsa, signal_timestamp, signal_type=direccion_senal,
                                batch_n=self.batch_n, batch_interval_s=self.batch_interval_s, limit_offset_bps=self.limit_offset_bps)
            print('P Compra :%s' %p_mdo)
            # Notify strategy of pending order
            if self.estrategia:
                self.estrategia.actualiza_orden_pendiente(nemo, 'buy')
            # Log order details for debugging
            if self.order_logger:
                self.order_logger.log_order(
                    orden, 
                    signal_type=direccion_senal,
                    cash_available=self.cuenta_actual['Caja'],
                    price_used=p_mdo,
                    final_quantity=cantidad_mdo
                )
            
        if direccion == 'FUERA' and cantidad_act > self.DUST_THRESHOLD:
            # ✅ CLOSING ORDER - signal_type='FUERA' will trigger reduce_only=True in execution
            # Dust tolerance: only close if position is above dust threshold
            orden = EventoOrden(nemo, tipo_orden, cantidad_act, 'sell', p_mdo, bolsa, signal_timestamp, signal_type='FUERA')
            print('P Venta :%s' %p_mdo)
            # Notify strategy of pending order
            if self.estrategia:
                self.estrategia.actualiza_orden_pendiente(nemo, 'sell')
            # Log order details
            if self.order_logger:
                self.order_logger.log_order(
                    orden,
                    signal_type=direccion_senal,
                    cash_available=self.cuenta_actual['Caja'],
                    price_used=p_mdo,
                    final_quantity=cantidad_act
                )

        # Si hay posiciones cortas (cantidad_act < 0), cerrar comprando
        if direccion == 'FUERA' and cantidad_act < -self.DUST_THRESHOLD:
            # ✅ CLOSING ORDER - signal_type='FUERA' will trigger reduce_only=True in execution
            # Dust tolerance: only close if position is above dust threshold
            orden = EventoOrden(nemo, tipo_orden, abs(cantidad_act), 'buy', p_mdo, bolsa, signal_timestamp, signal_type='FUERA')
            print('P Compra :%s' %p_mdo)
            # Notify strategy of pending order
            if self.estrategia:
                self.estrategia.actualiza_orden_pendiente(nemo, 'buy')
            # Log order details
            if self.order_logger:
                self.order_logger.log_order(
                    orden,
                    signal_type=direccion_senal,
                    cash_available=self.cuenta_actual['Caja'],
                    price_used=p_mdo,
                    final_quantity=abs(cantidad_act)
                )
            
        if direccion == 'sell' and abs(cantidad_act) < self.DUST_THRESHOLD:
            # Entry order (CORTO) - signal_type tracks original signal for reduce_only logic
            # ✅ Dust tolerance: treats micro-residual positions as zero
            orden = EventoOrden(nemo, tipo_orden, cantidad_mdo, 'sell', p_mdo, bolsa, signal_timestamp, signal_type=direccion_senal,
                                batch_n=self.batch_n, batch_interval_s=self.batch_interval_s, limit_offset_bps=self.limit_offset_bps)
            print('P Venta :%s' %p_mdo)
            # Notify strategy of pending order
            if self.estrategia:
                self.estrategia.actualiza_orden_pendiente(nemo, 'sell')
            # Log order details
            if self.order_logger:
                self.order_logger.log_order(
                    orden,
                    signal_type=direccion_senal,
                    cash_available=self.cuenta_actual['Caja'],
                    price_used=p_mdo,
                    final_quantity=cantidad_mdo
                )

#       if direccion=='LARGO' and cantidad_act>0:
#            if cantidad_act>=3*cantidad_mdo :
#                orden = EventoOrden(nemo, tipo_orden, 0,'BUY')
#            else:
#                EventoOrden(nemo,tipo_orden,min(cantidad_mdo,(cantidad_act-3*cantidad_mdo)),'COMPRA')
        
            
#        if direccion=='CORTO' and cantidad_act<0:
#            #if abs(cantidad_act)>=3*cantidad_mdo:
#                orden = EventoOrden(nemo, tipo_orden, 0,'SELL')
 
                
        return orden
              

    def actualiza_senal(self, evento):
        """
        Actua ante un evento de tipo señal generando una orden llamando al método
        de arriba y añadiendo un evento tipo Señal a la fila de eventos.
        """
        evento_orden = self.genera_orden(evento)
        #evento_stop=self.genera_stop(evento) Ojo, genera stops para todas
        self.eventos.put(evento_orden)

    # ========================
    # ESTADÍSTICAS DESPUES DE BTST
    # ========================

    def curva_equity_dataframe(self):
        """
        Crea un Dataframe con la serie de cuenta.
        
        Includes timestamp cleaning for consistent equity curve in live trading:
        - Normalizes timestamps to remove microseconds
        - Removes duplicate timestamps (keeps last)
        - Sorts by time
        - Ensures clean time index for plotting
        """
        curva = pd.DataFrame(self.cuenta)
        
        # ✅ CLEAN TIME INDEX for live trading
        # Convert to datetime (utc=True handles timezone-aware datetimes)
        curva['tiempo'] = pd.to_datetime(curva['tiempo'], utc=True)
        
        # Normalize to minute precision (remove seconds/microseconds)
        curva['tiempo'] = curva['tiempo'].dt.floor('T')  # Floor to minute
        
        # Remove duplicates (keep last value for each timestamp)
        curva = curva.drop_duplicates(subset=['tiempo'], keep='last')
        
        # Sort by time to ensure chronological order
        curva = curva.sort_values('tiempo').reset_index(drop=True)
        
        # Set time index
        curva.set_index('tiempo', inplace=True)
        
        # Calculate returns and equity curve
        curva['retornos'] = curva['total'].pct_change()
        curva['curva_equity'] = (1.0+curva['retornos']).cumprod()
        self.curva_equity = curva
        
        # Create outputs folder if it doesn't exist
        output_dir = 'outputs'
        os.makedirs(output_dir, exist_ok=True)
        
        # Create descriptive filename with pair and date
        pair_name = '_'.join(self.lista_nemos)  # e.g., "LINK_AVAX"
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Create figure with better size and styling
        plt.figure(figsize=(12, 6))
        plt.plot(self.curva_equity.index, self.curva_equity['curva_equity'], linewidth=2)
        plt.title(f'Equity Curve - {pair_name}', fontsize=14, fontweight='bold')
        plt.xlabel('Time', fontsize=12)
        plt.ylabel('Equity', fontsize=12)
        plt.grid(True, alpha=0.3)
        
        # Rotate x-axis labels for better readability
        plt.xticks(rotation=45, ha='right')
        
        # Save plot to outputs folder with descriptive name
        plot_filename = f'{pair_name}_equity_curve_{timestamp}.png'
        plot_path = os.path.join(output_dir, plot_filename)
        plt.savefig(plot_path, dpi=300, bbox_inches='tight')
        print(f"📊 Equity curve saved to {plot_path}")
        print(f"📈 Data points in equity curve: {len(self.curva_equity)}")
        print(f"📅 Time range: {self.curva_equity.index[0]} to {self.curva_equity.index[-1]}")
        
        # Display interactively
        #plt.show()

    def output_resumen_estadisticas(self):
        """
        Crea un resumen de las estadísticas del portafolio.
        """
        retorno_total= self.curva_equity['curva_equity'][-1]
        retornos = self.curva_equity['retornos']
        pnl = self.curva_equity['curva_equity']

        sharpe = sharpe_ratio(retornos,periodos=252*6.5*12)
        drawdown, max_dd, dd_duration = max_drawdowns(pnl)
        Kelly=kelly(retornos)
        self.curva_equity['Maximum DD'] = drawdown

        # ✅ FIX G: Guard NaN values before formatting
        safe_dd_duration = 0 if pd.isna(dd_duration) else int(dd_duration)
        safe_max_dd = 0.0 if pd.isna(max_dd) else max_dd
        safe_sharpe = 0.0 if pd.isna(sharpe) else sharpe
        safe_kelly = 0.0 if pd.isna(Kelly) else Kelly

        stats = [("Retorno Total", "%0.2f%%" % (
                     (retorno_total - 1.0) * 100.0)),
                 ("Sharpe", "%0.2f" % safe_sharpe),
                 ("Max Drawdown", "%0.2f%%" % (safe_max_dd * 100.0)),
                 ("Kelly", "%0.2f" % safe_kelly),
                 ("Drawdown Duration", "%d" % safe_dd_duration)]
        
        # Create outputs folder if it doesn't exist
        output_dir = 'outputs'
        os.makedirs(output_dir, exist_ok=True)
        
        # Create descriptive filename with pair and date
        pair_name = '_'.join(self.lista_nemos)  # e.g., "LINK_AVAX"
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        csv_filename = f'{pair_name}_equity_{timestamp}.csv'
        csv_path = os.path.join(output_dir, csv_filename)
        
        # Save to outputs folder with descriptive name
        self.curva_equity.to_csv(csv_path)
        print(f"📄 Equity data saved to {csv_path}")
        
        return stats
        



