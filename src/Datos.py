# -*- coding: utf-8 -*-
"""
Created on Fri Apr 28 17:13:58 2017

@author: Diego Ochoa
"""

from abc import ABCMeta, abstractmethod
import datetime
from datetime import timedelta
import os, os.path
import numpy as np
import pandas as pd
from time import sleep, strftime, localtime
import threading
import json
import time
import logging
import websocket
import requests

# Import Binance API 
from binance.client import Client as binanceClient

# To import API keys from .env file
from dotenv import load_dotenv
load_dotenv()

from Eventos import EventoMdo

# Configure logging
logger = logging.getLogger(__name__)
import bkcap as bk

class AdminDatos(object):
    """
    AdminDatos es una Clase Base Abstracta que provee una interfaz para todas
    las subsecuentes (heredadas) clases de DataHandler, tanto en vivo como 
    históricas.
    
    El objetivo es entregar un conjunto de velas ( OHLCVI) para cada nemo.

    Esto replica la forma en que uns estrategia en vivo funcionaría y la manera
    en que los datos se alimentan por goteo.
    """
    __metaclass__ = ABCMeta

    @abstractmethod
    def ultima_vela(self, symbol):
        """
        Devuelve la última vela.
        """
        raise NotImplementedError("Implemente get_ultima_vela")
    
    @abstractmethod
    def ultimas_velas(self, symbol, N=1):
        """
        Returns the last N bars updated.
        """
        raise NotImplementedError("Implemente get_ultimas_velas()")
    
    @abstractmethod
    def tiempo_ultima_vela(self,nemo):
        """
        devuelve un objeto datetime de python para la última vela
        """
        raise NotImplementedError("Debería implementar get_tiempo_ultima_vela()")
        
    @abstractmethod
    def valor_ultima_vela(self,nemo,tipoval):
        #Trae el Máximo, minimo, apertura, cierre, volúmen e interes abierto
        raise NotImplementedError("Debería Implementar get_valor_ultima_vela()")
        
    @abstractmethod
    def valor_ultimas_velas(self,nemo,tipoval,N=1):
        #Trae los últimos N valores del tipoval:  los  Máximos, minimo, apertura, cierre, volúmen e interes abierto
        raise NotImplementedError("Debería Implementar get_valor_ultimas_velas()")
        
    @abstractmethod
    def actualizar_velas(self):
        """
        Empuja el último valor de las velas a la fila de velas para cada nemo
        en una tupla formato OHLCVI (datetime,Open,High,Low,Close,Volume,InterestOpen) 
        """
        raise NotImplementedError("Debería implementar get_actualizar_velas()")


import pandas as pd
import numpy as np
import requests
import time
import websocket
import json
from datetime import datetime, timedelta
import logging
from typing import Dict, List, Tuple, Optional
import asyncio
import aiohttp
from statsmodels.tsa.vector_ar.vecm import coint_johansen
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sortedcontainers import SortedDict
import warnings
warnings.filterwarnings('ignore')
# Kaiko API Key import from .env file
kaiko_api_key = os.getenv("KAIKO_API_KEY")
# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class KaikoDataProvider:
    """
    Kaiko API integration for cryptocurrency market data
    Supports both REST API and WebSocket streaming
    """
    
    def __init__(self, api_key: str=kaiko_api_key):
        self.api_key = kaiko_api_key
        self.base_url = "https://us.market-api.kaiko.io"
        self.perpetual_url = "https://fapi.binance.com"
        self.ws_url = "wss://ws.market-api.kaiko.io/v2/stream"
        self.session = None
        self.perp_session = None
        
    async def init_session(self):
        """Initialize aiohttp session for async requests"""
        if not self.session:
            self.session = aiohttp.ClientSession()
    
    async def init_perp_session(self):
        """Initialize aiohttp session for perpetuals"""
        if not self.perp_session:
            self.perp_session = aiohttp.ClientSession()
    
    def get_headers(self):
        """Get API headers with authentication"""
        return {
            'X-Api-Key': self.api_key,
            'Accept': 'application/json'
        }
    
    async def get_exchanges(self) -> List[Dict]:
        """Get list of supported exchanges"""
        await self.init_session()
        url = f"{self.base_url}/v2/data/exchanges"
        
        async with self.session.get(url, headers=self.get_headers()) as response:
            data = await response.json()
            return data.get('data', [])
    
    async def get_instruments(self, exchange: str) -> List[Dict]:
        """Get trading pairs for specific exchange"""
        await self.init_session()
        url = f"{self.base_url}/v2/data/instruments"
        params = {'exchange': exchange}
        
        async with self.session.get(url, headers=self.get_headers(), params=params) as response:
            data = await response.json()
            return data.get('data', [])
    
    async def get_ohlcv(self, exchange: str, instrument: str, 
                       start_time: str, end_time: str, interval: str = '1m') -> pd.DataFrame:
        """
        Get OHLCV candlestick data
        
        Parameters:
        - exchange: Exchange code (e.g., 'cbse' for Coinbase)
        - instrument: Trading pair (e.g., 'btc-usd')
        - start_time: Start time in YYYY-MM format or ISO format
        - end_time: End time in YYYY-MM format or ISO format
        - interval: Time interval ('1m', '5m', '1h', '1d')
        """
        await self.init_session()
        url = f"{self.base_url}/v2/data/trades.v1/exchanges/{exchange}/spot/{instrument}/aggregations/count_ohlcv_vwap"
        # start_time end_time should be iso8601 string with optional precision up to millisecond
        # Format: 2018-10-03T13:29:26Z or 2018-10-03T13:29:26.530Z
        start_dt = pd.to_datetime(start_time)
        end_dt = pd.to_datetime(end_time)
        
        # Convert to UTC and format as ISO8601 with Z suffix
        if start_dt.tzinfo is None:
            start_dt = start_dt.tz_localize('UTC')
        else:
            start_dt = start_dt.tz_convert('UTC')
            
        if end_dt.tzinfo is None:
            end_dt = end_dt.tz_localize('UTC')
        else:
            end_dt = end_dt.tz_convert('UTC')
        
        # Format as ISO8601 with Z suffix (remove timezone info and add Z)
        start_time_formatted = start_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        end_time_formatted = end_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        
        params = {
            'start_time': start_time_formatted,
            'end_time': end_time_formatted,
            'interval': interval
        }
        
        async with self.session.get(url, headers=self.get_headers(), params=params) as response:
            data = await response.json()
            
            if 'data' in data and data['data']:
                df = pd.DataFrame(data['data'])
                if 'timestamp' in df.columns:
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                    df.set_index('timestamp', inplace=True)
                    
                    # Convert numeric columns from string to float
                    numeric_columns = ['open', 'high', 'low', 'close', 'volume']
                    for col in numeric_columns:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors='coerce')
                    
                    return df
                else:
                    logger.error(f"Error: 'timestamp' column not found in OHLCV data. Available columns: {df.columns.tolist()}")
                    return pd.DataFrame()
            else:
                logger.error(f"Error getting OHLCV data: {data}")
                return pd.DataFrame()
    
    async def get_perpetual_ohlcv(self, symbol: str, interval: str = '1d', 
                                   limit: int = 90) -> pd.DataFrame:
        """
        Get historical kline/candlestick data for perpetuals
        
        Parameters:
        - symbol: Trading symbol (e.g., 'BTCUSDT')
        - interval: Kline interval ('1m', '5m', '15m', '1h', '4h', '1d')
        - limit: Number of klines to retrieve (max 1500)
        
        Returns:
        - DataFrame with OHLCV data
        """
        await self.init_perp_session()
        
        try:
            url = f"{self.perpetual_url}/fapi/v1/klines"
            params = {
                'symbol': symbol,
                'interval': interval,
                'limit': limit
            }
            
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    df = pd.DataFrame(data, columns=[
                        'timestamp', 'open', 'high', 'low', 'close', 'volume',
                        'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                        'taker_buy_quote', 'ignore'
                    ])
                    
                    # Convert timestamp to datetime
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                    df.set_index('timestamp', inplace=True)
                    
                    # Convert price columns to float
                    for col in ['open', 'high', 'low', 'close', 'volume']:
                        df[col] = df[col].astype(float)
                    
                    return df[['open', 'high', 'low', 'close', 'volume']]
                else:
                    logger.error(f"Error getting perpetual klines for {symbol}: HTTP {response.status}")
                    return pd.DataFrame()
                    
        except Exception as e:
            logger.error(f"Error getting perpetual klines for {symbol}: {e}")
            return pd.DataFrame()
    
    async def get_open_interest(self, symbol: str) -> Optional[Dict]:
        """
        Get open interest for perpetual contract
        High open interest indicates strong market participation
        """
        await self.init_session()
        
        try:
            url = f"{self.base_url}/fapi/v1/openInterest"
            params = {'symbol': symbol}
            
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        'symbol': symbol,
                        'open_interest': float(data['openInterest']),
                        'timestamp': datetime.fromtimestamp(int(data['time']) / 1000)
                    }
                else:
                    return None
                    
        except Exception as e:
            logger.error(f"Error getting open interest for {symbol}: {e}")
            return None
    
    async def get_long_short_ratio(self, symbol: str, period: str = '5m') -> Optional[Dict]:
        """
        Get long/short ratio from top trader accounts
        Useful for sentiment analysis
        
        Parameters:
        - period: '5m', '15m', '30m', '1h', '2h', '4h', '6h', '12h', '1d'
        """
        await self.init_session()
        
        try:
            url = f"{self.base_url}/futures/data/topLongShortAccountRatio"
            params = {
                'symbol': symbol,
                'period': period,
                'limit': 1
            }
            
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if data:
                        latest = data[0]
                        return {
                            'symbol': symbol,
                            'long_short_ratio': float(latest['longShortRatio']),
                            'long_account': float(latest['longAccount']),
                            'short_account': float(latest['shortAccount']),
                            'timestamp': datetime.fromtimestamp(int(latest['timestamp']) / 1000)
                        }
                    return None
                else:
                    return None
                    
        except Exception as e:
            logger.error(f"Error getting long/short ratio for {symbol}: {e}")
            return None
    
    async def close(self):
        """Close the aiohttp session"""
        if self.session:
            await self.session.close()
    
    async def close_perp_session(self):
        """Close the aiohttp session for perpetuals"""
        if self.perp_session:
            await self.perp_session.close()

    async def get_current_prices(self, exchanges: List[str], instrument: str) -> Dict[str, float]:
        """Get current prices across multiple exchanges for arbitrage detection"""
        await self.init_session()
        prices = {}
        
        for exchange in exchanges:
            try:
                url = f"{self.base_url}/v2/data/trades.v1/exchanges/{exchange}/spot/{instrument}/aggregations/count_ohlcv_vwap"
                # Format timestamps as ISO8601 with Z suffix for UTC
                start_dt = datetime.now() - timedelta(minutes=5)
                end_dt = datetime.now()
                params = {
                    'start_time': start_dt.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    'end_time': end_dt.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    'interval': '1m'
                }
                
                async with self.session.get(url, headers=self.get_headers(), params=params) as response:
                    data = await response.json()
                    if 'data' in data and data['data']:
                        latest = data['data'][-1]
                        prices[exchange] = float(latest['close'])
                        
            except Exception as e:
                logger.error(f"Error getting price from {exchange}: {e}")
                
        return prices


# ============================================================================
# KaikoData - AdminDatos Implementation using Kaiko gRPC Streaming
# ============================================================================

