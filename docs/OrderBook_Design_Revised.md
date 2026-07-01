# Order Book Management - Revised Integration Design

## Executive Summary

This revised design integrates order book management **seamlessly into the existing `BinanceData(AdminDatos)` class** to maintain a single data manager per exchange. The `OrderBook` helper class is created as an internal component within `Datos.py`, and all order book methods are added directly to `BinanceData`.

### Key Design Principles
✅ **Single AdminDatos per Exchange** - No separate data managers  
✅ **Backward Compatible** - Existing code continues to work  
✅ **Minimal Impact** - No changes to base `AdminDatos` class  
✅ **Seamless Integration** - Order book methods alongside OHLCV methods  
✅ **Unified Interface** - One `BinanceData` instance handles both OHLCV and order books  

---

## 1. Architecture Overview

### 1.1 File Structure (No Changes)

```
src/Datos.py
├── AdminDatos (base class) ← NO CHANGES
├── OrderBook (NEW internal helper class)
└── BinanceData(AdminDatos) ← EXTENDED with order book methods
```

### 1.2 Class Hierarchy

```
AdminDatos (Abstract Base Class)
    ↓ inherits
BinanceData
    ├── OHLCV Methods (existing)
    │   ├── connect_websocket()
    │   ├── get_latest_kline()
    │   ├── get_kline_generator()
    │   └── ...
    │
    └── Order Book Methods (NEW)
        ├── subscribe_orderbook()
        ├── get_best_bid()
        ├── get_best_ask()
        ├── get_mid_price()
        ├── get_vwap_price()
        └── ...

OrderBook (Internal Helper Class)
    ├── Not exposed to external code
    ├── Used internally by BinanceData
    └── Manages individual symbol's book
```

---

## 2. Implementation Strategy

### 2.1 OrderBook Helper Class (Internal)

**Location**: Inside `Datos.py`, before `BinanceData` class  
**Purpose**: Efficient data structure for a single symbol's order book  
**Visibility**: Internal only, not imported by other modules

```python
# ============================================================================
# OrderBook Helper Class (Internal to BinanceData)
# ============================================================================

from sortedcontainers import SortedDict

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
        """Update or remove a bid price level"""
        if quantity == 0:
            self.bids.pop(-price, None)  # Remove if exists
        else:
            self.bids[-price] = quantity
            
    def update_ask(self, price: float, quantity: float):
        """Update or remove an ask price level"""
        if quantity == 0:
            self.asks.pop(price, None)
        else:
            self.asks[price] = quantity
            
    def get_best_bid(self) -> tuple:
        """Return (price, quantity) of best bid, or (None, None)"""
        if not self.bids:
            return None, None
        neg_price, qty = self.bids.peekitem(0)  # First item (highest)
        return -neg_price, qty
        
    def get_best_ask(self) -> tuple:
        """Return (price, quantity) of best ask, or (None, None)"""
        if not self.asks:
            return None, None
        return self.asks.peekitem(0)  # First item (lowest)
        
    def get_bids(self, levels: int = 10) -> list:
        """
        Return top N bid levels as [(price, qty), ...]
        Sorted descending by price
        """
        result = []
        for neg_price, qty in list(self.bids.items())[:levels]:
            result.append((-neg_price, qty))
        return result
        
    def get_asks(self, levels: int = 10) -> list:
        """
        Return top N ask levels as [(price, qty), ...]
        Sorted ascending by price
        """
        return list(self.asks.items())[:levels]
        
    def clear(self):
        """Clear all price levels"""
        self.bids.clear()
        self.asks.clear()
        self.last_update_id = 0
        self.is_synchronized = False
        
    def get_depth_snapshot(self, levels: int = 10) -> dict:
        """Return order book snapshot"""
        return {
            'symbol': self.symbol,
            'bids': self.get_bids(levels),
            'asks': self.get_asks(levels),
            'lastUpdateId': self.last_update_id,
            'is_synchronized': self.is_synchronized,
            'last_update_time': self.last_update_time
        }
```

