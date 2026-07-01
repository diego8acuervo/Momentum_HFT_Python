# Trading System Architecture — Binance Market Maker

Reference skill for understanding the current architecture, its concern-based layering,
monitoring wiring, and post-execution analysis pipeline.

Invoke when: the user asks how the system is structured, how components connect,
where to add new features, or how data flows from market tick to CSV log.

---

## High-level concern separation

The system is split into seven discrete concerns, each owned by a dedicated package.
No concern directly imports from a peer at the same level — all coupling flows
downward through shared event objects and Protocol interfaces.

```
┌─────────────────────────────────────────────────────────────┐
│  examples/as_market_making.py  ← composition root (wiring)  │
└───────────┬──────────────────────────────────────────────────┘
            │ constructs and starts
     ┌──────▼──────────────────────────────────┐
     │  data/         BinancePriceHandler       │  market feed
     │  strategy/     ASMarketMakerStrategy     │  signal generation
     │  portfolio/    Portfolio                 │  position sizing
     │  execution/    BinancePerpetualTrader    │  order routing
     │  models/       AvellanedaStoikovModel    │  pricing kernel
     │  notebooks/    LiveStateWriter           │  monitoring sink
     │  scripts/      analyze_run.py            │  post-run analysis
     └──────────────────────────────────────────┘
```

All inter-concern communication passes through the shared `queue.Queue` of
typed event objects (`TICK → SIGNAL → ORDER`).  Fill notifications arrive
out-of-band via a WebSocket callback, not through the event queue.

---

## Package map

| Package | Responsibility | Key file |
|---|---|---|
| `data/` | Connects to Binance WebSocket, normalises raw feed into `TickEvent`, pushes to queue | `binance_data.py` |
| `event/` | Typed event dataclasses: `TickEvent`, `SignalEvent`, `OrderEvent` | `event.py` |
| `models/` | Pricing Protocols + implementations. Stateless: `QuoteRequest → QuoteResponse` | `base.py`, `avellaneda_stoikov.py`, `config_loader.py` |
| `strategy/` | Consumes ticks, maintains per-symbol state (inventory, mid-history), emits signals | `strategy.py` |
| `portfolio/` | Translates signals into sized order events, tracks equity | `portfolio.py` |
| `execution/` | Places orders on Binance, logs CSVs, runs user-data WebSocket | `binance_perp.py`, `execution.py` |
| `notebooks/` | Writes `live_state.json` for Jupyter dashboard | `state_writer.py` |
| `scripts/` | Post-run analytics CLI | `analyze_run.py` |
| `examples/` | Entry-point scripts that wire every component together | `as_market_making.py` |

---

## Data-flow diagram

```
Binance WS (bookTicker)
        │
        ▼
BinancePriceHandler.stream_to_queue()
        │  TICK event
        ▼
ASMarketMakerStrategy.calculate_signals()
  ├─ estimate_alpha_ewma(mid_history)
  ├─ AvellanedaStoikovModel.compute_quotes(QuoteRequest)
  │       → QuoteResponse {bid_price, ask_price, reservation_price,
  │                         total_spread, quote_active}
  ├─ writes last_quotes[sym] = resp           ← shared dict (monitoring)
  └─ emits SIGNAL(buy) + SIGNAL(sell) if quote_active
        │
        ▼
Portfolio.execute_signal()
        │  ORDER event
        ▼
BinancePerpetualTrader.execute_order()
  ├─ POST /fapi/v1/order
  ├─ appends row to binance_perp_orders.csv
  └─ returns
        │
        ▼  (async, out-of-band)
User-data WebSocket  ORDER_TRADE_UPDATE(exec_type=TRADE)
        │
        ▼
_process_order_update()
  ├─ appends row to binance_perp_fills.csv   ← _log_fill_event()
  └─ fires fill callbacks
        │
        ▼
on_fill(symbol, side, qty, price, commission, order_id, realized_pnl)
  ├─ strategy.update_inventory(symbol, delta)
  └─ counters["fills"] += 1
```

---

## Models layer — Protocol + injection pattern

`models/base.py` defines a `@runtime_checkable` Protocol:

```python
class PricingModel(Protocol):
    def compute_quotes(self, request: QuoteRequest) -> QuoteResponse: ...
```

`QuoteRequest` carries: `mid_price`, `inventory`, `t ∈ [0,1]`, `alpha`.
`QuoteResponse` carries: `bid_price`, `ask_price`, `reservation_price`,
`total_spread`, `quote_active`.

`AvellanedaStoikovModel` satisfies the Protocol via duck-typing (no explicit
inheritance needed). New models (e.g. GLFT, Deep RL) only need to implement
`compute_quotes` to be plug-compatible.

`ASMarketMakerStrategy` accepts `models: PricingModel | dict[str, PricingModel]`
so different calibrations per symbol are supported out of the box.

---

## Config system — `as_mm_strategy_config.json`

Location: `Binance_Market_Maker/strategy/as_mm_strategy_config.json`

**Structure**:
```
cppi_defaults          ← baseline params for all tokens
cppi_overrides
  BTC, ETH, SOL, XRP, LINK, BNB, SUI, ...
    gamma0             → maps to ASParams.gamma
    kappa              → ASParams.kappa
    q_directional      → ASParams.q_max
    lambda_fill        → ASParams.lambda_ask / lambda_bid
    quote_model        → "as" | "cjp"
    alpha_source       → "ewma" | "ofi"
    binance            → {step_size, min_qty, tick_size, min_notional}
```

`models/config_loader.py::StrategyConfig` owns the mapping:
- `_base_token("BTCUSDT") → "BTC"` — strips quote suffix
- `_merged(symbol)` — applies defaults then per-token override
- `as_params(symbol) → ASParams` — field-by-field translation
- `exchange_filters(symbol) → dict` — Binance lot/price filters

