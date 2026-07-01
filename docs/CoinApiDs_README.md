# CoinApiDs - WebSocket Data Streaming for CoinAPI

## Overview

`CoinApiDs` is a new `AdminDatos` subclass that provides real-time cryptocurrency market data streaming from CoinAPI WebSocket API. It enables live trading strategies to receive OHLCV, trade, quote, and order book data from 300+ cryptocurrency exchanges through a single unified interface.

## Quick Start

### 1. Set API Key
```bash
export COINAPI_KEY='your-api-key-here'
```

### 2. Basic Usage
```python
import queue
from Datos import CoinApiDs

# Initialize
eventos = queue.Queue()
data = CoinApiDs(
    eventos=eventos,
    lista_bolsas=['BINANCEFTS'],
    lista_libros=['PERP'],
    lista_nemos=['BTC', 'ETH'],
    interval='1MIN'
)

# Get latest data
latest_btc = data.get_ultima_vela('BTC')
print(f"BTC Close: ${latest_btc['close']:.2f}")

# Clean shutdown
data.disconnect_websocket()
```

### 3. Integration with Trading Framework
```python
from trading import LiveTrading
from Datos import CoinApiDs
from PortAQMHFT import AQMPortHFT
from Estrategia import PairsTradingHFT

HFT = LiveTrading(
    lista_nemos=['BTC', 'ETH'],
    lista_bolsas=['BINANCEFTS'],
    lista_libros=['PERP'],
    capital_inicial=10000,
    heartbeat=1,
    start_time=datetime.now(),
    admin_datos=CoinApiDs,  # ← Use CoinApiDs
    admin_ejecucion=traderPerp,
    portafolio=AQMPortHFT,
    estrategia=PairsTradingHFT
)

HFT._corre_aplicacion_trading()
```

## Features

✅ **Real-Time Streaming**: WebSocket connection to CoinAPI  
✅ **Multi-Exchange**: Access 300+ exchanges via single API  
✅ **Multiple Data Types**: OHLCV, trades, quotes, order books  
✅ **Thread-Safe**: Concurrent access protected by locks  
✅ **Auto-Reconnect**: Exponential backoff on connection loss  
✅ **Event-Driven**: Publishes EventoMdo objects to queue  
✅ **Memory Efficient**: Circular buffers with automatic cleanup  
✅ **Generator Pattern**: Stream with historical context  

## Documentation

- **Full Documentation**: [CoinApiDs_Implementation.md](docs/CoinApiDs_Implementation.md)
- **Quick Reference**: [CoinApiDs_QuickRef.md](docs/CoinApiDs_QuickRef.md)
- **Summary**: [CoinApiDs_Summary.md](docs/CoinApiDs_Summary.md)
- **Examples**: [coinapi_websocket_example.py](examples/coinapi_websocket_example.py)

## Key Methods

### Data Access
```python
# Get latest candle
latest = data.get_ultima_vela('BTC')

# Get last N candles
candles = data.get_ultimas_velas('BTC', N=10)

# Get specific value
close = data.get_valor_ultima_vela('BTC', 'close')

# Stream with historical context
for latest, buffer in data.get_kline_generator('BTC', lookback=100):
    sma = buffer['close'].mean()
    print(f"SMA: {sma:.2f}")
```

### Connection Management
```python
# Connect (automatic on init)
data.connect_websocket()

# Disconnect
data.disconnect_websocket()

# Check status
if data.is_running:
    print("Connected")
```

## Symbol Format

CoinAPI uses the format: `{EXCHANGE}_{BOOKTYPE}_{SYMBOL}_{QUOTEASSET}`

Examples:
- `BINANCEFTS_PERP_BTC_USDT` - Binance Futures BTC perpetual
- `KRAKENFTS_PERP_ETH_USDT` - Kraken Futures ETH perpetual
- `BINANCE_SPOT_BTC_USDT` - Binance Spot BTC/USDT

