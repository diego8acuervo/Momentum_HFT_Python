---
name: momentum_multi_asset_trading
description: >
  Provides full operational context for the multi-asset daily momentum
  trading strategy. Covers signal generation (tanh-momentum / Turtle N-Weight),
  weight normalisation, daily rebalancing, weight-to-order conversion, and
  live monitoring across a 26-asset crypto universe on Binance/Bitget USDT-M
  Futures. MUST be loaded whenever the user asks to add features, debug a live
  run, change parameters, inspect PnL, or any task involving the momentum
  strategy — even when not named explicitly.

compatibility:
  tools: [bash, python, git, jupyter]
  dependencies:
    - pandas, numpy, requests, websocket-client
    - Binance Futures API (USDT-M Perpetuals)
    - Bitget Futures API V3 (Unified Trading Account)
    - Internal: src/MomentumStrategy.py, src/MomentumPortfolio.py,
      src/MomentumTrading.py, src/MomentumLiveMonitor.py,
      src/AQM_Momentum_Live.py, src/ejecucion.py, src/Datos.py,
      src/Eventos.py, src/account_manager.py
---

# SKILL: Multi-Asset Momentum Strategy (Turtle N-Weight / Tanh)

**scope:** Quantitative crypto momentum trading on USDT-perpetual futures.
**data sources:** `src/MomentumStrategy.py`, `src/MomentumPortfolio.py`,
`src/MomentumTrading.py`, `src/MomentumLiveMonitor.py`,
`src/AQM_Momentum_Live.py`, `notebooks/momentum_dashboard.ipynb`,
runtime files (`notebooks/log_momentum.txt`, `notebooks/live_state_momentum.json`),
exchange APIs (Binance USDT-M, Bitget UTA V3).
**expected outputs:** Rebalance orders (`EventoOrden` with `signal_type='REBALANCE'`),
fills (`EventoCalce`), JSON state snapshots, equity curve, per-asset PnL.

---

## HOW TO USE THIS SKILL

Load this file whenever the context involves the momentum strategy, its live
execution, or any modification to the listed modules. The architecture reuses
the MR event-driven system (`queue.Queue`) with four event types:
`EventoMdo` → `EventoSenal` → `EventoOrden` → `EventoCalce`.

```
Momentum_Strategy.md              ← you are here (router + output format)

§0   FUNDAMENTAL RULE              ← READ FIRST ALWAYS
§1   DATA VALIDATION               ← MANDATORY before any computation
§2   WEIGHT COHERENCE              ← MANDATORY second step
§3   POSITION COMPLETENESS         ← MANDATORY third step
§4   SIGNAL GENERATION             ← core: momentum indicator + variants
§5   WEIGHT NORMALISATION          ← core: gross=1.0, clip, stop-loss
§6   REBALANCING & SIZING          ← core: weight→quantity→order
§7   EXECUTION & POSITION SYNC     ← core: order→fill→position
§8   DIAGNOSTICS                   ← detecting live anomalies
§9   BACKTEST ALIGNMENT            ← ensuring live matches notebook
§10  PnL DECOMPOSITION             ← per-asset attribution, fee drag
§11  PARAMETER CALIBRATION         ← configuration coherence
§12  OUTPUT FORMAT                  ← standard report template
```

---

## §0 — FUNDAMENTAL RULE

**Never generate signals, orders, or PnL reports on data that has not passed
§1, §2, and §3.** If any check fails: **STOP**, report the failing metric
with exact values, and request confirmation before continuing.

**Architectural distinction from MR strategy:**

| Aspect | Mean Reversion (MR) | Momentum |
|--------|--------------------:|:---------|
| Assets per process | 2 (pair) | 26 (universe) |
| Signal type | Z-score entry/exit | Daily weight rebalance |
| Rebalance frequency | Continuous (every candle) | Daily (00:00 UTC) |
| Position sizing | ATR-based DVO1 per leg | Weight × portfolio value |
| `EventoSenal.fuerza` | Hedge ratio (1.0 / \|hr\|) | Target weight (-1 to +1) |
| `signal_type` on orders | `'LARGO'` / `'CORTO'` / `'FUERA'` | `'REBALANCE'` / `'FUERA'` |
| Pair circuit breaker | Active (2-leg protection) | Bypassed (`_is_rebalance` guard) |
| Orderbook subscriptions | Yes (VWAP) | No (`subscribe_orderbooks=False`) |
| Slow layer | OLS hedge ratio (6h refresh) | Daily momentum + ATR |
| WebSocket use | Real-time signal generation | Mark-to-market pricing only |

