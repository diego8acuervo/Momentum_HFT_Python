# BinanceData Architecture Diagram

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         BinanceData Class                                │
│                                                                          │
│  ┌─────────────────────┐      ┌──────────────────────────────────┐    │
│  │   Configuration     │      │     Data Storage                 │    │
│  ├─────────────────────┤      ├──────────────────────────────────┤    │
│  │ • lista_nemos       │      │ • datos_nemo: Dict[str, List]    │    │
│  │ • base_token        │      │   (Historical buffer)            │    │
│  │ • interval          │      │                                  │    │
│  │ • eventos: Queue    │      │ • ultimo_dato_nemo: Dict         │    │
│  │ • binance_api_key   │      │   (Latest candle per symbol)     │    │
│  └─────────────────────┘      └──────────────────────────────────┘    │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │              Thread Management                                   │  │
│  ├──────────────────────────────────────────────────────────────────┤  │
│  │ • ws_thread: Daemon thread running WebSocket connection         │  │
│  │ • data_lock: threading.Lock() for thread-safe access            │  │
│  │ • is_running: Connection state flag                             │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │
                                    ▼
         ┌──────────────────────────────────────────────────┐
         │    Binance WebSocket API                         │
         │    wss://stream.binance.com:9443/ws/             │
         │                                                   │
         │    • <symbol>@kline_1m                           │
         │    • <symbol>@kline_5m                           │
         │    • ... (multiple streams)                      │
         └──────────────────────────────────────────────────┘
```

## Data Flow Architecture

```
External Event                Internal Processing              User Access
━━━━━━━━━━━━━━              ━━━━━━━━━━━━━━━━━━━━━              ━━━━━━━━━━━

Binance Server                                                 Strategy Code
     │                                                              ▲
     │ JSON Message                                                │
     │ {"e":"kline",                                               │
     │  "E":1234567890,                                            │
     │  "s":"BTCUSDT",                                             │
     │  "k":{...}}                                                 │
     │                                                             │
     ▼                                                             │
┌──────────┐                                                      │
│ on_open  │ ─────► Log connection success                        │
└──────────┘       Reset reconnect counter                        │
                                                                  │
┌──────────┐                                                      │
│on_message│                                                      │
└────┬─────┘                                                      │
     │                                                            │
     ├─► Parse JSON                                               │
     │                                                            │
     ├─► Check if candle closed (k.x == true)                    │
     │   │                                                        │
     │   ├─ NO ──► Skip (avoid partial data)                     │
     │   │                                                        │
     │   └─ YES ─► Extract OHLCV data                            │
     │             │                                              │
     │             ▼                                              │
     │        ┌───────────────────┐                              │
     │        │ Acquire data_lock │                              │
     │        └─────────┬─────────┘                              │
     │                  │                                         │
     │                  ├─► Update ultimo_dato_nemo[symbol]      │
     │                  │                                         │
     │                  ├─► Append to datos_nemo[symbol]         │
     │                  │                                         │
     │                  ├─► Trim buffer if > max_size            │
     │                  │                                         │
     │                  └─► Release lock                          │
     │                                                            │
     ├─► eventos.put(EventoMdo()) ────────────────────────────►  │
     │                                                            │
     └─► Update last_message_time[symbol]                        │
                                                                  │
┌──────────┐                                                      │
│ on_error │ ─────► Log error                                    │
└──────────┘       Trigger reconnect_websocket()                 │
                                                                  │
┌──────────┐                                                      │
│ on_close │ ─────► Log closure                                  │
└──────────┘       Trigger reconnect if unexpected               │
                                                                  │
┌──────────┐                                                      │
│ on_ping  │ ─────► Auto-respond with pong                       │
│ on_pong  │       (Keep connection alive)                       │
└──────────┘                                                      │
                                                                  │
                  ┌───────────────────────────┐                  │
                  │  User Access Methods      │                  │
                  ├───────────────────────────┤                  │
                  │ get_latest_kline()        │ ─────────────────┤
                  │   (with lock protection)  │                  │
                  │                           │                  │
                  │ get_all_latest_klines()   │ ─────────────────┤
                  │   (with lock protection)  │                  │
                  │                           │                  │
                  │ get_kline_generator()     │ ─────────────────┤
                  │   (yields on new data)    │                  │
                  └───────────────────────────┘                  │
                                                                  │
                                                                  ▼
                                                          ┌────────────────┐
                                                          │ User Strategy  │
                                                          │ Processing     │
                                                          └────────────────┘
