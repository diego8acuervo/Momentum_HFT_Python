
# Import required libraries
import requests
import pandas as pd
import numpy as np
try:
    import matplotlib.pyplot as plt
    import seaborn as sns
except ImportError:
    plt = None
    sns = None
from datetime import datetime, timedelta, timezone
import json
from typing import Optional, List
import requests
import random


import scipy.stats as stats
import statsmodels.api as sm
from statsmodels.tsa.stattools import adfuller, kpss
from statsmodels.tsa.seasonal import seasonal_decompose
from statsmodels.tsa.arima.model import ARIMA
try:
    from statsmodels.graphics.tsaplots import plot_acf, plot_pacf
except ImportError:
    plot_acf = None
    plot_pacf = None



#%pip install boto3
# import boto3
# import os
# from botocore import UNSIGNED
# from botocore.client import Config

#=======================================================================================================================
#%% Avellaneda-Stoikov Market Making Model

# ----------------------------
# 1) Parámetros editables
# ----------------------------

# Precio actual de IBIT (USD). Sustituye por el último precio.
S = 3833

# Volatilidad intradía (desv. std. por periodo unitario). Ajusta según tu ventana (p.ej., 5m, 1m).
# Como punto de partida, 1.5% intradía para ilustración.
sigma = 2

# Valores de aversión al riesgo a comparar (en [0,1]). Calibrar según política
gammas = [0.1, 0.5, 0.9]

# Observaciones empíricas: pares (half_spread_bps, fill_rate_por_segundo)
# Reemplaza con tus mediciones (por ejemplo, de colas y ejecuciones reales).
observaciones = [
    (100.0, 2.00),   # a 1 bp de half-spread se llenan ~2 órdenes/seg
    (200.0, 1.00),
    (300.0, 0.50),
    (500.0, 0.25),
]

# ----------------------------
# 2) Estimación de la curva de intensidades lambda(delta) = A * exp(-k * delta)
# ----------------------------

def estimar_A_y_k(observaciones_bps, S):
    """
    Ajusta log(lambda) = log(A) - k * delta, donde delta = half-spread en dólares.
    observaciones_bps: lista de (half_spread_bps, fill_rate_por_segundo)
    Retorna A, k (k en unidades 1/USD).
    """
    # Convertir bps a dólares: 1 bp = 1e-4 del precio
    deltas_usd = np.array([bps * 1e-4 * S for bps, _ in observaciones_bps], dtype=float)
    lambdas = np.array([lam for _, lam in observaciones_bps], dtype=float)

    # Filtrar valores válidos
    msk = (deltas_usd > 0) & (lambdas > 0)
    deltas_usd = deltas_usd[msk]
    lambdas = lambdas[msk]

    # Regresión lineal: y = a + b x, con y = log(lambda), x = delta
    x = deltas_usd
    y = np.log(lambdas)
    b, a = np.polyfit(x, y, 1)  # y ≈ a + b x

    # En el modelo: log(lambda) = log(A) - k * delta -> a = log(A), b = -k
    A = np.exp(a)
    k = -b
    return A, k

#A_hat, k_hat = estimar_A_y_k(observaciones, S)

# ----------------------------
# 3) Half-spread óptimo con estabilidad numérica
#Haz k_t e σ^t dependientes de profundidad, desequilibrio del libro y volatilidad realizada reciente.

def delta_star(gamma, k, eps=1e-8):
    """
    Devuelve el half-spread óptimo. Maneja el límite gamma->0: delta* ≈ 1/k.
    Con 𝛾 mayor, el half-spread óptimo 𝛿⋆ crece y el centro se mueve más por
    unidad de inventario, reduciendo riesgo de acumulación.
    """
    if gamma < eps:
        return 1.0 / k
    return (1.0 / gamma) * np.log(1.0 + (gamma / k))

# ----------------------------
# 4) Precio de reserva y cotizaciones
# ----------------------------

def reserva_price(S, q, gamma, sigma):
    # r(q) = S - q * gamma * sigma^2
    return S - q * gamma * (sigma ** 2)


