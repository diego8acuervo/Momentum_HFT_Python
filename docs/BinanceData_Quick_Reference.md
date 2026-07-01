# BinanceData Quick Reference

## Initialization

```python
import queue
from Datos import BinanceData

eventos = queue.Queue()
symbols = ['BTC', 'ETH', 'SOL']

# Default 1-minute candles
data = BinanceData(eventos, symbols)

# Custom interval
data = BinanceData(eventos, symbols, interval='5m')
```

## Supported Intervals

`'1s'`, `'1m'`, `'3m'`, `'5m'`, `'15m'`, `'30m'`, `'1h'`, `'2h'`, `'4h'`, `'6h'`, `'8h'`, `'12h'`, `'1d'`, `'3d'`, `'1w'`, `'1M'`

## Get Latest Candle

```python
# Single symbol
kline = data.get_latest_kline('BTC')
print(f"BTC: ${kline['close']:.2f}")

# All symbols
all_klines = data.get_all_latest_klines()
for symbol, kline in all_klines.items():
    print(f"{symbol}: ${kline['close']:.2f}")
```

## Stream Real-Time Data

```python
# Generator pattern - yields on each completed candle
for kline in data.get_kline_generator('BTC'):
    print(f"New candle at {kline['open_time']}")
    print(f"  Open: {kline['open']:.2f}")
    print(f"  High: {kline['high']:.2f}")
    print(f"  Low: {kline['low']:.2f}")
    print(f"  Close: {kline['close']:.2f}")
    print(f"  Volume: {kline['volume']:.2f}")
    
    # Your strategy logic here
    if kline['close'] > kline['open']:
        print("  → Bullish!")
```

## Dynamic Subscriptions

```python
# Add new symbol
data.subscribe_symbol('DOGE', interval='1m')

# Remove symbol
data.unsubscribe_symbol('ETH', interval='1m')

# Current symbols
print(data.lista_nemos)
```

## Event-Driven Processing

```python
# The class automatically puts EventoMdo() in the queue
# on each completed candle

while True:
    try:
        event = eventos.get(timeout=1)
        
        # Process all symbols
        all_data = data.get_all_latest_klines()
        for symbol, kline in all_data.items():
            print(f"{symbol}: {kline['close']}")
            
    except queue.Empty:
        continue
```

## Connection Management

```python
# Check if running
if data.is_running:
    print("WebSocket is active")

# Manual reconnect (usually automatic)
data.reconnect_websocket()

# Clean shutdown
data.disconnect_websocket()
```

## Kline Data Structure

```python
{
    'open_time': datetime,      # Start time (datetime object)
    'close_time': datetime,     # End time (datetime object)
    'open': float,              # Opening price
    'high': float,              # Highest price in period
    'low': float,               # Lowest price in period
    'close': float,             # Closing price
    'volume': float,            # Volume in base asset
    'trades': int,              # Number of trades
    'interval': str             # '1m', '5m', etc.
}
```

## Common Patterns

### Pattern 1: Simple Price Monitor
```python
import time

while True:
    btc = data.get_latest_kline('BTC')
    eth = data.get_latest_kline('ETH')
    
    print(f"BTC: ${btc['close']:.2f} | ETH: ${eth['close']:.2f}")
    time.sleep(5)
```

### Pattern 2: Price Alert
```python
threshold = 95000

for kline in data.get_kline_generator('BTC'):
    if kline['close'] > threshold:
        print(f"ALERT! BTC crossed ${threshold}")
        break
```

### Pattern 3: Multi-Symbol Scanner
```python
def find_movers(threshold_pct=2.0):
    all_klines = data.get_all_latest_klines()
    
    movers = []
    for symbol, kline in all_klines.items():
        change = ((kline['close'] - kline['open']) / kline['open']) * 100
        if abs(change) > threshold_pct:
            movers.append((symbol, change))
    
    return sorted(movers, key=lambda x: abs(x[1]), reverse=True)

# Call every minute
import time
while True:
    top_movers = find_movers(threshold_pct=1.5)
    print("Top movers:", top_movers)
    time.sleep(60)
```

### Pattern 4: Volatility Monitor
```python
for kline in data.get_kline_generator('BTC'):
    # Calculate range
    range_pct = ((kline['high'] - kline['low']) / kline['low']) * 100
    
    print(f"BTC range: {range_pct:.2f}%")
    
    if range_pct > 0.5:  # 0.5% range
        print("HIGH VOLATILITY DETECTED")
```

### Pattern 5: Simple Breakout Detection
```python
import collections

# Maintain rolling window
high_prices = collections.deque(maxlen=20)

for kline in data.get_kline_generator('BTC'):
    high_prices.append(kline['high'])
    
    if len(high_prices) == 20:
        max_high = max(list(high_prices)[:-1])  # Exclude current
        
        if kline['close'] > max_high:
            print(f"BREAKOUT! BTC broke above {max_high:.2f}")
```

## Error Handling

```python
# Always wrap in try-except
try:
    kline = data.get_latest_kline('BTC')
    if kline is None:
        print("No data available yet")
    else:
        print(f"BTC: ${kline['close']:.2f}")
except Exception as e:
    print(f"Error: {e}")

# Check connection health
last_update = data.last_message_time.get('BTC', 0)
age = time.time() - last_update
if age > 120:
    print("WARNING: Data is stale")
```

## Rate Limits

⚠️ **Important Limits:**
- Max 5 control messages per second (subscribe/unsubscribe)
- Max 1024 streams per connection
- Max 300 connections per 5 minutes per IP
- Connection auto-closes after 24 hours (auto-reconnects)

## Performance Tips

1. **Use generator pattern** for continuous processing (most memory efficient)
2. **Batch subscribe/unsubscribe** operations to avoid rate limits
3. **Set appropriate lookback** values (default 100 is good for most cases)
4. **Monitor connection health** by checking `last_message_time`
5. **Clean shutdown** with `disconnect_websocket()` when done

## Debugging

```python
import logging

# Enable debug logging
logging.basicConfig(level=logging.DEBUG)

# Check internal state
print("Running:", data.is_running)
print("Reconnect attempts:", data.reconnect_attempts)
print("Tracked symbols:", data.lista_nemos)
print("Latest data:", list(data.ultimo_dato_nemo.keys()))
print("Buffer sizes:", {k: len(v) for k, v in data.datos_nemo.items()})
```

## Complete Example

```python
#!/usr/bin/env python3
import queue
import time
from Datos import BinanceData

def main():
    # Setup
    eventos = queue.Queue()
    symbols = ['BTC', 'ETH']
    data = BinanceData(eventos, symbols, interval='1m')
    
    # Wait for initial data
    time.sleep(5)
    
    # Stream and analyze
    for kline in data.get_kline_generator('BTC'):
        change = ((kline['close'] - kline['open']) / kline['open']) * 100
        
        print(f"\n[{kline['open_time']}] BTC")
        print(f"  Close: ${kline['close']:.2f} ({change:+.2f}%)")
        print(f"  Volume: {kline['volume']:.2f}")
        
        # Your strategy here
        if change > 0.5:
            print("  🟢 Strong bullish move!")
        elif change < -0.5:
            print("  🔴 Strong bearish move!")
    
    # Cleanup
    data.disconnect_websocket()

if __name__ == "__main__":
    main()
```
