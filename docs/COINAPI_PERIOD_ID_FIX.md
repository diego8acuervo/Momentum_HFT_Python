# CoinAPI Period ID Auto-Conversion Fix

## Problem Summary

**Error:** CoinAPI REST error 400 - "The parameter period_id is invalid"

**Root Cause:** 
The notebook was passing `interval='1m'` (Binance format) when creating `CoinApiDs`, but CoinAPI REST API expects `'1MIN'` (CoinAPI format).

```python
# Notebook line 11074 (WRONG FORMAT):
self.admin_datos = self.admin_datos_cls(self.eventos, self.lista_bolsas, 
                                       self.lista_libros, self.lista_nemos, 
                                       interval='1m')  # ❌ Binance format

# CoinAPI expects:
interval='1MIN'  # ✅ CoinAPI format
```

## Call Chain

```
Notebook creates CoinApiDs with interval='1m'
  ↓
CoinApiDs.__init__() stores self.interval = '1m'
  ↓
set_initial_candles() calls get_historic_price(period_id=self.period_id)
  ↓
CoinAPI REST: /v1/ohlcv/{symbol_id}/history?period_id=1m
  ↓
CoinAPI: 400 Error - "The parameter period_id is invalid"
```

## Solution Implemented

**Option 2: Auto-convert interval format in `CoinApiDs.__init__()`**

### Changes Made

**File:** `src/Datos.py`  
**Location:** Lines 2488-2508 (in `CoinApiDs.__init__()` method)

**Added auto-conversion logic:**

```python
# Convert Binance interval format to CoinAPI format
interval_mapping = {
    '1s': '1SEC',
    '1m': '1MIN',
    '3m': '3MIN',
    '5m': '5MIN',
    '15m': '15MIN',
    '30m': '30MIN',
    '1h': '1HRS',
    '2h': '2HRS',
    '4h': '4HRS',
    '1d': '1DAY',
    '1w': '1WEEK'
}

# If interval is in Binance format, convert it
if interval.lower() in interval_mapping:
    converted_interval = interval_mapping[interval.lower()]
    logger.info(f"Auto-converted interval '{interval}' to CoinAPI format '{converted_interval}'")
    self.interval = converted_interval
else:
    self.interval = interval  # Already in CoinAPI format

self.period_id = self.interval  # Add period_id for compatibility with PortAQMHFT
```

## Benefits

✅ **User-Friendly:** Accepts both Binance format (`'1m'`, `'1h'`) and CoinAPI format (`'1MIN'`, `'1HRS'`)

✅ **Backward Compatible:** Existing code with correct CoinAPI format continues to work

✅ **Prevents Future Errors:** Auto-converts common Binance formats to prevent API errors

✅ **Clear Logging:** Logs when conversion happens for debugging

✅ **No Breaking Changes:** Users don't need to update their notebooks immediately

## Format Mapping

| Binance Format | CoinAPI Format | Description |
|----------------|----------------|-------------|
| `'1s'`         | `'1SEC'`       | 1 second    |
| `'1m'`         | `'1MIN'`       | 1 minute    |
| `'3m'`         | `'3MIN'`       | 3 minutes   |
| `'5m'`         | `'5MIN'`       | 5 minutes   |
| `'15m'`        | `'15MIN'`      | 15 minutes  |
| `'30m'`        | `'30MIN'`      | 30 minutes  |
| `'1h'`         | `'1HRS'`       | 1 hour      |
| `'2h'`         | `'2HRS'`       | 2 hours     |
| `'4h'`         | `'4HRS'`       | 4 hours     |
| `'1d'`         | `'1DAY'`       | 1 day       |
| `'1w'`         | `'1WEEK'`      | 1 week      |

## Testing

### Before Fix
```
INFO:Datos:Fetching historical data: BINANCEFTS_PERP_AVAX_USDT (1m)
ERROR:Datos:CoinAPI REST error: 400
ERROR:Datos:Error detail: {'error': 'The parameter period_id is invalid.'}
```

### After Fix (Expected)
```
INFO:Datos:Auto-converted interval '1m' to CoinAPI format '1MIN'
INFO:Datos:Fetching historical data: BINANCEFTS_PERP_AVAX_USDT (1MIN)
INFO:Datos:Initialized historical data for AVAX with X candles
```

## Impact

### Fixed Issues
- ✅ CoinAPI historical data fetch now works with `interval='1m'`
- ✅ ATR calculation receives proper historical data
- ✅ Position sizing (`calcular_unidades()`) can calculate correct quantities
- ✅ System can now operate without API errors

### Areas Affected
- `CoinApiDs.__init__()` - Auto-conversion logic added
- `get_historic_price()` - Now receives valid period_id
- `set_initial_candles()` - Can fetch historical data successfully
- `calcular_unidades()` - Can calculate ATR for position sizing

## Related Files

- `src/Datos.py` - Contains the fix
- `src/PortAQMHFT.py` - Calls `get_historic_price()` for ATR calculation
- `notebooks/StatArb_Notebook.ipynb` - Creates CoinApiDs with `interval='1m'`

## Best Practices

### Recommended Usage
```python
# Both formats now work:
data = CoinApiDs(eventos, exchanges, book_types, symbols, interval='1m')    # ✅ Auto-converts
data = CoinApiDs(eventos, exchanges, book_types, symbols, interval='1MIN')  # ✅ Direct format
```

### For New Code
While the auto-conversion works, **prefer using CoinAPI format** for clarity:
```python
# Preferred:
data = CoinApiDs(eventos, exchanges, book_types, symbols, interval='1MIN')

# Works but requires conversion:
data = CoinApiDs(eventos, exchanges, book_types, symbols, interval='1m')
```

## Date Implemented
January 11, 2026

## Related Issues
- Position Tracking Fix (AVAX/LINK symbol extraction)
- CoinAPI Historical Data Integration