def cotizaciones_optimas(S, q, gamma, sigma, k):
    r_q = reserva_price(S, q, gamma, sigma)
    delta = delta_star(gamma, k)
    p_bid = r_q - delta
    p_ask = r_q + delta
    return r_q,p_bid, p_ask


def average_true_range(ohlcv_df, period=14):
    """
    Calcula el Average True Range (ATR) para un DataFrame OHLCV.
    """
    # Asegura que las columnas necesarias estén presentes
    if not all(col in ohlcv_df.columns for col in ['high', 'low', 'close']):
        raise ValueError("DataFrame must contain 'high', 'low', and 'close' columns")
    high = ohlcv_df['high']
    low = ohlcv_df['low']
    close = ohlcv_df['close']
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    return atr



# Rango de inventario en número de shares
#q_min, q_max, q_step = -100, 100, 10


#=======================================================================================================================
#%% CoinAPI
# Function to load API key from environment variable

def load_coinApi_key(env_var: str = "COINAPI_KEY") -> str:
    """Load API key from environment variable. Default: COINAPI_KEY"""
    import os
    api_key = os.environ.get(env_var)
    if not api_key:
        raise ValueError(f"API key not found in environment variable '{env_var}'")
    return api_key

# Load API key and base URL from environment variables at the top
def get_base_url() -> str:
    return "https://rest.coinapi.io"

BASE_URL = get_base_url()
# Load API key from environment variable - DO NOT hardcode API keys!
API_KEY = "1b47353a-63e5-4c8a-b5e3-1924291b40ce"  # os.environ.get("COINAPI_KEY", "")

if not API_KEY:
    print("⚠️  WARNING: COINAPI_KEY not set in environment variables")

def hist_coin_api_data(asset_id_base: str, asset_id_quote: str, period_id: str, time_start: str, time_end: str) -> Optional[List[dict]]:
    """
    Fetch historical exchange rate data from CoinAPI
    
    Args:
        asset_id_base: Base asset identifier (e.g., 'BTC')
        asset_id_quote: Quote asset identifier (e.g., 'USDT')
        period_id: Time period (e.g., '1HRS' for 1 hour)
        time_start: Start time in ISO format
        time_end: End time in ISO format
    
    Returns:
        List of exchange rate data points with OHLC data
    """
    url = f"{BASE_URL}/v1/exchangerate/{asset_id_base}/{asset_id_quote}/history"
    
    params = {
        'period_id': period_id,
        'time_start': time_start,
        'time_end': time_end,
        'limit': 10000
    }
    
    headers = {
        'X-CoinAPI-Key': API_KEY
    }
    
    try:
        print(f"🔄 Fetching data from {url}")
        print(f"📅 Time range: {time_start} to {time_end}")
        
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        
        data = response.json()
        print(f"✅ Successfully fetched {len(data)} data points")
        return data
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching data: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response status: {e.response.status_code}")
            print(f"Response text: {e.response.text}")
        return None

def subscribe_tick_data(asset_id_base: str, asset_id_quote: str):
    """
    Subscribe to real-time tick data from CoinAPI
    
    Args:
        asset_id_base: Base asset identifier (e.g., 'BTC')
        asset_id_quote: Quote asset identifier (e.g., 'USDT')
    
    Returns:
        WebSocket connection object
    """
    import websocket
    import threading
    import time

    ws_url = "wss://ws.coinapi.io/v1/"
    
    def on_message(ws, message):
        print(f"📈 Tick data: {message}")
    
    def on_error(ws, error):
        print(f"❌ WebSocket error: {error}")
    
    def on_close(ws, close_status_code, close_msg):
        print("❌ WebSocket closed")
    
    def on_open(ws):
        print("✅ WebSocket connection opened")
        subscribe_message = {
            "type": "hello",
            "apikey": API_KEY,
            "heartbeat": False,
            "subscribe_data_type": ["trade"],
            "subscribe_filter_symbol_id": [f"BITSTAMP_SPOT_{asset_id_base}_{asset_id_quote}"]
        }
        ws.send(json.dumps(subscribe_message))
    
    ws = websocket.WebSocketApp(ws_url,
                                on_open=on_open,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)
    
    wst = threading.Thread(target=ws.run_forever)
    wst.daemon = True
    wst.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        ws.close()
        print("❌ Stopped tick data subscription")