This is also the future home of CPPI layer parameters (`m_cppi`,
`floor_fraction`, `cushion_exponent`, `q_directional` as trend lever).

---

## Monitoring wiring — live dashboard

```
trading loop (every TICK or idle timeout)
        │
        ▼
LiveStateWriter.update(prices, last_quotes, inventory, counters)
        │ rate-limited to write_interval (default 5s)
        │ atomic write: .json.tmp → live_state.json
        ▼
notebooks/live_state.json
        │
        ▼  (Jupyter auto-refresh every 10s)
PerpDashboard.ipynb :: render_dashboard()
  ├─ market_data cards  (bid/ask/mid/spread_bps per symbol)
  ├─ quote cards        (reservation price, A-S spread, bid/ask, cycle count)
  ├─ fill cards         (count/volume/realised P&L/fees today)
  └─ session summary    (ticks/signals/orders/WS fills, accepted/rejected)
```

`live_state.json` schema:
```json
{
  "timestamp": "ISO8601",
  "strategy": "ASMarketMaker",
  "pairs": ["BTCUSDT", "LINKUSDT"],
  "market_data": { "BTCUSDT": {"bid":..., "ask":..., "mid":..., "spread_bps":...} },
  "inventory":   { "BTCUSDT": {"qty":..., "unrealized_pnl": null} },
  "quotes":      { "BTCUSDT": {"bid_price":..., "ask_price":...,
                               "reservation_price":..., "total_spread":...,
                               "quote_active": true} },
  "session": {"start":..., "ticks":..., "signals":..., "orders":..., "fills":...},
  "account": {"cash_usdt": null}
}
```

Dashboard notebook: `Binance_Market_Maker/notebooks/PerpDashboard.ipynb`
State writer:       `Binance_Market_Maker/notebooks/state_writer.py`

---

## Persistent CSV logs (execution layer)

| File | Written by | Content |
|---|---|---|
| `binance_perp_orders.csv` | `execution/binance_perp.py` | Every order sent: symbol, side, type, price, quantity, status, timestamp |
| `binance_perp_fills.csv` | `execution/binance_perp.py` | Every WS fill: symbol, side, price, quantity, commission, realized_pnl, order_id |
| `output/equity.csv` | `backtest/output.py` | Backtest equity curve (not written live) |

Fills were historically empty because `_log_fill_event()` was never called
from the WebSocket path. The fix was to call it inside `_process_order_update`
on every `exec_type == "TRADE"` event (alongside the existing callback dispatch).

---

## Post-execution analysis — `scripts/analyze_run.py`

```bash
python -m Binance_Market_Maker.scripts.analyze_run [--session last|all|N]
                                                    [--orders] [--fills]
```

**What it computes**:
- Groups order rows into sessions by time gaps > 60s between consecutive orders
- Per session × symbol: cycles (paired BUY+SELL limit orders), avg quoted spread in price
  units and bps, order acceptance rate
- Fill stats: count, volume, total realised P&L, total commission, avg fill price

**Key diagnostic**: if LINKUSDT shows spread >> BTC in bps, check `q_directional`
in config — a value like 0.05 when typical LINK order size is ~200 units means
the model thinks inventory is near `q_max` on every tick and wides aggressively.

---

## User-data WebSocket wiring

`BinancePerpetualTrader` (`execution/binance_perp.py`) runs a second WebSocket
thread for account events — always started at construction (not gated on legacy
`EventoCalce`).

**Fill callback registration**:
```python
execution = create_execution_handler(test_mode=True, market_type="perpetual")
execution.register_fill_callback(on_fill)    # in composition root
```

`ExecutionHandler.register_fill_callback` delegates to
`self._trader.register_fill_callback`, which appends to `self._fill_callbacks`.
On each `TRADE` exec_type the handler calls every registered callback:
```python
cb(symbol, side, filled_qty, fill_price, commission, str(order_id), realized_pnl)
```

Shutdown: call `execution.stop_fill_stream()` to cleanly close the user-data WS.

---

## Composition root — `as_market_making.py`

The entry-point script (`examples/as_market_making.py`) is the only place where
all components are constructed and wired together:

```
StrategyConfig → {ASParams per symbol}
               → {AvellanedaStoikovModel per symbol}   ← injected into strategy
               → {kappa_map per symbol}

BinancePriceHandler  →  events queue
ASMarketMakerStrategy(pairs, events, models, kappa_map, last_quotes)
Portfolio(prices, events)
ExecutionHandler
LiveStateWriter(pairs, path, strategy_name)

execution.register_fill_callback(on_fill)

Thread 1: prices.stream_to_queue()   ← WS feed
Thread 2: trade_loop(stop_event)     ← event dispatch
```

To add a new component (e.g. CPPI risk layer, OFI alpha source):
1. Implement the relevant Protocol in `models/base.py`
2. Write the implementation in `models/`
3. Wire it in `as_market_making.py` — no other files should change.

---

## Planned extensions (not yet implemented)

| Feature | Config location | Implementation hook |
|---|---|---|
| CPPI risk overlay | `cppi_defaults` / `cppi_overrides` in config | Wrap `AvellanedaStoikovModel` or modify `QuoteRequest.q_max` before calling `compute_quotes` |
| OFI alpha source | `alpha_source: "ofi"` per symbol | Replace `estimate_alpha_ewma` in `calculate_signals` |
| Account cash polling | `account_info` param in `LiveStateWriter.update` | Fetch `/fapi/v2/account` periodically and pass result |
| asyncio migration | — | Replace threaded loop with uvloop + aiohttp per architecture blueprint |
