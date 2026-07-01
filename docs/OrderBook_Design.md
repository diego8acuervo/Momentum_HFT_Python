# Order Book Management System - Design Document

## 1. Overview

This document outlines the design of a comprehensive order book management system for the BinanceData class, following Binance's official recommendations for maintaining accurate local order books.

### Key Features
- ✅ Support for Spot, Futures, and Perpetuals markets
- ✅ Accurate synchronization using Binance's recommended protocol
- ✅ Thread-safe operations for concurrent access
- ✅ Efficient price level management with sorted data structures
- ✅ Comprehensive query methods for trading strategies
- ✅ Automatic reconnection and recovery
- ✅ Multiple subscription types (full depth, partial depth, book ticker)

---

## 2. Architecture

### 2.1 OrderBook Internal Class

```python
from sortedcontainers import SortedDict

class OrderBook:
    """
    Represents a local order book for a single symbol.
    Uses SortedDict for O(log n) operations on price levels.
    """
    
    def __init__(self, symbol):
        self.symbol = symbol
        self.bids = SortedDict()  # price -> quantity (descending order)
        self.asks = SortedDict()  # price -> quantity (ascending order)
        self.last_update_id = 0
        self.is_synchronized = False
        self.update_lock = threading.Lock()
        
    def update_bid(self, price, quantity):
        """Update or remove a bid price level"""
        
    def update_ask(self, price, quantity):
        """Update or remove an ask price level"""
        
    def get_best_bid(self):
        """Return highest bid (price, quantity)"""
        
    def get_best_ask(self):
        """Return lowest ask (price, quantity)"""
        
    def get_depth(self, levels=10):
        """Return top N levels for both sides"""
        
    def clear(self):
        """Clear all price levels"""
```

### 2.2 BinanceData Extensions

New attributes to add:
```python
class BinanceData(AdminDatos):
    def __init__(self, eventos, lista_nemos, interval='1m'):
        # ... existing attributes ...
        
        # Order book management
        self.order_books = {}           # {symbol: OrderBook}
        self.order_book_buffers = {}    # {symbol: [events]} - Buffer during sync
        self.order_book_ws = None       # Separate WebSocket for order books
        self.ob_subscription_id = 0     # Subscription ID counter
        self.ob_lock = threading.Lock() # Lock for order book operations
```

---

## 3. Synchronization Protocol (Binance Official)

Following: https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams#how-to-manage-a-local-order-book-correctly

### Step-by-Step Process

```
1. Connect to WebSocket depth stream: wss://stream.binance.com:9443/ws/{symbol}@depth
2. Start buffering all incoming events, note the first event's U (first update ID)
3. Fetch snapshot via REST: GET /api/v3/depth?symbol={SYMBOL}&limit=5000
4. Validate: If snapshot.lastUpdateId < first_buffered_event.U, retry snapshot
5. Discard buffered events where event.u <= snapshot.lastUpdateId
6. Verify: First valid buffered event must have snapshot.lastUpdateId in [event.U, event.u]
7. Initialize local order book with snapshot data
8. Apply buffered events in order
9. Continue applying new events in real-time
```

### Update Validation Logic

```python
def _validate_and_apply_update(event):
    """
    Apply update event following Binance protocol
    """
    # Event format:
    # {
    #   "e": "depthUpdate",
    #   "E": 1672515782136,  # Event time
    #   "s": "BNBBTC",       # Symbol
    #   "U": 157,            # First update ID in event
    #   "u": 160,            # Final update ID in event
    #   "b": [["price", "qty"], ...],  # Bids to update
    #   "a": [["price", "qty"], ...]   # Asks to update
    # }
    
    if event.u < book.last_update_id:
        # Old event, ignore
        return False
        
    if event.U > book.last_update_id + 1:
        # Gap detected, missed events - MUST resynchronize
        logger.error(f"Update gap detected for {symbol}")
        resync_orderbook(symbol)
        return False
        
    # Apply updates
    for price, qty in event.bids:
        if qty == 0:
            remove_bid(price)
        else:
            update_bid(price, qty)
            
    for price, qty in event.asks:
        if qty == 0:
            remove_ask(price)
        else:
            update_ask(price, qty)
            
    book.last_update_id = event.u
    return True
```

