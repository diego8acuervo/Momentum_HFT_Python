# CoinApiDs Quick Reference

## Initialization

```python
from Datos import CoinApiDs
import queue

eventos = queue.Queue()
exchanges = ['BINANCEFTS']
book_types = ['PERP']
symbols = ['BTC', 'ETH']

data = CoinApiDs(eventos, exchanges, book_types, symbols, interval='1MIN')
```

## Get Latest Data

```python
# Get latest candle (dict)
latest = data.get_ultima_vela('BTC')
# {'open': 50000, 'high': 51000, 'low': 49000, 'close': 50500, 'volume': 1000, ...}

# Get specific value (float)
close = data.get_valor_ultima_vela('BTC', 'close')  # 50500.0

# Get last N candles (DataFrame)
last_10 = data.get_ultimas_velas('BTC', N=10)

# Get timestamp
time = data.get_tiempo_ultima_vela('BTC')  # datetime object
```

## Stream with Generator

```python
for latest, buffer in data.get_kline_generator('BTC', lookback=100):
    # latest: dict with current candle
    # buffer: DataFrame with last 100 candles
    
    # Calculate indicators
    sma = buffer['close'].mean()
    
    # Strategy logic
    if latest['close'] > sma:
        print("Buy signal!")
```

## Process Events

```python
while True:
    evento = eventos.get()
    
    if evento.type == 'MERCADO':
        print(f"{evento.symbol}: ${evento.close}")
        
        if evento.typeEvent == 'ohlcv':
            # Process candlestick data
            pass
        elif evento.typeEvent == 'trade':
            # Process trade data
            pass
        elif evento.typeEvent == 'quote':
            # Process quote data
            pass
```

## Symbol ID Format

```
{EXCHANGE}_{BOOKTYPE}_{SYMBOL}_{QUOTEASSET}

Examples:
- BINANCEFTS_PERP_BTC_USDT
- KRAKENFTS_PERP_ETH_USDT
- BINANCE_SPOT_BTC_USDT
```

## Supported Intervals

```python
# Seconds: 1SEC, 5SEC, 10SEC, 15SEC, 30SEC
# Minutes: 1MIN, 5MIN, 15MIN, 30MIN
# Hours: 1HRS, 4HRS, 12HRS
# Days: 1DAY, 7DAY
# Months: 1MTH, 3MTH
# Years: 1YRS
```

## EventoMdo Attributes

### OHLCV Event
- `nemo`: Symbol ('BTC')
- `msg_type`: 'ohlcv'
- `timestamp`: datetime
- `open_time`: Candle start
- `close_time`: Candle end
- `open`: Opening price
- `high`: Highest price
- `low`: Lowest price
- `close`: Closing price
- `volume`: Trading volume
- `trades`: Number of trades
- `interval`: Time interval

### Trade Event
- `nemo`: Symbol
- `msg_type`: 'trade'
- `timestamp`: datetime
- `price`: Trade price
- `quantity`: Trade size
- `taker_side`: 'BUY' or 'SELL'

### Quote Event
- `nemo`: Symbol
- `msg_type`: 'quote'
- `timestamp`: datetime
- `best_bid`: Best bid price
- `best_ask`: Best ask price
- `bid_size`: Bid quantity
- `ask_size`: Ask quantity

## Connection Management

```python
# Disconnect cleanly
data.disconnect_websocket()

# Connection status
if data.is_running:
    print("Connected")
else:
    print("Disconnected")

# Reconnection attempts
print(f"Reconnect attempts: {data.reconnect_attempts}/{data.max_reconnect_attempts}")
```

## Thread Safety

All methods are thread-safe:
- `get_ultima_vela()`
- `get_ultimas_velas()`
- `get_valor_ultima_vela()`
- `get_kline_generator()`

Use locks automatically for data access.

## Environment Setup

```bash
# Set API key
export COINAPI_KEY='your-api-key-here'

# Optional: logging level
export LOG_LEVEL='INFO'
```

## Common Patterns

### Strategy Integration
```python
from trading import LiveTrading

HFT = LiveTrading(
    lista_nemos=['BTC', 'ETH'],
    lista_bolsas=['BINANCEFTS'],
    lista_libros=['PERP'],
    capital_inicial=10000,
    heartbeat=1,
    start_time=datetime.now(),
    admin_datos=CoinApiDs,  # ← Use here
    admin_ejecucion=traderPerp,
    portafolio=AQMPortHFT,
    estrategia=PairsTradingHFT
)
```

### Multi-Symbol Monitoring
```python
symbols = ['BTC', 'ETH', 'SOL', 'ADA']
data = CoinApiDs(eventos, ['BINANCEFTS'], ['PERP'], symbols)

for symbol in symbols:
    close = data.get_valor_ultima_vela(symbol, 'close')
    volume = data.get_valor_ultima_vela(symbol, 'volume')
    print(f"{symbol}: ${close:.2f} (Vol: {volume:.2f})")
```

### Real-Time Indicators
```python
for latest, buffer in data.get_kline_generator('BTC', lookback=50):
    # Simple Moving Average
    sma_20 = buffer['close'].tail(20).mean()
    
    # RSI calculation
    delta = buffer['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = -delta.where(delta < 0, 0).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    print(f"SMA(20): {sma_20:.2f}, RSI(14): {rsi.iloc[-1]:.2f}")
```

## Error Handling

```python
try:
    data = CoinApiDs(eventos, exchanges, book_types, symbols)
    
    for latest, buffer in data.get_kline_generator('BTC'):
        # Your strategy here
        pass
        
except ValueError as e:
    print(f"Configuration error: {e}")
except ConnectionError as e:
    print(f"Connection failed: {e}")
finally:
    data.disconnect_websocket()
```

## Debugging

```python
import logging

# Enable debug logging
logging.basicConfig(level=logging.DEBUG)

# Check last message time
print(f"Last update: {data.last_message_time.get('BTC', 0)}")

# Check buffer size
print(f"Buffer size: {len(data.datos_nemo.get('BTC', []))}")

# Check connection status
print(f"Running: {data.is_running}")
print(f"Reconnect attempts: {data.reconnect_attempts}")
```

## Performance Tips

1. **Use Generator Pattern**: More memory-efficient than storing all data
2. **Limit Buffer Size**: Keep only necessary historical data
3. **Batch Processing**: Process multiple events before acting
4. **Use Appropriate Interval**: Don't use 1SEC if you only need 1MIN
5. **Monitor Memory**: Check `len(data.datos_nemo[symbol])` regularly

## Troubleshooting

| Problem | Solution |
|---------|----------|
| No data received | Check API key, symbol IDs, network |
| High memory usage | Reduce buffer size, use generator |
| Connection drops | Check internet, increase max_reconnect_attempts |
| Slow performance | Use longer intervals, reduce symbols |
| Missing candles | Enable debug logging, check CoinAPI status |

## API Limits

CoinAPI Free Plan:
- 100 requests/day (REST)
- 1 WebSocket connection
- Limited symbols per connection

Check your plan at: https://www.coinapi.io/pricing

## References

- Full Documentation: `docs/CoinApiDs_Implementation.md`
- Examples: `examples/coinapi_websocket_example.py`
- CoinAPI Docs: https://docs.coinapi.io/
