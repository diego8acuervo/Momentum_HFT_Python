# 🚀 Binance Perpetuals Refactoring Guide - Full Implementation

## 📋 Executive Summary

This document provides a complete step-by-step guide to refactor the `traderPerp` class from a monolithic trading executor into a clean **orchestrator pattern** that delegates to specialized market handlers.

**Implementation Option**: **Option B - Full Refactor**

### Architecture Changes

```
BEFORE (Monolithic):
┌─────────────────────────────────────┐
│         traderPerp                  │
│  - Spot trading logic               │
│  - Futures trading logic            │
│  - Bitso trading logic              │
│  - Order monitoring                 │
│  - Circuit breaker                  │
│  - Logging                          │
└─────────────────────────────────────┘

AFTER (Orchestrator Pattern):
┌─────────────────────────────────────┐
│      traderPerp (Orchestrator)      │
│  - Routes orders to correct handler │
│  - Unified interface                │
│  - Event queue management           │
└─────────────┬───────────────────────┘
              │
    ┌─────────┼─────────┐
    │         │         │
    ▼         ▼         ▼
┌──────┐ ┌────────┐ ┌────────┐
│ Spot │ │  Perp  │ │ Bitso  │
│Trader│ │ Trader │ │ Trader │
└──────┘ └────────┘ └────────┘
```

### Key Benefits

✅ **Separation of Concerns**: Each market has its own specialized handler  
✅ **Easier Testing**: Test spot and futures independently  
✅ **Backward Compatible**: Existing code using `traderPerp` continues to work  
✅ **Scalable**: Easy to add new exchanges (Bybit, OKX, etc.)  
✅ **Maintainable**: Changes to one market don't affect others  

---

## 📁 New File Structure

```
src/
├── binance_spot.py          # ✅ CREATED - Spot trading handler
├── binance_perp.py          # ✅ CREATED - Perpetuals trading handler
├── ejecucion.py             # ⏳ TO REFACTOR - Main orchestrator
├── test_binance_traders.py  # ✅ CREATED - Test suite
└── Eventos.py               # (unchanged)
```

---

## 🔧 Phase 1: Testing New Modules (30 minutes)

### Step 1.1: Set Up Environment Variables

Add testnet credentials to your `.env` file:

```bash
# Binance Spot (existing)
BINANCE_API_KEY=your_spot_api_key
BINANCE_SECRET_KEY=your_spot_secret_key

# Binance Futures Testnet (NEW - for testing)
BINANCE_TESTNET_API_KEY=your_testnet_api_key
BINANCE_TESTNET_SECRET_KEY=your_testnet_secret_key
```

**Get testnet credentials**:
1. Visit https://testnet.binancefuture.com/
2. Register (no KYC required)
3. Generate API keys
4. Save to `.env` file

### Step 1.2: Run Test Suite

```bash
cd /Users/diegoochoa/Library/CloudStorage/OneDrive-Personal/AQM/MR_HFT_Python/src
python test_binance_traders.py
```

**Expected Output**:

```
================================================================================
BINANCE TRADING MODULES - COMPREHENSIVE TEST SUITE
================================================================================

================================================================================
TESTING BINANCE SPOT TRADER
================================================================================

[TEST 1] Get Balance
--------------------------------------------------------------------------------
        free    locked    total
BTC    0.001     0.000    0.001
USDT  100.00     0.000  100.000

✅ BinanceSpotTrader tests completed successfully

================================================================================
TESTING BINANCE PERPETUAL TRADER (TESTNET)
================================================================================

[INIT] BinancePerpetualTrader initialized (TESTNET)
[TESTNET] Using Binance Futures Testnet

[TEST 1] Get Account Info
--------------------------------------------------------------------------------
[ACCOUNT PERP] Wallet: 10000.00 USDT
[ACCOUNT PERP] Available: 9950.00 USDT

✅ BinancePerpetualTrader tests completed successfully

================================================================================
TEST SUMMARY
================================================================================
BinanceSpotTrader:        ✅ PASSED
BinancePerpetualTrader:   ✅ PASSED
Interface Compatibility:  ✅ PASSED
================================================================================

🎉 ALL TESTS PASSED! Ready for integration into traderPerp.
```