---

## 4. Stream Types and Endpoints

### 4.1 Diff Depth Stream (Full Updates)
```
Stream: <symbol>@depth or <symbol>@depth@100ms
Example: btcusdt@depth or btcusdt@depth@100ms
Update Speed: 1000ms (default) or 100ms
Use Case: Complete order book updates, high accuracy
```

### 4.2 Partial Depth Stream (Top N Levels)
```
Stream: <symbol>@depth5, @depth10, @depth20
Example: btcusdt@depth5@100ms
Levels: 5, 10, or 20 price levels per side
Update Speed: 1000ms or 100ms
Use Case: Lower bandwidth, sufficient for most strategies
```

### 4.3 Book Ticker Stream (Best Bid/Ask Only)
```
Stream: <symbol>@bookTicker
Example: btcusdt@bookTicker
Data: Best bid/ask price and quantity only
Update Speed: Real-time
Use Case: Quick spread monitoring, minimal data
```

### 4.4 Market Type URLs

```python
ENDPOINTS = {
    'spot': {
        'ws': 'wss://stream.binance.com:9443',
        'rest': 'https://api.binance.com'
    },
    'futures': {
        'ws': 'wss://fstream.binance.com',
        'rest': 'https://fapi.binance.com'
    },
    'perpetuals': {
        'ws': 'wss://fstream.binance.com',  # Same as futures
        'rest': 'https://fapi.binance.com'
    }
}
```

---

## 5. Method Design

### 5.1 Subscription Methods

```python
def subscribe_orderbook(self, symbol, depth_type='full', speed='1000ms', 
                       market_type='spot'):
    """
    Subscribe to order book updates for a symbol.
    
    Args:
        symbol (str): Trading symbol (e.g., 'BTC', 'DOGE')
        depth_type (str): 'full', 'depth5', 'depth10', 'depth20', 'ticker'
        speed (str): '1000ms' or '100ms' (not applicable for 'ticker')
        market_type (str): 'spot', 'futures', or 'perpetuals'
        
    Returns:
        bool: Success status
        
    Example:
        binance_data.subscribe_orderbook('BTC', depth_type='depth10', 
                                        speed='100ms', market_type='spot')
    """
    
def unsubscribe_orderbook(self, symbol):
    """
    Unsubscribe from order book updates.
    
    Args:
        symbol (str): Trading symbol
        
    Returns:
        bool: Success status
    """
```

### 5.2 Initialization Methods

```python
def initialize_orderbook(self, symbol, market_type='spot'):
    """
    Initialize order book following Binance synchronization protocol.
    
    This method:
    1. Starts buffering WebSocket events
    2. Fetches REST API snapshot
    3. Validates and synchronizes
    4. Applies buffered events
    
    Args:
        symbol (str): Trading symbol
        market_type (str): 'spot', 'futures', or 'perpetuals'
        
    Returns:
        bool: Success status
    """
    
def _fetch_orderbook_snapshot(self, symbol, market_type='spot', limit=5000):
    """
    Fetch order book snapshot via REST API.
    
    Args:
        symbol (str): Trading symbol
        market_type (str): Market type
        limit (int): Number of levels (max 5000)
        
    Returns:
        dict: {lastUpdateId, bids: [[price, qty]], asks: [[price, qty]]}
    """
    
def _sync_orderbook(self, symbol, snapshot):
    """
    Synchronize local order book with snapshot and buffered events.
    
    Args:
        symbol (str): Trading symbol
        snapshot (dict): REST API snapshot data
        
    Returns:
        bool: Success status
    """
```

### 5.3 Update Processing Methods

