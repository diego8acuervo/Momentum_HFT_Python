# Constant Data Error Fix - Combined Approach

## Problem Summary

**Error:** "Invalid input, x is constant" causing trading loop to crash

**Full Error Message:**
```
No se generaron señales en esta iteración.
Error en Ciclo de trading: Invalid input, x is constant
```

## Root Cause Analysis

The error occurs in `src/Estrategia.py` when calculating the hedge ratio and cointegration test:

### Error Sources

1. **Line 176 (OLD):** `self.hedge_ratio=sm.OLS(y,x).fit().params[0]`
   - statsmodels OLS regression fails when x has zero variance
   - Happens when price data is constant (all values identical)

2. **Line 177 (OLD):** `c_t = self.test_cointegracion(x, y)`
   - Calls `sm.OLS(x, y).fit()` which also fails on constant data

3. **Line 184 (OLD):** `z_last=((spread-np.mean(spread))/np.std(spread))[-1]`
   - Division by zero when spread has no variance

### When This Occurs

- **WebSocket disconnection** - Data stops updating, old values repeated
- **Data provider issues** - CoinAPI/Binance not sending new candles
- **Market inactive** - No trades happening (rare but possible)
- **Initialization phase** - Insufficient unique candles loaded
- **Network issues** - Connection problems causing stale data

## Solution Implemented: Combined Approach (Option 4)

Multi-layered protection with diagnostics and error recovery.

### File Modified

**File:** `src/Estrategia.py`  
**Method:** `calcular_senal_pares()`  
**Lines:** 173-255 (approximate)

## Implementation Details

### Layer 1: Variance Validation (Prevention)

**Before any calculations**, check that data has sufficient variance:

```python
# ✅ Step 1: Validate data variance to prevent "constant input" errors
std_x = np.std(x)
std_y = np.std(y)
min_variance = 1e-8  # Minimum acceptable standard deviation

if std_x < min_variance or std_y < min_variance:
    # Diagnostic information
    print(f"⚠️ Datos constantes detectados:")
    print(f"   {self.par[1]}: std={std_x:.2e}, últimos valores={x[-3:]}")
    print(f"   {self.par[0]}: std={std_y:.2e}, últimos valores={y[-3:]}")
    
    # Track persistent constant data issues
    if not hasattr(self, '_constant_data_count'):
        self._constant_data_count = 0
    self._constant_data_count += 1
    
    if self._constant_data_count >= 3:
        print(f"   ⚠️ Datos constantes por {self._constant_data_count} iteraciones consecutivas")
        print(f"   Posible problema: WebSocket desconectado, datos estancados o mercado inactivo")
    
    return

# Reset counter when we get good data
self._constant_data_count = 0
```

**Features:**
- Checks variance before attempting OLS
- Shows last 3 values for debugging
- Tracks consecutive failures
- Warns about persistent issues after 3 iterations
- Suggests possible causes

### Layer 2: Spread Validation

After calculating spread, validate before z-score calculation:

```python
# ✅ Step 4: Calculate spread and validate
spread=y-self.hedge_ratio*x
spread_std = np.std(spread)

# Check for constant spread before calculating z-score
if spread_std < min_variance:
    print(f"⚠️ Spread constante (std={spread_std:.2e}). No se puede calcular z-score.")
    print(f"   Últimos valores de spread: {spread[-3:]}")
    return

# Calculate z-score safely
z_last=((spread-np.mean(spread))/spread_std)[-1]
```

**Features:**
- Prevents division by zero in z-score calculation
- Shows spread values for diagnosis
- Fails gracefully

### Layer 3: Try-Except Error Handling (Backup)

Wraps all calculations in comprehensive error handling:

```python
try:
    # ✅ Step 2: Calculate hedge ratio with error handling
    self.hedge_ratio=sm.OLS(y,x).fit().params[0]
    
    # ✅ Step 3: Test for cointegration with error handling
    c_t = self.test_cointegracion(x, y)
    
    # ... spread and z-score calculations
    
except ValueError as e:
    # ✅ Step 5: Catch any ValueError (including "constant input" errors)
    if "constant" in str(e).lower():
        print(f"⚠️ Error de regresión: Datos constantes detectados (captura secundaria)")
        print(f"   {self.par[0]}: valores={y[-5:]}")
        print(f"   {self.par[1]}: valores={x[-5:]}")
    else:
        print(f"⚠️ Error inesperado en cálculo de señal: {e}")
        import traceback
        traceback.print_exc()
    return
except Exception as e:
    # Catch any other unexpected errors
    print(f"⚠️ Error crítico en calcular_senal_pares: {e}")
    import traceback
    traceback.print_exc()
    return
```

**Features:**
- Catches "constant input" errors if they slip through
- Handles any other ValueError exceptions
- Provides full stack trace for unexpected errors
- Shows last 5 values for detailed debugging

### Layer 4: Code Cleanup

Removed duplicate calculations in the else branch:

```python
# OLD CODE (REDUNDANT):
else: 
    print(f'Último Spread: {spread[-1]} = y:{y[-1]} - hr:{self.hedge_ratio} * x:{x[-1]}')
    z_last=((spread-np.mean(spread))/np.std(spread))[-1]  # ❌ Recalculating unnecessarily
    senal_y,senal_x=self.calcular_senalXY(z_last)

# NEW CODE (OPTIMIZED):
else: 
    # Series not cointegrated, but still generate signals based on z-score
    # Note: spread and z_last already calculated above with proper validation
    senal_y,senal_x=self.calcular_senalXY(z_last)
```