### 2.2 BinanceData Class Extensions

**Strategy**: Add order book functionality to existing `BinanceData` class without breaking existing code.

#### 2.2.1 New Attributes in `__init__`

```python
class BinanceData(AdminDatos):
    """
    Obtiene datos de mercado en tiempo real desde Binance Spot y 
    Binance Perpetuals via WebSocket.
    
    Provides both OHLCV (klines) and Order Book data management.
    """
    
    def __init__(self, eventos, lista_nemos, interval='1m'):
        # ===== EXISTING ATTRIBUTES (unchanged) =====
        self.eventos = eventos
        self.lista_nemos = lista_nemos
        self.datos_nemo = {}
        self.ultimo_dato_nemo = {}
        self.continuar_backtest = True
        self.indice_vela = 0
        self.base_token = 'USDT'
        self.binance_api_key = os.getenv("BINANCE_API_KEY")
        self.binance_secret_key = os.getenv("BINANCE_SECRET_KEY")
        self.comb_index = None
        self.ws = None  # OHLCV WebSocket
        self.interval = interval
        
        # OHLCV WebSocket management attributes
        self.ws_thread = None
        self.data_lock = threading.Lock()
        self.is_running = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.last_message_time = {}
        self.subscription_id = 0
        
        # ===== NEW ORDER BOOK ATTRIBUTES =====
        # Order book data structures
        self.order_books = {}  # {symbol: OrderBook instance}
        self.order_book_buffers = {}  # {symbol: [buffered_events]}
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
        
        # ===== EXISTING INITIALIZATION (unchanged) =====
        self.api = self.binance_api_connect()
        self.connect_websocket(interval=self.interval)
```

#### 2.2.2 Order Book WebSocket Management

Add separate WebSocket connection for order books to avoid stream mixing:

```python
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
        # Determine base URL based on subscriptions (spot vs futures)
        # Default to spot, will be updated when subscriptions are added
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
```

#### 2.2.3 Order Book WebSocket Callbacks

```python
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
```

---

## 3. Public API Methods (Added to BinanceData)

### 3.1 Subscription Methods

```python
def subscribe_orderbook(self, symbol, depth_type='depth10', speed='100ms', 
                       market_type='spot'):
    """
    Suscribe a actualizaciones de order book para un símbolo.
    
    Args:
        symbol (str): Símbolo de trading (e.g., 'BTC', 'ETH')
        depth_type (str): Tipo de profundidad
            - 'full' o 'depth': Full order book diff stream
            - 'depth5': Top 5 levels per side
            - 'depth10': Top 10 levels per side (RECOMMENDED)
            - 'depth20': Top 20 levels per side
            - 'ticker': Best bid/ask only (bookTicker)
        speed (str): Velocidad de actualización
            - '1000ms': 1 segundo (default para spot)
            - '100ms': 100 milisegundos (más rápido)
        market_type (str): Tipo de mercado
            - 'spot': Spot trading
            - 'futures': Futuros
            - 'perpetuals': Perpetuos (mismo endpoint que futures)
            
    Returns:
        bool: True si la suscripción fue exitosa
        
    Example:
        # Para estrategia de pares, usar depth10 con 100ms
        binance_data.subscribe_orderbook('BTC', depth_type='depth10', 
                                         speed='100ms', market_type='spot')
        binance_data.subscribe_orderbook('ETH', depth_type='depth10', 
                                         speed='100ms', market_type='spot')
        
    Note:
        - El order book se sincroniza automáticamente usando el protocolo de Binance
        - Usa una conexión WebSocket separada de OHLCV
        - Thread-safe, puede llamarse desde cualquier thread
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
```

### 3.2 Query Methods (Core Interface for Strategies)