---

## §1 — DATA VALIDATION

### 1.1 Daily OHLCV freshness

```python
# MomentumStrategy.refresh_daily_data() fetches 60 daily bars per asset
# via velas.get_perpetual_ohlcv(nemo, '1d', 60)
# Verify the last bar is today or yesterday (not stale):
last_date = df_prices.index[-1]
assert (pd.Timestamp.utcnow() - last_date).days <= 1, \
    f"Stale daily data: last bar is {last_date}"
```

### 1.2 Minimum bars for lookback

```python
# Need at least long_window + 2 bars for signal computation
MIN_BARS = long_window + 2  # default: 32
for nemo in lista_nemos:
    df = velas.get_perpetual_ohlcv(nemo, '1d', limit=60)
    assert len(df) >= MIN_BARS, \
        f"{nemo}: only {len(df)} bars, need {MIN_BARS}"
```

### 1.3 Price sanity

```python
assert (df_prices > 0).all().all(), "Non-positive prices detected"
assert not df_prices.isnull().all(axis=0).any(), "Asset with all-NaN prices"
```

### 1.4 ATR sanity (Turtle variant)

```python
if variant == 'turtle':
    assert (df_atr > 0).dropna().all().all(), "Non-positive ATR values"
    # ATR as % of price should be 0.5%-50% for crypto
    atr_pct = df_atr / df_prices
    assert (atr_pct.dropna() < 0.50).all().all(), \
        "ATR > 50% of price — data anomaly"
```

---

## §2 — WEIGHT COHERENCE

### 2.1 Gross exposure normalisation

```python
# After compute_weights(), gross exposure MUST be ~1.0
weights = strategy.target_weights
gross = sum(abs(w) for w in weights.values())
assert 0.9 < gross < 1.1, \
    f"Gross exposure out of range: {gross:.4f}"
```

### 2.2 Per-asset clip

```python
for nemo, w in weights.items():
    assert abs(w) <= max_weight + 1e-6, \
        f"{nemo} weight {w:.4f} exceeds max_weight {max_weight}"
```

### 2.3 Stop-loss filter applied

```python
# Assets with yesterday return < stop_loss_pct should have weight = 0
df_returns = df_prices.pct_change()
yesterday_ret = df_returns.iloc[-2]  # shift(1) equivalent
for nemo in lista_nemos:
    if yesterday_ret.get(nemo, 0) < stop_loss_pct:
        assert weights.get(nemo, 0) == 0, \
            f"{nemo} should be zero-weighted (ret={yesterday_ret[nemo]:.4f})"
```

### 2.4 Weight variant consistency

```python
if variant == 'turtle':
    # Turtle weights use inverse-ATR → low-vol assets should have larger |weight|
    # Verify the top-weighted asset has lower ATR% than the bottom-weighted
    pass  # Directional check only, not strict assertion
elif variant == 'tanh':
    # Tanh weights are clipped to ±max_weight
    assert (pd.Series(weights).abs() <= max_weight + 1e-6).all()
```

---

## §3 — POSITION COMPLETENESS

### 3.1 All universe assets tracked

```python
for nemo in lista_nemos:
    assert nemo in portfolio.posiciones_actuales, \
        f"{nemo} missing from posiciones_actuales"
```

### 3.2 Exchange vs portfolio position drift

```python
for nemo in lista_nemos:
    exchange_qty = handler.get_position_info(f"{nemo}USDT")
    local_qty = portfolio.posiciones_actuales[nemo]
    drift = abs(exchange_qty - local_qty)
    if drift > portfolio.DUST_THRESHOLD:
        print(f"WARNING: {nemo} drift={drift:.6f} — sync needed")
```

### 3.3 State file present and fresh

```python
state_path = os.path.join(NOTEBOOKS_DIR, 'live_state_momentum.json')
assert os.path.exists(state_path), "State file not yet created"
age = time.time() - os.path.getmtime(state_path)
assert age < 120, f"State file stale ({age:.0f}s old)"
```

---

## §4 — SIGNAL GENERATION

