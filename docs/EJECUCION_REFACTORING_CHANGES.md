# 🔧 ejecucion.py Refactoring - Specific Changes

## Overview

This document shows the **exact changes** needed in `ejecucion.py` to transform `traderPerp` from a monolithic class into an orchestrator.

---

## Change 1: Add Imports (Top of File)

**Location**: After existing imports

**Add these lines**:
```python
# Import specialized market handlers
from binance_spot import BinanceSpotTrader
from binance_perp import BinancePerpetualTrader
```

---

## Change 2: Modify `__init__` Signature

**Current** (line ~364):
```python
def __init__(self, eventos, lista_nemos, lista_bolsas):
```

**New**:
```python
def __init__(self, eventos, lista_nemos, lista_bolsas, market_type='SPOT'):
    """
    Orchestrator for multi-exchange order execution.
    
    Args:
        eventos: Event queue for EventoOrden and EventoCalce
        lista_nemos: List of trading symbols (e.g., ['BTC', 'USDT'])
        lista_bolsas: List of exchanges (e.g., ['BINANCE', 'BITSO'])
        market_type: Market type for Binance ('SPOT' or 'PERP')
                    Default: 'SPOT' for backward compatibility
    """
```

---

## Change 3: Initialize Handlers in `__init__`

**Current** (line ~366-373):
```python
def __init__(self, eventos, lista_nemos, lista_bolsas):
    from binance import Client
    
    self.eventos = eventos
    self.lista_nemos = lista_nemos
    self.lista_bolsas = lista_bolsas
    self.binance_fills = []
    self.bitso_fills = []
    # ... rest of init
```

**New** (insert after `self.lista_bolsas = lista_bolsas`):
```python
def __init__(self, eventos, lista_nemos, lista_bolsas, market_type='SPOT'):
    self.eventos = eventos
    self.lista_nemos = lista_nemos
    self.lista_bolsas = lista_bolsas
    self.market_type = market_type.upper()
    
    # Initialize market-specific handlers
    self.binance_handler = None
    
    if 'BINANCE' in lista_bolsas or 'BINANCEFTS' in lista_bolsas:
        if self.market_type == 'PERP':
            # Use perpetuals handler
            self.binance_handler = BinancePerpetualTrader(
                lista_nemos=lista_nemos,
                testnet=False  # Set to True for testing
            )
            print("[INIT] ✅ Binance PERPETUALS handler initialized")
        else:
            # Use spot handler (default for backward compatibility)
            self.binance_handler = BinanceSpotTrader(
                lista_nemos=lista_nemos
            )
            print("[INIT] ✅ Binance SPOT handler initialized")
    
    # Keep existing attributes for backward compatibility
    self.binance_fills = []
    self.bitso_fills = []
    # ... rest of existing init code (Bitso, CoinAPI, etc.)
```

**Note**: You can remove these lines since handlers manage their own clients:
```python
# DELETE THESE (no longer needed):
self.binance_api_key = self.load_binance_key()
self.binance_api_secret = self.load_binance_secret()
self.taker = self.create_binance_conn()
```

---

## Change 4: Refactor `place_market_order_binance`

**Current** (line ~580):
```python
def place_market_order_binance(self, symbol, side, quantity):
    try:
        # Log order placement BEFORE sending
        self._log_order_placement(
            symbol=self.get_binance_symbol(),
            exchange='BINANCE',
            order_type='MARKET',
            side=side.upper(),
            quantity=quantity,
            price=None,
            order_id=None,
            status='SENDING'
        )
        
        order = self.taker.create_order(
            symbol=self.get_binance_symbol(),
            side=side,
            type='MARKET',
            quantity=quantity
        )
        
        # Record API success
        self.record_api_success('BINANCE')
        # ... rest of method
```

**New** (replace entire method):
```python
def place_market_order_binance(self, symbol, side, quantity):
    """
    Place market order on Binance (delegates to spot or perp handler).
    
    Args:
        symbol: Trading symbol (legacy parameter, now uses self.lista_nemos)
        side: 'buy' or 'sell'
        quantity: Order quantity
    """
    if not self.binance_handler:
        print(f"[ERROR] No Binance handler initialized")
        return None
    
    return self.binance_handler.place_market_order(
        side=side,
        quantity=quantity,
        strategy_id='TRADING'
    )
```

---

## Change 5: Refactor `place_limit_order_binance`

**Current** (line ~620):
```python
def place_limit_order_binance(self, side, quantity, price):
    try:
        # Log order placement
        self._log_order_placement(...)
        
        order = self.taker.create_order(
            symbol=self.get_binance_symbol(),
            side=side.upper(),
            type='LIMIT',
            timeInForce='GTC',
            quantity=quantity,
            price=str(int(price))
        )
        # ... rest of method
```