```python
def get_best_bid(self, symbol):
    """
    Obtiene el mejor precio de compra (bid) y cantidad.
    
    Args:
        symbol (str): Símbolo de trading
        
    Returns:
        tuple: (price, quantity) o (None, None) si no disponible
        
    Example:
        price, qty = binance_data.get_best_bid('BTC')
        if price:
            print(f"Best bid: {price} @ {qty}")
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
    
    Args:
        symbol (str): Símbolo de trading
        
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
    
    Más preciso que usar el precio de cierre para señales de trading.
    
    Args:
        symbol (str): Símbolo de trading
        
    Returns:
        float: Precio medio o None si no disponible
        
    Example:
        # En AQM_PT_HFT.calcular_senalXY()
        mid_y = self.velas.get_mid_price(p0)
        mid_x = self.velas.get_mid_price(p1)
        if mid_y and mid_x:
            # Usar mid prices en lugar de close
            spread = mid_y - hedge_ratio * mid_x
    """
    bid, _ = self.get_best_bid(symbol)
    ask, _ = self.get_best_ask(symbol)
    
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    return None

def get_spread(self, symbol):
    """
    Calcula el spread: best_ask - best_bid
    
    Útil para determinar costos de transacción y liquidez.
    
    Args:
        symbol (str): Símbolo de trading
        
    Returns:
        dict: {
            'absolute': float,      # Spread absoluto
            'percentage': float,    # Spread como % del mid price
            'bid': float,          # Best bid
            'ask': float,          # Best ask
            'mid': float           # Mid price
        } o None si no disponible
        
    Example:
        spread_info = binance_data.get_spread('BTC')
        if spread_info and spread_info['percentage'] > 0.1:
            print("Wide spread - low liquidity")
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
    
    Args:
        symbol (str): Símbolo de trading
        levels (int): Número de niveles a retornar (default: 10)
        
    Returns:
        dict: {
            'bids': [[price, qty], ...],  # Ordenado descendente
            'asks': [[price, qty], ...],  # Ordenado ascendente
            'lastUpdateId': int,
            'is_synchronized': bool
        } o None si no disponible
        
    Example:
        depth = binance_data.get_depth('BTC', levels=5)
        if depth:
            total_bid_vol = sum(qty for _, qty in depth['bids'])
            print(f"Total bid volume (top 5): {total_bid_vol}")
    """
    with self.ob_lock:
        if symbol not in self.order_books:
            return None
        return self.order_books[symbol].get_depth_snapshot(levels)

def get_vwap_price(self, symbol, target_volume, side):
    """
    Calcula el precio promedio ponderado por volumen (VWAP) para un tamaño de orden.
    
    CRÍTICO para estimar slippage y costos de ejecución.
    
    Args:
        symbol (str): Símbolo de trading
        target_volume (float): Tamaño de orden en moneda base (e.g., 10 BTC)
        side (str): 'buy' (usa asks) o 'sell' (usa bids)
        
    Returns:
        dict: {
            'vwap': float,              # Precio promedio ponderado
            'total_cost': float,        # Costo total en moneda quote
            'levels_used': int,         # Niveles consumidos
            'worst_price': float,       # Peor precio de ejecución
            'slippage_pct': float,     # Slippage vs mejor precio
            'achievable': bool          # Si hay suficiente liquidez
        } o None si no disponible
        
    Example:
        # Estimando compra de 10 BTC
        vwap_info = binance_data.get_vwap_price('BTC', 10.0, 'buy')
        if vwap_info:
            print(f"VWAP: ${vwap_info['vwap']:.2f}")
            print(f"Slippage: {vwap_info['slippage_pct']:.4f}%")
            
            if vwap_info['slippage_pct'] > 0.05:  # 0.05% threshold
                print("Slippage too high - consider limit order")
                
        # Integración en estrategia
        if signal == 'LARGO':
            vwap_y = binance_data.get_vwap_price(p0, size_y, 'buy')
            if vwap_y and vwap_y['slippage_pct'] < max_slippage:
                # Ejecutar con precio ajustado
                adjusted_price = vwap_y['vwap']
    """
    with self.ob_lock:
        if symbol not in self.order_books:
            return None
            
        book = self.order_books[symbol]
        if not book.is_synchronized:
            return None
        
        # Determine which side of the book to use
        if side.lower() == 'buy':
            levels = book.get_asks(levels=100)  # Get enough levels
            best_price = book.get_best_ask()[0]
        elif side.lower() == 'sell':
            levels = book.get_bids(levels=100)
            best_price = book.get_best_bid()[0]
        else:
            logger.error(f"Invalid side: {side}. Use 'buy' or 'sell'")
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
            
        # Check if order can be fully filled
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
    
    Args:
        symbol (str): Símbolo de trading
        side (str): 'bid' o 'ask'
        levels (int): Número de niveles a sumar (default: 5)
        
    Returns:
        float: Volumen total o None si no disponible
        
    Example:
        bid_vol = binance_data.get_total_volume('BTC', 'bid', levels=10)
        ask_vol = binance_data.get_total_volume('BTC', 'ask', levels=10)
        
        if bid_vol and ask_vol:
            imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)
            if abs(imbalance) > 0.5:
                print(f"Order book imbalance: {imbalance:.2%}")
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
    
    Args:
        symbol (str): Símbolo de trading
        
    Returns:
        dict: {
            'is_synchronized': bool,
            'last_update_id': int,
            'bid_levels': int,
            'ask_levels': int,
            'last_update_time': datetime
        } o None si no existe
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
```