```

## Connection Lifecycle

```
                          START
                            │
                            ▼
                 ┌────────────────────┐
                 │ __init__()         │
                 │ • Setup attributes │
                 │ • Connect to API   │
                 └──────────┬─────────┘
                            │
                            ▼
              ┌─────────────────────────────┐
              │ connect_websocket()         │
              │ • Build stream URL          │
              │ • Create WebSocketApp       │
              │ • Launch daemon thread      │
              │ • Set is_running = True     │
              └──────────┬──────────────────┘
                         │
                         ▼
              ┌──────────────────────┐
              │ WebSocket Thread     │◄────────────┐
              │ • run_forever()      │             │
              │ • Handle messages    │             │
              │ • Send/receive data  │             │
              └──────────┬───────────┘             │
                         │                         │
          ┌──────────────┼────────────┐           │
          │              │            │            │
          ▼              ▼            ▼            │
    ┌─────────┐    ┌─────────┐  ┌─────────┐      │
    │  Error  │    │  Close  │  │24h Timer│      │
    │Detected │    │Detected │  │ Expired │      │
    └────┬────┘    └────┬────┘  └────┬────┘      │
         │              │            │            │
         └──────────────┴────────────┘            │
                    │                             │
                    ▼                             │
          ┌──────────────────────┐                │
          │ reconnect_websocket()│                │
          │ • Increment counter  │                │
          │ • Calculate backoff  │                │
          │ • Sleep (2^n sec)    │                │
          │ • disconnect()       │                │
          │ • connect()          │ ───────────────┘
          └──────────────────────┘
                    │
                    │ If attempts > max
                    ▼
              ┌──────────┐
              │   STOP   │
              │(log error)│
              └──────────┘
                    ▲
                    │
          ┌─────────┴──────────┐
          │ User calls         │
          │ disconnect_        │
          │ websocket()        │
          └────────────────────┘
```

## Threading Model

```
┌─────────────────────────────────────────────────────────────┐
│                        Main Thread                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  • User strategy code execution                             │
│  • Call get_latest_kline()                                  │
│  • Call get_all_latest_klines()                             │
│  • Call get_kline_generator()                               │
│  • Process events from queue                                │
│                                                             │
│  ┌────────────────────────────────────────────────┐        │
│  │         Thread-Safe Access                      │        │
│  │  ┌──────────────────────────────────────────┐  │        │
│  │  │  with self.data_lock:                     │  │        │
│  │  │      read/write datos_nemo               │  │        │
│  │  │      read/write ultimo_dato_nemo         │  │        │
│  │  └──────────────────────────────────────────┘  │        │
│  └────────────────────────────────────────────────┘        │
│                          ▲                                  │
│                          │ Lock Protection                  │
│                          ▼                                  │
└──────────────────────────┼──────────────────────────────────┘
                           │
                           │ Shared Data Access
                           │
┌──────────────────────────┼──────────────────────────────────┐
│                          ▼                                  │
│                  WebSocket Thread                           │
│                  (Daemon Thread)                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  • Run WebSocketApp.run_forever()                           │
│  • Receive messages from Binance                            │
│  • Parse incoming JSON                                      │
│  • Update data structures (with lock)                       │
│  • Put events in queue                                      │
│  • Handle ping/pong                                         │
│                                                             │
│  ┌────────────────────────────────────────────────┐        │
│  │         Thread-Safe Update                      │        │
│  │  ┌──────────────────────────────────────────┐  │        │
│  │  │  with self.data_lock:                     │  │        │
│  │  │      datos_nemo[symbol].append(kline)    │  │        │
│  │  │      ultimo_dato_nemo[symbol] = kline    │  │        │
│  │  └──────────────────────────────────────────┘  │        │
│  └────────────────────────────────────────────────┘        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
                           ▲
                           │
                           │ WebSocket Connection
                           │
                           ▼
              ┌────────────────────────────┐
              │   Binance WebSocket API    │
              │   (External Service)       │
              └────────────────────────────┘
```

## Memory Architecture

```
datos_nemo: Dict[str, List[Dict]]
├── 'BTC': [                              ← Circular buffer
│   │   {'open_time': ..., 'close': 45000, ...},  (max 1000 candles)
│   │   {'open_time': ..., 'close': 45100, ...},
│   │   ...
│   └── {'open_time': ..., 'close': 45200, ...}  ← Oldest dropped
│   ]                                                when buffer full
├── 'ETH': [...]
└── 'SOL': [...]

ultimo_dato_nemo: Dict[str, Dict]
├── 'BTC': {'open_time': ..., 'close': 45200, ...}  ← Latest only
├── 'ETH': {'open_time': ..., 'close': 3200, ...}
└── 'SOL': {'open_time': ..., 'close': 110, ...}

eventos: Queue
├── EventoMdo()  ← FIFO queue
├── EventoMdo()
└── EventoMdo()
```

## Rate Limiting Strategy

```
Control Messages (SUBSCRIBE/UNSUBSCRIBE)
─────────────────────────────────────────

