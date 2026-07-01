# Binance Perpetuals Invalid Symbol Fix

## 📋 Problema Original

```
[ERROR] Failed to get open orders: APIError(code=-1121): Invalid symbol.
⚠️  [CIRCUIT BREAKER] PERP circuit OPENED after 5 errors
⚠️  [CIRCUIT BREAKER] Last error: APIError(code=-1121): Invalid symbol.
[ERROR] Failed to get trades: APIError(code=-1121): Invalid symbol.
```

### 🔍 Causa Raíz:

**El sistema intentaba usar un símbolo inválido: `LINKAVAX`**

#### Flujo del Error:

1. **Configuración en AQM_MR_Live.py:**
   ```python
   lista_nemos = ['LINK', 'AVAX']  # Pares para pairs trading
   lista_bolsas = ['BINANCEFTS']
   ```

2. **Código original en binance_perp.py (línea 314):**
   ```python
   def get_symbol(self) -> str:
       return self.lista_nemos[0] + self.lista_nemos[1]  # ❌ LINKAVAX
   ```

3. **Resultado:**
   - Símbolo generado: `'LINKAVAX'`
   - Binance espera: `'LINKUSDT'` o `'AVAXUSDT'`
   - Error: Binance no tiene el par LINKAVAX

#### Por qué ocurrió:

La clase `BinancePerpetualTrader` fue diseñada originalmente para un **único par** (e.g., ['BTC', 'USDT'] → 'BTCUSDT'), pero **Pairs Trading** requiere operar **múltiples pares independientes**:
- Orden para LINK → debe ir a `LINKUSDT`
- Orden para AVAX → debe ir a `AVAXUSDT`

---

## ✅ Solución Implementada - Opción A

### Estrategia:
Modificar `get_symbol()` para:
1. Aceptar parámetro `nemo` opcional
2. Siempre usar USDT como quote currency
3. Retornar formato correcto: `nemo + 'USDT'`

---

## 🔧 Cambios Realizados

### **Cambio 1: Modificar método `get_symbol()`**

**Ubicación:** `src/binance_perp.py`, línea ~312

**Antes:**
```python
def get_symbol(self) -> str:
    """Get formatted symbol for Binance Futures."""
    return self.lista_nemos[0] + self.lista_nemos[1]
```

**Después:**
```python
def get_symbol(self, nemo: Optional[str] = None) -> str:
    """
    Get formatted symbol for Binance Futures.
    
    Args:
        nemo (str, optional): Base asset symbol (e.g., 'LINK', 'AVAX').
                             If None, uses first symbol from lista_nemos.
    
    Returns:
        str: Formatted symbol with USDT as quote currency (e.g., 'LINKUSDT')
    """
    if nemo is None:
        nemo = self.lista_nemos[0]
    return nemo + 'USDT'
```

**Resultado:**
- `get_symbol('LINK')` → `'LINKUSDT'` ✅
- `get_symbol('AVAX')` → `'AVAXUSDT'` ✅
- `get_symbol()` → `'LINKUSDT'` (default al primer nemo) ✅

---

### **Cambio 2: Actualizar `place_market_order()`**

**Ubicación:** `src/binance_perp.py`, línea ~550

**Antes:**
```python
def place_market_order(self, side: str, quantity: float, 
                      reduce_only: bool = False,
                      strategy_id: Optional[str] = None) -> Optional[Dict]:
    ...
    symbol = self.get_symbol()  # ❌ Siempre usa el primer símbolo
```

**Después:**
```python
def place_market_order(self, side: str, quantity: float, 
                      reduce_only: bool = False,
                      strategy_id: Optional[str] = None,
                      nemo: Optional[str] = None) -> Optional[Dict]:  # ✅ Nuevo parámetro
    ...
    symbol = self.get_symbol(nemo)  # ✅ Usa el nemo específico
```

**Resultado:** Ahora acepta órdenes para diferentes activos en pairs trading.

---

### **Cambio 3: Actualizar `place_limit_order()`**

**Ubicación:** `src/binance_perp.py`, línea ~663

**Antes:**
```python
def place_limit_order(self, side: str, quantity: float, price: float,
                     time_in_force: str = 'GTC', reduce_only: bool = False,
                     strategy_id: Optional[str] = None) -> Optional[Dict]:
    ...
    symbol = self.get_symbol()
```

**Después:**
```python
def place_limit_order(self, side: str, quantity: float, price: float,
                     time_in_force: str = 'GTC', reduce_only: bool = False,
                     strategy_id: Optional[str] = None,
                     nemo: Optional[str] = None) -> Optional[Dict]:  # ✅ Nuevo parámetro
    ...
    symbol = self.get_symbol(nemo)
```

---

### **Cambio 4: Actualizar llamadas en `ejecucion.py`**