---

## 4. Integration with AQM_PT_HFT Strategy

### 4.1 Current Strategy Code

```python
# From AQM_MR_Live.py - AQM_PT_HFT class
def calcular_senalXY(self, datos, p0, p1):
    # Currently uses close prices
    close_y = datos.get_valor_ultimas_velas(p0, 'close', 1)[0]
    close_x = datos.get_valor_ultimas_velas(p1, 'close', 1)[0]
    
    # Calculate spread and z-score
    spread = close_y - self.beta * close_x
```

### 4.2 Enhanced Strategy with Order Book

```python
# Enhanced AQM_MR_Live.py - AQM_PT_HFT class
def calcular_senalXY(self, datos, p0, p1):
    """
    Enhanced signal calculation using order book data for precision.
    Falls back to close prices if order book not available.
    """
    # Try to use mid-price (more accurate than close)
    mid_y = datos.get_mid_price(p0)
    mid_x = datos.get_mid_price(p1)
    
    # Fallback to close if order book not available
    if mid_y is None:
        mid_y = datos.get_valor_ultimas_velas(p0, 'close', 1)[0]
    if mid_x is None:
        mid_x = datos.get_valor_ultimas_velas(p1, 'close', 1)[0]
    
    # Calculate spread using more accurate prices
    spread = mid_y - self.beta * mid_x
    z_score = (spread - self.spread_mean) / self.spread_std
    
    # ... rest of signal logic ...
    
    # When generating entry signal, use actual bid/ask... this should go to the execution or 
    # the portfolio 
    if signal == 'LARGO':
        # LARGO SPREAD: Buy Y, Sell X
        entry_y = datos.get_best_ask(p0)  # We're buying Y (pay ask)
        entry_x = datos.get_best_bid(p1)  # We're selling X (receive bid)
        
        if entry_y[0] and entry_x[0]:
            # Check liquidity and slippage
            vwap_y = datos.get_vwap_price(p0, self.position_size_y, 'buy')
            vwap_x = datos.get_vwap_price(p1, self.position_size_x, 'sell')
            
            if vwap_y and vwap_x:
                total_slippage = vwap_y['slippage_pct'] + vwap_x['slippage_pct']
                
                if total_slippage > self.max_slippage_threshold:
                    logger.warning(f"High slippage: {total_slippage:.4f}%")
                    # Adjust position size or skip
                    return None
                
                # Use VWAP prices for more realistic entry
                precio_entrada_y = vwap_y['vwap']
                precio_entrada_x = vwap_x['vwap']
            else:
                # Fallback to best bid/ask
                precio_entrada_y = entry_y[0]
                precio_entrada_x = entry_x[0]
        else:
            # Fallback to close prices
            precio_entrada_y = mid_y
            precio_entrada_x = mid_x
    
    elif signal == 'CORTO':
        # CORTO SPREAD: Sell Y, Buy X
        entry_y = datos.get_best_bid(p0)  # We're selling Y (receive bid)
        entry_x = datos.get_best_ask(p1)  # We're buying X (pay ask)
        
        # Similar logic as LARGO...
    
    # Generate EventoSenal with precise entry prices
    return senal
```