# Calcula el vwap de una orden de tamaño q el lado l
def vwap(lob: pd.DataFrame, q: float, side: str) -> float:
    cumulative_size = 0.0
    cumulative_cost = 0.0
    if side == 'buy':
        for index, row in lob.iterrows():
            trade_size = min(row['ask_size'], q - cumulative_size)
            cumulative_cost += trade_size * row['ask_price']
            cumulative_size += trade_size
            if cumulative_size >= q:
                return cumulative_cost / cumulative_size if cumulative_size > 0 else 0.0
    elif side == 'sell':
        for index, row in lob.iterrows():
            trade_size = min(row['bid_size'], q - cumulative_size)
            cumulative_cost += trade_size * row['bid_price']
            cumulative_size += trade_size
            if cumulative_size >= q:
                return cumulative_cost / cumulative_size if cumulative_size > 0 else 0.0
    return cumulative_cost / cumulative_size if cumulative_size > 0 else 0.0  # Si no se llena, retorna 0

def create_signed_headers() -> dict:
    headers = {
        'X-CoinAPI-Key': load_coinApi_key()
    }
    return headers

def get_current_depth(exchange: str, base_asset: str, quote_asset: str, depth: int = 10) -> pd.DataFrame:
    symbol_id = f"{exchange}_SPOT_{base_asset}_{quote_asset}"
    url = f"https://rest.coinapi.io/v1/orderbooks/{symbol_id}/current"
    headers=create_signed_headers()
    response = requests.request("GET", url, headers=headers, data=payload)
    data = json.loads(response.text)
    bids = pd.json_normalize(data, record_path=[['bids']])
    bids.columns = ['bid_price', 'bid_size']
    asks = pd.json_normalize(data, record_path=[['asks']])
    asks.columns = ['ask_price', 'ask_size']
    df_orderbook = pd.concat([bids, asks], axis=1)
    return df_orderbook.head(depth)

# calcula el impacto de mercado de una orden de tamaño q el lado l
def market_impact(lob: pd.DataFrame, q: float, side: str) -> float:
    cumulative_size = 0.0
    if side == 'buy':
        for index, row in lob.iterrows():
            cumulative_size += row['ask_size']
            print(cumulative_size, row['ask_price'])
            if cumulative_size >= q:
                return row['ask_price']
            return lob['ask_price'].iloc[-1]  # Si no se llena, retorna el último precio ask disponible
    elif side == 'sell':
        for index, row in lob.iterrows():
            cumulative_size += row['bid_size']
            print(cumulative_size, row['bid_price'])
            if cumulative_size >= q:
                return row['bid_price']
            return lob['bid_price'].iloc[-1]  # Si no se llena, retorna el último precio bid disponible
        
# Calcula el pnl bruto de arbitrar entre lobs un monto q
def arbitrage_pnl(lob1: pd.DataFrame, lob2: pd.DataFrame, q: float) -> float:
    buy_price = vwap(lob1, q, 'buy') # Precio medio compra libro 1
    sell_price = vwap(lob2, q, 'sell') # Precio medio venta libro 2
    pnl = (sell_price - buy_price) * q
    return buy_price,sell_price,pnl



#=======================================================================================================================

#%% BINANCE 
# Función para colocar una orden de mercado
import time
# %pip install python-binance
from binance import Client, ThreadedWebsocketManager, ThreadedDepthCacheManager

class binanceWallet():
    def __init__(self):
        self.api_key, self.api_secret = load_binance_api_keys()
        self.client = Client(self.api_key, self.api_secret)

    def get_balance(self, asset: str) -> float:
        """Get available balance for a specific asset."""
        try:
            balance_info = self.client.get_asset_balance(asset=asset)
            if balance_info:
                return float(balance_info['free'])
            else:
                print(f"❌ Asset {asset} not found")
                return 0.0
        except Exception as e:
            print(f"❌ Error fetching balance for {asset}: {e}")
            return 0.0

    def get_all_balances(self) -> pd.DataFrame:
        """Get all asset balances."""
        try:
            balances = self.client.get_account()['balances']
            df_balances = pd.DataFrame(balances)
            df_balances['free'] = df_balances['free'].astype(float)
            df_balances['locked'] = df_balances['locked'].astype(float)
            df_balances = df_balances[df_balances['free'] > 0]
            return df_balances
        except Exception as e:
            print(f"❌ Error fetching all balances: {e}")
            return pd.DataFrame()