**Core concept:** The momentum indicator measures cross-timeframe price
change, normalised by `tanh()` to bound signals to [-1, +1].

### 4.1 Momentum formula

```python
# Identical to Momentum_Backtest.ipynb cell 8
df_momo = (df_prices.shift(short_w) - df_prices.shift(long_w)) / df_prices.shift(long_w)
```

- `short_w=5`: fast momentum (5-day ago price)
- `long_w=30`: slow momentum (30-day ago price)
- Interpretation: positive momentum → price rose from 30d ago vs 5d ago → bullish

### 4.2 Tanh variant (`MomentumStrategy._tanh_weights`)

```python
df_raw_w = np.tanh(df_momo)
df_raw_w = df_raw_w.where(df_prices.notna(), 0).fillna(0)

# Normalise to gross exposure = 1.0
total_abs = df_raw_w.abs().sum(axis=1)
df_w = df_raw_w.div(total_abs.replace(0, np.nan), axis=0).fillna(0)

# Clip per-asset weights
df_w = df_w.clip(lower=-max_weight, upper=max_weight)  # default ±0.10

# Stop-loss filter: zero weight for assets down > 15% yesterday
df_returns = df_prices.pct_change()
df_w = df_w.where(df_returns.shift(1) >= stop_loss_pct, 0)
```

### 4.3 Turtle N-Weight variant (`MomentumStrategy._turtle_weights`)

```python
df_dir = np.tanh(df_momo)           # Direction signal
df_n_pct = df_atr / df_prices       # Volatility as % of price
df_inv_n = 1.0 / df_n_pct           # Inverse: low-vol → higher allocation
df_raw_w = df_dir * df_inv_n        # Direction × inverse volatility

# Normalise to gross = 1.0
total_abs = df_raw_w.abs().sum(axis=1)
df_w = df_raw_w.div(total_abs.replace(0, np.nan), axis=0).fillna(0)

# Stop-loss filter
df_w = df_w.where(df_returns.shift(1) >= stop_loss_pct, 0)
```

**Why Turtle outperforms:** By weighting inversely to ATR, each position
contributes roughly equal risk. High-vol assets get smaller weights,
low-vol assets get larger weights. This produces better risk-adjusted returns
(CAGR 26.88% vs 21.95%, Sortino 1.07 vs 0.95 in 5-year backtest).

### 4.4 ATR computation (`MomentumStrategy._build_rolling_atr`)

```python
# Matches build_rolling_atr from Momentum_Backtest.ipynb
c_prev = df_close.shift(1)
tr1 = df_high - df_low               # High-Low range
tr2 = (df_high - c_prev).abs()       # Gap up
tr3 = (df_low - c_prev).abs()        # Gap down
tr = max(tr1, tr2, tr3)              # per-asset element-wise max
atr = tr.rolling(20).mean()
atr = atr.shift(1)                   # Causal: use yesterday's ATR
```

### 4.5 Signal emission (`MomentumStrategy.calcular_senales`)

After `compute_weights()` produces target weights, the strategy compares
each asset's target vs current weight and emits signals:

```python
for nemo in lista_nemos:
    delta_w = target_w - current_w

    if abs(delta_w) < min_rebalance_threshold:  # default 0.5%
        continue  # Skip — not worth the fee

    if target_w == 0 and abs(current_w) > threshold:
        tipo = 'FUERA'       # Exit position entirely
    elif target_w > 0:
        tipo = 'LARGO'       # Long allocation
    else:
        tipo = 'CORTO'       # Short allocation

    senal = EventoSenal(
        id_estrategia=1,
        nemo=nemo,
        datetime=now_utc,
        tipo_senal=tipo,
        fuerza=target_w,     # ← weight, NOT hedge ratio
    )
    eventos.put(senal)
```

**CRITICAL DIFFERENCE FROM MR:** In MR, `fuerza` is 1.0 (primary) or
`|hedge_ratio|` (hedge). In momentum, `fuerza` IS the target weight
(positive = long, negative = short). `MomentumPortfolio.actualiza_senal`
interprets this accordingly.

---

## §5 — WEIGHT NORMALISATION RULES

### 5.1 Live uses last row = today's signal for tomorrow

The backtest applies `shift(1)` to weights — signals from day t execute on
day t+1. In live, we compute weights at 00:00 UTC (when the daily candle
closes) and execute rebalance orders immediately. This matches the backtest's
intent: compute on the closed bar, execute at the open of the next.