```python
def _process_depth_update(self, event):
    """
    Process a single depthUpdate event.
    
    Args:
        event (dict): WebSocket event data
        
    Returns:
        bool: Success status
    """
    
def _validate_update_sequence(self, symbol, event):
    """
    Validate update sequence numbers to ensure no gaps.
    
    Args:
        symbol (str): Trading symbol
        event (dict): Update event
        
    Returns:
        tuple: (is_valid, action) where action is 'apply', 'ignore', or 'resync'
    """
    
def _apply_price_levels(self, symbol, bids, asks):
    """
    Apply price level updates to the order book.
    
    Rules:
    - If quantity = 0: remove price level
    - Otherwise: update or insert price level
    
    Args:
        symbol (str): Trading symbol
        bids (list): [[price, qty], ...]
        asks (list): [[price, qty], ...]
    """
```

### 5.4 Query Methods (For Trading Strategies)

```python
def get_best_bid(self, symbol):
    """
    Get the best (highest) bid price and quantity.
    
    Args:
        symbol (str): Trading symbol
        
    Returns:
        tuple: (price, quantity) or (None, None) if not available
        
    Example:
        price, qty = binance_data.get_best_bid('BTC')
        if price:
            print(f"Best bid: {price} with {qty} quantity")
    """
    
def get_best_ask(self, symbol):
    """
    Get the best (lowest) ask price and quantity.
    
    Args:
        symbol (str): Trading symbol
        
    Returns:
        tuple: (price, quantity) or (None, None) if not available
    """
    
def get_mid_price(self, symbol):
    """
    Calculate mid price: (best_bid + best_ask) / 2
    
    Args:
        symbol (str): Trading symbol
        
    Returns:
        float: Mid price or None if not available
        
    Example:
        mid = binance_data.get_mid_price('BTC')
        if mid:
            print(f"Mid price: {mid}")
    """
    
def get_spread(self, symbol):
    """
    Calculate spread: best_ask - best_bid
    
    Args:
        symbol (str): Trading symbol
        
    Returns:
        dict: {'absolute': spread, 'percentage': spread_pct, 
               'bid': best_bid, 'ask': best_ask}
        
    Example:
        spread_info = binance_data.get_spread('BTC')
        print(f"Spread: {spread_info['absolute']} ({spread_info['percentage']}%)")
    """
    
def get_depth(self, symbol, levels=10):
    """
    Get top N levels of the order book.
    
    Args:
        symbol (str): Trading symbol
        levels (int): Number of levels to return
        
    Returns:
        dict: {
            'bids': [[price, qty], ...],  # Sorted descending
            'asks': [[price, qty], ...],  # Sorted ascending
            'lastUpdateId': int
        }
        
    Example:
        depth = binance_data.get_depth('BTC', levels=5)
        for price, qty in depth['bids']:
            print(f"Bid: {price} @ {qty}")
    """
    
def get_volume_at_price(self, symbol, target_price, side='both', tolerance=0.0001):
    """
    Get volume available at or near a specific price.
    
    Args:
        symbol (str): Trading symbol
        target_price (float): Price to query
        side (str): 'bid', 'ask', or 'both'
        tolerance (float): Price tolerance as percentage (0.0001 = 0.01%)
        
    Returns:
        dict: {'bid_volume': float, 'ask_volume': float, 'total': float}
        
    Example:
        vol = binance_data.get_volume_at_price('BTC', 50000.0, side='both')
        print(f"Volume at 50000: {vol['total']}")
    """
    
def get_total_volume(self, symbol, side, levels=5):
    """
    Get total volume available in top N levels.
    
    Args:
        symbol (str): Trading symbol
        side (str): 'bid' or 'ask'
        levels (int): Number of levels to sum
        
    Returns:
        float: Total volume
        
    Example:
        bid_volume = binance_data.get_total_volume('BTC', 'bid', levels=10)
        print(f"Total bid volume (top 10): {bid_volume}")
    """
    
def get_vwap_price(self, symbol, target_volume, side):
    """
    Calculate Volume-Weighted Average Price for a given order size.
    
    Useful for estimating execution price and slippage.
    
    Args:
        symbol (str): Trading symbol
        target_volume (float): Order size in base currency
        side (str): 'buy' (use asks) or 'sell' (use bids)
        
    Returns:
        dict: {
            'vwap': float,              # Volume-weighted average price
            'total_cost': float,        # Total cost in quote currency
            'levels_used': int,         # Number of price levels needed
            'worst_price': float,       # Worst execution price
            'slippage_pct': float      # Slippage vs best price
        }
        
    Example:
        # Buying 10 BTC
        vwap_info = binance_data.get_vwap_price('BTC', 10.0, 'buy')
        print(f"VWAP: {vwap_info['vwap']}")
        print(f"Slippage: {vwap_info['slippage_pct']}%")
        
        # Selling 5 BTC
        vwap_info = binance_data.get_vwap_price('BTC', 5.0, 'sell')
    """
    
def get_orderbook_status(self, symbol):
    """
    Get current status of order book.
    
    Args:
        symbol (str): Trading symbol
        
    Returns:
        dict: {
            'is_synchronized': bool,
            'last_update_id': int,
            'bid_levels': int,
            'ask_levels': int,
            'last_update_time': datetime
        }
    """
```