**Ubicación:** `src/ejecucion.py`, líneas 545-558

**Antes:**
```python
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
```

**Después:**
```python
if evento.tipo_orden == 'MKT':
    return self.binance_handler.place_market_order(
        side=evento.direccion,
        quantity=evento.cantidad,
        strategy_id='PAIRS_TRADING',
        nemo=evento.nemo  # ✅ Pasa el activo específico
    )

elif evento.tipo_orden == 'LMT':
    return self.binance_handler.place_limit_order(
        side=evento.direccion,
        quantity=evento.cantidad,
        price=evento.precio,
        strategy_id='PAIRS_TRADING',
        nemo=evento.nemo  # ✅ Pasa el activo específico
    )
```

**Resultado:** Cada orden usa el símbolo correcto según el activo (LINK → LINKUSDT, AVAX → AVAXUSDT).

---

### **Cambio 5: Mejorar `get_open_orders()` para múltiples símbolos**

**Ubicación:** `src/binance_perp.py`, línea ~860

**Problema:** Pairs trading necesita monitorear órdenes de AMBOS símbolos (LINKUSDT y AVAXUSDT).

**Antes:**
```python
def get_open_orders(self, symbol: Optional[str] = None) -> pd.DataFrame:
    """Get all open orders as DataFrame."""
    try:
        if symbol is None:
            symbol = self.get_symbol()  # ❌ Solo consulta UN símbolo
        
        orders = self.client.futures_get_open_orders(symbol=symbol)
        ...
```

**Después:**
```python
def get_open_orders(self, symbol: Optional[str] = None) -> pd.DataFrame:
    """
    Get all open orders as DataFrame.
    
    Args:
        symbol (str, optional): Specific symbol to query. If None, queries all symbols
                               in lista_nemos (e.g., ['LINK', 'AVAX'] → queries LINKUSDT and AVAXUSDT)
    
    Returns:
        pd.DataFrame: All open orders across queried symbols
    """
    try:
        all_orders = []
        
        if symbol is None:
            # ✅ Query all symbols in lista_nemos (for pairs trading)
            symbols_to_query = [self.get_symbol(nemo) for nemo in self.lista_nemos]
        else:
            # Query specific symbol
            symbols_to_query = [symbol]
        
        for query_symbol in symbols_to_query:
            try:
                orders = self.client.futures_get_open_orders(symbol=query_symbol)
                if orders:
                    all_orders.extend(orders)
            except BinanceAPIException as e:
                # Log but continue with other symbols
                print(f"[ERROR] Failed to get orders for {query_symbol}: {e}")
                continue
        
        if all_orders:
            df_orders = pd.DataFrame(all_orders)
            df_orders.set_index('orderId', inplace=True)
            df_orders['exchange'] = 'BINANCE_PERP'
            
            print(f"[ORDERS PERP] Found {len(all_orders)} open orders across {len(symbols_to_query)} symbols")
            for order in all_orders:
                print(f"              {order['symbol']}: {order['side']} {order['origQty']} @ {order['price']} (ID: {order['orderId']})")
            
            self.record_api_success()
            return df_orders
        else:
            return pd.DataFrame()
    ...
```

**Resultado:** Ahora monitorea órdenes de TODOS los pares configurados.

---

### **Cambio 6: Mejorar `get_trades()` para múltiples símbolos**

**Ubicación:** `src/binance_perp.py`, línea ~975

**Antes:**
```python
def get_trades(self, symbol: Optional[str] = None, limit: int = 50) -> pd.DataFrame:
    """Get recent trades."""
    try:
        if symbol is None:
            symbol = self.get_symbol()  # ❌ Solo consulta UN símbolo
        
        trades = self.client.futures_account_trades(symbol=symbol, limit=limit)
        ...
```

**Después:**
```python
def get_trades(self, symbol: Optional[str] = None, limit: int = 50) -> pd.DataFrame:
    """
    Get recent trades as DataFrame.
    
    Args:
        symbol (str, optional): Specific symbol to query. If None, queries all symbols
                               in lista_nemos (e.g., ['LINK', 'AVAX'] → queries LINKUSDT and AVAXUSDT)
        limit (int): Max trades per symbol
    
    Returns:
        pd.DataFrame: All trades across queried symbols
    """
    try:
        all_trades = []
        
        if symbol is None:
            # ✅ Query all symbols in lista_nemos (for pairs trading)
            symbols_to_query = [self.get_symbol(nemo) for nemo in self.lista_nemos]
        else:
            # Query specific symbol
            symbols_to_query = [symbol]
        
        for query_symbol in symbols_to_query:
            try:
                trades = self.client.futures_account_trades(
                    symbol=query_symbol,
                    limit=limit
                )
                
                if trades:
                    for trade in trades:
                        # Parse and add to all_trades
                        ...
                        all_trades.append({
                            ...
                            'symbol': query_symbol,  # ✅ Track which pair
                            ...
                        })
            except BinanceAPIException as e:
                # Log but continue with other symbols
                print(f"[ERROR] Failed to get trades for {query_symbol}: {e}")
                continue
        ...
```

