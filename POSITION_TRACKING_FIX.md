# Position Tracking Fix - EventoCalce Symbol Extraction

## Date: January 9, 2026

## Problem Summary

The system was not properly tracking AVAX positions while LINK positions were being updated. Investigation revealed that the `monitor_orders_with_polling()` thread was creating EventoCalce events with the WRONG symbol for all fills.

## Root Cause

### Bug #1: Wrong symbol in open orders monitoring (line ~1819)
```python
# BEFORE (WRONG)
base_asset = self.lista_nemos[0]  # Gets 'LINK' - always the first symbol
...
fill_event = EventoCalce(
    nemo=base_asset,  # ❌ All fills reported as 'LINK'
    ...
)
```

### Bug #2: Wrong symbol in trades endpoint monitoring (line ~1916)
```python
# BEFORE (WRONG)
fill_event = EventoCalce(
    nemo=self.get_binance_symbol(),  # ❌ Returns 'LINKAVAX' (wrong format)
    ...
)
```

### Impact
- ALL fills (both LINK and AVAX) were reported as LINK fills
- Portfolio received EventoCalce(nemo='LINK') for BOTH symbols
- LINK position was updated with both LINK and AVAX quantities
- AVAX position was NEVER updated
- Position closure signals failed because portfolio thought AVAX position = 0

## Solution

Extract the correct symbol from each order/trade data and convert to base asset by removing 'USDT' suffix.

### Fix #1: Open Orders Monitoring
```python
# AFTER (CORRECT)
for order_id, order in binance_orders.iterrows():
    # Extract symbol from order and get base asset (remove USDT suffix)
    order_symbol = order.get('symbol', '')  # e.g., 'LINKUSDT' or 'AVAXUSDT'
    nemo = order_symbol.replace('USDT', '') if order_symbol else self.lista_nemos[0]
    
    # ... fill detection logic ...
    
    fill_event = EventoCalce(
        iTiempo=datetime.now(timezone.utc),
        nemo=nemo,  # ✅ Use extracted base asset (LINK or AVAX)
        bolsa='BINANCE',
        cantidad=new_fill_qty,
        direccion=side,
        precioCalce=price,
        comision=None
    )
```

### Fix #2: Trades Endpoint Monitoring
```python
# AFTER (CORRECT)
for trade_time, trade_row in recent_trades.iterrows():
    # Extract symbol and get base asset (remove USDT suffix)
    trade_symbol = trade_row.get('symbol', '')  # e.g., 'LINKUSDT' or 'AVAXUSDT'
    nemo = trade_symbol.replace('USDT', '') if trade_symbol else self.lista_nemos[0]
    
    fill_event = EventoCalce(
        iTiempo=trade_time,
        nemo=nemo,  # ✅ Use extracted base asset (LINK or AVAX)
        bolsa='BINANCE',
        cantidad=float(trade_row['qty']),
        direccion=str(trade_row['side']).upper(),
        precioCalce=float(trade_row['price']),
        comision=float(trade_row['commission']),
        tipo="TAKER" if trade_row.get('isMaker', False) == False else "MAKER"
    )
```

## Files Modified

- `/Users/diegoochoa/Library/CloudStorage/OneDrive-Personal/AQM/MR_HFT_Python/src/ejecucion.py`
  - Line ~1763: Added symbol extraction for open orders
  - Line ~1819: Updated EventoCalce to use extracted `nemo`
  - Line ~1836: Updated print statement to show correct symbol
  - Line ~1900: Added symbol extraction for trades
  - Line ~1916: Updated EventoCalce to use extracted `nemo`
  - Line ~1933: Updated print statement to show correct symbol
  - Line ~1747: Removed unnecessary `base_asset = 'USDT'` line

## Expected Results

After this fix:
1. ✅ LINK fills generate EventoCalce(nemo='LINK')
2. ✅ AVAX fills generate EventoCalce(nemo='AVAX')
3. ✅ Portfolio receives correct symbol for each fill
4. ✅ Position tracking accurate for both symbols
5. ✅ Closure signals can properly close positions

## Testing

Run the trading system and verify:
1. Both LINK and AVAX positions are updated when fills occur
2. Terminal output shows correct symbol in fill messages:
   - `[FILL EVENT] BINANCE SELL 86.84 LINK @ 13.117`
   - `[FILL EVENT] BINANCE BUY 77.0 AVAX @ 13.757`
3. Position closure signals generate exit orders when z-score returns to threshold
4. Both positions are properly closed

## Related Issues

- Issue #1: EventoCalce generation missing (RESOLVED - monitoring thread handles it)
- Issue #2: Strategy position flags not cleared (SEPARATE - needs callback mechanism)
- Issue #3: Portfolio position tracking (FIXED by this change)

## Next Steps

1. Test the system with this fix
2. Monitor that both LINK and AVAX positions update correctly
3. Verify position closure works when z-score returns to threshold
4. Consider adding strategy callback mechanism to reset position flags