### 5.5 Utility Methods

```python
def _get_ws_url(self, market_type='spot'):
    """Get WebSocket URL for market type"""
    
def _get_rest_url(self, market_type='spot'):
    """Get REST API URL for market type"""
    
def _format_depth_stream(self, symbol, depth_type='full', speed='1000ms'):
    """
    Format stream name for order book subscription.
    
    Examples:
        btcusdt@depth         # Full depth, 1000ms
        btcusdt@depth@100ms   # Full depth, 100ms
        btcusdt@depth10       # Top 10 levels, 1000ms
        btcusdt@depth5@100ms  # Top 5 levels, 100ms
        btcusdt@bookTicker    # Best bid/ask only
    """
```

---

## 6. Integration with Pairs Trading Strategy

### 6.1 Current Strategy Code (AQM_MR_Live.py)

```python
class AQM_PT_HFT(Estrategia):
    def calcular_senalXY(self, datos, p0, p1):
        # Current approach uses close prices
        close_y = datos.get_valor_ultimas_velas(p0, 'close', 1)[0]
        close_x = datos.get_valor_ultimas_velas(p1, 'close', 1)[0]
```

### 6.2 Enhanced with Order Book Data

```python
class AQM_PT_HFT(Estrategia):
    def calcular_senalXY(self, datos, p0, p1):
        # Use mid-price for signal calculation (more accurate than close)
        mid_y = datos.get_mid_price(p0)
        mid_x = datos.get_mid_price(p1)
        
        if mid_y is None or mid_x is None:
            # Fallback to close if order book not available
            mid_y = datos.get_valor_ultimas_velas(p0, 'close', 1)[0]
            mid_x = datos.get_valor_ultimas_velas(p1, 'close', 1)[0]
        
        # ... calculate z-score using mid prices ...
        
        # When generating entry signal
        if signal == 'LARGO':
            # We're buying Y (go to ask side), selling X (go to bid side)
            entry_price_y = datos.get_best_ask(p0)
            entry_price_x = datos.get_best_bid(p1)
            
            # Check liquidity and estimate slippage
            vwap_y = datos.get_vwap_price(p0, position_size_y, 'buy')
            vwap_x = datos.get_vwap_price(p1, position_size_x, 'sell')
            
            estimated_slippage = (vwap_y['vwap'] - entry_price_y[0]) / entry_price_y[0]
            
            if estimated_slippage > max_slippage_threshold:
                logger.warning(f"High slippage detected: {estimated_slippage}%")
                # Adjust position size or skip trade
                
        elif signal == 'CORTO':
            # We're selling Y (go to bid side), buying X (go to ask side)
            entry_price_y = datos.get_best_bid(p0)
            entry_price_x = datos.get_best_ask(p1)
```

### 6.3 Spread Monitoring