**Resultado:** Ahora obtiene trades de TODOS los pares configurados.

---

## 🎯 Impacto de los Cambios

### ✅ Beneficios:

1. **Símbolos válidos:**
   - LINK → `LINKUSDT` ✅
   - AVAX → `AVAXUSDT` ✅
   - Sin más errores de "Invalid symbol"

2. **Soporte completo para Pairs Trading:**
   - Órdenes se colocan en el par correcto
   - Monitoreo de órdenes para TODOS los pares
   - Trades históricos de TODOS los pares

3. **Backward compatible:**
   - Si solo se usa un símbolo, funciona como antes
   - Parámetro `nemo` es opcional
   - `get_symbol()` sin parámetros usa el primer símbolo

4. **Quote currency consistente:**
   - Siempre usa USDT como cotización
   - Configurable si se necesita cambiar en el futuro

### 📊 Ejemplo de Uso:

**Configuración:**
```python
lista_nemos = ['LINK', 'AVAX']
lista_bolsas = ['BINANCEFTS']
```

**Comportamiento:**

| Operación | Antes | Después |
|-----------|-------|---------|
| `get_symbol()` | `'LINKAVAX'` ❌ | `'LINKUSDT'` ✅ |
| `get_symbol('LINK')` | N/A | `'LINKUSDT'` ✅ |
| `get_symbol('AVAX')` | N/A | `'AVAXUSDT'` ✅ |
| `place_market_order(nemo='LINK')` | Error ❌ | Orden en LINKUSDT ✅ |
| `place_market_order(nemo='AVAX')` | Error ❌ | Orden en AVAXUSDT ✅ |
| `get_open_orders()` | Solo LINKAVAX ❌ | LINKUSDT + AVAXUSDT ✅ |
| `get_trades()` | Solo LINKAVAX ❌ | LINKUSDT + AVAXUSDT ✅ |

---

## 🧪 Testing

### Verificar que funciona:

```bash
cd /Users/diegoochoa/Library/CloudStorage/OneDrive-Personal/AQM/MR_HFT_Python
source PairsTrading/bin/activate
python src/AQM_MR_Live.py
```

**Esperado:**
- ✅ Sin errores de "Invalid symbol"
- ✅ Circuit breaker NO se abre
- ✅ Logs muestran símbolos correctos:
  ```
  [ORDER PERP] Placing MARKET BUY 10 LINKUSDT
  [ORDER PERP] Placing MARKET SELL 10 AVAXUSDT
  [ORDERS PERP] Found 2 open orders across 2 symbols
                LINKUSDT: BUY 10 @ 14.03 (ID: 123456)
                AVAXUSDT: SELL 10 @ 14.58 (ID: 123457)
  ```

---

## 📝 Consideraciones Futuras

### Si se necesita cambiar el quote currency:

**Opción 1: Parámetro en constructor**
```python
class BinancePerpetualTrader:
    def __init__(self, lista_nemos, testnet=False, quote_currency='USDT'):
        self.quote_currency = quote_currency
        ...
    
    def get_symbol(self, nemo=None):
        if nemo is None:
            nemo = self.lista_nemos[0]
        return nemo + self.quote_currency  # 'LINKBUSD', 'AVAXUSD', etc.
```

**Opción 2: Quote currency por nemo**
```python
def __init__(self, lista_nemos, quote_currencies=None):
    self.lista_nemos = lista_nemos
    self.quote_currencies = quote_currencies or ['USDT'] * len(lista_nemos)
    
def get_symbol(self, nemo=None):
    if nemo is None:
        nemo = self.lista_nemos[0]
    idx = self.lista_nemos.index(nemo)
    return nemo + self.quote_currencies[idx]
```

---

## 📚 Referencias

- **Binance Futures API**: https://binance-docs.github.io/apidocs/futures/en/
- **Binance Error Codes**: https://binance-docs.github.io/apidocs/futures/en/#error-codes
- **Error -1121**: Invalid symbol - El símbolo no existe en el exchange
- **Pairs Trading**: Estrategia que opera dos activos correlacionados simultáneamente

---

**Fecha**: 6 de enero de 2026  
**Issue**: `APIError(code=-1121): Invalid symbol` (LINKAVAX)  
**Fix**: Opción A - Modificar `get_symbol()` para aceptar `nemo` y retornar `nemo + 'USDT'`  
**Estado**: ✅ Completado y listo para testing