# Una Funcion para obtener los saldos :
def get_balance_binance(snapshotType: str='SPOT') -> float:
    """Obtiene saldo disponibles en Binance"""
    account_info = client.get_account_snapshot(type=snapshotType)
    balances = account_info['snapshotVos'][0]['data']['balances']
    for balance in balances:
        asset = balance['asset']
        free = float(balance['free'])
        locked = float(balance['locked'])
        total = free + locked
        if total > 0:
            print(f"Asset: {asset}, Free: {free}, Locked: {locked}, Total: {total}")
    # Incorpora a un DataFrame para mejor visualización
    df_balances = pd.DataFrame(balances)
    return df_balances[df_balances['free'].astype(float) > 0]

# Get exchange info for USDTCOP
def load_binance_api_keys(env_var_api: str = "BINANCE_API_KEY", env_var_secret: str = "BINANCE_SECRET_KEY") -> tuple:
    """Load Binance API key and secret from environment variables."""
    import os
    api_key = os.environ.get(env_var_api)
    api_secret = os.environ.get(env_var_secret)
    if not api_key or not api_secret:
        raise ValueError(f"API key or secret not found in environment variables '{env_var_api}' and '{env_var_secret}'")
    return api_key, api_secret
#api_key, api_secret = load_binance_api_keys()
#client = Client(api_key, api_secret)

def binance_async(api_key: str, api_secret: str):
    """Start Binance WebSocket Manager."""
    if not api_key or not api_secret:
        raise ValueError("API key or secret not found in environment variables 'BINANCE_API_KEY' and 'BINANCE_SECRET_KEY'")
    twm = ThreadedWebsocketManager(api_key=api_key, api_secret=api_secret)
    return twm

def get_binance_info(symbol: str):
    """Get Binance exchange info for a specific symbol."""
    try:
        info = client.get_exchange_info()
        if info:
            return info
        else:
            print(f"❌ Symbol {symbol} not found")
            return None
    except Exception as e:
        print(f"❌ Error fetching symbol info: {e}")
        return None
    
def get_binance_price_filter(symbol: str):
    """Get price filter for a specific symbol."""
    info = get_binance_info(symbol)
    if info:
        exchange_info = next((s for s in info['symbols'] if s['symbol'] == symbol), None)
        if exchange_info:
            price_filter = next((f for f in exchange_info['filters'] if f['filterType'] == 'PRICE_FILTER'), None)
            return price_filter
        else:
            print(f"❌ Symbol {symbol} not found in exchange info")
            return None
    return None