### 5.2 Missing-data handling

```python
# Assets that fetch fails for get zero weight
df_raw_w = df_raw_w.where(df_prices.notna(), 0).fillna(0)
```

### 5.3 Re-normalisation after stop-loss filter

The stop-loss filter zeroes out some assets' weights but does NOT
re-normalise. This means gross exposure may drop below 1.0 on days when
multiple assets are filtered. This matches the backtest behavior exactly.

---

## §6 — REBALANCING & SIZING

### 6.1 Daily rebalance schedule (`MomentumTrading.trade`)

```python
# In the main loop:
if (now_utc.hour == rebalance_hour_utc          # default: 0 (midnight)
        and now_utc.date() != last_rebalance_date):
    self._execute_daily_rebalance()
    last_rebalance_date = now_utc.date()
```

### 6.2 Rebalance procedure (`MomentumTrading._execute_daily_rebalance`)

1. `estrategia.refresh_daily_data()` — fetch 60 daily bars for all 26 assets
   via REST (26 calls × 10s rate limit = ~4.3 min)
2. `estrategia.compute_weights()` — compute target weights (numpy, fast)
3. `estrategia.calcular_senales()` — emit REBALANCE signals for assets
   where `|delta_w| > min_rebalance_threshold`

### 6.3 Weight-to-quantity conversion (`MomentumPortfolio.actualiza_senal`)

```python
# For LARGO/CORTO signals:
target_notional = target_weight * portfolio_value
target_qty = target_notional / price
qty_delta = target_qty - current_qty

# Skip if delta notional is below $5 (min exchange notional)
if abs(qty_delta * price) < 5.0:
    return

# Generate order
direccion = 'buy' if qty_delta > 0 else 'sell'
orden = EventoOrden(
    nemo=nemo,
    tipo_orden='MKT',
    cantidad=abs(qty_delta),
    direccion=direccion,
    signal_type='REBALANCE',   # ← NOT 'LARGO'/'CORTO'
    batch_n=batch_n,           # default 3 slices
)
```

### 6.4 Portfolio value computation

```python
portfolio_value = cash + sum(qty[nemo] * price[nemo] for nemo in lista_nemos)
```

### 6.5 Min rebalance threshold

Default `0.005` (0.5% weight delta). Typical daily turnover at this
threshold: 10-15 orders per day across 26 assets.

### 6.6 Benchmarks

| Metric | Good | Warning | Critical |
|--------|------|---------|----------|
| Orders per rebalance | 5-15 | 20-26 | > 26 (bug) |
| Daily fee drag | < 0.02% | 0.02-0.05% | > 0.05% (threshold too low) |
| Rebalance duration | < 10 min | 10-30 min | > 30 min (API issues) |
| Weight drift (max single asset) | < 2% | 2-5% | > 5% (missed rebalance) |

---

## §7 — EXECUTION & POSITION SYNC

### 7.1 REBALANCE signal type bypass

The pair circuit breaker in `ejecucion.py` is designed for 2-leg pair trades.
With 26 independent assets, it would false-trip. The guard:

```python
# ejecucion.py — Binance routing block (line ~1272) and Bitget block (~1343)
_is_rebalance = getattr(evento, 'signal_type', None) == 'REBALANCE'
if not is_closing_order and not _is_rebalance:
    # pair circuit breaker tracking (skipped for momentum)
```

### 7.2 Batch limit orders

Entry rebalance orders use `batch_n=3` by default (3 passive limit slices
spaced 600s apart). Exit orders (`FUERA`) always use market orders
(`batch_n=1`).

### 7.3 Position sync (every 10 minutes)

Identical to MR: `portfolio.sync_positions_from_exchange(handler)` fetches
positions for all 26 symbols and corrects drift. With 26 symbols and the
REST rate limiter, each sync cycle takes ~20 seconds.

### 7.4 Orderbook subscriptions disabled

`BinanceData.__init__(subscribe_orderbooks=False)` skips the 26 L2 orderbook
WebSocket subscriptions. Momentum only needs kline streams for mark-to-market
pricing.

---

## §8 — DIAGNOSTICS

### 8.1 Data freshness

