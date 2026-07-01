# -*- coding: utf-8 -*-
"""
Created on Fri Apr 28 09:49:14 2017
Eventos.py
@author: Diego Ochoa
"""
class Evento(object):
    pass

class EventoMdo(Evento):    #Administra la llegada de nueva información de mdo
    def __init__(self, nemo:str , msg_type:str ='ohlcv' , timestamp=None, **kwargs):
        """
        Flexible initialization for market events.
        
        Parameters:
        -----------
        nemo : str, optional
            Symbol or ticker
        evento_tipo : str, optional depending on the subscription type. Normally used ohlcv
            Event type: 'trade', 'quote','book','book5','book20','ohlcv'
        timestamp : datetime, optional
            Event timestamp
        **kwargs : dict
            Additional attributes to set (e.g., open, high, low, close, volume, etc.)
        
        Examples:
        ---------
        # Minimal initialization
        evento = EventoMdo(nemo='BTC', evento_tipo='KLINE', timestamp=datetime.now())
        
        # Full initialization with all data
        evento = EventoMdo(
            nemo='BTC', 
            evento_tipo='KLINE', 
            timestamp=datetime.now(),
            open=50000,
            high=51000,
            low=49000,
            close=50500,
            volume=1000
        )
        
        # From dictionary (unpack vela dict)
        vela = {'open': 50000, 'high': 51000, ...}
        evento = EventoMdo(nemo='BTC', evento_tipo='KLINE', timestamp=vela['open_time'], **vela)
        """
        # Set default values for all attributes
        self.timestamp = timestamp
        self.type = 'MERCADO'
        self.symbol = nemo
        self.nemo = nemo  # Alias for symbol (backward compatibility)
        self.typeEvent = msg_type  # Tipo de evento: 'trade', 'quote', 'book', 'book5', 'book20', 'ohlcv'
        self.start_time = None
        self.end_time = None
        self.open = None
        self.high = None
        self.low = None
        self.close = None
        self.volume = None
        self.price = None
        self.quantity = None
        self.quote_asset_volume = None
        self.number_of_trades = None
        self.taker_buy_base_asset_volume = None
        self.taker_buy_quote_asset_volume = None
        self.wap = None  # Precio promedio ponderado por volumen
        self.pct_chg = None  # Cambio porcentual
        self.best_bid = None
        self.best_ask = None
        self.bid_depth = None
        self.ask_depth = None
        self.imbalance = None  # Order book imbalance
        
        # Map common key names to attribute names
        key_mapping = {
                    'open_time': 'start_time',
                    'close_time': 'end_time',
                    'trades': 'number_of_trades',
                    }
        
        # Populate attributes from kwargs
        for key, value in kwargs.items():
            # Use mapped key if exists, otherwise use original key
            attr_name = key_mapping.get(key, key)
            if hasattr(self, attr_name):
                setattr(self, attr_name, value)

  
class EventoSenal(Evento):#administra eventos tipo señal
    def __init__(self,id_estrategia, nemo, datetime, tipo_senal,fuerza=1):
        """
        Inicializa el evento señal.
        Parametros:
            id_estrategia: Identificador de la estrategia que genera la señal
            nemo: Símbolo o nemotécnico del activo financiero ej 'AAPL'
            datetime: Indicador del momento en el tiempo en que genera la señal
            tipo_senal:'LARGO' O 'CORTO'
            fuerza: Factor de ajuste que sugiere escalar el sizing.
        """
        self.type='SEÑAL'
        self.id_estrategia=id_estrategia
        self.nemo=nemo
        self.datetime=datetime
        self.tipo_senal=tipo_senal
        self.fuerza=fuerza
        
class EventoOrden(Evento):
    def __init__(self, nemo, tipo_orden, cantidad, direccion, precio=None, bolsa=None, timestamp=None, signal_type=None,
                 batch_n=1, batch_interval_s=300, limit_offset_bps=2):
        """
        Inicializa el objeto Orden y define si es orden de mercado, o límite o
        Stop, el subyacente, la dirección y la cantidad.
        PARAMETROS:
            nemo=Nemotécnico del contrato
            tipo: Tipo de orden: Mercado, Límite, Spread etc...
            cantidad: Cantidad del subyacente
            dirección: Largo/Corto
            precio: Precio esperado de ejecución (VWAP o close)
            bolsa: Exchange/bolsa donde se ejecutará (e.g., 'BINANCEFTS', 'BYBIT', etc.)
            timestamp: Timestamp of the candle that triggered the order
            signal_type: Original signal type ('LARGO', 'CORTO', 'FUERA') - used for reduce_only logic
            batch_n: Number of limit-order slices (1 = single MKT order, no change)
            batch_interval_s: Seconds between slices (default 300 = 5 min)
            limit_offset_bps: Passive limit offset in basis points (default 2 = 0.02%)
        """
        self.type = 'ORDEN'
        self.nemo = nemo
        self.tipo_orden = tipo_orden
        self.cantidad = cantidad
        self.direccion = direccion
        self.precio = precio
        self.bolsa = bolsa
        self.timestamp = timestamp
        self.signal_type = signal_type  # Track if this is a closing order (FUERA)
        self.batch_n           = batch_n
        self.batch_interval_s  = batch_interval_s
        self.limit_offset_bps  = limit_offset_bps
  
        
    def print_orden(self):#Imprime los valores de la orden
        print('Orden:nemo=%s, Tipo=%s, Cantidad=%s, direccion=%s, precio=%s, bolsa=%s'%(self.nemo, self.tipo_orden, self.cantidad, self.direccion, self.precio, self.bolsa))
           
class EventoCalce(Evento):
    """
    Encapsula la noción de una orden calzada de la forma que lo entrega el 
    Broker.Guarda los datos de calce como tiempo, cantidad calzada y comisiones
    """
    def __init__(self,iTiempo,nemo,bolsa,cantidad,direccion,precioCalce,comision=None, tipo = None):
        """
        """
        self.type='CALCE'
        self.iTiempo=iTiempo
        self.nemo=nemo
        self.bolsa=bolsa
        self.cantidad=cantidad
        self.direccion=direccion
        self.precioCalce=precioCalce
        self.tipo=tipo
        
        #Calcula la comisión. Si no viene en el mensaje del broker usa la de IB
        
        if comision is None:
            self.comision=self.calcular_comision_binance()
        else:
            self.comision=comision
            
    def calcular_comision_ib(self):
        """
        Calcula la comisiónde la operación con base en la tabla de comisiones 
        de Interactive Brokers
        """
        CostoPleno=1
        if abs(self.cantidad)<=300000:
            CostoPleno=max(2,0.005*self.cantidad)
        else:#Mas de 1000 acciones
            CostoPleno=max(2,0.0045*self.cantidad)
        return CostoPleno
    
    def calcular_comision_binance(self):
        """
        Calcula la comisión de la operación con base en la tabla de comisiones 
        de Binance
        """
        tasa_comision=0.0004 #0.1%
        
        # Las comisiones en futuros se calculan respecto del mov de caja o del nocional?
        CostoPleno=tasa_comision*(self.cantidad*self.precioCalce)
        return CostoPleno