class KaikoData(AdminDatos):
    """
    Kaiko data provider for Statistical Arbitrage trading system.
    
    Inherits from AdminDatos to provide a drop-in replacement for CoinApiDs.
    Uses Kaiko's gRPC streaming API for real-time OHLCV data and REST API
    for historical data initialization.
    
    Features:
    ---------
    - gRPC streaming for real-time OHLCV data (low latency)
    - REST API for historical data (initial candles)
    - Thread-safe data management with locks
    - Automatic reconnection with exponential backoff
    - Compatible with trading.py and Estrategia.py (zero changes required)
    
    Symbol Mapping:
    ---------------
    - Internal format: 'LINK', 'AVAX' (lista_nemos)
    - Kaiko format: exchange='binf', instrument_class='perpetual-future', code='link-usdt'
    
    Exchange Codes:
    ---------------
    - binf: Binance Futures (perpetual-future)
    - bina: Binance Spot
    - cbse: Coinbase
    - krkn: Kraken
    - ftxu: FTX (if available)
    
    Usage:
    ------
    >>> from Datos import KaikoData
    >>> import queue
    >>> eventos = queue.Queue()
    >>> admin_datos = KaikoData(
    ...     eventos=eventos,
    ...     lista_bolsas=['binf'],
    ...     lista_libros=['perpetual-future'],
    ...     lista_nemos=['LINK', 'AVAX'],
    ...     interval='1m'
    ... )
    >>> admin_datos.set_initial_candles()  # Load historical data
    >>> admin_datos.connect_websocket()    # Start streaming
    
    Note:
    -----
    Requires KAIKO_API_KEY environment variable to be set.
    Install dependencies: pip install kaikosdk grpcio grpcio-tools protobuf
    """
    
    # Exchange code mapping from our system to Kaiko
    EXCHANGE_MAPPING = {
        'BINANCEFTS': 'binf',
        'BINANCE': 'bina',
        'COINBASE': 'cbse',
        'KRAKEN': 'krkn',
        'BITSTAMP': 'bstp',
        'GEMINI': 'gmni',
        'HUOBI': 'huob',
        'OKEX': 'okex',
        # Add more as needed
    }
    
    # Instrument class mapping
    INSTRUMENT_CLASS_MAPPING = {
        'PERP': 'perpetual-future',
        'SPOT': 'spot',
        'FUTURE': 'future',
    }
    
    # Interval mapping from Binance format to Kaiko format
    INTERVAL_MAPPING = {
        '1s': '1s',
        '1m': '1m',
        '3m': '3m',
        '5m': '5m',
        '15m': '15m',
        '30m': '30m',
        '1h': '1h',
        '2h': '2h',
        '4h': '4h',
        '1d': '1d',
        '1w': '1w',
    }
    
    def __init__(self, eventos, lista_bolsas, lista_libros, lista_nemos, interval='1m'):
        """
        Initialize Kaiko data provider.
        
        Parameters:
        -----------
        eventos : queue.Queue
            Event queue for publishing EventoMdo objects
        lista_bolsas : list
            List of exchange IDs. Can use our format (e.g., ['BINANCEFTS']) 
            or Kaiko format (e.g., ['binf'])
        lista_libros : list
            List of instrument classes. Can use our format (e.g., ['PERP'])
            or Kaiko format (e.g., ['perpetual-future'])
        lista_nemos : list
            List of base asset symbols (e.g., ['LINK', 'AVAX'])
        interval : str
            Time interval for OHLCV data (e.g., '1m', '5m', '1h')
        """
        # Core attributes
        self.eventos = eventos
        self.lista_nemos = lista_nemos
        self.base_asset = 'USDT'
        
        # Map exchange codes to Kaiko format
        self.lista_bolsas = [
            self.EXCHANGE_MAPPING.get(b.upper(), b.lower()) 
            for b in lista_bolsas
        ]
        
        # Map instrument classes to Kaiko format
        self.lista_libros = [
            self.INSTRUMENT_CLASS_MAPPING.get(l.upper(), l.lower())
            for l in lista_libros
        ]
        
        # Map interval to Kaiko format
        self.interval = self.INTERVAL_MAPPING.get(interval.lower(), interval)
        self.period_id = self.interval  # Compatibility with PortAQMHFT
        
        # Data structures
        self.datos_nemo = {}        # {symbol: DataFrame} - Historical buffer
        self.ultimo_dato_nemo = {}  # {symbol: dict} - Latest candle
        self.continuar_backtest = True
        self.indice_vela = 0
        
        # API configuration
        self.api_key = self._load_api_key()
        self.grpc_endpoint = 'gateway-v0-grpc.kaiko.ovh'
        self.rest_base_url = 'https://us.market-api.kaiko.io'
        
        # gRPC components
        self.channel = None
        self.stub = None
        self.stream_threads = {}  # {nemo: thread}
        self.is_running = False
        
        # Thread safety
        self.data_lock = threading.Lock()
        self.last_message_time = {}
        
        # Reconnection settings
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        
        # Initialize data structures for each symbol
        for nemo in self.lista_nemos:
            self.ultimo_dato_nemo[nemo] = {}
            self.datos_nemo[nemo] = pd.DataFrame()
            self.last_message_time[nemo] = time.time()
        
        logger.info(f"KaikoData initialized with {len(self.lista_nemos)} symbols")
        logger.info(f"Exchanges: {self.lista_bolsas}, Classes: {self.lista_libros}")
        logger.info(f"Interval: {self.interval}")
    
    @staticmethod
    def _load_api_key():
        """Load Kaiko API key from environment variable"""
        api_key = os.environ.get("KAIKO_API_KEY")
        if api_key is None:
            raise ValueError(
                "Kaiko API key not found. Please set the KAIKO_API_KEY environment variable."
            )
        return api_key
    
    def _get_rest_headers(self):
        """Get headers for REST API requests"""
        return {
            'X-Api-Key': self.api_key,
            'Accept': 'application/json'
        }
    
    def _build_kaiko_code(self, nemo):
        """
        Build Kaiko instrument code from base symbol.
        
        Parameters:
        -----------
        nemo : str
            Base asset symbol (e.g., 'LINK')
        
        Returns:
        --------
        str : Kaiko code (e.g., 'link-usdt')
        """
        return f"{nemo.lower()}-{self.base_asset.lower()}"
    
    def _extract_nemo_from_code(self, code):
        """
        Extract base asset symbol from Kaiko code.
        
        Parameters:
        -----------
        code : str
            Kaiko code (e.g., 'link-usdt')
        
        Returns:
        --------
        str : Base asset symbol (e.g., 'LINK')
        """
        if '-' in code:
            return code.split('-')[0].upper()
        return code.upper()
    
    # =========================================================================
    # gRPC Streaming Methods
    # =========================================================================
    
    def connect_websocket(self):
        """
        Start gRPC streaming for real-time OHLCV data.
        
        Named 'websocket' for interface compatibility with CoinApiDs,
        but actually uses gRPC streaming protocol.
        """
        try:
            import grpc
            from kaikosdk import sdk_pb2_grpc
            from kaikosdk.stream.aggregates_ohlcv_v1 import request_pb2 as pb_ohlcv
            from kaikosdk.core import instrument_criteria_pb2
        except ImportError as e:
            logger.error(f"Kaiko SDK not installed. Install with: pip install kaikosdk grpcio")
            logger.error(f"Import error: {e}")
            # Fall back to REST polling mode
            logger.warning("Falling back to REST polling mode (every 5 seconds)")
            self._start_rest_polling()
            return
        
        self.is_running = True
        self.reconnect_attempts = 0
        
        # Setup gRPC credentials
        credentials = grpc.ssl_channel_credentials()
        call_credentials = grpc.access_token_call_credentials(self.api_key)
        composite_credentials = grpc.composite_channel_credentials(
            credentials, call_credentials
        )
        
        # Create channel
        self.channel = grpc.secure_channel(self.grpc_endpoint, composite_credentials)
        self.stub = sdk_pb2_grpc.StreamAggregatesOHLCVServiceV1Stub(self.channel)
        
        logger.info(f"✅ Kaiko gRPC channel established: {self.grpc_endpoint}")
        
        # Start streaming thread for each symbol
        for nemo in self.lista_nemos:
            thread = threading.Thread(
                target=self._stream_symbol_ohlcv,
                args=(nemo, pb_ohlcv, instrument_criteria_pb2),
                daemon=True
            )
            self.stream_threads[nemo] = thread
            thread.start()
            logger.info(f"📡 Started streaming thread for {nemo}")
        
        logger.info(f"✅ Kaiko gRPC streaming started for {len(self.lista_nemos)} symbols")
    
    def _stream_symbol_ohlcv(self, nemo, pb_ohlcv, instrument_criteria_pb2):
        """
        Stream OHLCV data for a single symbol.
        
        Parameters:
        -----------
        nemo : str
            Base asset symbol (e.g., 'LINK')
        pb_ohlcv : module
            Protocol buffer module for OHLCV requests
        instrument_criteria_pb2 : module
            Protocol buffer module for instrument criteria
        """
        bolsa = self.lista_bolsas[0]
        libro = self.lista_libros[0]
        code = self._build_kaiko_code(nemo)
        
        try:
            # Create instrument criteria
            criteria = instrument_criteria_pb2.InstrumentCriteria(
                exchange=bolsa,
                instrument_class=libro,
                code=code
            )
            
            # Create request
            request = pb_ohlcv.StreamAggregatesOHLCVRequestV1(
                aggregate=self.interval,
                instrument_criteria=criteria
            )
            
            logger.info(f"📥 Subscribing to {bolsa}/{libro}/{code} @ {self.interval}")
            
            # Stream responses
            responses = self.stub.Subscribe(request)
            
            for response in responses:
                if not self.is_running:
                    break
                self._process_grpc_ohlcv(nemo, response)
                
        except Exception as e:
            logger.error(f"gRPC streaming error for {nemo}: {e}")
            if self.is_running:
                self._handle_reconnection(nemo)
    
    def _process_grpc_ohlcv(self, nemo, response):
        """
        Process incoming OHLCV data from Kaiko gRPC stream.
        
        Parameters:
        -----------
        nemo : str
            Base asset symbol
        response : protobuf message
            Kaiko OHLCV response
        """
        try:
            # Parse timestamp
            timestamp = pd.to_datetime(response.timestamp)
            
            # Build candle dictionary (matching CoinApiDs format)
            vela = {
                'open_time': timestamp,
                'close_time': timestamp + pd.Timedelta(self.interval),
                'open': float(response.open),
                'high': float(response.high),
                'low': float(response.low),
                'close': float(response.close),
                'volume': float(response.volume),
                'trades': 0,  # Kaiko OHLCV doesn't include trade count in stream
                'interval': self.interval
            }
            
            with self.data_lock:
                last_candle = self.ultimo_dato_nemo.get(nemo)
                
                # Only update if this candle is newer
                if (last_candle is None or 
                    last_candle.get('close_time') is None or
                    vela['close_time'] > last_candle.get('close_time', vela['close_time'])):
                    
                    self.ultimo_dato_nemo[nemo] = vela
                    
                    # Append to historical buffer
                    if nemo not in self.datos_nemo:
                        self.datos_nemo[nemo] = pd.DataFrame()
                    
                    new_row = pd.DataFrame([vela])
                    self.datos_nemo[nemo] = pd.concat(
                        [self.datos_nemo[nemo], new_row], 
                        ignore_index=True
                    )
                    
                    # Limit buffer size
                    max_buffer_size = 1000
                    if len(self.datos_nemo[nemo]) > max_buffer_size:
                        self.datos_nemo[nemo] = self.datos_nemo[nemo].iloc[-max_buffer_size:]
                        self.datos_nemo[nemo].reset_index(drop=True, inplace=True)
            
            self.last_message_time[nemo] = time.time()
            
            # Create and push EventoMdo to queue
            from Eventos import EventoMdo
            evento = EventoMdo(
                nemo=nemo,
                msg_type='ohlcv',
                timestamp=timestamp,
                **vela
            )
            self.eventos.put(evento)
            
            logger.debug(f"📊 {nemo}: {vela['close']} @ {timestamp}")
            
        except Exception as e:
            logger.error(f"Error processing gRPC OHLCV for {nemo}: {e}")
    
    def _handle_reconnection(self, nemo):
        """Handle reconnection with exponential backoff"""
        self.reconnect_attempts += 1
        
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error(f"Max reconnection attempts ({self.max_reconnect_attempts}) reached for {nemo}")
            return
        
        backoff_time = min(300, (2 ** self.reconnect_attempts))
        logger.info(f"Reconnecting {nemo} in {backoff_time}s (attempt {self.reconnect_attempts})")
        time.sleep(backoff_time)
        
        # Restart streaming for this symbol
        try:
            import grpc
            from kaikosdk import sdk_pb2_grpc
            from kaikosdk.stream.aggregates_ohlcv_v1 import request_pb2 as pb_ohlcv
            from kaikosdk.core import instrument_criteria_pb2
            
            thread = threading.Thread(
                target=self._stream_symbol_ohlcv,
                args=(nemo, pb_ohlcv, instrument_criteria_pb2),
                daemon=True
            )
            self.stream_threads[nemo] = thread
            thread.start()
        except Exception as e:
            logger.error(f"Reconnection failed for {nemo}: {e}")
    
    def disconnect_websocket(self):
        """
        Disconnect gRPC streaming safely.
        
        Named 'websocket' for interface compatibility with CoinApiDs.
        """
        self.is_running = False
        
        # Wait for streaming threads to finish
        for nemo, thread in self.stream_threads.items():
            if thread and thread.is_alive():
                thread.join(timeout=5)
                logger.info(f"Stopped streaming for {nemo}")
        
        # Close gRPC channel
        if self.channel:
            self.channel.close()
            self.channel = None
        
        logger.info("✅ Kaiko gRPC streaming disconnected safely")
    
    def reconnect_websocket(self):
        """Reconnect gRPC streaming"""
        self.disconnect_websocket()
        time.sleep(2)
        self.connect_websocket()
    
    # =========================================================================
    # REST Polling Fallback (if gRPC not available)
    # =========================================================================
    
    def _start_rest_polling(self):
        """Start REST polling as fallback when gRPC is not available"""
        self.is_running = True
        self._polling_thread = threading.Thread(
            target=self._rest_polling_loop,
            daemon=True
        )
        self._polling_thread.start()
        logger.info("Started REST polling fallback (every 5 seconds)")
    
    def _rest_polling_loop(self):
        """Polling loop that fetches latest data via REST API"""
        import requests
        
        while self.is_running:
            for nemo in self.lista_nemos:
                try:
                    self._fetch_latest_candle_rest(nemo)
                except Exception as e:
                    logger.error(f"REST polling error for {nemo}: {e}")
            
            time.sleep(5)  # Poll every 5 seconds
    
    def _fetch_latest_candle_rest(self, nemo):
        """Fetch latest candle via REST API"""
        import requests
        
        bolsa = self.lista_bolsas[0]
        libro = self.lista_libros[0]
        code = self._build_kaiko_code(nemo)
        
        # Build URL
        url = f"{self.rest_base_url}/v2/data/trades.v1/exchanges/{bolsa}/{libro}/{code}/aggregations/count_ohlcv_vwap"
        
        # Time range: last 5 minutes
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(minutes=5)
        
        params = {
            'start_time': start_time.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'end_time': end_time.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'interval': self.interval
        }
        
        response = requests.get(url, headers=self._get_rest_headers(), params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if 'data' in data and data['data']:
                latest = data['data'][-1]
                
                timestamp = pd.to_datetime(latest.get('timestamp', latest.get('time_period_start')))
                
                vela = {
                    'open_time': timestamp,
                    'close_time': timestamp + pd.Timedelta(self.interval),
                    'open': float(latest.get('open', latest.get('price_open', 0))),
                    'high': float(latest.get('high', latest.get('price_high', 0))),
                    'low': float(latest.get('low', latest.get('price_low', 0))),
                    'close': float(latest.get('close', latest.get('price_close', 0))),
                    'volume': float(latest.get('volume', latest.get('volume_traded', 0))),
                    'trades': int(latest.get('trades_count', 0)),
                    'interval': self.interval
                }
                
                with self.data_lock:
                    self.ultimo_dato_nemo[nemo] = vela
                
                self.last_message_time[nemo] = time.time()
    
    # =========================================================================
    # Historical Data Methods (REST API)
    # =========================================================================
    
    def get_historic_price(self, symbol_id: str, period_id: str,
                          time_start, time_end, limit: int = None) -> pd.DataFrame:
        """
        Get historical OHLCV data from Kaiko REST API.
        
        Parameters:
        -----------
        symbol_id : str
            Symbol identifier (can be in various formats)
        period_id : str
            Time period (e.g., '1m', '5m', '1h', '1d')
        time_start : datetime or str
            Start time
        time_end : datetime or str
            End time
        limit : int, optional
            Maximum number of candles to return
        
        Returns:
        --------
        pd.DataFrame : DataFrame with OHLCV data
        """
        import requests
        
        # Parse symbol_id to extract components
        # Expected formats: 'LINK', 'binf/perpetual-future/link-usdt', etc.
        if '/' in symbol_id:
            parts = symbol_id.split('/')
            bolsa = parts[0] if len(parts) > 0 else self.lista_bolsas[0]
            libro = parts[1] if len(parts) > 1 else self.lista_libros[0]
            code = parts[2] if len(parts) > 2 else self._build_kaiko_code(symbol_id)
        else:
            bolsa = self.lista_bolsas[0]
            libro = self.lista_libros[0]
            code = self._build_kaiko_code(symbol_id)
        
        # Format times
        if isinstance(time_start, datetime):
            time_start_str = time_start.strftime('%Y-%m-%dT%H:%M:%SZ')
        else:
            time_start_str = str(time_start)
        
        if isinstance(time_end, datetime):
            time_end_str = time_end.strftime('%Y-%m-%dT%H:%M:%SZ')
        else:
            time_end_str = str(time_end)
        
        # Build URL
        url = f"{self.rest_base_url}/v2/data/trades.v1/exchanges/{bolsa}/{libro}/{code}/aggregations/count_ohlcv_vwap"
        
        params = {
            'start_time': time_start_str,
            'end_time': time_end_str,
            'interval': period_id
        }
        
        if limit:
            params['page_size'] = limit
        
        try:
            logger.info(f"Fetching historical data: {bolsa}/{libro}/{code} ({period_id}) from {time_start_str} to {time_end_str}")
            response = requests.get(url, headers=self._get_rest_headers(), params=params, timeout=30)
            
            if response.status_code != 200:
                logger.error(f"Kaiko REST error: {response.status_code}")
                try:
                    error_detail = response.json()
                    logger.error(f"Error detail: {error_detail}")
                except:
                    logger.error(f"Response text: {response.text[:200]}")
                return pd.DataFrame()
            
            data = response.json()
            
            if 'data' not in data or not data['data']:
                logger.warning(f"No historical data returned for {code}")
                return pd.DataFrame()
            
            # Convert to DataFrame
            df = pd.DataFrame(data['data'])
            
            # Rename columns to standard format
            column_mapping = {
                'price_open': 'open',
                'price_high': 'high',
                'price_low': 'low',
                'price_close': 'close',
                'volume_traded': 'volume',
                'trades_count': 'trades'
            }
            df.rename(columns=column_mapping, inplace=True)
            
            # Ensure we have the standard columns
            for col in ['open', 'high', 'low', 'close', 'volume']:
                if col not in df.columns:
                    df[col] = 0.0
            
            # Convert timestamp
            time_col = 'timestamp' if 'timestamp' in df.columns else 'time_period_start'
            if time_col in df.columns:
                df['time_period_start'] = pd.to_datetime(df[time_col])
                df.set_index('time_period_start', inplace=True)
            
            # Convert numeric columns
            for col in ['open', 'high', 'low', 'close', 'volume']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            
            logger.info(f"✅ Retrieved {len(df)} historical candles for {code}")
            return df
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error fetching historical data: {e}")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"Error processing historical data: {e}")
            return pd.DataFrame()
    
    def set_initial_candles(self):
        """
        Load historical candles for all symbols.
        
        Called during initialization to populate datos_nemo with
        historical data for strategy calculations (e.g., OLS window).
        """
        from datetime import datetime as dt_datetime
        for nemo in self.lista_nemos:
            symbol_id = f"{self.lista_bolsas[0]}/{self.lista_libros[0]}/{self._build_kaiko_code(nemo)}"
            df = self.get_historic_price(
            symbol_id=symbol_id,
            period_id=self.interval,
            time_start=dt_datetime.utcnow() - timedelta(days=5),
            time_end=dt_datetime.utcnow(),
            limit=500
            )
            
            if not df.empty:
                # Convert to expected format
                df_formatted = df.copy()
                
                # Add close_time column
                if 'close_time' not in df_formatted.columns:
                    df_formatted['close_time'] = df_formatted.index + pd.Timedelta(self.interval)
                
                # Add open_time column
                if 'open_time' not in df_formatted.columns:
                    df_formatted['open_time'] = df_formatted.index
                
                with self.data_lock:
                    self.datos_nemo[nemo] = df_formatted
                    
                    # Set ultimo_dato_nemo from last row
                    if not df_formatted.empty:
                        last_row = df_formatted.iloc[-1].to_dict()
                        last_row['close_time'] = df_formatted.index[-1] + pd.Timedelta(self.interval)
                        last_row['open_time'] = df_formatted.index[-1]
                        self.ultimo_dato_nemo[nemo] = last_row
                
                logger.info(f"Initialized historical data for {nemo} with {len(df)} candles")
            else:
                logger.warning(f"No historical data available for {nemo}")
    
    # =========================================================================
    # AdminDatos Interface Methods
    # =========================================================================
    
    def get_ultima_vela(self, nemo):
        """
        Get the latest completed candle for a symbol.
        
        Parameters:
        -----------
        nemo : str
            Base asset symbol (e.g., 'LINK')
        
        Returns:
        --------
        dict : Latest candle data
        """
        with self.data_lock:
            return self.ultimo_dato_nemo.get(nemo, {})
    
    # Alias for compatibility
    ultima_vela = get_ultima_vela
    
    def get_ultimas_velas(self, nemo, N=1):
        """
        Get the last N completed candles for a symbol.
        
        Parameters:
        -----------
        nemo : str
            Base asset symbol (e.g., 'LINK')
        N : int
            Number of candles to return
        
        Returns:
        --------
        pd.DataFrame : Last N candles
        """
        with self.data_lock:
            if nemo in self.datos_nemo and isinstance(self.datos_nemo[nemo], pd.DataFrame):
                df = self.datos_nemo[nemo]
                return df.iloc[-N:] if len(df) >= N else df
            return pd.DataFrame()
    
    # Alias for compatibility
    ultimas_velas = get_ultimas_velas
    
    def get_tiempo_ultima_vela(self, nemo):
        """
        Get timestamp of the latest candle.
        
        Parameters:
        -----------
        nemo : str
            Base asset symbol
        
        Returns:
        --------
        datetime : Timestamp of latest candle
        """
        with self.data_lock:
            return vela.get('open_time', dt_datetime.now())
            return vela.get('open_time', datetime.now())
    
    # Alias for compatibility
    tiempo_ultima_vela = get_tiempo_ultima_vela
    
    def get_valor_ultima_vela(self, nemo, tipoval):
        """
        Get specific value from latest candle.
        
        Parameters:
        -----------
        nemo : str
            Base asset symbol (e.g., 'LINK')
        tipoval : str
            Value type: 'open', 'high', 'low', 'close', 'volume'
        
        Returns:
        --------
        float : Requested value
        """
        with self.data_lock:
            vela = self.ultimo_dato_nemo.get(nemo, {})
            key = tipoval.lower()
            return vela.get(key, 0.0)
    
    # Alias for compatibility
    valor_ultima_vela = get_valor_ultima_vela
    
    def get_valor_ultimas_velas(self, nemo, tipoval, N=1):
        """
        Get specific values from last N candles.
        
        Parameters:
        -----------
        nemo : str
            Base asset symbol
        tipoval : str
            Value type: 'open', 'high', 'low', 'close', 'volume'
        N : int
            Number of values to return
        
        Returns:
        --------
        np.array : Array of requested values
        """
        with self.data_lock:
            if nemo in self.datos_nemo and isinstance(self.datos_nemo[nemo], pd.DataFrame):
                df = self.datos_nemo[nemo]
                col = tipoval.lower()
                if col in df.columns:
                    if len(df) >= N:
                        return df[col].iloc[-N:].values
                    else:
                        return df[col].values
            return np.array([])
    
    # Alias for compatibility
    valor_ultimas_velas = get_valor_ultimas_velas
    
    def actualizar_velas(self, evento=None):
        """
        Update candles from event or internal state.
        
        This method is called by the streaming handlers to update
        the internal candle data structures.
        
        Parameters:
        -----------
        evento : EventoMdo, optional
            Market data event
        """
        # Most updates happen in _process_grpc_ohlcv
        # This method exists for interface compatibility
        pass

# ============================================================================
# OrderBook Helper Class (Internal to BinanceData)
# ============================================================================
class OrderBook:
    """
    Internal class for managing a single symbol's order book.
    NOT intended to be used directly outside of BinanceData.
    
    Uses SortedDict for O(log n) price level operations.
    Thread-safe through BinanceData's locking mechanism.
    """
    
    def __init__(self, symbol):
        """
        Initialize order book for a symbol.
        
        Args:
            symbol (str): Trading symbol (e.g., 'BTCUSDT')
        """
        self.symbol = symbol
        # Use negative keys for bids to maintain descending order
        self.bids = SortedDict()  # {-price: quantity} for descending sort
        self.asks = SortedDict()  # {price: quantity} for ascending sort
        self.last_update_id = 0
        self.is_synchronized = False
        self.last_update_time = None
        
    def update_bid(self, price: float, quantity: float):
        """
        Update or remove a bid price level.
        
        Args:
            price (float): Price level
            quantity (float): Quantity (0 to remove)
        """
        if quantity == 0:
            self.bids.pop(-price, None)  # Remove if exists
        else:
            self.bids[-price] = quantity
            
    def update_ask(self, price: float, quantity: float):
        """
        Update or remove an ask price level.
        
        Args:
            price (float): Price level
            quantity (float): Quantity (0 to remove)
        """
        if quantity == 0:
            self.asks.pop(price, None)
        else:
            self.asks[price] = quantity
            
    def get_best_bid(self) -> tuple:
        """
        Return (price, quantity) of best bid, or (None, None).
        
        Returns:
            tuple: (price, quantity) or (None, None)
        """
        if not self.bids:
            return None, None
        neg_price, qty = self.bids.peekitem(0)  # First item (highest)
        return -neg_price, qty
        
    def get_best_ask(self) -> tuple:
        """
        Return (price, quantity) of best ask, or (None, None).
        
        Returns:
            tuple: (price, quantity) or (None, None)
        """
        if not self.asks:
            return None, None
        return self.asks.peekitem(0)  # First item (lowest)
        
    def get_bids(self, levels: int = 10) -> list:
        """
        Return top N bid levels as [(price, qty), ...].
        Sorted descending by price.
        
        Args:
            levels (int): Number of levels to return
            
        Returns:
            list: [(price, qty), ...]
        """
        result = []
        for neg_price, qty in list(self.bids.items())[:levels]:
            result.append((-neg_price, qty))
        return result
        
    def get_asks(self, levels: int = 10) -> list:
        """
        Return top N ask levels as [(price, qty), ...].
        Sorted ascending by price.
        
        Args:
            levels (int): Number of levels to return
            
        Returns:
            list: [(price, qty), ...]
        """
        return list(self.asks.items())[:levels]
        
    def clear(self):
        """Clear all price levels."""
        self.bids.clear()
        self.asks.clear()
        self.last_update_id = 0
        self.is_synchronized = False
        
    def get_depth_snapshot(self, levels: int = 10) -> dict:
        """
        Return order book snapshot.
        
        Args:
            levels (int): Number of levels per side
            
        Returns:
            dict: Snapshot data
        """
        return {
            'symbol': self.symbol,
            'bids': self.get_bids(levels),
            'asks': self.get_asks(levels),
            'lastUpdateId': self.last_update_id,
            'is_synchronized': self.is_synchronized,
            'last_update_time': self.last_update_time
        }

class BinanceData(AdminDatos):
    """
    Obtiene datos de mercado en tiempo real desde Binace Spot y 
    Binance Perpetuals via WebSocket
    """

    # ── Global REST rate limiter (shared across all instances in this process) ──
    _rest_lock = threading.Lock()
    _last_rest_call = 0.0
    _MIN_REST_INTERVAL = 2.0  # at most 1 REST call every 2 seconds

    # ── OHLCV-specific throttle (heavier endpoint, separate timer) ──
    _ohlcv_lock = threading.Lock()
    _last_ohlcv_call = 0.0
    _MIN_OHLCV_INTERVAL = 10.0   # ongoing: 1 OHLCV call every 10s
    _ohlcv_init_done = False      # after init, slow-layer uses longer gap
    _MIN_OHLCV_INTERVAL_SLOW = 60.0  # slow-layer refreshes: 1/min

    @classmethod
    def _rate_limit_wait(cls):
        """Block until at least _MIN_REST_INTERVAL seconds since last REST call."""
        with cls._rest_lock:
            now = time.time()
            elapsed = now - cls._last_rest_call
            if elapsed < cls._MIN_REST_INTERVAL:
                wait = cls._MIN_REST_INTERVAL - elapsed
                time.sleep(wait)
            cls._last_rest_call = time.time()

    @classmethod
    def _ohlcv_rate_limit_wait(cls):
        """Block until enough time has passed since the last
        OHLCV REST call.

        During startup (set_initial_candles), uses the shorter
        _MIN_OHLCV_INTERVAL gap (10 s).  After init is done,
        uses _MIN_OHLCV_INTERVAL_SLOW (60 s) so the slow-layer
        refreshes don't hammer the API.
        """
        interval = (
            cls._MIN_OHLCV_INTERVAL
            if not cls._ohlcv_init_done
            else cls._MIN_OHLCV_INTERVAL_SLOW
        )
        with cls._ohlcv_lock:
            now = time.time()
            elapsed = now - cls._last_ohlcv_call
            if elapsed < interval:
                wait = interval - elapsed
                import logging
                logging.getLogger(__name__).info(
                    "OHLCV throttle: sleeping %.1fs", wait
                )
                time.sleep(wait)
            cls._last_ohlcv_call = time.time()

    def __init__(self, eventos, lista_nemos, interval='1m', testnet=False,
                 subscribe_orderbooks=True):
        # Initialize basic attributes first
        self.eventos = eventos
        self.lista_nemos = lista_nemos
        self.datos_nemo = {}
        self.ultimo_dato_nemo = {}
        self.continuar_backtest = True
        self.indice_vela = 0
        self.base_token = 'USDT'
        
        # Initialize empty data structures for all symbols BEFORE WebSocket connects
        # This prevents KeyError if WebSocket messages arrive before set_initial_candles()
        for nemo in self.lista_nemos:
            self.datos_nemo[nemo] = pd.DataFrame()
            self.ultimo_dato_nemo[nemo] = {}
            logger.info(f"Initialized empty data structures for {nemo}")
        
        self.binance_api_key = os.getenv("BINANCE_API_KEY")
        self.binance_secret_key = os.getenv("BINANCE_SECRET_KEY")
        self.comb_index = None
        self.ws = None  # OHLCV WebSocket
        self.interval = interval
        self.period_id = interval 
        self.testnet = testnet
        
        # OHLCV WebSocket management attributes
        self.ws_thread = None
        self.data_lock = threading.Lock()
        self.is_running = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.last_message_time = {}
        self.subscription_id = 0
        
        # ===== ORDER BOOK MANAGEMENT ATTRIBUTES =====
        # Order book data structures
        self.order_books = {}  # {symbol: OrderBook instance}
        self.order_book_buffers = {}  # {symbol: [buffered_events]} - Buffer during sync
        self.order_book_subscriptions = {}  # {symbol: {'depth_type', 'speed', 'market_type'}}
        
        # Separate WebSocket for order books (to avoid mixing streams)
        self.ob_ws = None  # Order book WebSocket
        self.ob_ws_thread = None
        self.ob_is_running = False
        self.ob_subscription_id = 0
        self.ob_reconnect_attempts = 0
        self.ob_lock = threading.Lock()  # Separate lock for order book operations
        
        # Market type configuration for each symbol
        self.symbol_market_types = {}  # {symbol: 'spot'|'futures'|'perpetuals'}
        
        # Connect to  binance Futures API
        self.perpetual_url = "https://fapi.binance.com"
        self.perp_session = None

        
        # Connect WebSocket LAST, after all methods are available
        self.connect_websocket(interval=self.interval)
        
        # Subscribe to order books for all symbols (needed for VWAP calculation)
        if subscribe_orderbooks:
            logger.info("Subscribing to order books for VWAP calculations...")
            for nemo in self.lista_nemos:
                self.subscribe_orderbook(nemo, depth_type='depth20', speed='100ms', market_type='perpetuals')
                logger.info(f"Subscribed to order book for {nemo}")
            logger.info("Order book subscriptions complete")
        else:
            logger.info("Order book subscriptions skipped (subscribe_orderbooks=False)")

    def connect_websocket(self, interval='1m'):
        """Conecta al WebSocket de Binance y suscribe a los streams de datos"""
        self.is_running = True
        base_ws_url = "wss://stream.binance.com:9443/ws/"
        streams = '/'.join([f"{nemo.lower()}{self.base_token.lower()}@kline_{interval}" for nemo in self.lista_nemos])
        ws_url = f"{base_ws_url}{streams}"
        
        logger.info(f"Connecting to WebSocket: {ws_url}")
        
        self.ws = websocket.WebSocketApp(ws_url,
                                        on_message=self.on_message,
                                        on_error=self.on_error,
                                        on_close=self.on_close,
                                        on_open=self.on_open,
                                        on_ping=self.on_ping,
                                        on_pong=self.on_pong)
        
        # Execute WebSocket in separate thread
        self.ws_thread = threading.Thread(target=self.ws.run_forever)
        self.ws_thread.daemon = True
        self.ws_thread.start()
        time.sleep(1) # Allow time for connection to establish

    def disconnect_websocket(self):
        """Cierra la conexión WebSocket de forma segura"""
        self.is_running = False
        if self.ws:
            self.ws.close()
        if self.ws_thread and self.ws_thread.is_alive():
            self.ws_thread.join(timeout=5)
        logger.info("WebSocket disconnected safely")

    def reconnect_websocket(self):
        """Reintenta la conexión con backoff exponencial"""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error(f"Max reconnection attempts ({self.max_reconnect_attempts}) reached")
            return
        
        self.reconnect_attempts += 1
        backoff_time = min(300, (2 ** self.reconnect_attempts))
        logger.info(f"Reconnecting in {backoff_time} seconds (attempt {self.reconnect_attempts}/{self.max_reconnect_attempts})")
        time.sleep(backoff_time)
        
        self.disconnect_websocket()
        time.sleep(2)
        self.connect_websocket(interval=self.interval)

    def set_initial_candles(self, lookback: int = 5000):
        """
        Load historical candles for all symbols from Binance Futures REST API.
        
        This ensures historical data comes from the SAME SOURCE as live WebSocket data,
        avoiding discrepancies between CoinAPI historical and Binance live data.
        
        Parameters:
        -----------
        lookback : int
            Number of historical candles to load (default 5000)
        """
        logger.info(f"Loading {lookback} historical candles from Binance Futures API...")
        
        for nemo in self.lista_nemos:
            try:
                # Use the existing get_perpetual_ohlcv method
                df = self.get_perpetual_ohlcv(
                    symbol=nemo,
                    interval=self.interval,
                    limit=lookback
                )
                
                if not df.empty:
                    # Ensure column names match WebSocket format
                    if 'open_time' not in df.columns:
                        df['open_time'] = df.index
                    
                    # Store in datos_nemo as DataFrame
                    with self.data_lock:
                        self.datos_nemo[nemo] = df
                        
                        # Set ultimo_dato_nemo from last row
                        if not df.empty:
                            last_row = df.iloc[-1]
                            self.ultimo_dato_nemo[nemo] = {
                                'open_time': last_row.get('open_time', last_row.name),
                                'close_time': last_row.get('close_time'),
                                'open': float(last_row['open']),
                                'high': float(last_row['high']),
                                'low': float(last_row['low']),
                                'close': float(last_row['close']),
                                'volume': float(last_row['volume']),
                                'trades': int(last_row.get('trades', 0)),
                                'interval': self.interval
                            }
                    
                    logger.info(f"✅ Loaded {len(df)} historical candles for {nemo}")
                else:
                    logger.warning(f"⚠️ No historical data returned for {nemo}")
                    
            except Exception as e:
                logger.error(f"❌ Error loading historical data for {nemo}: {e}")
                import traceback
                traceback.print_exc()
        
        # Verify all symbols have data
        logger.info("Historical data initialization complete")
        for nemo in self.lista_nemos:
            if nemo in self.datos_nemo and not self.datos_nemo[nemo].empty:
                logger.info(f"✅ {nemo}: {len(self.datos_nemo[nemo])} candles ready")
            else:
                logger.warning(f"⚠️ {nemo}: No historical data loaded - will wait for WebSocket data")

        # Mark init phase done → subsequent OHLCV calls use slower cadence
        BinanceData._ohlcv_init_done = True

    def get_symbol_id(self, nemo, bolsa=None, libro=None):
        """
        Build symbol ID for compatibility with strategy code.
        
        For BinanceData, this returns the Binance format (e.g., 'BTCUSDT')
        
        Parameters:
        -----------
        nemo : str
            Base asset symbol (e.g., 'BTC', 'LINK')
        
        Returns:
        --------
        str : Symbol ID in Binance format (e.g., 'LINKUSDT')
        """
        return f"{nemo.upper()}{self.base_token.upper()}"

    def on_open(self, ws):
        """Maneja la apertura del WebSocket"""
        logger.info("WebSocket connection opened")
        self.reconnect_attempts = 0

    def on_message(self, ws, message):
        """Maneja los mensajes entrantes del WebSocket"""
        try:
            data = json.loads(message)
            kline = data['k']
            symbol_full = data['s']  # e.g., 'BTCUSDT'
            nemo = symbol_full.replace(self.base_token, '')  # Extract symbol
            
            # Only process completed candles to avoid partial data
            if not kline['x']:  # x=false means candle not closed yet
                return
            
            vela = {
                'open_time': datetime.fromtimestamp(kline['t'] / 1000),
                'close_time': datetime.fromtimestamp(kline['T'] / 1000),
                'open': float(kline['o']),
                'high': float(kline['h']),
                'low': float(kline['l']),
                'close': float(kline['c']),
                'volume': float(kline['v']),
                'trades': int(kline['n']),
                'interval': kline['i']
            }

            # Thread-safe update
            with self.data_lock:
                self.ultimo_dato_nemo[nemo] = vela
                
                # Append to DataFrame buffer
                if nemo not in self.datos_nemo or not isinstance(self.datos_nemo[nemo], pd.DataFrame):
                    self.datos_nemo[nemo] = pd.DataFrame()
                
                new_row = pd.DataFrame([vela])
                self.datos_nemo[nemo] = pd.concat([self.datos_nemo[nemo], new_row], ignore_index=True)
                
                # Limit buffer size to prevent memory issues
                max_buffer_size = 1000
                if len(self.datos_nemo[nemo]) > max_buffer_size:
                    self.datos_nemo[nemo] = self.datos_nemo[nemo].iloc[-max_buffer_size:]
                    self.datos_nemo[nemo].reset_index(drop=True, inplace=True)
            
            # Create and push EventoMdo to queue
            evento = EventoMdo(
                nemo=nemo,
                evento_tipo='KLINE',
                timestamp=vela['open_time'],
                **vela
            )
            
            self.eventos.put(evento)
            logger.debug(f"📊 {nemo}: {vela['close']} @ {vela['close_time']}")
            self.last_message_time[nemo] = time.time()
            
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            import traceback
            traceback.print_exc()

    def on_error(self, ws, error):
        """Maneja errores del WebSocket"""
        logger.error(f"WebSocket error: {error}")
        if self.is_running:
            self.reconnect_websocket()
    
    def on_close(self, ws, close_status_code, close_msg):
        """Maneja el cierre del WebSocket"""
        logger.info(f"WebSocket closed: {close_status_code} - {close_msg}")
        if self.is_running:  # Unexpected closure
            self.reconnect_websocket()

    def on_ping(self, ws, message):
        """Maneja ping frames del servidor"""
        logger.debug("Received ping from server")

    def on_pong(self, ws, message):
        """Maneja pong frames del servidor"""
        logger.debug("Received pong from server")
    
    # ===== Data Access Methods =====
    
    def get_latest_kline(self, symbol):
        """
        Obtiene la última vela completada para un símbolo.
        
        Args:
            symbol (str): Símbolo del activo (e.g., 'BTC', 'ETH')
            
        Returns:
            dict: Diccionario con datos OHLCV o None si no hay datos
        """
        with self.data_lock:
            return self.ultimo_dato_nemo.get(symbol)
    
    def get_all_latest_klines(self):
        """
        Obtiene las últimas velas completadas para todos los símbolos.
        
        Returns:
            dict: Diccionario {symbol: kline_data}
        """
        with self.data_lock:
            return self.ultimo_dato_nemo.copy()

    def get_kline_generator(self, symbol, lookback=91):
        """
        Genera velas en tiempo real con contexto histórico.
        
        Mantiene un buffer de las últimas N velas y yields tanto la vela
        más reciente como el buffer completo cada vez que se completa una nueva vela.
        
        Args:
            symbol (str): Símbolo del activo (e.g., 'BTC', 'ETH')
            lookback (int): Número de velas históricas a mantener en buffer
            
        Yields:
            tuple: (latest_candle, historical_buffer)
                - latest_candle (dict): Vela más reciente con datos OHLCV
                - historical_buffer (list): Lista de las últimas N velas (más reciente al final)
        
        Example:
            >>> for latest, buffer in data.get_kline_generator('BTC', lookback=50):
            >>>     print(f"Latest: {latest['close']}")
            >>>     print(f"Buffer size: {len(buffer)}")
            >>>     # Calculate SMA from buffer
            >>>     prices = [k['close'] for k in buffer]
            >>>     sma = sum(prices) / len(prices)
        """
        last_time = None
        # Initialize buffer dictionary with empty DataFrames for each symbol
        buffer = {nemo: pd.DataFrame() for nemo in self.lista_nemos}
        
        # Initialize buffer with existing historical data if available
        with self.data_lock:
            if symbol in self.datos_nemo and len(self.datos_nemo[symbol]) > 0:
                # Get the most recent 'lookback' candles from existing data
                buffer[symbol] = self.datos_nemo[symbol][-lookback:].copy() if len(self.datos_nemo[symbol]) >= lookback else self.datos_nemo[symbol].copy()
                logger.info(f"Initialized generator buffer for {symbol} with {len(buffer[symbol])} historical candles")
        
        # If the specific symbol doesn't have historical data, fetch from API
        if symbol not in self.datos_nemo or len(self.datos_nemo.get(symbol, [])) == 0:
            try:
                # Format symbol for Binance API (e.g., 'BTC' -> 'BTCUSDT')
                full_symbol = f"{symbol}{self.base_token}"
                logger.info(f"Fetching historical data for {full_symbol}...")
                # Calculate start time based on interval and lookback
                interval_minutes = self._interval_to_minutes(self.interval)
                start_time = datetime.now() - timedelta(minutes=interval_minutes * lookback)
                start_str = start_time.strftime('%d %b %Y %H:%M:%S')
                logger.info(f"Fetching historical data from {start_str} for {full_symbol}...")
                buffer[symbol] = self.get_perpetual_ohlcv(full_symbol, interval=self.interval, limit=lookback)
                if not buffer[symbol].empty:
                    self.ultimo_dato_nemo[symbol] = buffer[symbol].iloc[-1]
                    logger.info(f"Fetched {len(buffer[symbol])} candles for {symbol}")
    
            except Exception as e:
                logger.warning(f"Could not fetch historical data for {symbol}: {e}. Starting with empty buffer.")
                
        
        # Stream real-time updates
        while self.is_running:
            with self.data_lock:
                for symbol in self.lista_nemos:
                    latest = self.ultimo_dato_nemo[symbol]
                    # Print only symbol and last close price (instead of full Series)
                    if 'close' in latest:
                        print(f"{symbol}: {latest['close']}")
                    current_time = latest['close_time']
                    
                    # Solo yield cuando hay una nueva vela
                    if last_time is None or current_time > last_time:
                        last_time = current_time
                        
                        # Agregar nueva vela al buffer (DataFrame)
                        # Convert Series to DataFrame with single row
                        new_row = pd.DataFrame([latest])
                        buffer[symbol] = pd.concat([buffer[symbol], new_row], ignore_index=True)
                        
                        # Mantener tamaño del buffer
                        if len(buffer[symbol]) > lookback:
                            buffer[symbol] = buffer[symbol].iloc[1:]  # Remueve la vela más antigua
                            buffer[symbol].reset_index(drop=True, inplace=True)

                        # Yield la vela más reciente y una copia del buffer
                        yield latest, buffer[symbol].copy()
            
            time.sleep(0.5)  # Evita consumo excesivo de CPU
    
    def get_last_n_klines(self, symbol,valueType, N=91):
        """ Obtiene las últimas N velas completadas para un símbolo 
        extrayendo del generador de velas en tiempo real en formato DataFrame"""
        klines = []
        gen = self.get_kline_generator(symbol, lookback=N)
        
        try:
            for _ in range(N):
                latest, _ = next(gen)
                klines.append(latest)
        except StopIteration:
            pass
        
        df = pd.DataFrame(klines)
        return df[valueType]

    def carga_datos_nemo(self):
        "Usa get_kline_generator para cargar datos históricos en datos_nemo"
        for nemo in self.lista_nemos:
            gen = self.get_kline_generator(nemo, lookback=500)
            self.datos_nemo[nemo] = gen
    
    def get_nueva_vela(self,nemo):
            #entrega la última vela desde la fuente de datos
            latest_candle_gen = self.get_kline_generator(nemo, lookback=500)
            for v in latest_candle_gen:
                yield v

    def get_ultima_vela(self,nemo):
        #Entrega la última vela de la lista de nemos
        try:
            ultima_vela = self.ultimo_dato_nemo[nemo]  # This is a Series with the latest candle
        except KeyError: #Si no encuentra datos en la base...
            print('El nemotécnico solicitado no existe en la base')
            raise
        else:
            return ultima_vela  # Return the Series directly
    
    def get_ultimas_velas(self,nemo,N=1):
        #Devuelve las últimas N velas para el nemotécnico o N-k si no hay 
        #mas disponibles
        try:
            # Use datos_nemo (DataFrame) to get last N rows
            if nemo in self.datos_nemo and isinstance(self.datos_nemo[nemo], pd.DataFrame):
                df = self.datos_nemo[nemo]
                if not df.empty:
                    return df.iloc[-N:] if len(df) >= N else df
                else:
                    logger.warning(f"⚠️ DataFrame for {nemo} is empty, waiting for data...")
                    return pd.DataFrame()  # Return empty DataFrame instead of failing
            elif nemo in self.ultimo_dato_nemo and self.ultimo_dato_nemo[nemo]:
                # Fallback: return just the last vela as dict
                return self.ultimo_dato_nemo[nemo]
            else:
                logger.warning(f"⚠️ No data available yet for {nemo}, returning empty DataFrame")
                return pd.DataFrame()  # Return empty DataFrame to prevent crash
        except KeyError:#Si no encuentra datos en la base...
            print('El nemotécnico no está disponible en la base de datos')
            raise
    
    def get_tiempo_ultima_vela(self,nemo):
        #Devuelve un Datetime para la última vela
        try:
            ultima_vela = self.ultimo_dato_nemo[nemo]  # This is a Series with the latest candle
        except KeyError:#Si no encuentra datos en la base...
            print('El Nemotécnico no está disponible en la base de datos')
            raise
        else:
            return ultima_vela['open_time']  # Access 'open_time' from Series
    
    def get_valor_ultima_vela(self, nemo, tipoval):
        #Devuelve el valor solicitado para la última vela.
        #Open, High, Low, Close, Volumen, Open interest
        try:
            ultima_vela = self.ultimo_dato_nemo[nemo]
        except KeyError:
            logger.warning(
                f"Nemo '{nemo}' no disponible en ultimo_dato_nemo"
            )
            return None

        # ✅ FIX E: Guard against empty dict (REST init failed)
        if not ultima_vela:
            logger.warning(
                f"ultimo_dato_nemo['{nemo}'] está vacío "
                "(datos iniciales no cargados aún)"
            )
            return None

        key = tipoval.lower()
        if key not in ultima_vela:
            logger.warning(
                f"Key '{key}' not found in ultima_vela for '{nemo}'"
            )
            return None

        return ultima_vela[key]
            
    def get_valor_ultimas_velas(self, nemo, tipoval='close', N=1):
        """
        Devuelve uno de los valores (columnas) de las N últimas velas o N-k si hay menos que N.
        
        Parameters:
        -----------
        nemo : str
            Symbol/nemotécnico
        tipoval : str
            Column name to extract ('open', 'high', 'low', 'close', 'volume')
        N : int
            Number of candles to retrieve
            
        Returns:
        --------
        np.array : Array of values for the requested column
        """
        try:
            lista_velas = self.get_ultimas_velas(nemo, N)
            
        except KeyError:
            print('El nemotécnico no está disponible en la base')
            raise
        else:
            # lista_velas is a DataFrame, access column directly
            if isinstance(lista_velas, pd.DataFrame):
                return lista_velas[tipoval.lower()].values
            elif isinstance(lista_velas, pd.Series):
                # Single row case
                return np.array([lista_velas[tipoval.lower()]])
            else:
                # Fallback for dict or other structures
                return np.array([lista_velas.get(tipoval.lower(), lista_velas.get(tipoval))])
            
    def actualizar_velas(self):
        #Empuja los datos de la última vela en la estructura denominada
        #ultimo_dato_nemo para todos los nemos en la lista de nemos denominada
        #lista_nemos
        for s in self.lista_nemos:
            try:
                # get_nueva_vela returns tuple (latest, buffer) from generator
                vela_tuple = next(self.get_nueva_vela(s))
                latest_candle, buffer = vela_tuple  # Unpack the tuple
            except StopIteration:
                self.continuar_backtest=False
            else:
                if latest_candle is not None:
                    # Update ultimo_dato_nemo with the latest candle (Series)
                    self.ultimo_dato_nemo[s] = latest_candle
                    
                    # Update datos_nemo with the full buffer (DataFrame)
                    if s not in self.datos_nemo:
                        self.datos_nemo[s] = buffer
                    else:
                        # Replace with updated buffer from generator
                        self.datos_nemo[s] = buffer
                    
        self.eventos.put(EventoMdo()) 


    # ===== Dynamic Subscription Methods =====
    
    def _interval_to_minutes(self, interval):
        """
        Convierte un intervalo de Binance a minutos.
        
        Args:
            interval (str): Intervalo de Binance (e.g., '1m', '5m', '1h', '1d')
            
        Returns:
            int: Número de minutos
        """
        unit = interval[-1]
        value = int(interval[:-1])
        
        if unit == 's':
            return value / 60  # seconds to minutes
        elif unit == 'm':
            return value
        elif unit == 'h':
            return value * 60
        elif unit == 'd':
            return value * 60 * 24
        elif unit == 'w':
            return value * 60 * 24 * 7
        elif unit == 'M':
            return value * 60 * 24 * 30  # Approximate month
        else:
            return 1  # Default to 1 minute
    
    def _format_symbol(self, nemo):
        """
        Formatea el símbolo al formato de Binance.
        
        Args:
            nemo (str): Símbolo base (e.g., 'BTC')
            
        Returns:
            str: Símbolo en formato Binance (e.g., 'btcusdt')
        """
        return f"{nemo.lower()}{self.base_token.lower()}"
    
    def _send_subscribe(self, symbols, interval='1m'):
        """
        Envía mensaje de suscripción al WebSocket.
        
        Args:
            symbols (list): Lista de símbolos a suscribir
            interval (str): Intervalo de las velas
        """
        if not self.ws:
            logger.error("WebSocket not connected")
            return False
        
        params = [f"{self._format_symbol(s)}@kline_{interval}" for s in symbols]
        self.subscription_id += 1
        
        subscribe_msg = {
            "method": "SUBSCRIBE",
            "params": params,
            "id": self.subscription_id
        }
        
        try:
            self.ws.send(json.dumps(subscribe_msg))
            logger.info(f"Subscribed to: {params}")
            return True
        except Exception as e:
            logger.error(f"Error subscribing: {e}")
            return False
    
    def _send_unsubscribe(self, symbols, interval='1m'):
        """
        Envía mensaje de desuscripción al WebSocket.
        
        Args:
            symbols (list): Lista de símbolos a desuscribir
            interval (str): Intervalo de las velas
        """
        if not self.ws:
            logger.error("WebSocket not connected")
            return False
        
        params = [f"{self._format_symbol(s)}@kline_{interval}" for s in symbols]
        self.subscription_id += 1
        
        unsubscribe_msg = {
            "method": "UNSUBSCRIBE",
            "params": params,
            "id": self.subscription_id
        }
        
        try:
            self.ws.send(json.dumps(unsubscribe_msg))
            logger.info(f"Unsubscribed from: {params}")
            return True
        except Exception as e:
            logger.error(f"Error unsubscribing: {e}")
            return False
    
    def subscribe_symbol(self, symbol, interval='1m'):
        """
        Agrega un nuevo símbolo a la conexión WebSocket existente.
        
        Args:
            symbol (str): Símbolo a agregar (e.g., 'DOGE')
            interval (str): Intervalo de velas
            
        Returns:
            bool: True si exitoso
        """
        if symbol not in self.lista_nemos:
            self.lista_nemos.append(symbol)
        
        return self._send_subscribe([symbol], interval)
    
    def unsubscribe_symbol(self, symbol, interval='1m'):
        """
        Remueve un símbolo de la conexión WebSocket.
        
        Args:
            symbol (str): Símbolo a remover
            interval (str): Intervalo de velas
            
        Returns:
            bool: True si exitoso
        """
        if symbol in self.lista_nemos:
            self.lista_nemos.remove(symbol)
        
        return self._send_unsubscribe([symbol], interval)
    
    # ===== Historical Data Methods =====
    
    async def init_perp_session(self):
        """Initialize aiohttp session for perpetuals"""
        if not self.perp_session:
            self.perp_session = aiohttp.ClientSession()
    
    def get_headers(self):
        """Get API headers with authentication"""
        return {
            'X-Api-Key': self.binance_api_key,
            'Accept': 'application/json'
        }

    async def close_perp_session(self):
        """Close the aiohttp session for perpetuals"""
        if self.perp_session:
            await self.perp_session.close()
    
    async def get_instruments(self, exchange: str) -> List[Dict]:
        """Get trading pairs for specific exchange"""
        await self.init_perp_session()
        url = f"{self.perpetual_url}/v2/data/instruments"
        params = {'exchange': exchange}
        
        async with self.perp_session.get(url, headers=self.get_headers(), params=params) as response:
            data = await response.json()
            return data.get('data', [])
    
    async def get_spot_ohlcv(self, exchange: str, instrument: str, 
                       start_time: str, end_time: str, interval: str = '1m') -> pd.DataFrame:
        """
        Get OHLCV candlestick data
        
        Parameters:
        - exchange: Exchange code (e.g., 'cbse' for Coinbase)
        - instrument: Trading pair (e.g., 'btc-usd')
        - start_time: Start time in YYYY-MM format or ISO format
        - end_time: End time in YYYY-MM format or ISO format
        - interval: Time interval ('1m', '5m', '1h', '1d')
        """
        await self.init_perp_session()
        url = f"{self.perpetual_url}/v2/data/trades.v1/exchanges/{exchange}/spot/{instrument}/aggregations/count_ohlcv_vwap"
        # start_time end_time should be iso8601 string with optional precision up to millisecond
        # Format: 2018-10-03T13:29:26Z or 2018-10-03T13:29:26.530Z
        start_dt = pd.to_datetime(start_time)
        end_dt = pd.to_datetime(end_time)
        
        # Convert to UTC and format as ISO8601 with Z suffix
        if start_dt.tzinfo is None:
            start_dt = start_dt.tz_localize('UTC')
        else:
            start_dt = start_dt.tz_convert('UTC')
            
        if end_dt.tzinfo is None:
            end_dt = end_dt.tz_localize('UTC')
        else:
            end_dt = end_dt.tz_convert('UTC')
        
        # Format as ISO8601 with Z suffix (remove timezone info and add Z)
        start_time_formatted = start_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        end_time_formatted = end_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        
        params = {
            'start_time': start_time_formatted,
            'end_time': end_time_formatted,
            'interval': interval
        }

        async with self.perp_session.get(url, headers=self.get_headers(), params=params) as response:
            data = await response.json()
            
            if 'data' in data and data['data']:
                df = pd.DataFrame(data['data'])
                if 'timestamp' in df.columns:
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                    df.set_index('timestamp', inplace=True)
                    
                    # Convert numeric columns from string to float
                    numeric_columns = ['open', 'high', 'low', 'close', 'volume']
                    for col in numeric_columns:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors='coerce')
                    
                    return df
                else:
                    logger.error(f"Error: 'timestamp' column not found in OHLCV data. Available columns: {df.columns.tolist()}")
                    return pd.DataFrame()
            else:
                logger.error(f"Error getting OHLCV data: {data}")
                return pd.DataFrame()

    def get_perpetual_ohlcv(self, symbol: str, interval: str = '1m',
                            limit: int = 500) -> pd.DataFrame:
        """
        Synchronous version: Get historical kline/candlestick data for perpetuals
        
        Parameters:
        - symbol: Trading symbol (e.g., 'BTCUSDT' or 'BTC' - will auto-format)
        - interval: Kline interval ('1m', '5m', '15m', '1h', '4h', '1d')
        - limit: Number of klines to retrieve (max 1500)
        
        Returns:
        - DataFrame with OHLCV data
        """
        # ── OHLCV-specific rate-limit: at most 1 call per minute ──
        self._ohlcv_rate_limit_wait()
        # Also respect the general REST limiter
        self._rate_limit_wait()

        # ✅ FIX B: Binance /fapi/v1/klines allows max 1500
        BINANCE_MAX_LIMIT = 1500
        if limit > BINANCE_MAX_LIMIT:
            logger.warning(
                f"Requested limit={limit} exceeds Binance max "
                f"({BINANCE_MAX_LIMIT}), clamping."
            )
            limit = BINANCE_MAX_LIMIT

        try:
            # Format symbol if needed (e.g., 'BTC' -> 'BTCUSDT')
            if not symbol.endswith(self.base_token):
                formatted_symbol = f"{symbol.upper()}{self.base_token}"
            else:
                formatted_symbol = symbol.upper()
            
            url = f"{self.perpetual_url}/fapi/v1/klines"
            params = {
                'symbol': formatted_symbol,
                'interval': interval,
                'limit': limit
            }
            
            logger.info(f"Requesting {url} with params: {params}")
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                df = pd.DataFrame(data, columns=[
                    'timestamp', 'open', 'high', 'low', 'close', 'volume',
                    'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                    'taker_buy_quote', 'ignore'
                ])
                
                # Convert timestamp to datetime
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df.set_index('timestamp', inplace=True)
                
                # Add open_time column to match WebSocket data structure
                df['open_time'] = df.index
                
                # Convert close_time to datetime
                df['close_time'] = pd.to_datetime(df['close_time'], unit='ms')
                
                # Convert price columns to float
                for col in ['open', 'high', 'low', 'close', 'volume', 'quote_volume', 'trades', 'taker_buy_base',
                    'taker_buy_quote']:
                    df[col] = df[col].astype(float)
                
                return df
            else:
                logger.error(f"Error getting perpetual klines for {formatted_symbol}: HTTP {response.status_code}")
                logger.error(f"Response body: {response.text}")
                logger.error(f"Request URL: {response.url}")
                return pd.DataFrame()
        
        except Exception as e:
            logger.error(f"Error getting perpetual klines for {symbol}: {e}")
            
            return pd.DataFrame()

    async def get_long_short_ratio(self, symbol: str, period: str = '5m') -> Optional[Dict]:
        """
        Synchronous version: Get long/short ratio from top trader accounts
        Useful for sentiment analysis
        
        Parameters:
        - period: '5m', '15m', '30m', '1h', '2h', '4h', '6h', '12h', '1d'
        """
        import requests
        
        try:
            url = f"{self.perpetual_url}/futures/data/topLongShortAccountRatio"
            params = {
                'symbol': symbol,
                'period': period,
                'limit': 1
            }

            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data:
                    latest = data[0]
                    return {
                        'symbol': symbol,
                        'long_short_ratio': float(latest['longShortRatio']),
                        'long_account': float(latest['longAccount']),
                        'short_account': float(latest['shortAccount']),
                        'timestamp': datetime.fromtimestamp(int(latest['timestamp']) / 1000)
                    }
                return None
            else:
                return None
                    
        except Exception as e:
            logger.error(f"Error getting long/short ratio for {symbol}: {e}")
            return None
    


    # ========================================================================
    # ORDER BOOK MANAGEMENT METHODS
    # ========================================================================
    
    def connect_orderbook_websocket(self):
        """
        Conecta al WebSocket de Binance para order books.
        Usa una conexión separada de la de OHLCV para evitar interferencias.
        """
        if self.ob_ws_thread and self.ob_ws_thread.is_alive():
            logger.warning("Order book WebSocket already connected")
            return
            
        self.ob_is_running = True
        
        def run_websocket():
            # Base URL for spot (will be determined by subscriptions)
            ws_url = "wss://stream.binance.com:9443/ws"
            
            self.ob_ws = websocket.WebSocketApp(
                ws_url,
                on_open=self._on_ob_open,
                on_message=self._on_ob_message,
                on_error=self._on_ob_error,
                on_close=self._on_ob_close,
                on_ping=self._on_ob_ping,
                on_pong=self._on_ob_pong
            )
            
            self.ob_ws.run_forever(
                ping_interval=30,
                ping_timeout=10
            )
        
        self.ob_ws_thread = threading.Thread(target=run_websocket, daemon=True)
        self.ob_ws_thread.start()
        logger.info("Order book WebSocket connection started")
    
    def disconnect_orderbook_websocket(self):
        """Desconecta el WebSocket de order books"""
        self.ob_is_running = False
        if self.ob_ws:
            self.ob_ws.close()
        if self.ob_ws_thread:
            self.ob_ws_thread.join(timeout=5)
        logger.info("Order book WebSocket disconnected")
    
    def _on_ob_open(self, ws):
        """Callback cuando se abre la conexión de order books"""
        logger.info("Order book WebSocket connection opened")
        self.ob_reconnect_attempts = 0
        
        # Resubscribe to all active order books
        with self.ob_lock:
            for symbol, config in self.order_book_subscriptions.items():
                self._send_orderbook_subscribe(
                    symbol, 
                    config['depth_type'], 
                    config['speed']
                )
    
    def _on_ob_message(self, ws, message):
        """Procesa mensajes del WebSocket de order books"""
        try:
            data = json.loads(message)
            
            # Handle subscription confirmation
            if 'result' in data and data['result'] is None:
                logger.info(f"Order book subscription confirmed: {data.get('id')}")
                return
                
            # Handle depth update
            if 'e' in data and data['e'] == 'depthUpdate':
                self._process_depth_update(data)
                
            # Handle book ticker
            elif 'e' in data and data['e'] == 'bookTicker':
                self._process_book_ticker(data)
                
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding order book message: {e}")
        except Exception as e:
            logger.error(f"Error processing order book message: {e}")
    
    def _on_ob_error(self, ws, error):
        """Maneja errores del WebSocket de order books"""
        logger.error(f"Order book WebSocket error: {error}")
    
    def _on_ob_close(self, ws, close_status_code, close_msg):
        """Maneja el cierre del WebSocket de order books"""
        logger.warning(f"Order book WebSocket closed: {close_status_code} - {close_msg}")
        
        if self.ob_is_running and self.ob_reconnect_attempts < self.max_reconnect_attempts:
            self.ob_reconnect_attempts += 1
            wait_time = min(2 ** self.ob_reconnect_attempts, 60)
            logger.info(f"Reconnecting order book WebSocket in {wait_time}s... (attempt {self.ob_reconnect_attempts})")
            time.sleep(wait_time)
            self.connect_orderbook_websocket()
    
    def _on_ob_ping(self, ws, message):
        """Responde a ping del servidor"""
        pass
    
    def _on_ob_pong(self, ws, message):
        """Maneja pong del servidor"""
        pass
    
    # ========================================================================
    # SUBSCRIPTION METHODS
    # ========================================================================
    
    def subscribe_orderbook(self, symbol, depth_type='depth10', speed='100ms', market_type='spot'):
        """
        Suscribe a actualizaciones de order book para un símbolo.
        
        Args:
            symbol (str): Símbolo de trading (e.g., 'BTC', 'ETH')
            depth_type (str): 'full'/'depth', 'depth5', 'depth10', 'depth20', 'ticker'
            speed (str): '1000ms' o '100ms'
            market_type (str): 'spot', 'futures', 'perpetuals'
            
        Returns:
            bool: True si la suscripción fue exitosa
        """
        with self.ob_lock:
            # Format symbol for Binance
            formatted_symbol = self._format_orderbook_symbol(symbol, market_type)
            
            # Store subscription info
            self.symbol_market_types[symbol] = market_type
            self.order_book_subscriptions[symbol] = {
                'depth_type': depth_type,
                'speed': speed,
                'market_type': market_type
            }
            
            # Initialize order book instance
            self.order_books[symbol] = OrderBook(formatted_symbol)
            self.order_book_buffers[symbol] = []
            
            # Connect WebSocket if not connected
            if not self.ob_ws or not self.ob_is_running:
                self.connect_orderbook_websocket()
                time.sleep(1)  # Wait for connection
            
            # Send subscription message
            success = self._send_orderbook_subscribe(symbol, depth_type, speed)
            
            if success:
                # Start synchronization process in background
                sync_thread = threading.Thread(
                    target=self._initialize_orderbook,
                    args=(symbol, market_type),
                    daemon=True
                )
                sync_thread.start()
                logger.info(f"Order book subscription started for {symbol}")
                
            return success
    
    def unsubscribe_orderbook(self, symbol):
        """
        Cancela suscripción a order book de un símbolo.
        
        Args:
            symbol (str): Símbolo de trading
            
        Returns:
            bool: True si se canceló exitosamente
        """
        with self.ob_lock:
            if symbol not in self.order_book_subscriptions:
                logger.warning(f"No active order book subscription for {symbol}")
                return False
                
            config = self.order_book_subscriptions[symbol]
            success = self._send_orderbook_unsubscribe(
                symbol, 
                config['depth_type'], 
                config['speed']
            )
            
            if success:
                # Clean up
                del self.order_book_subscriptions[symbol]
                if symbol in self.order_books:
                    del self.order_books[symbol]
                if symbol in self.order_book_buffers:
                    del self.order_book_buffers[symbol]
                if symbol in self.symbol_market_types:
                    del self.symbol_market_types[symbol]
                    
                logger.info(f"Order book unsubscribed for {symbol}")
                
            return success
    
    # ========================================================================
    # UTILITY METHODS
    # ========================================================================
    
    def _format_orderbook_symbol(self, symbol, market_type='spot'):
        """Formatea símbolo para Binance (e.g., 'BTC' -> 'BTCUSDT')"""
        return f"{symbol.upper()}{self.base_token.upper()}"
    
    def _reverse_format_symbol(self, formatted_symbol):
        """Convierte 'BTCUSDT' de vuelta a 'BTC'"""
        if formatted_symbol.endswith(self.base_token.upper()):
            return formatted_symbol[:-len(self.base_token)]
        return formatted_symbol
    
    def _format_depth_stream(self, symbol, depth_type='depth10', speed='100ms'):
        """
        Formatea nombre del stream para suscripción.
        
        Examples:
            btcusdt@depth         # Full depth, 1000ms
            btcusdt@depth@100ms   # Full depth, 100ms
            btcusdt@depth10@100ms # Top 10, 100ms
        """
        formatted_symbol = self._format_orderbook_symbol(symbol).lower()
        
        if depth_type == 'ticker':
            return f"{formatted_symbol}@bookTicker"
        
        if depth_type in ['depth', 'full']:
            stream = f"{formatted_symbol}@depth"
        else:
            stream = f"{formatted_symbol}@{depth_type}"
        
        if speed == '100ms' and depth_type != 'ticker':
            stream += '@100ms'
        
        return stream
    
    def _send_orderbook_subscribe(self, symbol, depth_type, speed):
        """Envía mensaje de suscripción al WebSocket"""
        if not self.ob_ws:
            return False
        
        stream = self._format_depth_stream(symbol, depth_type, speed)
        self.ob_subscription_id += 1
        
        msg = {
            "method": "SUBSCRIBE",
            "params": [stream],
            "id": self.ob_subscription_id
        }
        
        try:
            self.ob_ws.send(json.dumps(msg))
            logger.info(f"Subscribed to order book: {stream}")
            return True
        except Exception as e:
            logger.error(f"Error subscribing to order book: {e}")
            return False
    
    def _send_orderbook_unsubscribe(self, symbol, depth_type, speed):
        """Envía mensaje de desuscripción"""
        if not self.ob_ws:
            return False
        
        stream = self._format_depth_stream(symbol, depth_type, speed)
        self.ob_subscription_id += 1
        
        msg = {
            "method": "UNSUBSCRIBE",
            "params": [stream],
            "id": self.ob_subscription_id
        }
        
        try:
            self.ob_ws.send(json.dumps(msg))
            logger.info(f"Unsubscribed from order book: {stream}")
            return True
        except Exception as e:
            logger.error(f"Error unsubscribing from order book: {e}")
            return False
    
    # ========================================================================
    # SYNCHRONIZATION PROTOCOL (Following Binance Official Documentation)
    # ========================================================================
    
    def _initialize_orderbook(self, symbol, market_type='spot'):
        """
        Inicializa order book siguiendo el protocolo oficial de Binance.
        
        Proceso:
        1. Conecta a depth stream y empieza a bufferear eventos
        2. Obtiene snapshot vía REST API
        3. Valida snapshot vs primer evento buffered
        4. Descarta eventos antiguos
        5. Verifica continuidad
        6. Inicializa book con snapshot
        7. Aplica eventos buffered
        8. Continúa con eventos en tiempo real
        """
        try:
            logger.info(f"Initializing order book for {symbol}...")
            
            # Step 2: Fetch snapshot
            snapshot = self._fetch_orderbook_snapshot(symbol, market_type)
            if not snapshot:
                logger.error(f"Failed to fetch snapshot for {symbol}")
                return False
            
            with self.ob_lock:
                book = self.order_books[symbol]
                buffer = self.order_book_buffers[symbol]
                
                # Step 3 & 4: Validate and discard old events
                snapshot_id = snapshot['lastUpdateId']
                
                # Find first valid event
                valid_events = []
                found_valid_start = False
                
                for event in buffer:
                    event_U = event.get('U')
                    event_u = event.get('u')
                    
                    # Discard events older than snapshot
                    if event_u <= snapshot_id:
                        continue
                    
                    # Step 5: Verify continuity
                    if not found_valid_start:
                        if event_U <= snapshot_id + 1 <= event_u:
                            found_valid_start = True
                            valid_events.append(event)
                        else:
                            logger.error(f"Gap detected in {symbol}: snapshot_id={snapshot_id}, event=[{event_U}, {event_u}]")
                            time.sleep(1)
                            return self._initialize_orderbook(symbol, market_type)
                    else:
                        valid_events.append(event)
                
                # Step 6: Initialize with snapshot
                book.clear()
                for price_str, qty_str in snapshot['bids']:
                    book.update_bid(float(price_str), float(qty_str))
                for price_str, qty_str in snapshot['asks']:
                    book.update_ask(float(price_str), float(qty_str))
                book.last_update_id = snapshot_id
                
                # Step 7: Apply valid buffered events
                for event in valid_events:
                    self._apply_depth_update(symbol, event)
                
                # Step 8: Mark as synchronized
                book.is_synchronized = True
                book.last_update_time = datetime.now()
                
                # Clear buffer
                self.order_book_buffers[symbol] = []
                
                logger.info(f"✓ Order book synchronized for {symbol}")
                logger.info(f"  Bids: {len(book.bids)} levels, Asks: {len(book.asks)} levels")
                
                return True
                
        except Exception as e:
            logger.error(f"Error initializing order book for {symbol}: {e}")
            return False
    
    def _fetch_orderbook_snapshot(self, symbol, market_type='spot', limit=1000):
        """
        Obtiene snapshot del order book vía REST API.
        """
        formatted_symbol = self._format_orderbook_symbol(symbol, market_type)
        
        if market_type == 'spot':
            url = f"https://api.binance.com/api/v3/depth"
        else:
            url = f"https://fapi.binance.com/fapi/v1/depth"
        
        params = {
            'symbol': formatted_symbol,
            'limit': limit
        }
        
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching snapshot for {symbol}: {e}")
            return None
    
    # ========================================================================
    # UPDATE PROCESSING METHODS
    # ========================================================================
    
    def _process_depth_update(self, event):
        """Procesa evento depthUpdate del WebSocket"""
        try:
            symbol_formatted = event.get('s', '')
            symbol = self._reverse_format_symbol(symbol_formatted)
            
            if not symbol or symbol not in self.order_books:
                return
            
            with self.ob_lock:
                book = self.order_books[symbol]
                
                # If not synchronized yet, buffer the event
                if not book.is_synchronized:
                    self.order_book_buffers[symbol].append(event)
                    return
                
                # Validate sequence
                event_U = event.get('U')
                event_u = event.get('u')
                
                # Old event, ignore
                if event_u <= book.last_update_id:
                    return
                
                # Gap detected, resynchronize
                if event_U > book.last_update_id + 1:
                    logger.error(f"Update gap for {symbol}: expected {book.last_update_id + 1}, got {event_U}")
                    book.is_synchronized = False
                    threading.Thread(
                        target=self._initialize_orderbook,
                        args=(symbol, self.symbol_market_types.get(symbol, 'spot')),
                        daemon=True
                    ).start()
                    return
                
                # Apply update
                self._apply_depth_update(symbol, event)
                
        except Exception as e:
            logger.error(f"Error processing depth update: {e}")
    
    def _apply_depth_update(self, symbol, event):
        """Aplica las actualizaciones de precio al order book"""
        book = self.order_books[symbol]
        
        # Update bids
        for price_str, qty_str in event.get('b', []):
            price = float(price_str)
            qty = float(qty_str)
            book.update_bid(price, qty)
        
        # Update asks
        for price_str, qty_str in event.get('a', []):
            price = float(price_str)
            qty = float(qty_str)
            book.update_ask(price, qty)
        
        # Update metadata
        book.last_update_id = event.get('u')
        book.last_update_time = datetime.now()
    
    def _process_book_ticker(self, event):
        """Procesa evento bookTicker (best bid/ask only)"""
        try:
            symbol_formatted = event.get('s', '')
            symbol = self._reverse_format_symbol(symbol_formatted)
            
            if not symbol or symbol not in self.order_books:
                return
            
            # Book ticker provides best bid/ask directly
            # This is a simplified version, not full order book
            logger.debug(f"Book ticker update for {symbol}: {event}")
            
        except Exception as e:
            logger.error(f"Error processing book ticker: {e}")
    
    # ========================================================================
    # QUERY METHODS (For Trading Strategies)
    # ========================================================================
    
    def get_best_bid(self, symbol):
        """
        Obtiene el mejor precio de compra (bid) y cantidad.
        
        Returns:
            tuple: (price, quantity) o (None, None) si no disponible
        """
        with self.ob_lock:
            if symbol not in self.order_books:
                return None, None
            if not self.order_books[symbol].is_synchronized:
                return None, None
            return self.order_books[symbol].get_best_bid()
    
    def get_best_ask(self, symbol):
        """
        Obtiene el mejor precio de venta (ask) y cantidad.
        
        Returns:
            tuple: (price, quantity) o (None, None) si no disponible
        """
        with self.ob_lock:
            if symbol not in self.order_books:
                return None, None
            if not self.order_books[symbol].is_synchronized:
                return None, None
            return self.order_books[symbol].get_best_ask()
    
    def get_mid_price(self, symbol):
        """
        Calcula el precio medio: (best_bid + best_ask) / 2
        
        Returns:
            float: Precio medio o None si no disponible
        """
        bid, _ = self.get_best_bid(symbol)
        ask, _ = self.get_best_ask(symbol)
        
        if bid is not None and ask is not None:
            return (bid + ask) / 2.0
        return None
    
    def get_spread(self, symbol):
        """
        Calcula el spread: best_ask - best_bid
        
        Returns:
            dict: Información del spread o None
        """
        bid, _ = self.get_best_bid(symbol)
        ask, _ = self.get_best_ask(symbol)
        
        if bid is None or ask is None:
            return None
            
        spread_abs = ask - bid
        mid = (bid + ask) / 2.0
        spread_pct = (spread_abs / mid) * 100 if mid > 0 else 0
        
        return {
            'absolute': spread_abs,
            'percentage': spread_pct,
            'bid': bid,
            'ask': ask,
            'mid': mid
        }
    
    def get_depth(self, symbol, levels=10):
        """
        Obtiene los primeros N niveles del order book.
        
        Returns:
            dict: Order book snapshot o None
        """
        with self.ob_lock:
            if symbol not in self.order_books:
                return None
            return self.order_books[symbol].get_depth_snapshot(levels)
    
    def get_vwap_price(self, symbol, target_volume, side):
        """
        Calcula el precio promedio ponderado por volumen (VWAP).
        
        Args:
            symbol (str): Símbolo de trading
            target_volume (float): Tamaño de orden
            side (str): 'buy' o 'sell'
            
        Returns:
            dict: Información del VWAP o None
        """
        with self.ob_lock:
            if symbol not in self.order_books:
                return None
                
            book = self.order_books[symbol]
            if not book.is_synchronized:
                return None
            
            # Determine which side to use
            if side.lower() == 'buy':
                levels = book.get_asks(levels=100)
                best_price = book.get_best_ask()[0]
            elif side.lower() == 'sell':
                levels = book.get_bids(levels=100)
                best_price = book.get_best_bid()[0]
            else:
                logger.error(f"Invalid side: {side}")
                return None
                
            if not levels or best_price is None:
                return None
            
            # Calculate VWAP
            remaining_volume = target_volume
            total_cost = 0.0
            levels_used = 0
            worst_price = best_price
            
            for price, qty in levels:
                if remaining_volume <= 0:
                    break
                    
                consumed = min(remaining_volume, qty)
                total_cost += consumed * price
                remaining_volume -= consumed
                levels_used += 1
                worst_price = price
                
            achievable = remaining_volume <= 0
            
            if total_cost == 0:
                return None
                
            vwap = total_cost / (target_volume - remaining_volume)
            slippage_pct = abs((vwap - best_price) / best_price) * 100
            
            return {
                'vwap': vwap,
                'total_cost': total_cost,
                'levels_used': levels_used,
                'worst_price': worst_price,
                'slippage_pct': slippage_pct,
                'achievable': achievable,
                'remaining_volume': remaining_volume
            }
    
    def get_total_volume(self, symbol, side, levels=5):
        """
        Obtiene el volumen total disponible en los primeros N niveles.
        
        Returns:
            float: Volumen total o None
        """
        with self.ob_lock:
            if symbol not in self.order_books:
                return None
                
            book = self.order_books[symbol]
            if not book.is_synchronized:
                return None
                
            if side.lower() == 'bid':
                price_levels = book.get_bids(levels)
            elif side.lower() == 'ask':
                price_levels = book.get_asks(levels)
            else:
                return None
                
            return sum(qty for _, qty in price_levels)
    
    def get_orderbook_status(self, symbol):
        """
        Obtiene el estado actual del order book.
        
        Returns:
            dict: Estado del order book o None
        """
        with self.ob_lock:
            if symbol not in self.order_books:
                return None
                
            book = self.order_books[symbol]
            return {
                'is_synchronized': book.is_synchronized,
                'last_update_id': book.last_update_id,
                'bid_levels': len(book.bids),
                'ask_levels': len(book.asks),
                'last_update_time': book.last_update_time
            }