```python
def check_trading_conditions(self, datos, p0, p1):
    """
    Check if trading conditions are favorable.
    """
    # Check spreads
    spread_y = datos.get_spread(p0)
    spread_x = datos.get_spread(p1)
    
    # Wide spreads indicate low liquidity or high volatility
    if spread_y['percentage'] > 0.1 or spread_x['percentage'] > 0.1:
        logger.warning(f"Wide spreads detected: Y={spread_y['percentage']}%, X={spread_x['percentage']}%")
        return False
        
    # Check depth
    depth_y = datos.get_depth(p0, levels=10)
    depth_x = datos.get_depth(p1, levels=10)
    
    total_bid_volume_y = sum(qty for _, qty in depth_y['bids'])
    total_ask_volume_y = sum(qty for _, qty in depth_y['asks'])
    
    # Imbalanced book might indicate one-sided pressure
    imbalance_y = abs(total_bid_volume_y - total_ask_volume_y) / (total_bid_volume_y + total_ask_volume_y)
    
    if imbalance_y > 0.7:
        logger.info(f"Order book imbalance detected: {imbalance_y}")
        
    return True
```

---

## 7. Error Handling and Recovery

### 7.1 Connection Issues

```python
def _on_orderbook_error(self, ws, error):
    """
    Handle WebSocket errors.
    - Log error
    - Attempt reconnection with exponential backoff
    - Mark order books as unsynchronized
    """
    
def _on_orderbook_close(self, ws, close_status_code, close_msg):
    """
    Handle WebSocket closure.
    - Log closure reason
    - Schedule reconnection
    - Clear buffers
    """
```

### 7.2 Synchronization Failures

```python
def resync_orderbook(self, symbol):
    """
    Resynchronize order book from scratch.
    
    Called when:
    - Update gap detected (missed events)
    - Snapshot fetch failed
    - Validation failed
    """
    with self.ob_lock:
        # Clear existing data
        self.order_books[symbol].clear()
        self.order_books[symbol].is_synchronized = False
        self.order_book_buffers[symbol] = []
        
        # Restart initialization
        self.initialize_orderbook(symbol)
```

### 7.3 Data Validation

```python
def _validate_orderbook_data(self, symbol):
    """
    Validate order book integrity.
    
    Checks:
    - Best bid < best ask (no crossed book)
    - No negative quantities
    - Reasonable price levels
    - Update ID continuity
    """
```

---

## 8. Performance Considerations

### 8.1 Memory Usage

```python
# Limit order book depth to prevent memory issues
MAX_DEPTH_LEVELS = 1000  # Keep top 1000 levels per side

def _trim_orderbook(self, symbol):
    """
    Trim order book to maximum depth.
    Called periodically or after large updates.
    """
```

### 8.2 Update Frequency

```
- Full Depth (@depth): ~1-10 updates/second depending on symbol
- 100ms Depth (@depth@100ms): Up to 10 updates/second
- Partial Depth (@depth10): ~1-10 updates/second, smaller payload
- Book Ticker (@bookTicker): 100+ updates/second (best bid/ask only)
```

**Recommendation**: 
- High-frequency strategies: Use `@depth10@100ms` or `@bookTicker`
- Standard strategies: Use `@depth20` or `@depth`
- Memory constrained: Use `@depth5` or `@bookTicker`

### 8.3 Thread Safety

```python
# All query methods use locks
def get_best_bid(self, symbol):
    with self.ob_lock:
        if symbol not in self.order_books:
            return None, None
        return self.order_books[symbol].get_best_bid()
```

---

## 9. Testing Strategy

### 9.1 Unit Tests

```python
def test_orderbook_initialization():
    """Test snapshot + buffer synchronization"""
    
def test_update_sequence_validation():
    """Test gap detection and handling"""
    
def test_price_level_updates():
    """Test bid/ask updates and removals"""
    
def test_vwap_calculation():
    """Test volume-weighted average price"""
```

### 9.2 Integration Tests

```python
def test_live_orderbook_sync():
    """Test real Binance connection and sync"""
    
def test_market_type_switching():
    """Test spot vs futures vs perpetuals"""
    
def test_strategy_integration():
    """Test with actual pairs trading strategy"""
```

---

## 10. Implementation Checklist