### Step 1.3: Validate Test Results

**Checklist**:
- [ ] Spot trader connects successfully
- [ ] Perp trader connects to testnet
- [ ] Both traders retrieve balances
- [ ] Both traders query open orders
- [ ] Both traders retrieve trade history
- [ ] Interface compatibility verified

---

## 🏗️ Phase 2: Refactor `traderPerp` to Orchestrator (2 hours)

### Step 2.1: Add Market Type Detection

**File**: `src/ejecucion.py`

**Current `__init__` signature**:
```python
def __init__(self, eventos, lista_nemos, lista_bolsas):
```

**New `__init__` signature**:
```python
def __init__(self, eventos, lista_nemos, lista_bolsas, market_type='SPOT'):
    """
    Args:
        eventos: Event queue
        lista_nemos: List of symbols
        lista_bolsas: List of exchanges
        market_type: 'SPOT' or 'PERP' (default: 'SPOT' for backward compatibility)
    """
```

### Step 2.2: Modify `__init__` Method

Add these imports at the top of `ejecucion.py`:

```python
from binance_spot import BinanceSpotTrader
from binance_perp import BinancePerpetualTrader
```

Then modify the `__init__` method to add market detection:

```python
def __init__(self, eventos, lista_nemos, lista_bolsas, market_type='SPOT'):
    """
    Orchestrator for multi-exchange order execution.
    
    Args:
        eventos: Event queue for EventoOrden and EventoCalce
        lista_nemos: List of trading symbols (e.g., ['BTC', 'USDT'])
        lista_bolsas: List of exchanges (e.g., ['BINANCE', 'BITSO'])
        market_type: Market type for Binance ('SPOT' or 'PERP')
    """
    self.eventos = eventos
    self.lista_nemos = lista_nemos
    self.lista_bolsas = lista_bolsas
    self.market_type = market_type.upper()
    
    # Initialize handlers based on exchanges
    self.binance_handler = None
    self.bitso_handler = None
    
    if 'BINANCE' in lista_bolsas or 'BINANCEFTS' in lista_bolsas:
        if self.market_type == 'PERP':
            # Use perpetuals handler
            self.binance_handler = BinancePerpetualTrader(
                lista_nemos=lista_nemos,
                testnet=False  # Use live account
            )
            print("[INIT] Binance PERPETUALS handler initialized")
        else:
            # Use spot handler (default for backward compatibility)
            self.binance_handler = BinanceSpotTrader(
                lista_nemos=lista_nemos
            )
            print("[INIT] Binance SPOT handler initialized")
    
    # ... rest of existing init code for Bitso ...
    
    # NOTE: Monitoring thread for order fills will be handled separately
    # in a portfolio manager module (not in this class)
```

### Step 2.3: Route Order Methods

Modify order placement methods to delegate to appropriate handler:

```python
def ejecutar_orden(self, evento):
    """
    Route order execution to appropriate exchange handler.
    """
    if evento.type != 'ORDEN':
        return None
    
    print(f'Orden recibida en OMS: {evento.tipo_orden}-{evento.direccion} '
          f'{evento.cantidad} {evento.nemo} @ {evento.precio} en {evento.bolsa}')
    
    # Route to Binance (Spot or Perp)
    if evento.bolsa in ['BINANCE', 'BINANCEFTS']:
        if not self.binance_handler:
            print(f"[ERROR] No Binance handler initialized")
            return None
        
        return self._execute_binance_order(evento)
    
    # Route to Bitso
    elif evento.bolsa == 'BITSO':
        return self._execute_bitso_order(evento)
    
    else:
        print(f"[ERROR] Unknown exchange: {evento.bolsa}")
        return None

def _execute_binance_order(self, evento):
    """Execute order on Binance (delegates to spot or perp handler)."""
    handler = self.binance_handler
    
    if evento.tipo_orden == 'MKT':
        # Market order
        return handler.place_market_order(
            side=evento.direccion,
            quantity=evento.cantidad,
            strategy_id='PAIRS_TRADING'
        )
    
    elif evento.tipo_orden == 'LMT':
        # Limit order
        return handler.place_limit_order(
            side=evento.direccion,
            quantity=evento.cantidad,
            price=evento.precio,
            strategy_id='PAIRS_TRADING'
        )
    
    else:
        print(f"[ERROR] Unknown order type: {evento.tipo_orden}")
        return None
```

