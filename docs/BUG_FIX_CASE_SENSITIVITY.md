# Bug Fix: Portfolio Holdings Not Updating

## Date: November 28, 2025

## Problem Description
Portfolio holdings (positions) were not being updated when orders filled. Only commission fees were being deducted from cash, but positions remained at 0 despite fills being received.

**Symptoms:**
- Equity CSV showed LINK and AVAX columns always at 0.0
- Commissions accumulating correctly (~2,190 in fees)
- Cash decreasing only by commission amounts
- Total equity dropping by commission cost only (-2.19%)

## Root Cause
**Case sensitivity mismatch** in the order direction field between execution and portfolio modules:

1. **EjecutorSimulado (ejecucion.py line 77)**: 
   - Sends fills with **lowercase** direction: `'buy'` or `'sell'`
   - Passes `evento.direccion` directly from EventoOrden to EventoCalce

2. **PortAQMHFT (PortAQMHFT.py lines 153-157)**:
   - Checked for **UPPERCASE** direction: `'BUY'` or `'SELL'`
   - Failed match → `calce_dir` remained 0 → positions never updated

## Code Analysis

### Source of lowercase (ejecucion.py):
```python
calce_evento = EventoCalce(
    datetime.datetime.utcnow(), evento.nemo,
    'SMART', evento.cantidad, evento.direccion, None  # ← lowercase from EventoOrden
)
```

### Failed comparison (PortAQMHFT.py - BEFORE):
```python
calce_dir = 0
if calce.direccion == 'BUY':    # ← Never matched 'buy'
    calce_dir = 1
if calce.direccion == 'SELL':   # ← Never matched 'sell'
    calce_dir = -1
# Result: calce_dir stayed 0, positions unchanged
```

## Solution Implemented

Made the direction comparison **case-insensitive** in both methods:

### 1. actualiza_posiciones_calce() - Fixed:
```python
calce_dir = 0
if calce.direccion.upper() == 'BUY':    # ← .upper() added
    calce_dir = 1
if calce.direccion.upper() == 'SELL':   # ← .upper() added
    calce_dir = -1
self.posiciones_actuales[calce.nemo] += calce_dir*calce.cantidad
```

### 2. actualiza_cuenta_calce() - Fixed:
```python
calce_dir = 0
if calce.direccion.upper() == 'BUY':    # ← .upper() added
    calce_dir = 1
if calce.direccion.upper() == 'SELL':   # ← .upper() added
    calce_dir = -1
# ... rest of cash/commission calculations
```

### 3. Enhanced Debug Output:
```python
def actualiza_calce(self, evento):
    if evento.type == 'CALCE':
        self.actualiza_posiciones_calce(evento)
        self.actualiza_cuenta_calce(evento)
        print(f"✅ Calce procesado: {evento.nemo} {evento.direccion} {evento.cantidad} unidades")
        print(f"   Posición actual {evento.nemo}: {self.posiciones_actuales[evento.nemo]}")
        print(f"   Caja actual: ${self.cuenta_actual['Caja']:.2f}")
```

## Expected Results After Fix

Now when fills arrive:
1. ✅ Direction comparison works: `'buy'.upper() == 'BUY'` → True
2. ✅ `calce_dir` set correctly: +1 for buys, -1 for sells
3. ✅ Positions update: `posiciones_actuales[nemo] += calce_dir * cantidad`
4. ✅ Holdings DataFrame shows actual positions
5. ✅ Cash decreases by (position_cost + commission)
6. ✅ Equity reflects true portfolio value

## Files Modified

1. **`src/PortAQMHFT.py`**:
   - Line ~153: Added `.upper()` to actualiza_posiciones_calce()
   - Line ~170: Added `.upper()` to actualiza_cuenta_calce()
   - Line ~189: Enhanced debug output in actualiza_calce()

## Testing

Run the system and verify:
```bash
python src/AQM_MR_Live.py
```

Monitor output for:
- ✅ Debug messages showing actual positions after fills
- ✅ equity.csv showing non-zero holdings in LINK/AVAX columns
- ✅ Cash decreasing by full order value (not just commissions)

## Lesson Learned

**Always use case-insensitive comparisons** for string-based enums/directions that pass through multiple system layers. Better yet, normalize case at entry points or use Python enums.

### Prevention Strategy:
- Consider using Python's `enum.Enum` for direction types
- Or normalize all directions to uppercase at EventoOrden creation
- Add unit tests for event type conversions
