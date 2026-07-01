# KaikoData Provider - Implementation Guide

## Overview

`KaikoData` is an `AdminDatos` subclass that provides real-time and historical OHLCV data from Kaiko's market data APIs. It serves as a drop-in replacement for `CoinApiDs`, requiring **zero changes** to the rest of the trading system.

## Architecture

```
┌─────────────────────────────────────────────────┐
│         AdminDatos (ABC)                        │
│  Abstract methods:                              │
│  - ultima_vela(symbol)                          │
│  - ultimas_velas(symbol, N)                     │
│  - tiempo_ultima_vela(nemo)                     │
│  - valor_ultima_vela(nemo, tipoval)             │
│  - valor_ultimas_velas(nemo, tipoval, N)        │
│  - actualizar_velas()                           │
└────────────────┬────────────────────────────────┘
                 │ implements
        ┌────────┼────────────────┐
        │        │                │
┌───────▼──────┐ │ ┌─────────────▼────────────┐
│ BinanceData  │ │ │ CoinApiDs                │
│ (WebSocket)  │ │ │ (WebSocket + REST)       │
└──────────────┘ │ └──────────────────────────┘
                 │
         ┌───────▼────────┐
         │ KaikoData      │ ← NEW
         │ (gRPC + REST)  │
         └────────────────┘
                 │
                 │ used by
    ┌────────────┴─────────────┐
    │                          │
┌───▼────────────┐  ┌─────────▼──────────────┐
│ trading.py     │  │ Estrategia.py          │
│ (Live Trading) │  │ (Signal Generation)    │
└────────────────┘  └────────────────────────┘
```

## Key Features

| Feature | Description |
|---------|-------------|
| **gRPC Streaming** | Real-time OHLCV data with low latency |
| **REST Historical** | Fetch historical candles for strategy initialization |
| **Thread-Safe** | All data access protected by locks |
| **Auto-Reconnection** | Exponential backoff on connection failures |
| **Fallback Mode** | REST polling when gRPC unavailable |
| **Symbol Mapping** | Automatic conversion between internal and Kaiko formats |

## Installation

### Dependencies

```bash
pip install kaikosdk grpcio grpcio-tools protobuf
```

Or add to `requirements.txt`:
```
kaikosdk>=1.0.0
grpcio>=1.50.0
grpcio-tools>=1.50.0
protobuf>=4.0.0
```

### Environment Variable

Set your Kaiko API key:
```bash
export KAIKO_API_KEY="your-api-key-here"
```

Or add to `.env` file:
```
KAIKO_API_KEY=your-api-key-here
```

## Usage

### Basic Usage (Identical to CoinApiDs)

```python
from Datos import KaikoData
import queue

# Create event queue
eventos = queue.Queue()

# Initialize Kaiko data provider
admin_datos = KaikoData(
    eventos=eventos,
    lista_bolsas=['binf'],              # Binance Futures
    lista_libros=['perpetual-future'],   # Perpetual contracts
    lista_nemos=['LINK', 'AVAX'],        # Symbols to track
    interval='1m'                         # 1-minute candles
)

# Load historical data
admin_datos.set_initial_candles()

# Start real-time streaming
admin_datos.connect_websocket()

# ... trading loop ...

# Cleanup
admin_datos.disconnect_websocket()
```

### Migration from CoinApiDs

Simply change the import and class name:

```python
# Before (CoinAPI)
from Datos import CoinApiDs
admin_datos = CoinApiDs(
    eventos=eventos,
    lista_bolsas=['BINANCEFTS'],
    lista_libros=['PERP'],
    lista_nemos=['LINK', 'AVAX'],
    interval='1m'
)

# After (Kaiko)
from Datos import KaikoData
admin_datos = KaikoData(
    eventos=eventos,
    lista_bolsas=['binf'],              # Or 'BINANCEFTS' (auto-mapped)
    lista_libros=['perpetual-future'],   # Or 'PERP' (auto-mapped)
    lista_nemos=['LINK', 'AVAX'],
    interval='1m'
)
```

## Exchange & Symbol Mapping

### Exchange Codes

| Our System | Kaiko Code | Description |
|------------|------------|-------------|
| `BINANCEFTS` | `binf` | Binance Futures |
| `BINANCE` | `bina` | Binance Spot |
| `COINBASE` | `cbse` | Coinbase |
| `KRAKEN` | `krkn` | Kraken |
| `BITSTAMP` | `bstp` | Bitstamp |
| `GEMINI` | `gmni` | Gemini |
| `HUOBI` | `huob` | Huobi |
| `OKEX` | `okex` | OKEx |

### Instrument Classes

| Our System | Kaiko Class | Description |
|------------|-------------|-------------|
| `PERP` | `perpetual-future` | Perpetual futures |
| `SPOT` | `spot` | Spot trading |
| `FUTURE` | `future` | Dated futures |

### Symbol Format

| Internal | Kaiko Code |
|----------|------------|
| `LINK` | `link-usdt` |
| `AVAX` | `avax-usdt` |
| `BTC` | `btc-usdt` |
| `ETH` | `eth-usdt` |

## API Methods

### Data Access

| Method | Description | Returns |
|--------|-------------|---------|
| `get_ultima_vela(nemo)` | Latest candle | `dict` |
| `get_ultimas_velas(nemo, N)` | Last N candles | `DataFrame` |
| `get_tiempo_ultima_vela(nemo)` | Latest candle timestamp | `datetime` |
| `get_valor_ultima_vela(nemo, tipoval)` | Single value from latest | `float` |
| `get_valor_ultimas_velas(nemo, tipoval, N)` | Array of N values | `np.array` |

### Lifecycle

