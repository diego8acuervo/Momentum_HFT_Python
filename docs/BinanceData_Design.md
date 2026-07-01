# BinanceData Class - WebSocket Streaming Architecture

## Overview

The `BinanceData` class provides real-time cryptocurrency market data from Binance via WebSocket streams. It implements a robust, thread-safe architecture for handling high-frequency kline (candlestick) data updates with proper error handling and connection management.

## Architecture Design

### Key Components

1. **WebSocket Connection Manager**
   - Maintains persistent connection to Binance WebSocket API
   - Handles automatic reconnection on failures
   - Manages subscription lifecycle
   - Implements rate limiting compliance (5 messages/second)

2. **Data Buffer & Generator**
   - Thread-safe data storage using `threading.Lock`
   - Generator-based access for memory efficiency
   - Maintains circular buffer for recent candles
   - Updates only on completed candles to avoid partial data

3. **Event System Integration**
   - Publishes `EventoMdo` to queue on new completed candles
   - Enables event-driven strategy execution
   - Maintains backtest compatibility

4. **Connection Resilience**
   - Automatic ping/pong handling
   - 24-hour reconnection logic
   - Exponential backoff on failures
   - Graceful shutdown mechanisms

## Class Structure

### Core Attributes

```python
self.eventos: queue.Queue          # Event queue for strategy notifications
self.lista_nemos: List[str]         # List of symbols to track (e.g., ['BTC', 'ETH'])
self.base_token: str                # Base currency (default: 'USDT')
self.datos_nemo: Dict               # Historical data storage
self.ultimo_dato_nemo: Dict         # Latest completed candle per symbol
self.ws: WebSocketApp              # WebSocket connection instance
self.ws_thread: threading.Thread    # Background thread for WebSocket
self.data_lock: threading.Lock      # Thread safety for data access
self.is_running: bool               # Connection state flag
self.reconnect_attempts: int        # Reconnection counter
self.max_reconnect_attempts: int    # Max reconnection tries (default: 10)
```

### Methods

#### Connection Management

**`connect_websocket(interval: str = '1m') -> None`**
- Establishes WebSocket connection to Binance streams
- Subscribes to kline streams for all symbols in `lista_nemos`
- Launches connection in separate daemon thread
- **Parameters:**
  - `interval`: Kline interval ('1s', '1m', '5m', '15m', '30m', '1h', '4h', '1d')

**`disconnect_websocket() -> None`**
- Gracefully closes WebSocket connection
- Cleans up threads and resources
- Safe to call multiple times

**`reconnect_websocket() -> None`**
- Implements exponential backoff reconnection strategy
- Automatically called on connection failures
- Respects `max_reconnect_attempts` limit

#### Data Access Methods

**`get_latest_kline(symbol: str) -> Optional[Dict]`**
- Returns the most recent **completed** candle for a symbol
- Thread-safe read operation
- Returns `None` if no data available
- **Returns:** Dictionary with keys: `open_time`, `open`, `high`, `low`, `close`, `volume`

**`get_kline_generator(symbol: str, lookback: int = 100) -> Generator`**
- Yields klines as they complete in real-time
- Maintains buffer of recent candles
- Memory-efficient streaming access
- **Parameters:**
  - `symbol`: Trading pair symbol (e.g., 'BTC', 'ETH')
  - `lookback`: Number of historical candles to maintain
- **Yields:** Dictionary with OHLCV data

**`get_all_latest_klines() -> Dict[str, Dict]`**
- Returns latest completed candle for all tracked symbols
- Useful for multi-symbol strategies
- Thread-safe operation

**`subscribe_symbol(symbol: str, interval: str = '1m') -> bool`**
- Dynamically add new symbol to existing connection
- Respects 1024 stream limit per connection
- Sends SUBSCRIBE message via WebSocket
- **Returns:** `True` if successful

**`unsubscribe_symbol(symbol: str, interval: str = '1m') -> bool`**
- Remove symbol from active streams
- Frees up connection slots
- Sends UNSUBSCRIBE message via WebSocket
- **Returns:** `True` if successful

#### WebSocket Event Handlers

**`on_open(ws: WebSocketApp) -> None`**
- Called when connection established
- Logs connection success
- Resets reconnection counter

**`on_message(ws: WebSocketApp, message: str) -> None`**
- Processes incoming kline updates
- Parses JSON payload
- Updates `ultimo_dato_nemo` only for completed candles (`x=true`)
- Triggers event queue notification
- **Message Format:**
```json
{
  "e": "kline",
  "E": 1672515782136,
  "s": "BTCUSDT",
  "k": {
    "t": 1672515780000,
    "T": 1672515839999,
    "s": "BTCUSDT",
    "i": "1m",
    "o": "0.0010",
    "c": "0.0020",
    "h": "0.0025",
    "l": "0.0015",
    "v": "1000",
    "x": true
  }
}
```

**`on_error(ws: WebSocketApp, error: Exception) -> None`**
- Logs WebSocket errors
- Triggers reconnection logic
- Does not crash main process

**`on_close(ws: WebSocketApp, close_status_code: int, close_msg: str) -> None`**
- Handles connection closure
- Logs disconnect reason
- Initiates reconnection if unexpected