The class automatically builds these from your inputs.

## EventoMdo Format

### OHLCV Events (Primary)
```python
EventoMdo(
    nemo='BTC',
    msg_type='ohlcv',
    timestamp=datetime(...),
    open_time=datetime(...),
    close_time=datetime(...),
    open=50000.0,
    high=51000.0,
    low=49000.0,
    close=50500.0,
    volume=1000.0,
    trades=150,
    interval='1MIN'
)
```

### Trade Events
```python
EventoMdo(
    nemo='BTC',
    msg_type='trade',
    timestamp=datetime(...),
    price=50500.0,
    quantity=0.5,
    taker_side='BUY'
)
```

## Supported Intervals

**Seconds**: 1SEC, 5SEC, 10SEC, 15SEC, 30SEC  
**Minutes**: 1MIN, 5MIN, 15MIN, 30MIN  
**Hours**: 1HRS, 4HRS, 12HRS  
**Days**: 1DAY, 7DAY  
**Months**: 1MTH, 3MTH  
**Years**: 1YRS  

## Examples

### Run Examples
```bash
cd /path/to/MR_HFT_Python
python examples/coinapi_websocket_example.py
```

Available examples:
1. **Basic Setup** - Connect and receive data
2. **Latest Data Access** - Access current market data
3. **Generator Pattern** - Stream with historical context
4. **Multi-Symbol Monitoring** - Monitor multiple pairs
5. **Event Types** - Different message types

## Requirements

### Python Packages
```bash
pip install websocket-client pandas numpy python-dotenv
```

### Environment
- `COINAPI_KEY` environment variable
- Internet connectivity
- CoinAPI account (free tier available)

## Architecture

```
CoinAPI WebSocket
       ↓
  on_message()
       ↓
  Parse & Route
       ↓
Update Buffers (thread-safe)
       ↓
  EventoMdo
       ↓
  Queue.put()
       ↓
Strategy/Portfolio
```

## Comparison with BinanceData

| Feature | BinanceData | CoinApiDs |
|---------|-------------|-----------|
| Exchanges | Binance only | 300+ exchanges |
| Authentication | None | API Key |
| Data Types | OHLCV, orderbook | OHLCV, trades, quotes, books |
| Intervals | 1m-1M | 1SEC-5YRS |
| Compatible | ✅ Yes | ✅ Yes |

## Integration

### No Changes Required
✅ `PortAQMHFT.py` - Works without modification  
✅ `trading.py` - Works without modification  
✅ `AQM_MR_Live.py` - Works without modification  
✅ `Eventos.py` - Works without modification  

### Drop-In Replacement
Simply change the data provider:
```python
# Before
LiveTrading(..., admin_datos=BinanceData, ...)

# After
LiveTrading(..., admin_datos=CoinApiDs, ...)
```

## Troubleshooting

### No data received
- Check `COINAPI_KEY` is set correctly
- Verify symbol IDs are correct
- Check network connectivity
- Enable debug logging

### Connection drops
- Check internet stability
- Increase `max_reconnect_attempts`
- Verify API key is valid

### High memory usage
- Reduce buffer size
- Use generator pattern
- Process data more frequently

## Performance

- **Latency**: 50-300ms
- **CPU**: Minimal (JSON parsing)
- **Memory**: ~1MB per 10K candles per symbol
- **Bandwidth**: ~1KB per update

## Support

- **Documentation**: `docs/CoinApiDs_Implementation.md`
- **Quick Ref**: `docs/CoinApiDs_QuickRef.md`
- **Examples**: `examples/coinapi_websocket_example.py`
- **CoinAPI**: https://docs.coinapi.io/
- **Debug**: `logging.basicConfig(level=logging.DEBUG)`

## License

Same as parent project (AQM/MR_HFT_Python)

## Author

Diego Ochoa  
January 6, 2025

## Version

CoinApiDs v1.0.0