### Step 2.4: Route Balance Queries

```python
def get_balance_binance(self) -> pd.DataFrame:
    """
    Get Binance balance (delegates to spot or perp handler).
    """
    if not self.binance_handler:
        print("[ERROR] No Binance handler initialized")
        return pd.DataFrame()
    
    return self.binance_handler.get_balance()
```

### Step 2.5: Route Order Monitoring

```python
def get_open_orders_binance(self) -> pd.DataFrame:
    """
    Get open orders from Binance (delegates to spot or perp handler).
    """
    if not self.binance_handler:
        print("[ERROR] No Binance handler initialized")
        return pd.DataFrame()
    
    return self.binance_handler.get_open_orders()

def cancel_order_binance(self, order_id: int, symbol: str = None):
    """
    Cancel order on Binance (delegates to spot or perp handler).
    """
    if not self.binance_handler:
        print("[ERROR] No Binance handler initialized")
        return None
    
    return self.binance_handler.cancel_order(order_id, symbol)

def cancel_all_orders_binance(self):
    """
    Cancel all orders on Binance (delegates to spot or perp handler).
    """
    if not self.binance_handler:
        print("[ERROR] No Binance handler initialized")
        return None
    
    return self.binance_handler.cancel_all_orders()
```

### Step 2.6: Route Trade History

```python
def get_binance_trades(self) -> pd.DataFrame:
    """
    Get trade history from Binance (delegates to spot or perp handler).
    """
    if not self.binance_handler:
        print("[ERROR] No Binance handler initialized")
        return pd.DataFrame()
    
    return self.binance_handler.get_trades()
```

---

## 🧪 Phase 3: Integration Testing (1 hour)

### Step 3.1: Test Spot Mode (Backward Compatibility)

Create test script `test_spot_integration.py`:

```python
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from ejecucion import traderPerp
import queue

# Test SPOT mode (default - backward compatible)
eventos = queue.Queue()

trader = traderPerp(
    eventos=eventos,
    lista_nemos=['BTC', 'USDT'],
    lista_bolsas=['BINANCE'],
    market_type='SPOT'  # Explicitly set to SPOT
)

# Test balance query
print("Testing Spot Balance:")
balance = trader.get_balance_binance()
print(balance)

# Test open orders query
print("\nTesting Spot Open Orders:")
orders = trader.get_open_orders_binance()
print(f"Found {len(orders)} open orders")

print("\n✅ Spot integration test completed")
```

Run test:
```bash
python test_spot_integration.py
```

### Step 3.2: Test Perp Mode (New Functionality)

Create test script `test_perp_integration.py`:

```python
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from ejecucion import traderPerp
import queue

# Test PERP mode (new functionality)
eventos = queue.Queue()

trader = traderPerp(
    eventos=eventos,
    lista_nemos=['BTC', 'USDT'],
    lista_bolsas=['BINANCEFTS'],  # Note: BINANCEFTS or BINANCE both work
    market_type='PERP'  # Use perpetuals
)

# Test balance query
print("Testing Perp Balance:")
balance = trader.get_balance_binance()
print(balance)

# Test position info (perp-specific)
print("\nTesting Position Info:")
positions = trader.binance_handler.get_position_info()

# Test open orders query
print("\nTesting Perp Open Orders:")
orders = trader.get_open_orders_binance()
print(f"Found {len(orders)} open orders")

print("\n✅ Perp integration test completed")
```

Run test:
```bash
python test_perp_integration.py
```

### Step 3.3: Test Order Placement (Testnet)

**⚠️ WARNING**: Only test on TESTNET first!