**`on_ping(ws: WebSocketApp, message: str) -> None`**
- Receives ping frames from server (every 20 seconds)
- Automatically sends pong response
- Prevents 60-second timeout disconnect

**`on_pong(ws: WebSocketApp, message: str) -> None`**
- Confirms pong frame reception
- Logs heartbeat for monitoring

#### Helper Methods

**`_format_symbol(nemo: str) -> str`**
- Converts symbol to Binance format
- Example: 'BTC' → 'btcusdt'
- Ensures lowercase as per Binance requirements

**`_parse_kline(kline_data: Dict) -> Dict`**
- Extracts OHLCV from raw kline object
- Converts timestamps to datetime
- Ensures float types for numeric values

**`_send_subscribe(symbols: List[str], interval: str) -> None`**
- Sends dynamic subscription message
- Format: `{"method": "SUBSCRIBE", "params": [...], "id": <id>}`
- Respects 5 messages/second limit

**`_send_unsubscribe(symbols: List[str], interval: str) -> None`**
- Sends dynamic unsubscription message
- Format: `{"method": "UNSUBSCRIBE", "params": [...], "id": <id>}`

## Data Flow

```
Binance WebSocket Stream
        ↓
on_message() handler
        ↓
Parse JSON payload
        ↓
Check if candle closed (x=true)
        ↓
Update ultimo_dato_nemo with lock
        ↓
Append to datos_nemo buffer
        ↓
Put EventoMdo() in queue
        ↓
Strategy processes event
        ↓
Call get_latest_kline() or generator
        ↓
Read data with lock protection
```

## Usage Examples

### Basic Setup

```python
import queue
from Datos import BinanceData

# Initialize event queue and symbol list
eventos = queue.Queue()
symbols = ['BTC', 'ETH', 'ADA', 'SOL']

# Create data connection
data_conn = BinanceData(eventos, symbols)

# Data automatically streams in background
```

### Access Latest Candle

```python
# Get most recent completed candle for BTC
latest_btc = data_conn.get_latest_kline('BTC')
print(f"BTC Close: {latest_btc['close']}")
```

### Real-time Streaming

```python
# Stream klines as they complete
for kline in data_conn.get_kline_generator('BTC', lookback=50):
    print(f"Time: {kline['open_time']}, Close: {kline['close']}")
    
    # Your strategy logic here
    if kline['close'] > threshold:
        place_order()
```

### Multi-Symbol Monitoring

```python
# Get latest data for all symbols
all_data = data_conn.get_all_latest_klines()
for symbol, kline in all_data.items():
    print(f"{symbol}: {kline['close']}")
```

### Dynamic Subscription

```python
# Add new symbol during runtime
data_conn.subscribe_symbol('DOGE', interval='5m')

# Remove symbol to free connection
data_conn.unsubscribe_symbol('ADA', interval='1m')
```

### Clean Shutdown

```python
# Gracefully close connection
data_conn.disconnect_websocket()
```

## Best Practices

1. **Connection Limits**
   - Maximum 1024 streams per connection
   - Use single connection for multiple symbols
   - Consider connection pooling for >100 symbols

2. **Rate Limiting**
   - Limit to 5 control messages per second
   - Batch subscribe/unsubscribe operations
   - Avoid rapid reconnections

3. **Thread Safety**
   - Always use provided methods for data access
   - Don't directly modify `datos_nemo` or `ultimo_dato_nemo`
   - Use locks when implementing custom methods

4. **Memory Management**
   - Set reasonable `lookback` values in generators
   - Periodically clear old data from `datos_nemo`
   - Monitor buffer sizes for high-frequency intervals

5. **Error Handling**
   - Implement try-except in strategy callbacks
   - Monitor reconnection attempts
   - Log connection state changes

6. **24-Hour Reconnection**
   - Binance disconnects after 24 hours
   - Automatic reconnection is handled
   - Ensure no critical operations during reconnect

## Performance Considerations

- **Update Frequency:** 
  - 1000ms for '1s' interval
  - 2000ms for other intervals
  
- **Latency:** Typically 50-200ms from exchange to client

- **Bandwidth:** ~1KB per kline update

- **CPU:** Minimal (JSON parsing + dict operations)

- **Memory:** ~1MB per 10,000 candles per symbol

## Error Codes & Troubleshooting

| Error | Cause | Solution |
|-------|-------|----------|
| Connection refused | Invalid endpoint | Check URL format |
| 429 Rate limit | Too many messages | Implement backoff |
| Disconnected after 24h | Normal behavior | Wait for auto-reconnect |
| Symbol not found | Invalid ticker | Verify symbol exists on Binance |
| x=false candles | Incomplete data | Filter for x=true only |

## Future Enhancements

- [ ] Support for timezone offset klines
- [ ] Orderbook depth stream integration  
- [ ] Trade stream for tick data
- [ ] Aggregate trade stream
- [ ] Connection pool for >1000 symbols
- [ ] Redis-based distributed caching
- [ ] Historical gap filling on reconnect
- [ ] WebSocket compression support

## References

- [Binance WebSocket Streams Documentation](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams)
- [Binance API Rate Limits](https://developers.binance.com/docs/binance-spot-api-docs/general-info)
- [websocket-client Library](https://websocket-client.readthedocs.io/)