### 4.3 Pre-Trade Checks Function

```python
def check_trading_conditions(self, datos, p0, p1):
    """
    Verifica condiciones de mercado antes de ejecutar trade.
    
    Returns:
        dict: {'ok': bool, 'reason': str, 'metrics': dict}
    """
    # Check spreads
    spread_y = datos.get_spread(p0)
    spread_x = datos.get_spread(p1)
    
    if not spread_y or not spread_x:
        return {'ok': False, 'reason': 'Order book not available', 'metrics': {}}
    
    # Wide spreads indicate illiquidity
    if spread_y['percentage'] > 0.15 or spread_x['percentage'] > 0.15:
        return {
            'ok': False, 
            'reason': f"Wide spreads: Y={spread_y['percentage']:.3f}%, X={spread_x['percentage']:.3f}%",
            'metrics': {'spread_y': spread_y, 'spread_x': spread_x}
        }
    
    # Check order book depth
    depth_y = datos.get_depth(p0, levels=10)
    depth_x = datos.get_depth(p1, levels=10)
    
    if not depth_y or not depth_x:
        return {'ok': False, 'reason': 'Insufficient depth data', 'metrics': {}}
    
    # Calculate order book imbalance
    bid_vol_y = sum(qty for _, qty in depth_y['bids'])
    ask_vol_y = sum(qty for _, qty in depth_y['asks'])
    imbalance_y = (bid_vol_y - ask_vol_y) / (bid_vol_y + ask_vol_y) if (bid_vol_y + ask_vol_y) > 0 else 0
    
    bid_vol_x = sum(qty for _, qty in depth_x['bids'])
    ask_vol_x = sum(qty for _, qty in depth_x['asks'])
    imbalance_x = (bid_vol_x - ask_vol_x) / (bid_vol_x + ask_vol_x) if (bid_vol_x + ask_vol_x) > 0 else 0
    
    metrics = {
        'spread_y': spread_y,
        'spread_x': spread_x,
        'imbalance_y': imbalance_y,
        'imbalance_x': imbalance_x,
        'bid_vol_y': bid_vol_y,
        'ask_vol_y': ask_vol_y,
        'bid_vol_x': bid_vol_x,
        'ask_vol_x': ask_vol_x
    }
    
    # Extreme imbalance might indicate one-sided pressure
    if abs(imbalance_y) > 0.8 or abs(imbalance_x) > 0.8:
        return {
            'ok': False,
            'reason': f"Extreme order book imbalance: Y={imbalance_y:.2f}, X={imbalance_x:.2f}",
            'metrics': metrics
        }
    
    return {'ok': True, 'reason': 'All checks passed', 'metrics': metrics}
```

---

## 5. Main Script Initialization

### 5.1 Enhanced Initialization in AQM_MR_Live.py

