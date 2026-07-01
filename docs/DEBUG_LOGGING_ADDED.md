# Debug Logging Implementation

## Changes Made

Added comprehensive debug logging to trace the quantity formatting and order placement flow in `src/binance_perp.py`. **No logic was changed** - only logging was added.

---

## 1. Debug Logging in `format_quantity()` Method

**Location:** `src/binance_perp.py` lines 329-399

**Added logging for:**

### Input Validation
```
[DEBUG FORMAT_QTY] Input: {symbol} quantity={quantity (15 decimals)}
```

### Exchange Info Status
- ⚠️ Exchange info not available → using fallback (2 decimals)
- ⚠️ Symbol not found in exchange info → using fallback (2 decimals)
- ⚠️ LOT_SIZE filter not found → using quantityPrecision

### Successful Formatting
```
[DEBUG FORMAT_QTY] ✓ Found LOT_SIZE: stepSize={value}, minQty={value}
[DEBUG FORMAT_QTY] Calculation: (quantity - minQty) / stepSize = {steps} steps
[DEBUG FORMAT_QTY] Output: {formatted_value} (formatted from {original_value})
```

**Purpose:** Identify if the formatting function is:
1. Being called correctly
2. Finding the exchange info
3. Retrieving the correct step size
4. Performing the calculation correctly

---

## 2. Debug Logging in `place_market_order()` Method

**Location:** `src/binance_perp.py` lines 643-695

### A. Pre-API Call Logging

**Added comprehensive order tracking:**

```
================================================================================
[DEBUG ORDER] === PLACE MARKET ORDER CALLED ===
[DEBUG ORDER] Input Parameters:
[DEBUG ORDER]   - symbol: {symbol}
[DEBUG ORDER]   - side: {side}
[DEBUG ORDER]   - quantity (RAW): {15 decimals}
[DEBUG ORDER]   - reduce_only: {boolean}
[DEBUG ORDER]   - strategy_id: {id}
[DEBUG ORDER]   - nemo: {asset}
[DEBUG ORDER] Quantity after format_quantity(): {15 decimals}
[DEBUG ORDER] Precision change: {original} → {formatted}
```

**Then logs the exact API call:**

```
[DEBUG ORDER] === SENDING TO BINANCE API ===
[DEBUG ORDER] API Call Parameters:
[DEBUG ORDER]   - symbol: {symbol}
[DEBUG ORDER]   - side: {side}
[DEBUG ORDER]   - type: MARKET
[DEBUG ORDER]   - quantity: {value} (type: {python_type})
[DEBUG ORDER]   - positionSide: {BOTH/LONG/SHORT}
[DEBUG ORDER]   - reduceOnly: {boolean}
[DEBUG ORDER]   - newClientOrderId: {id}
```

### B. API Response Logging

**Records the complete Binance response:**

```
[DEBUG ORDER] === BINANCE API RESPONSE ===
[DEBUG ORDER] Full Response: {JSON formatted response}
================================================================================
```

### C. Error Logging

**Location:** `src/binance_perp.py` lines 743-775

**Captures detailed error information:**

```
================================================================================
[DEBUG ERROR] === BINANCE API EXCEPTION ===
[DEBUG ERROR] Exception Type: {exception_class}
[DEBUG ERROR] Error Message: {error_text}
[DEBUG ERROR] Error Code: {binance_error_code}
[DEBUG ERROR] Error Status Code: {http_status}
[DEBUG ERROR] Full Exception: {full_repr}
[DEBUG ERROR] Request Parameters:
[DEBUG ERROR]   - symbol: {symbol}
[DEBUG ERROR]   - side: {BUY/SELL}
[DEBUG ERROR]   - quantity sent: {formatted_value}
[DEBUG ERROR]   - quantity original: {15 decimals}
================================================================================
```

---

## What to Look For in Output

### 1. **Verify format_quantity() is called:**
- Should see `[DEBUG FORMAT_QTY] Input:` for each order
- Should see step size retrieval
- Should see formatted output

### 2. **Verify formatted quantity is used:**
- `[DEBUG ORDER] Quantity after format_quantity():` should show proper precision
- LINK should show: `xxx.xx` (2 decimals)
- AVAX should show: `xxx` or `xxx.0` (whole numbers)