Time Window: 1 second
Max Messages: 5

   0s      1s      2s      3s      4s      5s
   │───────│───────│───────│───────│───────│
   │ 5 msg │ 5 msg │ 5 msg │ 5 msg │ 5 msg │
   └───────┴───────┴───────┴───────┴───────┘
   
   ✓ Allowed: 5 subscribe messages in 1 second
   ✗ Rejected: 6th message within same second


Stream Limit Per Connection
────────────────────────────

   Single Connection: Max 1024 streams
   
   ┌────────────────────────────────┐
   │   WebSocket Connection         │
   ├────────────────────────────────┤
   │ • btcusdt@kline_1m    (1/1024) │
   │ • ethusdt@kline_1m    (2/1024) │
   │ • solusdt@kline_1m    (3/1024) │
   │ • ...                           │
   │ • symbol1024@kline_1m (1024)   │
   └────────────────────────────────┘
   
   For >1024 symbols: Create multiple connections


Connection Rate Limit
──────────────────────

   Time Window: 5 minutes
   Max Connections: 300 per IP
   
   0min    5min    10min   15min
   │───────│───────│───────│
   │ 300   │ 300   │ 300   │
   └───────┴───────┴───────┘


Connection Duration
───────────────────

   Max Duration: 24 hours
   
   0h          12h         24h
   ├───────────┼───────────┤
   │   Active Connection    │  ← Auto-disconnect
   └────────────────────────┘
          │
          └──► Auto-reconnect triggered
```

## Error Recovery Flow

```
                  Normal Operation
                        │
                        ▼
              ┌──────────────────┐
              │ Messages flowing │
              │ normally         │
              └────────┬─────────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
        ▼              ▼              ▼
   ┌────────┐    ┌────────┐    ┌──────────┐
   │Network │    │Server  │    │Timeout   │
   │Error   │    │Close   │    │(24h)     │
   └───┬────┘    └───┬────┘    └────┬─────┘
       │             │              │
       └─────────────┴──────────────┘
                     │
                     ▼
           ┌──────────────────┐
           │ on_error/on_close│
           │ triggered        │
           └────────┬─────────┘
                    │
                    ▼
           ┌──────────────────┐
           │ reconnect_       │
           │ websocket()      │
           └────────┬─────────┘
                    │
                    ▼
           ┌──────────────────┐
           │ Attempt = 1      │
           │ Wait = 2^1 = 2s  │
           └────────┬─────────┘
                    │
                    ▼
           ┌──────────────────┐
           │ Try connect      │
           └────────┬─────────┘
                    │
        ┌───────────┴───────────┐
        │                       │
        ▼                       ▼
   ┌─────────┐            ┌─────────┐
   │ Success │            │ Failure │
   └────┬────┘            └────┬────┘
        │                      │
        ▼                      ▼
   Resume              ┌──────────────┐
   Operation           │ Attempt = 2  │
                       │ Wait = 2^2=4s│
                       └──────┬───────┘
                              │
                              ▼
                       Try connect...
                              │
                    ... (continues) ...
                              │
                    Attempt = 10 (max)
                              │
                              ▼
                       ┌─────────────┐
                       │ Give up     │
                       │ Log error   │
                       └─────────────┘
```

## Component Interaction Sequence

```
User Code    BinanceData    WebSocket     Binance
    │             │             │             │
    │ __init__()  │             │             │
    │────────────>│             │             │
    │             │             │             │
    │             │ connect()   │             │
    │             │────────────>│             │
    │             │             │ ws://...    │
    │             │             │────────────>│
    │             │             │             │
    │             │             │ Connected   │
    │             │<────────────│<────────────│
    │             │             │             │
    │             │ on_open()   │             │
    │             │<────────────│             │
    │             │             │             │
    │             │             │  kline msg  │
    │             │ on_message()│<────────────│
    │             │<────────────│             │
    │             │             │             │
    │             │ [Lock]      │             │
    │             │ Update data │             │
    │             │ [Unlock]    │             │
    │             │             │             │
    │             │ put(event)  │             │
    │<────────────│             │             │
    │             │             │             │
    │get_latest() │             │             │
    │────────────>│             │             │
    │             │ [Lock]      │             │
    │<────────────│ Read data   │             │
    │  kline dict │ [Unlock]    │             │
    │             │             │             │
    │subscribe()  │             │             │
    │────────────>│             │             │
    │             │ SUBSCRIBE   │             │
    │             │────────────>│             │
    │             │             │────────────>│
    │             │             │     ACK     │
    │             │<────────────│<────────────│
    │             │             │             │
    │disconnect() │             │             │
    │────────────>│             │             │
    │             │ close()     │             │
    │             │────────────>│             │
    │             │             │ disconnect  │
    │             │             │────────────>│
    │             │             │             │
```