| Method | Description |
|--------|-------------|
| `set_initial_candles()` | Load historical data (5 days, 500 candles) |
| `connect_websocket()` | Start gRPC streaming |
| `disconnect_websocket()` | Stop streaming gracefully |
| `reconnect_websocket()` | Restart streaming |

### Historical Data

| Method | Description |
|--------|-------------|
| `get_historic_price(symbol_id, period_id, time_start, time_end, limit)` | Fetch OHLCV history |

## Data Structures

### Candle Format (vela)

```python
{
    'open_time': datetime,      # Candle start time
    'close_time': datetime,     # Candle end time
    'open': float,              # Open price
    'high': float,              # High price
    'low': float,               # Low price
    'close': float,             # Close price
    'volume': float,            # Trading volume
    'trades': int,              # Number of trades (may be 0 in stream)
    'interval': str             # Time interval (e.g., '1m')
}
```

### Internal Data Structures

| Attribute | Type | Description |
|-----------|------|-------------|
| `datos_nemo` | `{str: DataFrame}` | Historical buffer per symbol |
| `ultimo_dato_nemo` | `{str: dict}` | Latest candle per symbol |
| `last_message_time` | `{str: float}` | Unix timestamp of last update |

## Configuration Options

### Intervals Supported

| Interval | Description |
|----------|-------------|
| `1s` | 1 second |
| `1m` | 1 minute |
| `3m` | 3 minutes |
| `5m` | 5 minutes |
| `15m` | 15 minutes |
| `30m` | 30 minutes |
| `1h` | 1 hour |
| `2h` | 2 hours |
| `4h` | 4 hours |
| `1d` | 1 day |
| `1w` | 1 week |

## Error Handling

### Automatic Reconnection

The provider automatically reconnects with exponential backoff:

- Initial delay: 2 seconds
- Max delay: 300 seconds (5 minutes)
- Max attempts: 10

### Fallback Mode

If `kaikosdk` is not installed, the provider falls back to REST polling:

```
⚠️ Kaiko SDK not installed. Install with: pip install kaikosdk grpcio
⚠️ Falling back to REST polling mode (every 5 seconds)
```

## Use Cases

### 1. Statistical Arbitrage (Pairs Trading)

```python
# In AQM_MR_Live.py
admin_datos = KaikoData(
    eventos=eventos,
    lista_bolsas=['binf'],
    lista_libros=['perpetual-future'],
    lista_nemos=['LINK', 'AVAX'],
    interval='1m'
)
```

### 2. Cross-Exchange Arbitrage

```python
# Multiple exchanges for arbitrage
admin_datos = KaikoData(
    eventos=eventos,
    lista_bolsas=['bina', 'cbse', 'krkn'],  # Multiple spot exchanges
    lista_libros=['spot'],
    lista_nemos=['BTC'],
    interval='1s'  # 1-second for arbitrage
)
```

### 3. Multi-Asset Portfolio

```python
# Track multiple assets
admin_datos = KaikoData(
    eventos=eventos,
    lista_bolsas=['binf'],
    lista_libros=['perpetual-future'],
    lista_nemos=['BTC', 'ETH', 'SOL', 'LINK', 'AVAX'],
    interval='5m'
)
```

### 4. Research & Backtesting

```python
# Get historical data only (no streaming)
admin_datos = KaikoData(
    eventos=queue.Queue(),
    lista_bolsas=['binf'],
    lista_libros=['perpetual-future'],
    lista_nemos=['LINK'],
    interval='1h'
)

# Fetch specific date range
df = admin_datos.get_historic_price(
    symbol_id='LINK',
    period_id='1h',
    time_start=datetime(2025, 1, 1),
    time_end=datetime(2025, 1, 15),
    limit=1000
)
```

## Comparison: KaikoData vs CoinApiDs

| Feature | KaikoData | CoinApiDs |
|---------|-----------|-----------|
| **Protocol** | gRPC streaming | WebSocket |
| **Latency** | Lower | Medium |
| **Historical API** | REST | REST |
| **Authentication** | API Key (call credentials) | API Key (hello message) |
| **Reconnection** | Per-symbol threads | Single connection |
| **Fallback** | REST polling | None |

## Troubleshooting

### Common Issues

**1. API Key Not Found**
```
ValueError: Kaiko API key not found. Please set the KAIKO_API_KEY environment variable.
```
Solution: Set `KAIKO_API_KEY` environment variable.

**2. SDK Not Installed**
```
Kaiko SDK not installed. Install with: pip install kaikosdk grpcio
```
Solution: Install required packages.

**3. Connection Timeout**
```
gRPC streaming error for LINK: <timeout details>
```
Solution: Check network connectivity and API key validity.

**4. No Historical Data**
```
No historical data returned for link-usdt
```
Solution: Verify exchange/instrument class combination is valid for Kaiko.

## Performance Considerations

1. **Buffer Size**: Default 1000 candles per symbol. Adjust if memory-constrained.
2. **Thread Count**: One thread per symbol for streaming. Consider pool for many symbols.
3. **Lock Contention**: Data access is serialized via `data_lock`. High-frequency access may bottleneck.

## Future Enhancements

- [ ] Add trade-level data streaming
- [ ] Add order book streaming
- [ ] Add VWAP endpoint support
- [ ] Add async/await interface
- [ ] Add connection pooling for many symbols

## References

- [Kaiko Stream Documentation](https://docs.kaiko.com/stream/data-feeds/level-1-and-level-2-data/level-1-aggregations/ohlcv)
- [Kaiko SDK Python](https://github.com/kaikodata/kaiko-sdk-python)
- [gRPC Python Documentation](https://grpc.io/docs/languages/python/)

---

**Created:** January 15, 2026  
**Author:** Trading System Team  
**Version:** 1.0.0