### 3. **Check API call parameters:**
- `[DEBUG ORDER] API Call Parameters:` shows what's sent to Binance
- Verify `quantity:` field matches formatted value

### 4. **Check API response or error:**
- Success: `[DEBUG ORDER] Full Response:` shows order details
- Failure: `[DEBUG ERROR] Error Code: -1111` indicates precision error

### 5. **Identify the problem:**
- If `format_quantity()` not called → Fix call site
- If step size not found → Fix exchange info loading
- If formatted but still rejected → Check Binance API requirements
- If type mismatch → Check quantity conversion

---

## Expected Flow for Successful Order

```
[DEBUG FORMAT_QTY] Input: LINKUSDT quantity=210.085054678006740
[DEBUG FORMAT_QTY] ✓ Found LOT_SIZE: stepSize=0.01, minQty=0.01
[DEBUG FORMAT_QTY] Calculation: (210.085054678006740 - 0.01) / 0.01 = 21007 steps
[DEBUG FORMAT_QTY] Output: 210.080000000000000 (formatted from 210.085054678006740)

================================================================================
[DEBUG ORDER] === PLACE MARKET ORDER CALLED ===
[DEBUG ORDER] Input Parameters:
[DEBUG ORDER]   - symbol: LINKUSDT
[DEBUG ORDER]   - side: buy
[DEBUG ORDER]   - quantity (RAW): 210.085054678006740
[DEBUG ORDER] Quantity after format_quantity(): 210.080000000000000
[DEBUG ORDER] Precision change: 210.085054678006740 → 210.080000000000000

[DEBUG ORDER] === SENDING TO BINANCE API ===
[DEBUG ORDER] API Call Parameters:
[DEBUG ORDER]   - symbol: LINKUSDT
[DEBUG ORDER]   - side: BUY
[DEBUG ORDER]   - type: MARKET
[DEBUG ORDER]   - quantity: 210.08 (type: <class 'float'>)
[DEBUG ORDER]   - positionSide: BOTH
[DEBUG ORDER]   - reduceOnly: False

[DEBUG ORDER] === BINANCE API RESPONSE ===
[DEBUG ORDER] Full Response: {
  "orderId": 12345678,
  "symbol": "LINKUSDT",
  "status": "FILLED",
  "executedQty": "210.08",
  "avgPrice": "13.1675",
  ...
}
================================================================================
```

---

## Expected Flow for Failed Order (Precision Error)

```
[DEBUG FORMAT_QTY] Input: LINKUSDT quantity=210.085054678006740
[DEBUG FORMAT_QTY] ⚠️  Exchange info not available, using fallback (2 decimals)
[DEBUG FORMAT_QTY] Output: 210.090000000000000

================================================================================
[DEBUG ORDER] === PLACE MARKET ORDER CALLED ===
[DEBUG ORDER] Quantity after format_quantity(): 210.090000000000000

[DEBUG ORDER] === SENDING TO BINANCE API ===
[DEBUG ORDER]   - quantity: 210.09 (type: <class 'float'>)

[DEBUG ERROR] === BINANCE API EXCEPTION ===
[DEBUG ERROR] Exception Type: BinanceAPIException
[DEBUG ERROR] Error Message: APIError(code=-1111): Precision is over the maximum defined for this asset.
[DEBUG ERROR] Error Code: -1111
[DEBUG ERROR]   - quantity sent: 210.09
[DEBUG ERROR]   - quantity original: 210.085054678006740
================================================================================
```

---

## Files Modified

- **`src/binance_perp.py`**
  - Added debug logging to `format_quantity()` method (lines 329-399)
  - Added debug logging to `place_market_order()` method (lines 643-695)
  - Added error logging to exception handler (lines 743-775)

## No Logic Changes

✅ No business logic was modified
✅ No calculation methods changed
✅ No API calls modified
✅ Only logging statements added

---

## Next Steps

1. **Run the system** and capture the output
2. **Look for the debug messages** starting with `[DEBUG FORMAT_QTY]` and `[DEBUG ORDER]`
3. **Identify where the flow breaks:**
   - Is `format_quantity()` being called?
   - Is exchange info loaded?
   - Is the step size correct?
   - Is the formatted quantity being sent to the API?
   - What does Binance's error response say?

4. **Share the debug output** for analysis

The debug logs will show us exactly where the precision formatting is failing.