```python
from ejecucion import traderPerp
from Eventos import EventoOrden
import queue
from datetime import datetime, timezone

# Initialize with TESTNET
eventos = queue.Queue()

# For testnet, modify binance_perp.py temporarily to use testnet=True
trader = traderPerp(
    eventos=eventos,
    lista_nemos=['BTC', 'USDT'],
    lista_bolsas=['BINANCEFTS'],
    market_type='PERP'
)

# Create test order event
order_event = EventoOrden(
    timeStamp=datetime.now(timezone.utc),
    nemo='BTC',
    bolsa='BINANCEFTS',
    tipo_orden='MKT',  # Market order for immediate execution
    direccion='buy',
    cantidad=0.001,  # Small amount
    precio=None  # Not needed for market order
)

# Execute order
print("Placing test market order on testnet...")
result = trader.ejecutar_orden(order_event)

if result:
    print(f"✅ Order placed successfully!")
    print(f"   Order ID: {result.get('orderId')}")
    print(f"   Status: {result.get('status')}")
else:
    print("❌ Order placement failed")
```

---

## 📊 Phase 4: Production Deployment (30 minutes)

### Step 4.1: Pre-Deployment Checklist

- [ ] All tests pass on testnet
- [ ] Backward compatibility verified (spot mode)
- [ ] Order placement tested (testnet)
- [ ] Order monitoring tested
- [ ] Trade history retrieval tested
- [ ] Circuit breaker functional
- [ ] Logging files created correctly

### Step 4.2: Update Main Trading Script

In your main backtest/livetest script, change:

```python
# OLD (implicitly uses spot)
trader = traderPerp(
    eventos=eventos,
    lista_nemos=['BTC', 'USDT'],
    lista_bolsas=['BINANCE']
)

# NEW (explicitly use perpetuals)
trader = traderPerp(
    eventos=eventos,
    lista_nemos=['BTC', 'USDT'],
    lista_bolsas=['BINANCEFTS'],  # or 'BINANCE' works too
    market_type='PERP'  # ← ONLY CHANGE NEEDED
)
```

### Step 4.3: Set Leverage (Important!)

Before trading, set leverage to conservative value:

```python
# Initialize trader
trader = traderPerp(
    eventos=eventos,
    lista_nemos=['BTC', 'USDT'],
    lista_bolsas=['BINANCEFTS'],
    market_type='PERP'
)

# Set leverage to 1x (same as spot)
trader.binance_handler.set_leverage(leverage=1)

# Set margin type to CROSSED
trader.binance_handler.set_margin_type(margin_type='CROSSED')

print("✅ Leverage set to 1x - ready for trading")
```

### Step 4.4: Monitor First Orders

For the first 10 orders in production:

1. **Monitor logs closely**:
   ```bash
   tail -f binance_perp_orders.csv
   tail -f binance_perp_fills.csv
   ```

2. **Check positions**:
   ```python
   trader.binance_handler.get_position_info()
   ```

3. **Verify balances**:
   ```python
   trader.get_balance_binance()
   ```

---

## 🔍 Phase 5: Verification & Monitoring

### Key Metrics to Monitor

| Metric | Command | Expected |
|--------|---------|----------|
| Account Balance | `trader.get_balance_binance()` | USDT balance matches |
| Open Positions | `trader.binance_handler.get_position_info()` | Positions track correctly |
| Open Orders | `trader.get_open_orders_binance()` | Orders visible |
| Order Fills | Check `binance_perp_fills.csv` | Fills logged |
| API Health | `trader.binance_handler.api_health` | Status: 'HEALTHY' |

### Log Files to Monitor

```bash
# Order placement log
tail -f src/binance_perp_orders.csv

# Fill execution log
tail -f src/binance_perp_fills.csv

# Spot orders (if still using spot)
tail -f src/binance_spot_orders.csv
```

---

## 🚨 Troubleshooting Guide

### Issue: "API credentials not found"

**Solution**: Check environment variables
```bash
echo $BINANCE_API_KEY
echo $BINANCE_SECRET_KEY
```

### Issue: "Symbol not found on exchange"

**Solution**: Verify symbol format
- Spot: `BTCUSDT`
- Perp: `BTCUSDT` (same format)

### Issue: "Circuit breaker OPEN"

**Solution**: Wait for timeout or check API status
```python
# Check API health
print(trader.binance_handler.api_health)

# Manually reset if needed (be careful!)
trader.binance_handler.api_health['status'] = 'HEALTHY'
trader.binance_handler.api_health['error_count'] = 0
```