```python
# In MomentumStrategy.refresh_daily_data():
# Check how many assets successfully loaded
if len(skipped) > 5:
    print(f"WARNING: {len(skipped)} assets skipped — check API connectivity")
```

### 8.2 Rebalance didn't fire

```python
# Check last_signal_date in live_state_momentum.json
state = json.load(open('live_state_momentum.json'))
last_signal = state['strategy']['last_signal_date']
if last_signal != str(date.today()):
    print(f"WARNING: last rebalance was {last_signal}, not today")
```

### 8.3 Weight drift accumulation

```python
# Compare target vs current weights
for nemo, w in state['weights'].items():
    delta = abs(w['weight_delta'])
    if delta > 0.05:  # 5% drift
        print(f"WARNING: {nemo} weight drift {delta:.4f}")
```

### 8.4 Process health

```python
# From momentum_dashboard.ipynb:
alive = proc.poll() is None
json_path = os.path.join(NOTEBOOKS_DIR, 'live_state_momentum.json')
json_age = time.time() - os.path.getmtime(json_path) if os.path.exists(json_path) else None
# json_age should be < 120s (LiveMonitor refreshes every 60s)
```

---

## §9 — BACKTEST ALIGNMENT

### 9.1 Formula verification checklist

| Element | Backtest (notebook) | Live | Match? |
|---------|:-------------------:|:----:|:------:|
| Momentum | `(p.shift(5) - p.shift(30)) / p.shift(30)` | Same in `compute_weights()` | Yes |
| Transform | `np.tanh(df_momo)` | Same | Yes |
| Normalisation | `div(abs().sum())` | Same | Yes |
| Clip (tanh) | `clip(-0.10, 0.10)` | Same, configurable | Yes |
| ATR | `rolling(20).mean()` on TR | Same in `_build_rolling_atr()` | Yes |
| Stop-loss | `.where(ret.shift(1) >= -0.15, 0)` | Same | Yes |
| Execution delay | `shift(1)` | Compute at 00:00 UTC, execute immediately | ~Yes |
| Fee rate | 0.05% per leg | Actual exchange fee (typically lower) | Live better |
| Data source | Binance public klines API | Binance Futures OHLCV REST | Same |

### 9.2 Signal verification procedure

```python
# Run this to compare live weights against backtest on the same date:
from MomentumStrategy import MomentumStrategy

# 1. Instantiate strategy with same params
strat = MomentumStrategy(velas, eventos, variant='turtle',
                          short_window=5, long_window=30)

# 2. Fetch data and compute
strat.refresh_daily_data()
live_weights = strat.compute_weights()

# 3. Compare with notebook cell 8/9 output for the same date
# Max absolute deviation should be < 1e-10
for nemo, w in live_weights.items():
    backtest_w = notebook_weights.get(nemo, 0)
    assert abs(w - backtest_w) < 1e-6, \
        f"{nemo}: live={w:.6f} backtest={backtest_w:.6f}"
```

---

## §10 — PnL DECOMPOSITION

### 10.1 Per-asset attribution

```python
# From live_state_momentum.json:
for nemo, pos in state['positions'].items():
    upnl = pos.get('unrealized_pnl', 0)
    weight = state['weights'][nemo]['target_weight']
    print(f"{nemo}: weight={weight:+.4f}  uPnL=${upnl:+.2f}")
```

### 10.2 Fee drag estimate