```python
if __name__ == "__main__":
    # ... existing setup ...
    
    # Initialize BinanceData (AdminDatos) - SAME AS BEFORE
    binance_data = BinanceData(
        eventos,
        lista_nemos=['DOGE', 'ADA'],  # Your cointegrated pair
        interval='1m'
    )
    
    # NEW: Subscribe to order books for both symbols
    # Use depth10 with 100ms for good balance of data/bandwidth
    binance_data.subscribe_orderbook('DOGE', depth_type='depth10', 
                                     speed='100ms', market_type='spot')
    binance_data.subscribe_orderbook('ADA', depth_type='depth10', 
                                     speed='100ms', market_type='spot')
    
    # Wait for order book synchronization
    time.sleep(3)
    
    # Verify order books are ready
    for symbol in ['DOGE', 'ADA']:
        status = binance_data.get_orderbook_status(symbol)
        if status and status['is_synchronized']:
            logger.info(f"✓ {symbol} order book synchronized")
        else:
            logger.warning(f"✗ {symbol} order book not ready")
    
    # ... rest of existing code ...
    
    # Strategy initialization - NO CHANGES NEEDED
    estrategia = AQM_PT_HFT(
        binance_data,  # Same AdminDatos interface
        eventos,
        lista_nemos=['DOGE', 'ADA'],
        zHigh=2.33,
        zLow=0.5,
        ventanaOLS=90
    )
    
    # Everything else stays the same!
```

---

## 6. Internal Implementation Details

### 6.1 Synchronization Protocol (Following Binance Spec)

```python
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
        # Step 1: Already buffering from WebSocket
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
                # First valid event must have snapshot_id in [U, u]
                if not found_valid_start:
                    if event_U <= snapshot_id + 1 <= event_u:
                        found_valid_start = True
                        valid_events.append(event)
                    else:
                        logger.error(f"Gap detected in {symbol}: snapshot_id={snapshot_id}, event=[{event_U}, {event_u}]")
                        # Retry
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
            
            # Clear buffer (no longer needed)
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
    
    Args:
        symbol (str): Símbolo (e.g., 'BTC')
        market_type (str): 'spot', 'futures', 'perpetuals'
        limit (int): Número de niveles (max 5000 para spot, 1000 para futures)
        
    Returns:
        dict: {lastUpdateId, bids: [[price, qty]], asks: [[price, qty]]}
    """
    formatted_symbol = self._format_orderbook_symbol(symbol, market_type)
    
    if market_type == 'spot':
        url = f"https://api.binance.com/api/v3/depth"
    else:  # futures or perpetuals
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

def _process_depth_update(self, event):
    """
    Procesa evento depthUpdate del WebSocket.
    
    Event format:
    {
        "e": "depthUpdate",
        "E": 1672515782136,
        "s": "BTCUSDT",
        "U": 157,
        "u": 160,
        "b": [["0.0024", "10"], ...],
        "a": [["0.0026", "100"], ...]
    }
    """
    try:
        symbol_formatted = event.get('s', '')
        
        # Find our internal symbol
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
                # Restart sync in background
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
    """
    Aplica las actualizaciones de precio al order book.
    
    Debe llamarse con ob_lock ya adquirido.
    """
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
```

### 6.2 Utility Methods

```python
def _format_orderbook_symbol(self, symbol, market_type='spot'):
    """
    Formatea símbolo para Binance.
    
    Args:
        symbol (str): 'BTC', 'ETH', etc.
        market_type (str): 'spot', 'futures', 'perpetuals'
        
    Returns:
        str: 'BTCUSDT' para spot, puede ser diferente para futures
    """
    # For spot and futures, usually same format
    return f"{symbol.upper()}{self.base_token.upper()}"

def _reverse_format_symbol(self, formatted_symbol):
    """
    Convierte 'BTCUSDT' de vuelta a 'BTC'.
    """
    # Remove base token suffix
    if formatted_symbol.endswith(self.base_token.upper()):
        return formatted_symbol[:-len(self.base_token)]
    return formatted_symbol

def _format_depth_stream(self, symbol, depth_type='depth10', speed='100ms'):
    """
    Formatea nombre del stream para suscripción.
    
    Examples:
        btcusdt@depth         # Full depth, 1000ms
        btcusdt@depth@100ms   # Full depth, 100ms
        btcusdt@depth10       # Top 10, 1000ms
        btcusdt@depth10@100ms # Top 10, 100ms
        btcusdt@bookTicker    # Best bid/ask
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
```