#%% CoinAPI
class coinApi(AdminDatos):
    "Gestor de conexiones y datastream desde CoinAPI"
    def __init__(self, eventos,lista_nemos,lista_bolsas,lista_libros,interval):
        self.eventos=eventos
        self.lista_nemos=lista_nemos
        self.baseAsset='USDT'
        self.lista_bolsas=self.get_all_exchanges_metadata() if lista_bolsas==[] else lista_bolsas
        self.lista_libros=lista_libros
        self.datos_nemo={}
        self.ultimo_dato_nemo={}
        self.continuar_backtest=True
        self.indice_vela=0
        self.baseUrl="https://rest.coinapi.io"
        self.apiKey=self.load_coinAPI_key()
        self._symbols_cache = None  # Initialize cache for symbols
        freqDict = {
            '1m': '1MIN',
            '5m': '5MIN',
            '15m': '15MIN',
            '1HRS': '1HRS',
            '1DAY': '1DAY'
        }
        self.interval = freqDict.get(interval, '1MIN')
        self.total_candles = {}  # ✅ ADD: Store candle counts


    def load_coinAPI_key(self):
        """Load API key from environment variable. Default: COINAPI_KEY"""
        import os
        api_key = os.environ.get("COINAPI_KEY")
        if api_key is None:
            raise ValueError("API key not found. Please set the COINAPI_KEY environment variable.")
        return api_key
    
    @staticmethod
    def load_coinAPI_key():
        """Load API key from environment variable. Default: COINAPI_KEY"""
        import os
        api_key = os.environ.get("COINAPI_KEY")
        if api_key is None:
            raise ValueError("API key not found. Please set the COINAPI_KEY environment variable.")
        return api_key
     
    @staticmethod
    def _format_time_for_coinapi(time_input):
        """
        Formatea tiempo para CoinAPI en ISO 8601 sin microsegundos.
        
        CoinAPI espera formato: '2025-12-15T17:00:00Z' o '2025-12-15T17:00:00'
        
        Args:
            time_input: puede ser datetime.datetime o string
            
        Returns:
            str: Tiempo formateado como '2025-12-15T17:00:00Z'
        """
        import datetime as dt
        
        # Si ya es string, retornar tal cual (asumimos que está bien formateado)
        if isinstance(time_input, str):
            return time_input
        
        # Si es datetime, formatear apropiadamente
        if isinstance(time_input, dt.datetime):
            # Remover microsegundos
            time_clean = time_input.replace(microsecond=0)
            # Convertir a ISO 8601 y reemplazar +00:00 con Z
            time_str = time_clean.isoformat()
            if time_str.endswith('+00:00'):
                time_str = time_str.replace('+00:00', 'Z')
            elif not time_str.endswith('Z'):
                # Si no tiene timezone info, agregar Z para UTC
                time_str += 'Z'
            return time_str
        
        raise ValueError(f"time_input debe ser datetime o str, recibido: {type(time_input)}")    

    def get_all_exchanges_metadata(self) -> dict:
        """ Get metadata for all exchanges from CoinAPI """
        import requests
        import json
        
        url = f"{self.baseUrl}/v1/exchanges"
        payload = {}
        headers = {'X-CoinAPI-Key': self.apiKey}
        
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            print(f"Error fetching exchanges metadata: {response.status_code}")
            return {}
            
        data = json.loads(response.text)
        exchanges_metadata = {item['exchange_id']: item for item in data}
        
        return exchanges_metadata
    
    def get_all_symbols_cached(self) -> dict:
        """
        Get all symbols from CoinAPI and cache them to avoid repeated API calls.
        
        This method fetches all trading symbols once and stores them in memory.
        Subsequent calls return the cached data, reducing API usage significantly.
        
        Returns:
            dict: Dictionary of all symbols keyed by symbol_id
        """
        # Return cached data if available
        if self._symbols_cache is not None:
            print("📦 Using cached symbols data")
            return self._symbols_cache
        
        import requests
        import json
        
        print("🔄 Fetching all symbols from CoinAPI (first time only)...")
        url = f"{self.baseUrl}/v1/symbols"
        headers = {'X-CoinAPI-Key': self.apiKey}
        
        try:
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code != 200:
                print(f"❌ Error fetching symbols: {response.status_code}")
                try:
                    error_detail = response.json()
                    print(f"Error detail: {error_detail}")
                except:
                    print(f"Response text: {response.text[:200]}")
                return {}
            
            data = json.loads(response.text)
            
            # Cache the symbols
            self._symbols_cache = {item['symbol_id']: item for item in data}
            
            print(f"✅ Cached {len(self._symbols_cache)} symbols from CoinAPI")
            return self._symbols_cache
            
        except requests.exceptions.Timeout:
            print("⏱️ Timeout fetching symbols from CoinAPI")
            return {}
        except Exception as e:
            print(f"❌ Unexpected error fetching symbols: {e}")
            return {}
    
    def get_exchange_assets(self, exchange:str = 'BINANCEFTS') -> dict:
        """
        Get active trading symbols for a specific exchange from cached CoinAPI data.
        
        This method filters the cached symbols by exchange to avoid making
        repeated API calls for each exchange query.
        
        Args:
            exchange (str): Exchange ID (e.g., 'BINANCEFTS', 'KRAKENFTS')
            
        Returns:
            dict: Dictionary of symbols for the specified exchange keyed by symbol_id
        """
        # Get all symbols (from cache if available)
        all_symbols = self.get_all_symbols_cached()
        
        if not all_symbols:
            return {}
        
        # Filter symbols that belong to the specified exchange
        exchange_symbols = {
            symbol_id: data 
            for symbol_id, data in all_symbols.items() 
            if symbol_id.startswith(f"{exchange}_")
        }
        
        print(f"📊 Found {len(exchange_symbols)} symbols for {exchange}")
        return exchange_symbols
    
    def get_active_assets(self, exchange:str = 'BINANCEFTS') -> dict:
        """ 
        DEPRECATED: Use get_exchange_assets() instead.
        Get active assets for a specific exchange from CoinAPI.
        This method is kept for backwards compatibility.
        """
        return self.get_exchange_assets(exchange)

    def get_symbol_id(self,nemo:str,exchange:str = 'BINANCEFTS', book:str = 'PERP') -> str:
        """ Get symbol ID for a specific exchange and book type from CoinAPI """
        import requests
        import json

        symbol_id = f"{exchange}_{book}_{nemo}_{self.baseAsset}"
        # Validates if the assembled symbol exists in the exchange's metadata
        active_assets = self.get_active_assets(exchange)
        if symbol_id not in active_assets:
            print(f"Error: Symbol {symbol_id} not found in active assets for {exchange}")
            return ""
        else:
            return symbol_id

    def get_top_of_book(self,nemo:str, exchange:str = 'BINANCEFTS', book:str = 'PERP') -> tuple:
        """ Get top of book (best bid and ask price ) for a specific exchange """
        import requests
        import json
        import pandas as pd
        symbol_id = self.get_symbol_id(nemo, exchange, book)
        url = f"https://rest.coinapi.io/v1/orderbooks/{symbol_id}/current"
        payload = {}
        headers = {'X-CoinAPI-Key': self.apiKey}
        
        try:
            response = requests.get(url, headers=headers, timeout=15)
            
            if response.status_code == 550:
                print(f"⚠️ CoinAPI: {exchange} order book temporarily unavailable")
                # Return last known data or None
                cached_data = self._get_cached_orderbook(exchange)
                if cached_data is not None and not cached_data.empty:
                    return (cached_data['bid_price'].max(), cached_data['ask_price'].min())
                return (None, None)
            
            if response.status_code != 200:
                # print(f"Error fetching order book for {symbol_id}: {response.status_code}")
                return (None, None)
                
            data = json.loads(response.text)
            bids = pd.json_normalize(data, record_path=[['bids']])
            bids.columns = ['bid_price', 'bid_size']

            asks = pd.json_normalize(data, record_path=[['asks']])
            asks.columns = ['ask_price', 'ask_size']
            
            df_orderbook = pd.concat([bids, asks], axis=1)
            
            # Cache for fallback
            self._cache_orderbook(exchange, df_orderbook)
            
            # Return best bid and best ask as tuple
            return (df_orderbook['bid_price'].max(), df_orderbook['ask_price'].min())
            
        except requests.exceptions.Timeout:
            print(f"⏱️ Timeout fetching {exchange} order book")
            cached_data = self._get_cached_orderbook(exchange)
            if cached_data is not None and not cached_data.empty:
                return (cached_data['bid_price'].max(), cached_data['ask_price'].min())
            return (None, None)
        except Exception as e:
            print(f"❌ Unexpected error fetching {exchange} order book: {e}")
            return (None, None)

    def _cache_orderbook(self, exchange, df):
        """Cache order book data for fallback"""
        if not hasattr(self, '_orderbook_cache'):
            self._orderbook_cache = {}
        self._orderbook_cache[exchange] = {
            'data': df,
            'timestamp': dt.datetime.now()
        }
    
    def _get_cached_orderbook(self, exchange):
        """Get cached order book if available and recent"""
        if not hasattr(self, '_orderbook_cache'):
            return None
        
        cached = self._orderbook_cache.get(exchange)
        if cached:
            age = (dt.datetime.now() - cached['timestamp']).seconds
            if age < 30:  # Use cache if less than 30 seconds old
                print(f"📦 Using cached {exchange} order book ({age}s old)")
                return cached['data']
        return None

    def get_historic_price(self, symbol_id: str, time_start :str, time_end:str , period_id: str = None ,limit: int = None) -> pd.DataFrame:
            """Obtiene datos de velas OHLCV desde CoinAPI
            
            Args:
                symbol_id (str): El ID del símbolo en CoinAPI (e.g., 'BINANCEFTS_PERP_BTC_USDT')
                period_id (str): El período de la vela (e.g., '1MIN', '5MIN', '1HRS', '1DAY')
                time_start: Fecha y hora de inicio (datetime object o string ISO 8601)
                time_end: Fecha y hora de fin (datetime object o string ISO 8601)
                limit (int, optional): Máximo número de velas a retornar (default: None)
                
            Returns:
                pd.DataFrame: DataFrame con las velas OHLCV indexado por time_period_start
                
            Note:
                - Los datetime objects se convierten automáticamente al formato requerido por CoinAPI
                - CoinAPI espera formato ISO 8601 sin microsegundos: '2025-12-15T17:00:00Z'
                - El DataFrame retornado tiene time_period_start como índice
            """
            
            # Formatear tiempos para CoinAPI (sin microsegundos, con Z para UTC)
            time_start_str = self._format_time_for_coinapi(time_start)
            time_end_str = self._format_time_for_coinapi(time_end)
            if  period_id is None :
                period_id=self.interval
            
            url = f"{self.baseUrl}/v1/ohlcv/{symbol_id}/history"
            params = {
                'period_id': period_id,
                'time_start': time_start_str,
                'time_end': time_end_str
            }
            
            # Agregar limit si se especificó
            if limit is not None:
                params['limit'] = limit
            
            headers = {'X-CoinAPI-Key': self.apiKey}    
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code != 200:
                print(f"Error fetching OHLCV data: {response.status_code}")
                try:
                    error_detail = response.json()
                    print(f"Error detail: {error_detail}")
                except:
                    print(f"Response text: {response.text[:200]}")
                return pd.DataFrame()
            
            data = json.loads(response.text)
            
            # Convert to DataFrame
            df = pd.DataFrame(data)
            
            # Convert time columns to datetime and set index
            if not df.empty:
                df['time_period_start'] = pd.to_datetime(df['time_period_start'])
                df['time_period_end'] = pd.to_datetime(df['time_period_end'])
                if 'time_open' in df.columns:
                    df['time_open'] = pd.to_datetime(df['time_open'])
                if 'time_close' in df.columns:
                    df['time_close'] = pd.to_datetime(df['time_close'])
                
                    # Set time_period_start as index (inplace=True to modify the DataFrame)
                    df.set_index('time_period_start', inplace=True)
                return df
            else:
                raise ValueError("No data returned from CoinAPI for the given parameters.")
    
    def load_candles(self, inicio, fin, limit:int=100000):
        """Carga datos históricos para todos los nemos y almacena en un diccionario 
        que contiene un iterador por cada nemotécnico"""
        for nemo in self.lista_nemos:
            # ✅ OPTION C FIX: Pass exchange and book parameters explicitly
            self.datos_nemo[nemo] = self.get_historic_price(
                symbol_id=self.get_symbol_id(nemo, self.lista_bolsas[0], self.lista_libros[0]),
                period_id=self.interval,
                time_start=inicio,
                time_end=fin
            )
                        # ✅ STORE THE LENGTH BEFORE CONVERTING TO ITERATOR
            self.total_candles[nemo] = len(self.datos_nemo[nemo])
            self.ultimo_dato_nemo[nemo] = []
            self.datos_nemo[nemo] = self.datos_nemo[nemo].iterrows()

    def get_nueva_vela(self,nemo):
                #entrega la última vela desde la fuente de datos
                for v in self.datos_nemo[nemo]:
                    yield v
                    
    def get_ultima_vela(self,nemo):
        #Entrega la última vela de la lista de nemos
        try:
            lista_velas=self.ultimo_dato_nemo[nemo]
        except KeyError: #Si no encuentra datos en la base...
            print('El nemotécnico solicitado no existe en la base')
            raise
        else:
            return lista_velas[-1]
    
    def get_ultimas_velas(self,nemo,N=1):
        #Devuelve las últimas N velas para el nemotécnico o N-k si no hay 
        #mas disponibles
        try:
            lista_velas=self.ultimo_dato_nemo[nemo]
        except KeyError:#Si no encuentra datos en la base...
            print('El nemotécnico no está disponible en la base de datos')
            raise
        else:
            return lista_velas[-N:]
    
    def get_tiempo_ultima_vela(self,nemo):
        #Devuelve un Datetime para la última vela
        try:
            lista_velas=self.ultimo_dato_nemo[nemo]
        except KeyError:#Si no encuentra datos en la base...
            print('El Nemotécnico no está disponible en la base de datos')
            raise
        else:
            return lista_velas[-1][0]
    
    def get_valor_ultima_vela(self,nemo,tipoval):
        #Devuelve el valor solicitado para la última vela.
        #Open, High, Low, Close, Volumen, Open interest
        try:
            lista_velas=self.ultimo_dato_nemo[nemo]
        except KeyError:
            print('El Nemotécnico no está disponible en la base de datos')
            raise
        else:
            return getattr(lista_velas[-1][-1],tipoval)
            
    def get_valor_ultimas_velas(self,nemo,tipoval,N=1):
        #Devuelve las N últimas velas o N-k si hay menos que N
        try:
            lista_velas=self.get_ultimas_velas(nemo,N)
            
        except KeyError:
            print('El nemotécnico no está disponible en la base')
            raise
        else:
            return np.array([getattr(v[1],tipoval) for v in lista_velas])
            
    def get_actualizar_velas(self):
        #Empuja los datos de la última vela en la estructura denominada
        #ultimo_dato_nemo para todos los nemos en la lista de nemos denominada
        #lista_nemos
        for s in self.lista_nemos:
            try:
                vela=next(self.get_nueva_vela(s))
            except StopIteration:
                self.continuar_backtest=False
            else:
                if vela is not None:
                    self.ultimo_dato_nemo[s].append(vela)
                    
        self.eventos.put(EventoMdo())
    
    def actualizar_velas(self):
        #Empuja los datos de la última vela en la estructura denominada
        #ultimo_dato_nemo para todos los nemos en la lista de nemos denominada
        #lista_nemos
        for s in self.lista_nemos:
            try:
                vela=next(self.get_nueva_vela(s))
            except StopIteration:
                self.continuar_backtest=False
            else:
                if vela is not None:
                    self.ultimo_dato_nemo[s].append(vela)
                    
        self.eventos.put(EventoMdo(nemo=s,msg_type='ohlcv',timestamp=self.get_tiempo_ultima_vela(s)))

    def get_vwap(self, exchange,q,side) -> float:
        """ Get VWAP for a specific exchange """
        ob=self.get_orderbook(exchange, depth=10)
        vwap=bk.vwap(ob, q, side)
        return vwap

    def get_orderbook(self,exchange, depth: int = 10) -> pd.DataFrame:
        import requests
        import json
        import pandas as pd
        
        symbol_id = f"{exchange}_SPOT_{self.lista_nemos[0]}_{self.lista_nemos[1]}"
        
        # Try direct Bitso API first if it's BITSO (more reliable)
        if exchange == 'BITSO':
            try:
                bitso_data = self._get_bitso_direct_orderbook()
                if bitso_data is not None and not bitso_data.empty:
                    return bitso_data.head(depth)
            except Exception as e:
                print(f"⚠️ Bitso direct API failed: {e}, trying CoinAPI...")
        
        # Try CoinAPI
        url = f"{self.baseUrl}/v1/orderbooks/{symbol_id}/current"
        headers = {'X-CoinAPI-Key': self.apiKey}
        
        try:
            response = requests.get(url, headers=headers, timeout=15)
            
            # Handle 550 error specifically (temporarily unavailable)
            if response.status_code == 550:
                print(f"⚠️ CoinAPI: {exchange} order book temporarily unavailable (550)")
                if exchange == 'BITSO':
                    print("Retrying with direct Bitso API...")
                    bitso_data = self._get_bitso_direct_orderbook()
                    if bitso_data is not None and not bitso_data.empty:
                        return bitso_data.head(depth)
                raise ValueError(f"Order book for {symbol_id} temporarily unavailable")
            
            # Handle other non-200 status codes
            if response.status_code != 200:
                raise ValueError(f"Error fetching order book for {symbol_id}: {response.status_code} - {response.text}")
            
            # Parse successful response
            data = json.loads(response.text)
            bids = pd.json_normalize(data, record_path=[['bids']])
            bids.columns = ['bid_price', 'bid_size']
            asks = pd.json_normalize(data, record_path=[['asks']])
            asks.columns = ['ask_price', 'ask_size']
            df_orderbook = pd.concat([bids, asks], axis=1)
            
            return df_orderbook.head(depth)
            
        except requests.exceptions.RequestException as e:
            # Handle network/connection errors
            raise ValueError(f"Network error fetching order book for {exchange}: {e}")
        except ValueError as ve:
            # Handle API errors (including 550)
            print(f"⚠️ CoinAPI error for {exchange}: {ve}")
            if exchange == 'BITSO':
                print("Trying direct Bitso API as final fallback...")
                df_orderbook = self._get_bitso_direct_orderbook()
                if df_orderbook is None or df_orderbook.empty:
                    raise ValueError("Direct Bitso API also failed to provide order book data.")
                return df_orderbook.head(depth)
            else:
                raise ve
        except Exception as e:
            # Handle unexpected errors
            raise ValueError(f"Unexpected error fetching order book for {exchange}: {e}")
    
    def get_orderbooks(self, depth: int = 2) -> dict:
        """ Get order books for all exchanges in self.lista_bolsas """
        lob_dict = {}
        for exchange in self.lista_bolsas:
            lob_dict[exchange] = self.get_orderbook(exchange, depth)
        return lob_dict
    
    def get_combined_lob(self, depth: int = 2) -> pd.DataFrame:
        """ Get combined limit order book from all exchanges in self.lista_bolsas """
        print("Combining order books...")
        # Fetch order books for all exchanges
        lob_dict = self.get_orderbooks(depth)              
        # Combine bids and asks from all exchanges ordering from max to min in bids and from min to max in asks and get top 'depth' levels
        combined_bids = pd.concat([lob_dict[exch][['bid_price', 'bid_size']] for exch in lob_dict.keys()]).sort_values(by='bid_price', ascending=False).head(depth)
        combined_asks = pd.concat([lob_dict[exch][['ask_price', 'ask_size']] for exch in lob_dict.keys()]).sort_values(by='ask_price', ascending=True).head(depth)
        combined_lob = pd.concat([combined_bids.reset_index(drop=True), combined_asks.reset_index(drop=True)], axis=1)
        print("Order books combined.")
        return combined_lob.sort_index()
        return combined_lob
        # Funcion que calcula el mid price a partir de libros de ordenes combinados
    
    def mid_price(self) -> float:
        """ Calculate mid price from combined order book of a list of exchanges in self.lista_bolsas """
        print("Calculating mid price...")
        lob_combined = self.get_combined_lob()
        best_bid = lob_combined['bid_price'].max()
        best_ask = lob_combined['ask_price'].min()
        mid = (best_bid + best_ask) / 2
        print(f"Mid price calculated: {mid}")
        return  mid