- [ ] Install `sortedcontainers` package: `pip install sortedcontainers`
- [ ] Create `OrderBook` class with SortedDict
- [ ] Add order book attributes to `BinanceData.__init__()`
- [ ] Implement subscription methods
- [ ] Implement synchronization protocol
- [ ] Implement update processing with validation
- [ ] Implement query methods for strategies
- [ ] Add error handling and recovery
- [ ] Create comprehensive tests
- [ ] Update `AQM_MR_Live.py` to use order book data
- [ ] Create usage examples
- [ ] Document best practices

---

## 11. Usage Examples

### Example 1: Basic Order Book Subscription

```python
from Datos import BinanceData
import queue

# Setup
eventos = queue.Queue()
binance_data = BinanceData(eventos, ['BTC', 'ETH'], interval='1m')

# Subscribe to order books
binance_data.subscribe_orderbook('BTC', depth_type='depth10', speed='100ms', market_type='spot')
binance_data.subscribe_orderbook('ETH', depth_type='depth10', speed='100ms', market_type='spot')

# Wait for synchronization
import time
time.sleep(2)

# Query order book
best_bid, bid_qty = binance_data.get_best_bid('BTC')
best_ask, ask_qty = binance_data.get_best_ask('BTC')
mid_price = binance_data.get_mid_price('BTC')

print(f"BTC Best Bid: {best_bid} @ {bid_qty}")
print(f"BTC Best Ask: {best_ask} @ {ask_qty}")
print(f"BTC Mid Price: {mid_price}")
```

### Example 2: Slippage Estimation

```python
# Calculate VWAP for a 10 BTC buy order
vwap_info = binance_data.get_vwap_price('BTC', 10.0, 'buy')

print(f"VWAP: ${vwap_info['vwap']:.2f}")
print(f"Total Cost: ${vwap_info['total_cost']:.2f}")
print(f"Levels Used: {vwap_info['levels_used']}")
print(f"Slippage: {vwap_info['slippage_pct']:.4f}%")

# Decision logic
if vwap_info['slippage_pct'] > 0.05:  # 0.05% threshold
    print("Slippage too high, use limit order")
else:
    print("Acceptable slippage, can use market order")
```

### Example 3: Spread Monitoring

```python
# Monitor spread changes
spread_info = binance_data.get_spread('BTC')

print(f"Absolute Spread: ${spread_info['absolute']:.2f}")
print(f"Percentage Spread: {spread_info['percentage']:.4f}%")
print(f"Best Bid: ${spread_info['bid']}")
print(f"Best Ask: ${spread_info['ask']}")

# Wide spread indicates poor liquidity
if spread_info['percentage'] > 0.1:
    print("Wide spread detected - low liquidity or high volatility")
```

### Example 4: Depth Analysis

```python
# Get top 5 levels
depth = binance_data.get_depth('BTC', levels=5)

print("Top 5 Bids:")
for price, qty in depth['bids']:
    print(f"  {price} @ {qty}")

print("Top 5 Asks:")
for price, qty in depth['asks']:
    print(f"  {price} @ {qty}")

# Calculate total volume
total_bid_volume = binance_data.get_total_volume('BTC', 'bid', levels=5)
total_ask_volume = binance_data.get_total_volume('BTC', 'ask', levels=5)

print(f"Total Bid Volume (top 5): {total_bid_volume}")
print(f"Total Ask Volume (top 5): {total_ask_volume}")
```

---

## 12. Next Steps

1. **Review this design document** and provide feedback
2. **Implement OrderBook class** in `Datos.py`
3. **Implement BinanceData methods** following the design
4. **Create comprehensive tests**
5. **Update pairs trading strategy** to use order book data
6. **Create usage examples**
7. **Performance testing** with real market data

---

## References

- [Binance WebSocket Streams Documentation](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams)
- [How to Manage a Local Order Book Correctly](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams#how-to-manage-a-local-order-book-correctly)
- [SortedContainers Documentation](http://www.grantjenks.com/docs/sortedcontainers/)