**New** (replace entire method):
```python
def place_limit_order_binance(self, side, quantity, price):
    """
    Place limit order on Binance (delegates to spot or perp handler).
    
    Args:
        side: 'buy' or 'sell'
        quantity: Order quantity
        price: Limit price
    """
    if not self.binance_handler:
        print(f"[ERROR] No Binance handler initialized")
        return None
    
    return self.binance_handler.place_limit_order(
        side=side,
        quantity=quantity,
        price=price,
        strategy_id='TRADING'
    )
```

---

## Change 6: Refactor `check_order_status_binance`

**Current** (line ~700):
```python
def check_order_status_binance(self):
    """Get open orders from Binance and return as DataFrame indexed by order ID"""
    symbol = self.get_binance_symbol()  
    try:
        orders = self.taker.get_open_orders(symbol=symbol)
        
        if orders:
            df_orders = pd.DataFrame(orders)
            df_orders.set_index('orderId', inplace=True)
            df_orders['exchange'] = 'BINANCE'
            return df_orders
        # ... rest of method
```

**New** (replace entire method):
```python
def check_order_status_binance(self):
    """Get open orders from Binance (delegates to spot or perp handler)."""
    if not self.binance_handler:
        print(f"[ERROR] No Binance handler initialized")
        return pd.DataFrame()
    
    return self.binance_handler.get_open_orders()
```

---

## Change 7: Refactor `get_balance_binance`

**Current** (line ~800):
```python
def get_balance_binance(self) -> pd.DataFrame:
    """Get the balance for all relevant assets from Binance."""
    try:
        account_info = self.taker.get_account()
        balances = account_info['balances']
        
        d = {}
        for balance in balances:
            asset = balance['asset']
            if asset in self.lista_nemos:
                d[asset] = {
                    'free': float(balance['free']),
                    'locked': float(balance['locked']),
                    'total': float(balance['free']) + float(balance['locked'])
                }
        # ... rest of method
```

**New** (replace entire method):
```python
def get_balance_binance(self) -> pd.DataFrame:
    """Get Binance balance (delegates to spot or perp handler)."""
    if not self.binance_handler:
        print(f"[ERROR] No Binance handler initialized")
        return pd.DataFrame()
    
    return self.binance_handler.get_balance()
```

---

## Change 8: Refactor `get_binance_trades`

**Current** (line ~800):
```python
def get_binance_trades(self):
    """Get trade history from Binance for a specific symbol."""
    symbol = self.lista_nemos[0] + self.lista_nemos[1]
    try:
        trade_history = self.taker.get_my_trades(symbol=symbol)
        if trade_history:
            trades_list = []
            for trade in trade_history:
                # Parse time...
                trades_list.append({...})
            trade_df = pd.DataFrame(trades_list)
            # ... rest of method
```

**New** (replace entire method):
```python
def get_binance_trades(self):
    """Get trade history from Binance (delegates to spot or perp handler)."""
    if not self.binance_handler:
        print(f"[ERROR] No Binance handler initialized")
        return pd.DataFrame()
    
    return self.binance_handler.get_trades()
```

---

## Change 9: Refactor `cancel_order` for Binance

**Current** (line ~1200):
```python
def cancel_order(self, symbol=None, orderId=None, exchange=None):
    """Cancel an order on the appropriate exchange."""
    try:
        if exchange == 'BITSO':
            result = self.cancel_order_bitso(orderId)
            # ...
        elif exchange == 'BINANCE':
            if not symbol:
                print(f"Error: symbol is required for Binance...")
                return None
            
            result = self.taker.cancel_order(symbol=symbol, orderId=orderId)
            print(f"Order cancelled on Binance: {result}")
            self.record_api_success('BINANCE')
            return result
        # ... rest of method
```

**New** (replace Binance section only):
```python
def cancel_order(self, symbol=None, orderId=None, exchange=None):
    """Cancel an order on the appropriate exchange."""
    try:
        if exchange == 'BITSO':
            result = self.cancel_order_bitso(orderId)
            # ... keep existing Bitso logic
            return result
            
        elif exchange == 'BINANCE':
            # Delegate to handler
            if not self.binance_handler:
                print(f"[ERROR] No Binance handler initialized")
                return None
            
            result = self.binance_handler.cancel_order(orderId, symbol)
            print(f"Order cancelled on Binance: {result}")
            return result
        # ... rest of method
```

---

## Change 10: Update `ejecutar_orden` for BINANCEFTS

**Current** (line ~495):
```python
def ejecutar_orden(self, evento):
    # ... validation checks ...
    
    if evento.bolsa == 'BINANCE':
        if evento.tipo_orden == 'MKT':
            if evento.direccion == 'buy':
                self.place_market_order_binance(evento.nemo, 'buy', evento.cantidad)
            # ... rest of BINANCE logic
    
    elif evento.bolsa == 'BINANCEFTS':
        if evento.tipo_orden == 'MKT':
            if evento.direccion == 'buy':
                self.place_market_order_binance_perp('buy', evento.cantidad)
            # ... rest of BINANCEFTS logic
```