def get_binance_size_filter(symbol: str):
    """Get lot size filter for a specific symbol."""
    info = get_binance_info(symbol)
    if info:
        exchange_info = next((s for s in info['symbols'] if s['symbol'] == symbol), None)
        if exchange_info:
            lot_size_filter = next((f for f in exchange_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
            return lot_size_filter
        else:
            print(f"❌ Symbol {symbol} not found in exchange info")
            return None
    return None

# Find LOT_SIZE filter for quantity
# lot_size_filter = get_binance_size_filter('USDTCOP')
# print(f"LOT_SIZE: {lot_size_filter}")

def format_price_for_binance(price, tick_size):
    """Format price according to Binance tick size requirements"""
    from decimal import Decimal, ROUND_DOWN
    
    tick_size_decimal = Decimal(str(tick_size))
    price_decimal = Decimal(str(price))
    
    # Round down to nearest tick size
    formatted_price = float(price_decimal.quantize(tick_size_decimal, rounding=ROUND_DOWN))
    return formatted_price

def format_quantity_for_binance(quantity, step_size):
    """Format quantity according to Binance step size requirements"""
    from decimal import Decimal, ROUND_DOWN
    
    step_size_decimal = Decimal(str(step_size))
    quantity_decimal = Decimal(str(quantity))
    
    formatted_quantity = float(quantity_decimal.quantize(step_size_decimal, rounding=ROUND_DOWN))
    return formatted_quantity

def place_market_order(symbol, side, quantity):
    try:
        order = client.create_order(
            symbol=symbol,
            side=side,
            type='MARKET',
            quantity=quantity
        )
        print(f"Market order placed: {order}")
        return order
    except Exception as e:
        print(f"Error placing market order: {e}")
        return None

# Funcion para colocar una orden límite
def place_limit_order(symbol, side, quantity, price):
    """Coloca una orden límite en Binance. Requiere símbolo, lado (BUY/SELL), cantidad y precio."""
    try:
        order = client.create_order(
            symbol=symbol,
            side=side,
            type='LIMIT',
            timeInForce='GTC',
            quantity=quantity,
            price=price
        )
        print(f"Limit order placed: {order}")
        return order
    except Exception as e:
        print(f"Error placing limit order: {e}")
        return None

# Función para revisar el estado de una orden
def check_order_status(symbol, orderId):
    try:
        order = client.get_order(symbol=symbol, orderId=orderId)
        print(f"Order status: {order}")
        return order
    except Exception as e:
        print(f"Error checking order status: {e}")
        return None

# Función para cancelar una orden
def cancel_order(symbol, orderId):
    try:
        result = client.cancel_order(symbol=symbol, orderId=orderId)
        print(f"Order cancelled: {result}")
        return result
    except Exception as e:
        print(f"Error cancelling order: {e}")
        return None

# Funcion para modificar el precio de una orden abierta
def modify_order_price(symbol, orderId, new_price):
    try:
        # Cancelar la orden existente
        cancel_result = cancel_order(symbol, orderId)
        if cancel_result:
            # Recolocar la orden con el nuevo precio
            new_order = client.create_order(
                symbol=symbol,
                side=cancel_result['side'],
                type='LIMIT',
                timeInForce='GTC',
                quantity=cancel_result['origQty'],
                price=new_price
            )
            print(f"Order modified: {new_order}")
            return new_order
        else:
            print("Failed to cancel the existing order.")
            return None
    except Exception as e:
        print(f"Error modifying order price: {e}")
        return None
    
# Función para colocar una orden de mercado
def place_market_order(symbol, side, quantity):
    try:
        order = client.create_order(
            symbol=symbol,
            side=side,
            type='MARKET',
            quantity=quantity
        )
        print(f"Market order placed: {order}")
        return order
    except Exception as e:
        print(f"Error placing market order: {e}")
        return None

# Funcion para colocar una orden límite
def place_limit_order(symbol, side, quantity, price):
    """Coloca una orden límite en Binance. Requiere símbolo, lado (BUY/SELL), cantidad y precio."""
    try:
        order = client.create_order(
            symbol=symbol,
            side=side,
            type='LIMIT',
            timeInForce='GTC',
            quantity=quantity,
            price=price
        )
        print(f"Limit order placed: {order}")
        return order
    except Exception as e:
        print(f"Error placing limit order: {e}")
        return None

# Función para revisar el estado de una orden
def check_order_status(symbol, orderId):
    try:
        order = client.get_order(symbol=symbol, orderId=orderId)
        print(f"Order status: {order}")
        return order
    except Exception as e:
        print(f"Error checking order status: {e}")
        return None

# Función para cancelar una orden
def cancel_order(symbol, orderId):
    try:
        result = client.cancel_order(symbol=symbol, orderId=orderId)
        print(f"Order cancelled: {result}")
        return result
    except Exception as e:
        print(f"Error cancelling order: {e}")
        return None

# Funcion para modificar el precio de una orden abierta
def modify_order_price(symbol, orderId, new_price):
    try:
        # Cancelar la orden existente
        cancel_result = cancel_order(symbol, orderId)
        if cancel_result:
            # Recolocar la orden con el nuevo precio
            new_order = client.create_order(
                symbol=symbol,
                side=cancel_result['side'],
                type='LIMIT',
                timeInForce='GTC',
                quantity=cancel_result['origQty'],
                price=new_price
            )
            print(f"Order modified: {new_order}")
            return new_order
        else:
            print("Failed to cancel the existing order.")
            return None
    except Exception as e:
        print(f"Error modifying order price: {e}")
        return None
    
def transfer_binance_to_binance(sub_account, asset, amount):
    """Transfer assets between Binance main account and sub-account."""
    try:
        result = client.sub_account_transfer(
            toEmail=sub_account,
            asset=asset,
            amount=amount
        )
        print(f"Transfer successful: {result}")
    except Exception as e:
        print(f"Error during transfer: {e}")

def withdraw_from_binance(asset, address, amount, network=None):
    """Withdraw assets from Binance to an external address."""
    try:
        result = client.withdraw(
            asset=asset,
            address=address,
            amount=amount,
            network=network
        )
        print(f"Withdrawal successful: {result}")
    except Exception as e:
        print(f"Error during withdrawal: {e}")

def transfer_binance_to_fiat(asset, amount, fiat_account):
    """Transfer assets from Binance to a fiat account."""
    try:
        result = client.fiat_withdraw(
            asset=asset,
            amount=amount,
            fiatAccount=fiat_account
        )
        print(f"Fiat transfer successful: {result}")
    except Exception as e:
        print(f"Error during fiat transfer: {e}")

def transfer_fiat_to_binance(asset, amount, fiat_account):
    """Transfer assets from a fiat account to Binance."""
    try:
        result = client.fiat_deposit(
            asset=asset,
            amount=amount,
            fiatAccount=fiat_account
        )
        print(f"Fiat deposit successful: {result}")
    except Exception as e:
        print(f"Error during fiat deposit: {e}")

def withdraw_from_binance(asset, address, amount, network=None):


    """Withdraw assets from Binance to an external address."""
    try:
        result = client.withdraw(
            coin=asset,
            address=address,
            amount=amount,
            network=network
        )
        print(f"Withdrawal successful: {result}")
    except Exception as e:
        print(f"Error during withdrawal: {e}")
# Example usage
#withdraw_from_binance(asset='USDT', address='<address>', amount='100', network='MATIC') 

# Get all available networks for USDT
def get_usdt_networks():
    """Get all available networks for USDT withdrawals"""
    try:
        coin_info = client.get_all_coins_info()
        usdt_info = next((coin for coin in coin_info if coin['coin'] == 'USDT'), None)
        
        if usdt_info and 'networkList' in usdt_info:
            print("Available USDT networks:")
            for network in usdt_info['networkList']:
                print(f"Network: {network['network']}, Name: {network['name']}")
                print(f"  Withdraw Enable: {network['withdrawEnable']}")
                print(f"  Withdraw Fee: {network['withdrawFee']}")
                print(f"  Withdraw Min: {network['withdrawMin']}")
                print("---")
        return usdt_info
    except Exception as e:
        print(f"Error getting network info: {e}")
        return None

#=======================================================================================================================

    #%% BITSO
# Función para obtener el balance de la cuenta
def generate_nonce_v2():
    # Get current timestamp in milliseconds (13 digits)
    timestamp = int(time.time() * 1000)
    
    # Generate random salt (6 digits)
    salt = random.randint(100000, 999999)  # Range: 100000-999999
    
    # Concatenate timestamp and salt
    return f"{timestamp}{salt}"

# Header Pre-request script

# bitso_api_key = "YniwWKvXMb"
# bitso_api_secret = "0aba980d54fbae5e3e6acb5fa6ba6a62"

def sign_request(api_key, api_secret, method, endpoint, body=""):
    import hmac
    import hashlib
    import base64
    import time

    nonce = generate_nonce_v2()
    message = nonce + method + endpoint + body
    signature = hmac.new(api_secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    
    return {
        'Authorization': f'Bitso {api_key}:{nonce}:{signature}',
        'Content-Type': 'application/json'
    }

# Funcion que calcula el saldo disponible para negociar en Bitso en la moneda dada
def get_balance_bitso(currency: str) -> float:
    """Obtiene saldo disponible en Bitso para la moneda dada"""
    currency = currency.lower()
    url = "https://api.bitso.com/v3/balance/"
    headers = sign_request(bitso_api_key, bitso_api_secret, 'GET', '/v3/balance/')
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        data = response.json()
        balances = data['payload']['balances']
        for balance in balances:
            if balance['currency'] == currency:
                available = float(balance['available'])
                print(f"Balance for {currency}: {available}")
                return available
    print(f"No balance found for {currency}.")

def withdraw_to_coink_account(amount, coink_recipient_id):
    """
    Withdraw COP from Bitso to a Coink account
    Note: Coink must be registered as a recipient first
    """
    import time
    import hmac
    import hashlib
    import json
    
    # Get API credentials
    api_key = os.getenv("BITSO_API_KEY")
    api_secret = os.getenv("BITSO_SECRET_KEY")
    
    if not api_key or not api_secret:
        raise ValueError("Bitso API credentials not found")
    
    # Bitso withdrawal endpoint
    url = "https://api.bitso.com/v3/withdrawals/"
    endpoint = "/v3/withdrawals/"
    
    # Generate nonce
    nonce = str(int(time.time() * 1000))
    
    # Request body for COP withdrawal
    body = {
        "currency": "cop",                    # Colombian Peso
        "amount": str(amount),               # Amount as string
        "recipient_id": coink_recipient_id   # Coink account recipient ID
    }
    
    # Convert to JSON
    json_payload = json.dumps(body, separators=(',', ':'))
    
    # Create signature
    message = nonce + "POST" + endpoint + json_payload
    signature = hmac.new(
        api_secret.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()
    
    # Headers
    headers = {
        'Authorization': f'Bitso {api_key}:{nonce}:{signature}',
        'Content-Type': 'application/json'
    }
    
    try:
        response = requests.post(url, headers=headers, data=json_payload)
        response.raise_for_status()
        
        result = response.json()
        if result.get('success'):
            print(f"Withdrawal to Coink successful!")
            print(f"Withdrawal ID: {result['payload']['wid']}")
            print(f"Amount: {amount} COP")
            return result
        else:
            print(f"Withdrawal failed: {result}")
            return None
            
    except requests.exceptions.RequestException as e:
        print(f"Error during withdrawal: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response: {e.response.text}")
        return None

# max_quantity = get_balance_bitso('USDT')
# max_quantity

#%% Consolida Trades
# Resamples average buy and average sell prices to daily frequency
def resample_trade_prices(trades_df:pd.DataFrame, freq:str ='D') -> pd.DataFrame:
    """Calculates the weighted average of buys and sales per day and resamples to daily frequency.
    inputs:
        trades_df: DataFrame with trade data, must contain columns 'Type' (BUY/SELL), 'Price', 'Amount', and a DateTimeIndex from all exchanges.
        freq: Resampling frequency string (default 'D' for daily).
    outputs:
        DataFrame with daily aggregated buy and sell volumes and average prices.
        
    """
    # Lowercase Buy/Sell types for consistency
    trades_df['Type'] = trades_df['side'].str.upper()

    # Separate buy and sell trades
    buy_trades = trades_df[trades_df['Type'] == 'BUY']
    sell_trades = trades_df[trades_df['Type'] == 'SELL']

    # Compute daily traded volumes
    buy_volumes_daily = buy_trades['amount'].resample(freq).sum().rename('Buy_Volume')
    sell_volumes_daily = sell_trades['amount'].resample(freq).sum().rename('Sell_Volume')

    # Compute weighted average prices
    weighted_buy_prices = (buy_trades['price'] * buy_trades['amount']).resample(freq).sum() / buy_volumes_daily
    weighted_sell_prices = (sell_trades['price'] * sell_trades['amount']).resample(freq).sum() / sell_volumes_daily

    # Resample to frequency and compute average prices
    buy_prices_resampled = weighted_buy_prices.resample(freq).mean().rename('Avg_Buy_Price')
    sell_prices_resampled = weighted_sell_prices.resample(freq).mean().rename('Avg_Sell_Price')

    # Combine into a single DataFrame that aggregates buys and sells per day as single trades
    resample_trade_prices = pd.concat([buy_volumes_daily, sell_volumes_daily, buy_prices_resampled, sell_prices_resampled], axis=1)

    return resample_trade_prices