#%% CoinAPI Data Stream for live trading

class CoinApiDs(AdminDatos):
    """
    Real-time market data streaming from CoinAPI WebSocket
    Provides OHLCV, trades, quotes, and order book data via WebSocket
    
    Based on CoinAPI WebSocket API documentation:
    https://docs.coinapi.io/market-data/websocket/
    
    Architecture inspired by BinanceData implementation with thread-safe
    data management and automatic reconnection handling.
    """
    
    def __init__(self, eventos, lista_bolsas, lista_libros, lista_nemos, interval='1MIN'):
        """
        Initialize CoinAPI WebSocket data provider
        
        Parameters:
        -----------
        eventos : queue.Queue
            Event queue for publishing EventoMdo objects
        lista_bolsas : list
            List of exchange IDs (e.g., ['BINANCEFTS', 'KRAKENFTS'])
        lista_libros : list
            List of book types (e.g., ['PERP', 'SPOT'])
        lista_nemos : list
            List of base asset symbols (e.g., ['BTC', 'ETH'])
        interval : str
            Time interval for OHLCV data (e.g., '1MIN', '5MIN', '1HRS')
            Supported: 1SEC, 1MIN, 5MIN, 15MIN, 30MIN, 1HRS, 4HRS, 1DAY
            Also accepts Binance format (e.g., '1m', '1h') which will be auto-converted
        """
        # Basic attributes
        self.eventos = eventos
        self.lista_bolsas = lista_bolsas
        self.lista_libros = lista_libros
        self.lista_nemos = lista_nemos
        self.base_asset = 'USDT'
        
        # Convert Binance interval format to CoinAPI format
        interval_mapping = {
            '1s': '1SEC',
            '1m': '1MIN',
            '3m': '3MIN',
            '5m': '5MIN',
            '15m': '15MIN',
            '30m': '30MIN',
            '1h': '1HRS',
            '2h': '2HRS',
            '4h': '4HRS',
            '1d': '1DAY',
            '1w': '1WEEK'
        }
        
        # If interval is in Binance format, convert it
        if interval.lower() in interval_mapping:
            converted_interval = interval_mapping[interval.lower()]
            logger.info(f"Auto-converted interval '{interval}' to CoinAPI format '{converted_interval}'")
            self.interval = converted_interval
        else:
            self.interval = interval  # Already in CoinAPI format
        
        self.period_id = self.interval  # Add period_id for compatibility with PortAQMHFT
        
        # Data structures
        self.datos_nemo = {}  # {symbol: DataFrame} - Historical buffer
        self.ultimo_dato_nemo = {}  # {symbol: dict} - Latest candle
        self.continuar_backtest = True
        self.indice_vela = 0
        
        # CoinAPI configuration
        self.baseUrl = "https://rest.coinapi.io"
        self.apiKey = self.load_coinAPI_key()
        self.ws_url = "wss://ws.coinapi.io/v1/"
        
        # WebSocket management
        self.ws = None
        self.ws_thread = None
        self.data_lock = threading.Lock()
        self.is_running = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.last_message_time = {}
        
        # Symbol management
        self._symbols_cache = None
        self.symbol_ids = self._build_symbol_ids()
        
        # Initialize data structures for each symbol
        for symbol_id in self.symbol_ids:
            nemo = self._extract_nemo_from_symbol_id(symbol_id)
            self.ultimo_dato_nemo[nemo] = {}
            self.datos_nemo[nemo] = pd.DataFrame()
            self.last_message_time[nemo] = time.time()
        
        # Connect WebSocket
        logger.info("Initializing CoinAPI WebSocket connection...")
        self.connect_websocket()
    
    @staticmethod
    def load_coinAPI_key():
        """Load API key from environment variable"""
        import os
        api_key = os.environ.get("COINAPI_KEY")
        if api_key is None:
            raise ValueError("API key not found. Please set the COINAPI_KEY environment variable.")
        return api_key
    
    def _build_symbol_ids(self):
        """
        Build list of CoinAPI symbol IDs from exchange, book type, and nemo lists
        
        Returns:
        --------
        list : Symbol IDs in format 'EXCHANGE_BOOKTYPE_SYMBOL_BASEQUOTE'
               e.g., ['BINANCEFTS_PERP_BTC_USDT', 'BINANCEFTS_PERP_ETH_USDT']
        """
        symbol_ids = []
        for bolsa in self.lista_bolsas:
            for libro in self.lista_libros:
                for nemo in self.lista_nemos:
                    symbol_id = f"{bolsa}_{libro}_{nemo}_{self.base_asset}"
                    symbol_ids.append(symbol_id)
        
        logger.info(f"Built {len(symbol_ids)} symbol IDs: {symbol_ids}")
        return symbol_ids
    
    def _extract_nemo_from_symbol_id(self, symbol_id):
        """
        Extract base asset symbol from CoinAPI symbol ID
        
        Parameters:
        -----------
        symbol_id : str
            Full symbol ID (e.g., 'BINANCEFTS_PERP_BTC_USDT')
        
        Returns:
        --------
        str : Base asset symbol (e.g., 'BTC')
        """
        parts = symbol_id.split('_')
        if len(parts) >= 3:
            return parts[2]  # Third part is the base asset
        return symbol_id
    
    def connect_websocket(self):
        """
        Establish WebSocket connection to CoinAPI
        
        Connection flow:
        1. Connect to wss://ws.coinapi.io/v1/
        2. Send Hello message with API key
        3. Subscribe to OHLCV streams for all symbols
        """
        self.is_running = True
        
        logger.info(f"Connecting to CoinAPI WebSocket: {self.ws_url}")
        
        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open,
            on_ping=self.on_ping,
            on_pong=self.on_pong
        )
        
        # Execute WebSocket in separate thread
        self.ws_thread = threading.Thread(target=self.ws.run_forever)
        self.ws_thread.daemon = True
        self.ws_thread.start()
        
        time.sleep(2)  # Allow time for connection to establish
        logger.info("CoinAPI WebSocket connection established")
    
    def disconnect_websocket(self):
        """Close WebSocket connection safely"""
        self.is_running = False
        if self.ws:
            self.ws.close()
        if self.ws_thread and self.ws_thread.is_alive():
            self.ws_thread.join(timeout=5)
        logger.info("CoinAPI WebSocket disconnected safely")
    
    def reconnect_websocket(self):
        """Reconnect with exponential backoff"""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error(f"Max reconnection attempts ({self.max_reconnect_attempts}) reached")
            return
        
        self.reconnect_attempts += 1
        backoff_time = min(300, (2 ** self.reconnect_attempts))
        logger.info(f"Reconnecting in {backoff_time} seconds (attempt {self.reconnect_attempts}/{self.max_reconnect_attempts})")
        time.sleep(backoff_time)
        
        self.disconnect_websocket()
        time.sleep(2)
        self.connect_websocket()
    
    def on_open(self, ws):
        """
        Handle WebSocket connection opening
        Send Hello message and subscribe to streams
        
        CRITICAL: Must include subscribe_filter_period_id to get the correct
        OHLCV interval. Without it, CoinAPI defaults to 1DAY candles!
        """
        logger.info("CoinAPI WebSocket connection opened")
        self.reconnect_attempts = 0
        
        # Send Hello message with API key
        # CRITICAL: subscribe_filter_period_id MUST be included for OHLCV data
        # Without it, CoinAPI defaults to daily candles (1DAY)
        hello_msg = {
            "type": "hello",
            "apikey": self.apiKey,
            "heartbeat": True,
            "subscribe_data_type": ["ohlcv"],  # Subscribe to OHLCV data
            "subscribe_filter_symbol_id": self.symbol_ids,
            "subscribe_filter_period_id": [self.interval]  # CRITICAL: Specify the candle period!
        }
        
        logger.info(f"Sending Hello message with {len(self.symbol_ids)} symbols, period={self.interval}")
        self.ws.send(json.dumps(hello_msg))
        logger.info(f"Hello message sent (period_id={self.interval}), waiting for data...")
    
    def on_message(self, ws, message):
        """
        Handle incoming WebSocket messages
        
        Message types:
        - hello: Connection confirmation
        - heartbeat: Keep-alive ping
        - ohlcv: OHLCV candlestick data
        - trade: Individual trade data
        - quote: Best bid/ask quotes
        - book: Order book snapshots/updates
        """
        try:
            data = json.loads(message)
            msg_type = data.get('type', '')
            
            if msg_type == 'hello':
                logger.info("Received Hello confirmation from CoinAPI")
                return
            
            elif msg_type == 'heartbeat' or msg_type == 'hearbeat':
                logger.debug("Received heartbeat from CoinAPI")
                return
            
            elif msg_type == 'ohlcv':
                self._process_ohlcv_message(data)
            
            elif msg_type == 'trade':
                self._process_trade_message(data)
            
            elif msg_type == 'quote':
                self._process_quote_message(data)
            
            elif msg_type == 'book':
                self._process_book_message(data)
            
            else:
                logger.warning(f"Unknown message type: {msg_type}")
                
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON message: {e}")
        except Exception as e:
            logger.error(f"Error processing message: {e}")
    
    def _process_ohlcv_message(self, data):
        """
        Process OHLCV (candlestick) message
        
        Expected format:
        {
            "type": "ohlcv",
            "symbol_id": "BINANCEFTS_PERP_BTC_USDT",
            "time_period_start": "2025-01-06T12:00:00.0000000Z",
            "time_period_end": "2025-01-06T12:01:00.0000000Z",
            "time_open": "2025-01-06T12:00:00.0000000Z",
            "time_close": "2025-01-06T12:00:59.9999999Z",
            "price_open": 50000.0,
            "price_high": 51000.0,
            "price_low": 49000.0,
            "price_close": 50500.0,
            "volume_traded": 1000.0,
            "trades_count": 150
        }
        """
        try:
            symbol_id = data.get('symbol_id', '')
            nemo = self._extract_nemo_from_symbol_id(symbol_id)
            
            # Parse timestamps
            from dateutil.parser import isoparse
            time_start = isoparse(data['time_period_start'])
            time_end = isoparse(data['time_period_end'])
            
            # Build candle dictionary
            vela = {
                'open_time': time_start,
                'close_time': time_end,
                'open': float(data.get('price_open', 0)),
                'high': float(data.get('price_high', 0)),
                'low': float(data.get('price_low', 0)),
                'close': float(data.get('price_close', 0)),
                'volume': float(data.get('volume_traded', 0)),
                'trades': int(data.get('trades_count', 0)),
                'interval': self.interval
            }
            
            # Thread-safe update
            with self.data_lock:
                last_candle = self.ultimo_dato_nemo.get(nemo)
                # Only update if this candle is newer or if there is no previous candle
                if (
                    last_candle is None
                    or vela['close_time'] > last_candle.get('close_time', vela['close_time'])
                ):
                    self.ultimo_dato_nemo[nemo] = vela

                    # Append to historical buffer
                    if nemo not in self.datos_nemo:
                        self.datos_nemo[nemo] = pd.DataFrame()

                    # Convert vela to DataFrame row and append
                    new_row = pd.DataFrame([vela])
                    self.datos_nemo[nemo] = pd.concat([self.datos_nemo[nemo], new_row], ignore_index=True)

                    # Limit buffer size to prevent memory issues
                    max_buffer_size = 1000
                    if len(self.datos_nemo[nemo]) > max_buffer_size:
                        self.datos_nemo[nemo] = self.datos_nemo[nemo].iloc[-max_buffer_size:]
                        self.datos_nemo[nemo].reset_index(drop=True, inplace=True)
            
            # Create and push EventoMdo to queue
            evento = EventoMdo(
                nemo=nemo,
                msg_type='ohlcv',
                timestamp=time_start,
                **vela
            )
            self.actualizar_velas(evento)
            
            
            self.last_message_time[nemo] = time.time()
            
        except Exception as e:
            logger.error(f"Error processing OHLCV message: {e}")
    
    def _process_trade_message(self, data):
        """
        Process individual trade message
        
        Expected format:
        {
            "type": "trade",
            "symbol_id": "BINANCEFTS_PERP_BTC_USDT",
            "time_exchange": "2025-01-06T12:00:00.0000000Z",
            "time_coinapi": "2025-01-06T12:00:00.0000000Z",
            "uuid": "...",
            "price": 50500.0,
            "size": 0.5,
            "taker_side": "BUY"
        }
        """
        try:
            symbol_id = data.get('symbol_id', '')
            nemo = self._extract_nemo_from_symbol_id(symbol_id)
            
            timestamp = datetime.fromisoformat(data['time_exchange'].replace('Z', '+00:00'))
            
            # Create trade event
            evento = EventoMdo(
                nemo=nemo,
                msg_type='trade',
                timestamp=timestamp,
                price=float(data.get('price', 0)),
                quantity=float(data.get('size', 0)),
                taker_side=data.get('taker_side', '')
            )

            self.actualizar_velas(evento)
            
            self.eventos.put(evento)
            logger.debug(f"💹 Trade: {nemo} @ {data.get('price')} x {data.get('size')}")
            
        except Exception as e:
            logger.error(f"Error processing trade message: {e}")
    
    def _process_quote_message(self, data):
        """
        Process quote (best bid/ask) message
        
        Expected format:
        {
            "type": "quote",
            "symbol_id": "BINANCEFTS_PERP_BTC_USDT",
            "time_exchange": "2025-01-06T12:00:00.0000000Z",
            "time_coinapi": "2025-01-06T12:00:00.0000000Z",
            "ask_price": 50520.0,
            "ask_size": 1.5,
            "bid_price": 50500.0,
            "bid_size": 2.0
        }
        """
        pass
        # Commented to avoid updating candles without changes in actual trading data
        # Kept it for future reference
        # try:
        #     symbol_id = data.get('symbol_id', '')
        #     nemo = self._extract_nemo_from_symbol_id(symbol_id)
            
        #     timestamp = datetime.fromisoformat(data['time_exchange'].replace('Z', '+00:00'))
            
        #     # Create quote event
        #     evento = EventoMdo(
        #         nemo=nemo,
        #         msg_type='quote',
        #         timestamp=timestamp,
        #         best_bid=float(data.get('bid_price', 0)),
        #         best_ask=float(data.get('ask_price', 0)),
        #         bid_size=float(data.get('bid_size', 0)),
        #         ask_size=float(data.get('ask_size', 0))
        #     )
            
        #     self.eventos.put(evento)
        #     logger.debug(f"📈 Quote: {nemo} Bid: {data.get('bid_price')} Ask: {data.get('ask_price')}")
            
        # except Exception as e:
        #     logger.error(f"Error processing quote message: {e}")
    
    def _process_book_message(self, data):
        """
        Process order book message
        
        Expected format:
        {
            "type": "book",
            "symbol_id": "BINANCEFTS_PERP_BTC_USDT",
            "time_exchange": "2025-01-06T12:00:00.0000000Z",
            "time_coinapi": "2025-01-06T12:00:00.0000000Z",
            "asks": [[50520.0, 1.5], [50525.0, 2.0]],
            "bids": [[50500.0, 2.0], [50495.0, 1.8]]
        }
        """
        # We will eventually need to process book messages. Not for this implementation (Pairs trading , only requires
        # price and volume data ...)
        pass

        # try:
        #     symbol_id = data.get('symbol_id', '')
        #     nemo = self._extract_nemo_from_symbol_id(symbol_id)
            
        #     timestamp = datetime.fromisoformat(data['time_exchange'].replace('Z', '+00:00'))
            
        #     # Create book event
        #     evento = EventoMdo(
        #         nemo=nemo,
        #         msg_type='book',
        #         timestamp=timestamp,
        #         asks=data.get('asks', []),
        #         bids=data.get('bids', [])
        #     )
            
        #     self.eventos.put(evento)
        #     logger.debug(f"📚 Order book update: {nemo}")
            
        # except Exception as e:
        #     logger.error(f"Error processing book message: {e}")
    
    def on_error(self, ws, error):
        """Handle WebSocket errors"""
        logger.error(f"CoinAPI WebSocket error: {error}")
        if self.is_running:
            self.reconnect_websocket()
    
    def on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket closure"""
        logger.info(f"CoinAPI WebSocket closed: {close_status_code} - {close_msg}")
        if self.is_running:  # Unexpected closure
            self.reconnect_websocket()
    
    def on_ping(self, ws, message):
        """Handle ping from server"""
        logger.debug("Received ping from CoinAPI")
    
    def on_pong(self, ws, message):
        """Handle pong from server"""
        logger.debug("Received pong from CoinAPI")
    
    # ===== Data Access Methods (AdminDatos interface) =====
    
    def get_ultima_vela(self, nemo):
        """Get the latest completed candle for a symbol"""
        with self.data_lock:
            return self.ultimo_dato_nemo.get(nemo, {})
    
    def get_ultimas_velas(self, nemo, N=1):
        """Get the last N completed candles for a symbol"""
        with self.data_lock:
            if nemo in self.datos_nemo and isinstance(self.datos_nemo[nemo], pd.DataFrame):
                df = self.datos_nemo[nemo]
                return df.iloc[-N:] if len(df) >= N else df
            return pd.DataFrame()
    
    def get_tiempo_ultima_vela(self, nemo):
        """Get timestamp of the latest candle"""
        with self.data_lock:
            vela = self.ultimo_dato_nemo.get(nemo, {})
            return vela.get('open_time', datetime.now())
    
    def get_valor_ultima_vela(self, nemo, tipoval):
        """
        Get specific value from latest candle
        
        Parameters:
        -----------
        nemo : str
            Symbol (e.g., 'BTC')
        tipoval : str
            Value type: 'open', 'high', 'low', 'close', 'volume'
        """
        with self.data_lock:
            vela = self.ultimo_dato_nemo.get(nemo, {})
            key = tipoval.lower()
            return vela.get(key, 0.0)
    
    def get_valor_ultimas_velas(self, nemo, tipoval, N=1):
        """Get specific values from last N candles stored in self.datos_nemo"""
        with self.data_lock:
            if nemo in self.datos_nemo and isinstance(self.datos_nemo[nemo], pd.DataFrame):
                df = self.datos_nemo[nemo]
                if len(df) >= N:
                    print(f"Getting last {N} values of '{tipoval}' for {nemo}")
                    return df[tipoval.lower()].iloc[-N:].values
                else:
                    return df[tipoval.lower()].values if tipoval.lower() in df.columns else np.array([])
            return np.array([])
    
    def actualizar_velas(self,evento):
        """
        Receives EventoMdo and updates close price if the event is a trade (updating high if the  trade is higher 
        and low if event is lower). If the event is OHLCV, updates self.datos_nemo with an additional candle data
        """
        if evento.type == 'TRADE':
          #Check if the timestamp is newer and if at least 1 minute has passed
            last_candle = self.ultimo_dato_nemo.get(evento.nemo)
            if (
                last_candle is None
                or evento.close_time > last_candle.get('close_time', evento.close_time)
            ):
                self.ultimo_dato_nemo[evento.nemo] = {
                    'close_time': evento.close_time,
                    'close': evento.price,
                    'high': max(self.ultimo_dato_nemo[evento.nemo].get('high', 0), evento.price),
                    'low': min(self.ultimo_dato_nemo[evento.nemo].get('low', float('inf')), evento.price),
                'volume': evento.volume
            }
        elif evento.type == 'OHLCV':
            self.datos_nemo.setdefault(evento.nemo, pd.DataFrame()).loc[evento.close_time] = evento.data
            self.eventos.put(evento)
        else:   
            pass

    def get_kline_generator(self, symbol, lookback=91):
        """
        Generator that yields real-time candles with historical context
        
        Parameters:
        -----------
        symbol : str
            Base asset symbol (e.g., 'BTC')
        lookback : int
            Number of historical candles to maintain in buffer
        
        Yields:
        -------
        tuple : (latest_candle, historical_buffer)
            - latest_candle (dict): Most recent candle
            - historical_buffer (DataFrame): Last N candles
        """
        last_time = None
        buffer = pd.DataFrame()
        
        # Initialize buffer with existing data
        with self.data_lock:
            if symbol in self.datos_nemo and not self.datos_nemo[symbol].empty:
                buffer = self.datos_nemo[symbol].iloc[-lookback:].copy()
                logger.info(f"Initialized generator buffer for {symbol} with {len(buffer)} candles")
        
        # Stream real-time updates
        while self.is_running:
            with self.data_lock:
                if symbol in self.ultimo_dato_nemo:
                    latest = self.ultimo_dato_nemo[symbol]
                    
                    if latest and 'close_time' in latest:
                        current_time = latest['close_time']
                        
                        # Only yield when new candle arrives
                        if last_time is None or current_time > last_time:
                            last_time = current_time
                            
                            # Add new candle to buffer
                            new_row = pd.DataFrame([latest])
                            buffer = pd.concat([buffer, new_row], ignore_index=True)
                            
                            # Maintain buffer size
                            if len(buffer) > lookback:
                                buffer = buffer.iloc[1:]
                                buffer.reset_index(drop=True, inplace=True)
                            
                            # Yield latest candle and buffer copy
                            yield latest, buffer.copy()
            
            time.sleep(0.5)  # Prevent excessive CPU usage
    
    # ===== Historical Data Methods (for ATR calculation in PortAQMHFT) =====
    
    def get_symbol_id(self, nemo, bolsa=None, libro=None):
        """
        Build CoinAPI symbol ID from components
        
        Parameters:
        -----------
        nemo : str
            Base asset symbol (e.g., 'BTC', 'LINK')
        bolsa : str, optional
            Exchange ID (e.g., 'BINANCEFTS'). Uses first from lista_bolsas if None
        libro : str, optional
            Book type (e.g., 'PERP'). Uses first from lista_libros if None
        
        Returns:
        --------
        str : Symbol ID (e.g., 'BINANCEFTS_PERP_BTC_USDT')
        """
        bolsa = bolsa or self.lista_bolsas[0]
        libro = libro or self.lista_libros[0]
        return f"{bolsa}_{libro}_{nemo}_{self.base_asset}"
    
    def _format_time_for_coinapi(self, time_input):
        """
        Format time for CoinAPI REST API
        
        Parameters:
        -----------
        time_input : datetime or str
            Time to format
        
        Returns:
        --------
        str : ISO 8601 format without microseconds (e.g., '2025-01-06T12:00:00Z')
        """
        if isinstance(time_input, datetime):
            # Remove microseconds and add Z for UTC
            return time_input.replace(microsecond=0).isoformat() + 'Z'
        elif isinstance(time_input, str):
            # Assume already formatted or parse if needed
            return time_input
        else:
            raise ValueError(f"Unsupported time format: {type(time_input)}")
    
    def get_historic_price(self, symbol_id: str, period_id: str, 
                    time_start, time_end, limit: int = None) -> pd.DataFrame:
        """
        Get historical OHLCV data from CoinAPI REST API
        
        Parameters:
        -----------
        symbol_id : str
            CoinAPI symbol ID (e.g., 'BINANCEFTS_PERP_BTC_USDT')
        period_id : str
            Time period (e.g., '1MIN', '5MIN', '1HRS', '1DAY')
        time_start : datetime or str
            Start time (datetime object or ISO 8601 string)
        time_end : datetime or str
            End time (datetime object or ISO 8601 string)
        limit : int, optional
            Maximum number of candles to return
        
        Returns:
        --------
        pd.DataFrame : DataFrame with OHLCV data indexed by time_period_start
                       Columns: time_period_end, price_open, price_high, 
                               price_low, price_close, volume_traded, trades_count
        
        Note:
        -----
        - Uses CoinAPI REST API (not WebSocket) for historical data
        - Requires COINAPI_KEY environment variable
        """
        import requests
        
        # Format times for CoinAPI
        time_start_str = self._format_time_for_coinapi(time_start)
        time_end_str = self._format_time_for_coinapi(time_end)
        
        url = f"{self.baseUrl}/v1/ohlcv/{symbol_id}/history"
        params = {
            'period_id': period_id,
            'time_start': time_start_str,
            'time_end': time_end_str
        }
        
        if limit is not None:
            params['limit'] = limit
        
        headers = {'X-CoinAPI-Key': self.apiKey}
        
        try:
            logger.info(f"Fetching historical data: {symbol_id} ({period_id}) from {time_start_str} to {time_end_str}")
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code != 200:
                logger.error(f"CoinAPI REST error: {response.status_code}")
                try:
                    error_detail = response.json()
                    logger.error(f"Error detail: {error_detail}")
                except:
                    logger.error(f"Response text: {response.text[:200]}")
                return pd.DataFrame()
            
            data = response.json()
            
            if not data:
                logger.warning(f"No historical data returned for {symbol_id}")
                return pd.DataFrame()
            
            # Convert to DataFrame
            df = pd.DataFrame(data)
            
            # Rename columns to match expected format
            column_mapping = {
                'price_open': 'open',
                'price_high': 'high',
                'price_low': 'low',
                'price_close': 'close',
                'volume_traded': 'volume',
                'trades_count': 'trades'
            }
            df.rename(columns=column_mapping, inplace=True)
            
            # Convert time columns to datetime
            df['time_period_start'] = pd.to_datetime(df['time_period_start'])
            df['time_period_end'] = pd.to_datetime(df['time_period_end'])
            
            if 'time_open' in df.columns:
                df['time_open'] = pd.to_datetime(df['time_open'])
            if 'time_close' in df.columns:
                df['time_close'] = pd.to_datetime(df['time_close'])
            
            # Set index
            df.set_index('time_period_start', inplace=True)
            
            logger.info(f"✅ Retrieved {len(df)} historical candles for {symbol_id}")
            return df
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error fetching historical data: {e}")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"Error processing historical data: {e}")
            return pd.DataFrame()

    def get_latest_candles(self, symbol_id: str, period_id: str, limit: int = 500) -> pd.DataFrame:
        """
        Get LATEST OHLCV data from CoinAPI REST API using /latest endpoint.
        
        This endpoint returns the most recent candles in DESCENDING order,
        which ensures continuity with WebSocket real-time data.
        
        The /history endpoint can be delayed by hours/days for some symbols,
        but /latest always returns the most recent available data.
        
        Parameters:
        -----------
        symbol_id : str
            CoinAPI symbol ID (e.g., 'BINANCEFTS_PERP_ETH_USDT')
        period_id : str
            Time period (e.g., '1MIN', '5MIN', '1HRS', '1DAY')
        limit : int
            Number of candles to return (default 500)
        
        Returns:
        --------
        pd.DataFrame : DataFrame with OHLCV data in ASCENDING time order
        """
        import requests
        
        # Use /latest endpoint instead of /history
        url = f"{self.baseUrl}/v1/ohlcv/{symbol_id}/latest"
        params = {
            'period_id': period_id,
            'limit': limit
        }
        
        headers = {'X-CoinAPI-Key': self.apiKey}
        
        try:
            logger.info(f"Fetching LATEST candles: {symbol_id} ({period_id}) limit={limit}")
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code != 200:
                logger.error(f"CoinAPI REST error: {response.status_code}")
                try:
                    error_detail = response.json()
                    logger.error(f"Error detail: {error_detail}")
                except:
                    logger.error(f"Response text: {response.text[:200]}")
                return pd.DataFrame()
            
            data = response.json()
            
            if not data:
                logger.warning(f"No latest data returned for {symbol_id}")
                return pd.DataFrame()
            
            # Convert to DataFrame
            df = pd.DataFrame(data)
            
            # Rename columns to match expected format
            column_mapping = {
                'price_open': 'open',
                'price_high': 'high',
                'price_low': 'low',
                'price_close': 'close',
                'volume_traded': 'volume',
                'trades_count': 'trades'
            }
            df.rename(columns=column_mapping, inplace=True)
            
            # Convert time columns to datetime
            df['time_period_start'] = pd.to_datetime(df['time_period_start'])
            df['time_period_end'] = pd.to_datetime(df['time_period_end'])
            
            if 'time_open' in df.columns:
                df['time_open'] = pd.to_datetime(df['time_open'])
            if 'time_close' in df.columns:
                df['time_close'] = pd.to_datetime(df['time_close'])
            
            # IMPORTANT: /latest returns data in DESCENDING order (newest first)
            # We need to reverse it to ASCENDING order (oldest first) to match WebSocket append logic
            df = df.sort_values('time_period_start', ascending=True).reset_index(drop=True)
            
            # Log the time range of data received
            if not df.empty:
                oldest = df['time_period_start'].iloc[0]
                newest = df['time_period_start'].iloc[-1]
                logger.info(f"✅ Retrieved {len(df)} LATEST candles for {symbol_id}")
                logger.info(f"   Time range: {oldest} to {newest}")
            
            return df
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error fetching latest data: {e}")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"Error processing latest data: {e}")
            import traceback