### Issue: "Order validation failed"

**Solution**: Check order parameters
```python
# Test validation
is_valid, error = trader.binance_handler.validate_order(
    symbol='BTCUSDT',
    side='BUY',
    quantity=0.001,
    price=50000,
    order_type='LIMIT'
)
print(f"Valid: {is_valid}, Error: {error}")
```

### Issue: "Position shows unexpected value"

**Solution**: Close all positions and restart
```python
# Get current position
pos = trader.binance_handler.get_position_info()

# If unexpected, close manually via Binance UI first
# Then restart your trading system
```

---

## 📚 API Reference Quick Guide

### Common Operations

#### Place Market Order (Perp)
```python
order = trader.binance_handler.place_market_order(
    side='BUY',          # or 'SELL'
    quantity=0.001,      # Amount to trade
    reduce_only=False,   # True to only close position
    strategy_id='PAIRS'  # Optional tracking ID
)
```

#### Place Limit Order (Perp)
```python
order = trader.binance_handler.place_limit_order(
    side='BUY',
    quantity=0.001,
    price=50000,
    time_in_force='GTC',  # Good Till Cancel
    reduce_only=False,
    strategy_id='PAIRS'
)
```

#### Place Batch Orders (Perp)
```python
orders = [
    {
        'symbol': 'BTCUSDT',
        'side': 'BUY',
        'type': 'LIMIT',
        'quantity': 0.001,
        'price': '50000',
        'timeInForce': 'GTC'
    },
    {
        'symbol': 'ETHUSDT',
        'side': 'SELL',
        'type': 'LIMIT',
        'quantity': 0.01,
        'price': '3000',
        'timeInForce': 'GTC'
    }
]

result = trader.binance_handler.place_batch_orders(orders)
```

#### Get Position Info (Perp Only)
```python
positions = trader.binance_handler.get_position_info()
```

#### Set Leverage (Perp Only)
```python
trader.binance_handler.set_leverage(leverage=2)  # 1x-125x
```

#### Get Balance (Both)
```python
balance_df = trader.get_balance_binance()
```

---

## ✅ Success Criteria

Your refactoring is successful when:

1. ✅ All tests pass
2. ✅ Spot mode still works (backward compatibility)
3. ✅ Perp mode works on testnet
4. ✅ Orders execute correctly on production
5. ✅ Fills are detected and logged
6. ✅ Positions track correctly
7. ✅ No breaking changes to existing strategies
8. ✅ Circuit breaker functions properly
9. ✅ Logs show correct market type (SPOT vs PERP)
10. ✅ EventoCalce events created for fills

---

## 🎯 Next Steps

After successful refactoring:

1. **Portfolio Manager Integration**
   - Create WebSocket user data stream handler
   - Monitor fills in real-time (< 100ms latency)
   - Replace REST API polling

2. **Risk Management Enhancements**
   - Add position size limits
   - Implement max leverage checks
   - Add daily loss limits

3. **Multi-Exchange Support**
   - Add Bybit perpetuals handler
   - Add OKX perpetuals handler
   - Unified position tracking across exchanges

4. **Performance Optimization**
   - Batch order placement for pairs
   - Order modification instead of cancel-replace
   - Connection pooling for API calls

---

## 📞 Support & References

- **Binance Futures API**: https://developers.binance.com/docs/derivatives/usds-margined-futures
- **Python-Binance Docs**: https://python-binance.readthedocs.io/en/latest/futures.html
- **Testnet**: https://testnet.binancefuture.com/

---

## 🏁 Conclusion

You now have:

✅ **3 new modules**:
- `binance_spot.py` - Spot trading handler
- `binance_perp.py` - Perpetuals trading handler
- `test_binance_traders.py` - Comprehensive test suite

✅ **Refactoring plan** for `traderPerp` orchestrator

✅ **Testing strategy** from testnet to production

✅ **Monitoring guide** for production deployment

**Ready to proceed?** Run the tests first, then start the refactoring!

```bash
cd src
python test_binance_traders.py
```

Good luck! 🚀