**New** (consolidate BINANCE and BINANCEFTS):
```python
def ejecutar_orden(self, evento):
    # ... keep existing validation checks ...
    
    # Route to Binance (Spot or Perp handler will handle it)
    if evento.bolsa in ['BINANCE', 'BINANCEFTS']:
        if not self.binance_handler:
            print(f"[ERROR] No Binance handler initialized")
            return None
        
        if evento.tipo_orden == 'MKT':
            return self.binance_handler.place_market_order(
                side=evento.direccion,
                quantity=evento.cantidad,
                strategy_id='PAIRS_TRADING'
            )
        
        elif evento.tipo_orden == 'LMT':
            return self.binance_handler.place_limit_order(
                side=evento.direccion,
                quantity=evento.cantidad,
                price=evento.precio,
                strategy_id='PAIRS_TRADING'
            )
    
    # ... keep existing Bitso logic unchanged ...
```

---

## Change 11: Remove Obsolete Methods

**Delete these methods** (no longer needed):
```python
# DELETE (line ~??):
def load_binance_key(self):
    """Handled by BinanceSpotTrader/BinancePerpetualTrader now"""

def load_binance_secret(self):
    """Handled by BinanceSpotTrader/BinancePerpetualTrader now"""

def create_binance_conn(self):
    """Handled by BinanceSpotTrader/BinancePerpetualTrader now"""

def place_market_order_binance_perp(self, side, quantity):
    """Merged into place_market_order_binance() via handler routing"""
```

---

## Change 12: Update Circuit Breaker Methods

**Current**: Circuit breaker tracks `BINANCE` exchange

**New**: Circuit breaker is per-handler (each handler has its own)

**Update `record_api_success` and `record_api_error`**:

```python
# OLD approach (in traderPerp):
def record_api_success(self, exchange):
    health = self.api_health[exchange]
    # ...

# NEW approach (delegate to handler):
# Just call handler's methods directly:
self.binance_handler.record_api_success()
self.binance_handler.record_api_error(error)
```

**Note**: Each handler manages its own circuit breaker now.

---

## Summary of Changes

| Method | Change Type | Lines Affected |
|--------|-------------|----------------|
| Imports | ADD | Top of file |
| `__init__` | MODIFY | ~364-420 |
| `place_market_order_binance` | REPLACE | ~580-620 |
| `place_limit_order_binance` | REPLACE | ~620-680 |
| `check_order_status_binance` | REPLACE | ~700-730 |
| `get_balance_binance` | REPLACE | ~800-850 |
| `get_binance_trades` | REPLACE | ~850-900 |
| `cancel_order` (Binance part) | MODIFY | ~1200-1250 |
| `ejecutar_orden` | MODIFY | ~495-550 |
| Obsolete methods | DELETE | Various |

---

## Testing After Changes

### Test 1: Spot Mode (Backward Compatibility)
```python
trader = traderPerp(
    eventos=queue.Queue(),
    lista_nemos=['BTC', 'USDT'],
    lista_bolsas=['BINANCE'],
    market_type='SPOT'  # Or omit (defaults to SPOT)
)

balance = trader.get_balance_binance()
print(balance)  # Should work as before
```

### Test 2: Perp Mode (New Functionality)
```python
trader = traderPerp(
    eventos=queue.Queue(),
    lista_nemos=['BTC', 'USDT'],
    lista_bolsas=['BINANCEFTS'],
    market_type='PERP'  # Use perpetuals
)

balance = trader.get_balance_binance()
print(balance)  # Should show futures balance

# Perp-specific features
trader.binance_handler.set_leverage(leverage=1)
positions = trader.binance_handler.get_position_info()
```

---

## Backward Compatibility Guarantee

✅ **These still work unchanged**:
```python
# Old code (no changes needed)
trader = traderPerp(eventos, ['BTC', 'USDT'], ['BINANCE'])
trader.get_balance_binance()
trader.place_market_order_binance('BTC', 'buy', 0.001)
trader.place_limit_order_binance('buy', 0.001, 50000)
trader.get_binance_trades()
```

✅ **New code for perpetuals**:
```python
# New code (simple addition)
trader = traderPerp(
    eventos, 
    ['BTC', 'USDT'], 
    ['BINANCEFTS'], 
    market_type='PERP'  # ← Only addition
)
# All methods work the same!
```

---

## Next Steps

1. ✅ Make these changes to `ejecucion.py`
2. ✅ Run `python test_binance_traders.py` to verify handlers work
3. ✅ Test spot mode for backward compatibility
4. ✅ Test perp mode on testnet
5. ✅ Deploy to production

Good luck! 🚀