---

## 7. Benefits of This Design

### 7.1 Seamless Integration ✅
- **Single AdminDatos instance** per exchange
- **No changes to base class** - maintains abstraction
- **Backward compatible** - existing code continues to work
- **Same interface** for strategies - just one `BinanceData` object

### 7.2 Clean Separation ✅
- **Separate WebSocket connections** - OHLCV and order books don't interfere
- **Separate locks** - concurrent access to different data types
- **Internal OrderBook class** - not exposed to external code
- **Modular methods** - easy to test and maintain

### 7.3 Minimal Impact ✅
- **No changes to AdminDatos base class**
- **No changes to existing BinanceData methods**
- **No changes to strategy base class**
- **Optional feature** - order books only used if subscribed

### 7.4 Production Ready ✅
- **Thread-safe** - proper locking mechanisms
- **Error handling** - reconnection and resync logic
- **Binance compliant** - follows official protocol
- **Efficient** - SortedDict for O(log n) operations
- **Flexible** - supports spot, futures, perpetuals

---

## 8. Implementation Checklist

- [ ] **Install dependency**: `pip install sortedcontainers`
- [ ] **Add OrderBook class** to `Datos.py` (before BinanceData)
- [ ] **Add order book attributes** to `BinanceData.__init__()`
- [ ] **Implement order book WebSocket** methods
- [ ] **Implement synchronization** protocol
- [ ] **Implement query methods** for strategies
- [ ] **Test with single symbol**
- [ ] **Test with multiple symbols**
- [ ] **Update AQM_MR_Live.py** to use order book data
- [ ] **Test end-to-end** with live market data
- [ ] **Create usage examples**
- [ ] **Performance testing**

---

## 9. Usage Example (Complete Flow)

```python
# In AQM_MR_Live.py - Main Script

import queue
from Eventos import EventoMdo, EventoSenal
from Datos import BinanceData
from Estrategia import AQM_PT_HFT
import time

# Setup event queue
eventos = queue.Queue()

# Initialize BinanceData (AdminDatos) - handles both OHLCV and order books
lista_nemos = ['DOGE', 'ADA']
binance_data = BinanceData(
    eventos,
    lista_nemos=lista_nemos,
    interval='1m'
)

# Subscribe to order books (NEW - but seamless)
for symbol in lista_nemos:
    binance_data.subscribe_orderbook(
        symbol, 
        depth_type='depth10',  # Good balance
        speed='100ms',         # Fast updates
        market_type='spot'
    )

# Wait for synchronization
time.sleep(3)

# Verify order books are ready
for symbol in lista_nemos:
    status = binance_data.get_orderbook_status(symbol)
    if status and status['is_synchronized']:
        print(f"✓ {symbol} order book ready")
        
        # Example: Check current market conditions
        spread = binance_data.get_spread(symbol)
        if spread:
            print(f"  Spread: {spread['percentage']:.3f}%")
            print(f"  Best Bid: {spread['bid']}")
            print(f"  Best Ask: {spread['ask']}")

# Initialize strategy - SAME AS BEFORE
estrategia = AQM_PT_HFT(
    binance_data,  # Same AdminDatos interface!
    eventos,
    lista_nemos=lista_nemos,
    zHigh=2.33,
    zLow=0.5,
    ventanaOLS=90
)

# Strategy will now automatically use order book data when available
# Falls back to close prices if order book not ready
```

---

## 10. Summary

This revised design:

1. **Maintains single AdminDatos per exchange** ✅
2. **No changes to base AdminDatos class** ✅
3. **OrderBook as internal helper class** ✅
4. **All methods added to BinanceData** ✅
5. **Seamless integration with existing code** ✅
6. **Minimal impact on other components** ✅

**Next Step**: Proceed with implementation or request further modifications!