With 26 assets, 10-15 orders per day, and 0.04% maker fee:
- Daily fee drag: ~0.01% of capital
- Annual fee drag: ~3.7% (vs backtest's 5%/year at 0.05% taker)

### 10.3 Turnover monitoring

```python
# Daily turnover = sum of |weight_delta| across all assets
daily_turnover = sum(abs(w['weight_delta'])
                     for w in state['weights'].values())
# Healthy range: 5-20% gross turnover per rebalance
```

---

## §11 — PARAMETER CALIBRATION

### 11.1 Live parameter reference

| Parameter | Default | CLI flag | Location |
|-----------|---------|----------|----------|
| `variant` | `turtle` | `--variant` | `MomentumStrategy.__init__` |
| `short_window` | 5 | `--short-window` | `MomentumStrategy.__init__` |
| `long_window` | 30 | `--long-window` | `MomentumStrategy.__init__` |
| `max_weight` | 0.10 | `--max-weight` | `MomentumStrategy.__init__` |
| `stop_loss_pct` | -0.15 | `--stop-loss` | `MomentumStrategy.__init__` |
| `atr_period` | 20 | (hardcoded) | `MomentumStrategy.__init__` |
| `rebalance_hour_utc` | 0 | `--rebalance-utc` | `MomentumTrading.__init__` |
| `min_rebalance_threshold` | 0.005 | `--min-rebalance` | Both Strategy & Portfolio |
| `batch_n` | 3 | `--batch-n` | `MomentumPortfolio / ejecucion` |
| `batch_interval_s` | 600 | `--batch-interval` | `MomentumPortfolio / ejecucion` |
| `limit_offset_bps` | 2 | `--limit-offset-bps` | `MomentumPortfolio / ejecucion` |
| `capital` | 100000 | `--capital` | `MomentumTrading.__init__` |
| `subscribe_orderbooks` | False | (hardcoded) | `MomentumTrading._generar_instancias_trading` |

### 11.2 Universe (26 tokens)

```python
DEFAULT_UNIVERSE = [
    "AAVE", "AIXBT", "AVAX", "BCH", "BNB", "BTC", "COMP", "DOGE", "DOT",
    "DYDX", "EIGEN", "ENA", "ETH", "ETHFI", "FORM", "INJ", "JUP", "LTC",
    "NEAR", "PNUT", "RAY", "SOL", "SUI", "TRX", "UNI", "WIF",
]
```

Override with `--tokens BTC,ETH,SOL,...`

### 11.3 Backtest performance reference (5-year, 2021-2026)

| Metric | Tanh-Momentum | Turtle N-Weight |
|--------|:-------------:|:---------------:|
| Total Return | +169.80% | +229.05% |
| CAGR | +21.95% | +26.88% |
| Max Drawdown | -58.57% | -54.35% |
| Sortino | 0.9498 | 1.0686 |
| Sharpe | 0.6277 | 0.6906 |
| Total Fees (5yr) | +22.08% | +25.77% |

---

## §12 — OUTPUT FORMAT

When generating diagnostics or status reports, use this template:

```
══════════════════════════════════════════════════════
 MOMENTUM STRATEGY STATUS REPORT
══════════════════════════════════════════════════════

1. DATA QUALITY
   Last data refresh: {timestamp}
   Assets loaded: {n_loaded}/26
   Skipped: {skipped_list}
   Last bar date: {last_bar}

2. WEIGHT COMPUTATION
   Variant: {variant}
   Short={short_w}d  Long={long_w}d
   Long positions: {n_long}
   Short positions: {n_short}
   Gross exposure: {gross:.4f}
   Net exposure: {net:.4f}
   Stop-loss filtered: {n_filtered} assets

3. REBALANCE STATUS
   Last rebalance: {last_signal_date}
   Next rebalance: {next_rebalance}
   Orders emitted: {n_orders}
   Daily turnover: {turnover:.2%}

4. POSITIONS & PnL
   Portfolio value: ${portfolio_value:,.0f}
   Cash: ${cash:,.0f}
   Unrealised PnL: ${upnl:+,.2f}
   Top contributors: {top_3}
   Worst contributors: {bottom_3}

5. SYSTEM STATE
   Process: {alive/stopped}
   State file age: {age}s
   Position sync: {last_sync}
   Exchange: {exchange}
   Account: {account}

══════════════════════════════════════════════════════
```

---

## ARCHITECTURE DIAGRAM

```
┌─────────────────────────────────────────────────────────┐
│            AQM_Momentum_Live.py (CLI entry)             │
│  --tokens ... --variant turtle --capital 100000         │
└──────────────────────┬──────────────────────────────────┘
                       │
         ┌─────────────▼──────────────┐
         │    MomentumTrading          │
         │  (daily rebalance loop)     │
         │                            │
         │  00:00 UTC → rebalance     │
         │  10 min   → position sync  │
         │  60 s     → LiveMonitor    │
         └─────┬─────┬──────┬────────┘
               │     │      │
    ┌──────────▼┐  ┌─▼────┐ ┌▼──────────────────┐
    │Momentum   │  │Datos │ │MomentumLiveMonitor │
    │Strategy   │  │(WS+  │ │                    │
    │           │  │REST) │ │ live_state_         │
    │ weights() │  │      │ │ momentum.json      │
    │ signals() │  │26sym │ └────────────────────┘
    └─────┬─────┘  └──────┘
          │ EventoSenal (LARGO/CORTO/FUERA, fuerza=weight)
    ┌─────▼──────────────┐
    │ MomentumPortfolio   │
    │                    │
    │ weight → qty_delta │
    │ → EventoOrden      │
    └─────┬──────────────┘
          │ EventoOrden (signal_type='REBALANCE')
    ┌─────▼──────────────┐
    │   traderPerp        │  (reused from MR)
    │                    │
    │ • Pair CB bypassed │
    │ • Batch scheduler  │
    │ • Rate limiting    │
    │ • Min notional     │
    └─────┬──────────────┘
          │
    ┌─────▼──────┐   ┌──────────┐
    │BinancePerp │   │BitgetPerp│
    │(fapi)      │   │(UTA V3)  │
    └────────────┘   └──────────┘
```

---

## COMPONENT REUSE MAP

| Component | Shared with MR? | Notes |
|-----------|:--------------:|-------|
| `Eventos.py` | Yes | EventoSenal/Orden/Calce unchanged |
| `ejecucion.py` / `traderPerp` | Yes | +2-line REBALANCE guard |
| `binance_perp.py` | Yes | Unchanged |
| `bitget_perp.py` | Yes | Unchanged |
| `account_manager.py` | Yes | Unchanged |
| `TradeLogger.py` | Yes | Unchanged |
| `SignalOrderLogger.py` | Yes | Unchanged |
| `Datos.py` / `BinanceData` | Yes | +`subscribe_orderbooks` param |
| `PortAQMHFT.py` | Parent class | `MomentumPortfolio` extends it |
| `Estrategia.py` | Parent class | `MomentumStrategy` extends it |
| `trading.py` / `LiveTrading` | Parent class | `MomentumTrading` extends it |
| `LiveMonitor.py` | Parent class | `MomentumLiveMonitor` extends it |

---

## CLI REFERENCE

```bash
# Full argument list
python src/AQM_Momentum_Live.py \
  --tokens AAVE,AIXBT,AVAX,...      # comma-separated universe
  --capital 100000                   # total portfolio capital
  --variant turtle                   # 'turtle' or 'tanh'
  --short-window 5                   # momentum short lookback (days)
  --long-window 30                   # momentum long lookback (days)
  --max-weight 0.10                  # per-asset weight cap
  --stop-loss -0.15                  # yesterday return filter
  --rebalance-utc 0                  # UTC hour for daily rebalance
  --min-rebalance 0.005              # min weight delta to order
  --batch-n 3                        # limit order slices
  --batch-interval 600               # seconds between slices
  --limit-offset-bps 2               # passive limit offset
  --exchange binance                 # 'binance' or 'bitget'
  --account binance_live             # account preset
  --state-file live_state_momentum.json
  --interval 1d                      # WebSocket candle interval
  --testnet                          # Binance testnet
  --paper                            # Bitget paper trading
  --gcp                              # BigQuery logging
  --gcp-project my-project
  --gcp-dataset trading
```

---

## JSON STATE FILE SCHEMA (`live_state_momentum.json`)

```json
{
  "timestamp": "2026-06-24T00:05:00Z",
  "strategy_type": "MOMENTUM",
  "pair": ["AAVE", "AIXBT", "AVAX", ...],
  "strategy": {
    "variant": "turtle",
    "short_window": 5,
    "long_window": 30,
    "last_signal_date": "2026-06-24",
    "data_ready": true,
    "n_long": 12,
    "n_short": 14,
    "n_flat": 0,
    "gross_exposure": 0.9873,
    "net_exposure": -0.0042
  },
  "weights": {
    "BTC": {
      "target_weight": 0.0523,
      "current_weight": 0.0510,
      "weight_delta": 0.0013,
      "direction": "LONG",
      "position_value": 5100.00
    }
  },
  "positions": {
    "BTC": {
      "qty": 0.0822,
      "entry_price": 62100.0,
      "mark_price": 62350.0,
      "unrealized_pnl": 20.55
    }
  },
  "account": {
    "cash": 94500.00,
    "commission": 45.23,
    "total_equity": 100245.78
  },
  "market_data": { "...": "per-asset bid/ask/mid/last_close" },
  "circuit_breaker": { "is_open": false }
}
```