## Benefits

### 1. **Multi-Layered Protection**
- Prevention (variance check)
- Validation (spread check)
- Recovery (try-except)

### 2. **Diagnostic Information**
- Shows which asset has constant data
- Displays recent values for debugging
- Tracks consecutive failures
- Suggests probable causes

### 3. **Persistent Issue Detection**
- Counts consecutive constant data occurrences
- Warns after 3 failures
- Helps identify WebSocket disconnection or data provider issues

### 4. **Graceful Degradation**
- System continues running
- Skips signal generation for this iteration
- Waits for new data
- Recovers automatically when data resumes

### 5. **Production Ready**
- Handles all edge cases
- Provides detailed logging
- Enables troubleshooting
- No system crashes

## Expected Behavior

### Normal Operation
```
Último Spread: 0.000123 = y:50.123456 - hr:1.234567 * x:40.654321
Último Z-score: 1.2345
```

### When Data is Constant (First Detection)
```
⚠️ Datos constantes detectados:
   AVAX: std=0.00e+00, últimos valores=[50.12 50.12 50.12]
   LINK: std=1.23e-02, últimos valores=[40.65 40.66 40.65]
```

### When Issue Persists (After 3 Iterations)
```
⚠️ Datos constantes detectados:
   AVAX: std=0.00e+00, últimos valores=[50.12 50.12 50.12]
   LINK: std=1.23e-02, últimos valores=[40.65 40.66 40.65]
   ⚠️ Datos constantes por 3 iteraciones consecutivas
   Posible problema: WebSocket desconectado, datos estancados o mercado inactivo
```

### If Error Still Occurs (Backup Catch)
```
⚠️ Error de regresión: Datos constantes detectados (captura secundaria)
   AVAX: valores=[50.12 50.12 50.12 50.12 50.12]
   LINK: valores=[40.65 40.66 40.65 40.66 40.65]
```

### Constant Spread (Rare Case)
```
⚠️ Spread constante (std=0.00e+00). No se puede calcular z-score.
   Últimos valores de spread: [0.0 0.0 0.0]
```

## Testing

### Test Case 1: Normal Operation
**Setup:** Fresh data with price variation  
**Expected:** Calculations proceed normally, signals generated

### Test Case 2: Constant Price Data
**Setup:** WebSocket disconnected, old data repeated  
**Expected:** 
- Variance check catches issue
- Warning printed with diagnostic info
- Signal generation skipped
- System continues running

### Test Case 3: Persistent Constant Data
**Setup:** 3+ iterations with constant data  
**Expected:**
- Counter increments
- Special warning after 3rd iteration
- Suggests probable causes

### Test Case 4: Constant Spread (Perfect Correlation)
**Setup:** y = k * x exactly (no noise)  
**Expected:**
- Spread std check catches issue
- Warning printed
- Z-score calculation skipped

### Test Case 5: Recovery After Issue
**Setup:** Constant data → then fresh data arrives  
**Expected:**
- Counter resets to 0
- Normal calculations resume
- Signals generated again

## Troubleshooting

### If You See Constant Data Warnings

1. **Check WebSocket Connection**
   ```python
   # Look for disconnection messages in logs
   # CoinAPI should be printing connection status
   ```

2. **Verify Data is Updating**
   ```python
   # Check if ultimas_velas is receiving new candles
   print(self.velas.datos_nemo[nemo].tail(10))
   ```

3. **Check CoinAPI Status**
   - Verify API key is valid
   - Check rate limits
   - Confirm symbol IDs are correct

4. **Network Issues**
   - Test internet connectivity
   - Check firewall settings
   - Verify DNS resolution

### If Warnings Persist

After 3+ consecutive warnings:
- **Restart WebSocket connection**
- **Check CoinAPI service status**
- **Verify market is active** (trading hours, liquidity)
- **Inspect data provider logs** for errors

## Related Files

- `src/Estrategia.py` - Contains the fix
- `src/Datos.py` - Data provider (CoinApiDs)
- `src/trading.py` - Trading loop where error is caught
- `COINAPI_PERIOD_ID_FIX.md` - Related CoinAPI fix

## Performance Impact

- **Negligible** - Added checks are simple numpy operations
- **Two std() calls per iteration** - O(n) where n=ventanaOLS
- **Memory** - Single counter variable per strategy instance
- **No performance degradation** in normal operation

## Date Implemented
January 12, 2026

## Related Issues
- CoinAPI Period ID Fix (interval='1m' → '1MIN')
- Position Tracking Fix (AVAX/LINK symbol extraction)
- Order Execution Monitoring Thread

## Future Enhancements

Potential improvements (not implemented yet):

1. **Auto-Reconnection**
   - Trigger WebSocket reconnection after N consecutive failures
   - Implement in data provider layer

2. **Data Freshness Check**
   - Compare candle timestamps to detect stale data
   - Alert if data is older than expected

3. **Alternative Hedge Ratio Method**
   - Fall back to historical hedge ratio if OLS fails
   - Use exponential moving average of past hedge ratios

4. **Variance Threshold Configuration**
   - Make min_variance configurable parameter
   - Adjust based on asset volatility

5. **Metrics Collection**
   - Track constant data frequency
   - Log to monitoring system
   - Generate alerts for operations team
