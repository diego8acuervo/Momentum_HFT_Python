# Fix: KeyError 'BINANCEFTS' in api_health Dictionary

## 📋 Problema Original

### Error observado:
```
📊 LINK: 500 candles, last=13.38 | AVAX: 500 candles, last=13.99
Último Spread: 0.00865786341343977 = y:13.3795 - hr:0.9559819923917035 * x:13.9865
Último Z-score: 2.5664690026504924
Último Spread: 0.00865786341343977 = y:13.3795 - hr:0.9559819923917035 * x:13.9865
📝 Order logged: LINK sell 1636.661211129297 @ 13.3795
Error en Ciclo de trading: 'BINANCEFTS'
```

### 🔍 Causa Raíz:

**KeyError en `ejecucion.py` línea 309 (ahora línea 322 tras los cambios):**

```python
def check_api_health(self, exchange):
    health = self.api_health[exchange]  # ❌ KeyError: 'BINANCEFTS'
```

#### **Por qué ocurre:**

1. **EventoOrden creado con `bolsa='BINANCEFTS'`** en `PortAQMHFT.py`:
   ```python
   bolsa = 'BINANCEFTS'  # O desde lista_bolsas[0]
   orden = EventoOrden(nemo, tipo_orden, cantidad, direccion, precio, bolsa)
   ```

2. **ejecucion.py intenta verificar salud del API** (línea 523):
   ```python
   if not self.check_api_health(evento.bolsa):  # evento.bolsa = 'BINANCEFTS'
   ```

3. **Pero api_health solo tenía 'BINANCE' y 'BITSO'** (línea 155-169):
   ```python
   self.api_health = {
       'BINANCE': {...},   # ✅
       'BITSO': {...}      # ✅
       # ❌ FALTABA 'BINANCEFTS'
   }
   ```

4. **Resultado:** `KeyError: 'BINANCEFTS'` cuando se accede a clave inexistente

---

## ✅ Solución Implementada: Option 1 + Option 4 (Robust Solution)

### **Estrategia:**
- ✅ **Option 1:** Agregar exchanges faltantes al diccionario `api_health`
- ✅ **Option 4:** Agregar fallback defensivo en métodos que acceden al diccionario

---

## 🔧 Cambios Realizados

### **Cambio 1: Agregar exchanges al diccionario api_health**

**Archivo:** `src/ejecucion.py`  
**Ubicación:** Líneas 155-187 (método `__init__`)

**Antes:**
```python
self.api_health = {
    'BINANCE': {
        'status': 'HEALTHY',
        'last_error': None,
        'error_count': 0,
        'last_success': time.time()
    },
    'BITSO': {
        'status': 'HEALTHY',
        'last_error': None,
        'error_count': 0,
        'last_success': time.time()
    }
}
```

**Después:**
```python
self.api_health = {
    'BINANCE': {
        'status': 'HEALTHY',
        'last_error': None,
        'error_count': 0,
        'last_success': time.time()
    },
    'BINANCEFTS': {  # ✅ NUEVO
        'status': 'HEALTHY',
        'last_error': None,
        'error_count': 0,
        'last_success': time.time()
    },
    'BITSO': {
        'status': 'HEALTHY',
        'last_error': None,
        'error_count': 0,
        'last_success': time.time()
    },
    'BYBIT': {  # ✅ NUEVO (preparado para futuro)
        'status': 'HEALTHY',
        'last_error': None,
        'error_count': 0,
        'last_success': time.time()
    },
    'OKX': {  # ✅ NUEVO (preparado para futuro)
        'status': 'HEALTHY',
        'last_error': None,
        'error_count': 0,
        'last_success': time.time()
    }
}
```

**Beneficios:**
- ✅ Soporta BINANCEFTS (perpetuals)
- ✅ Tracking separado de salud para spot vs perpetuals
- ✅ Preparado para BYBIT, OKX y otros exchanges
- ✅ Circuit breaker independiente por exchange

---

### **Cambio 2: Fallback defensivo en check_api_health()**

**Archivo:** `src/ejecucion.py`  
**Ubicación:** Líneas 308-331 (método `check_api_health`)

**Antes:**
```python
def check_api_health(self, exchange):
    """
    Check if exchange API is healthy before sending orders.
    Implements circuit breaker pattern.
    
    Args:
        exchange (str): 'BINANCE' or 'BITSO'
        
    Returns:
        bool: True if API is healthy, False if circuit is open
    """
    health = self.api_health[exchange]  # ❌ Puede fallar con KeyError
    current_time = time.time()
    # ...
```

**Después:**
```python
def check_api_health(self, exchange):
    """
    Check if exchange API is healthy before sending orders.
    Implements circuit breaker pattern.
    
    Args:
        exchange (str): Exchange identifier (e.g., 'BINANCE', 'BINANCEFTS', 'BITSO', 'BYBIT', 'OKX')
        
    Returns:
        bool: True if API is healthy, False if circuit is open
    """
    # Defensive fallback: if exchange not in health dict, create default entry
    if exchange not in self.api_health:
        print(f"[WARNING] Exchange '{exchange}' not in api_health dict, adding with default HEALTHY status")
        self.api_health[exchange] = {
            'status': 'HEALTHY',
            'last_error': None,
            'error_count': 0,
            'last_success': time.time()
        }
    
    health = self.api_health[exchange]  # ✅ Ahora siempre existe
    current_time = time.time()
    # ...
```

**Beneficios:**
- ✅ No crashea si exchange no está en diccionario
- ✅ Crea entrada automáticamente con estado HEALTHY
- ✅ Log de warning para debugging
- ✅ Permite agregar exchanges dinámicamente

---

### **Cambio 3: Fallback defensivo en record_api_error()**

**Archivo:** `src/ejecucion.py`  
**Ubicación:** Líneas 354-371 (método `record_api_error`)

**Antes:**
```python
def record_api_error(self, exchange, error):
    """
    Record API error and potentially open circuit breaker.
    
    Args:
        exchange (str): 'BINANCE' or 'BITSO'
        error: Error message or exception
    """
    health = self.api_health[exchange]  # ❌ Puede fallar con KeyError
    health['error_count'] += 1
    health['last_error'] = time.time()
    # ...
```

**Después:**
```python
def record_api_error(self, exchange, error):
    """
    Record API error and potentially open circuit breaker.
    
    Args:
        exchange (str): Exchange identifier (e.g., 'BINANCE', 'BINANCEFTS', 'BITSO', 'BYBIT', 'OKX')
        error: Error message or exception
    """
    # Defensive fallback: if exchange not in health dict, create default entry
    if exchange not in self.api_health:
        print(f"[WARNING] Exchange '{exchange}' not in api_health dict, adding with default HEALTHY status")
        self.api_health[exchange] = {
            'status': 'HEALTHY',
            'last_error': None,
            'error_count': 0,
            'last_success': time.time()
        }
    
    health = self.api_health[exchange]  # ✅ Ahora siempre existe
    health['error_count'] += 1
    health['last_error'] = time.time()
    # ...
```

**Beneficios:**
- ✅ Consistente con `check_api_health()`
- ✅ Registra errores incluso para exchanges no inicializados
- ✅ No pierde información de errores

---

## 🎯 Flujo Completo Corregido

### **Escenario: Orden BINANCEFTS Perpetuals**

```
1. Señal generada → direccion='sell', nemo='LINK'

2. PortAQMHFT.genera_orden():
   bolsa = 'BINANCEFTS'  # Desde lista_bolsas[0] o default
   orden = EventoOrden('LINK', 'MKT', 1636.66, 'sell', 13.3795, 'BINANCEFTS')
   ✅ orden.bolsa = 'BINANCEFTS'

3. ejecucion.py recibe EventoOrden:
   print(f'Orden recibida en OMS: {evento.tipo_orden}-{evento.direccion} {evento.cantidad} {evento.nemo} @ {evento.precio} en {evento.bolsa}')
   ✅ Output: "Orden recibida en OMS: MKT-sell 1636.66 LINK @ 13.3795 en BINANCEFTS"

4. ejecucion.py verifica salud del API:
   if not self.check_api_health(evento.bolsa):  # evento.bolsa = 'BINANCEFTS'
   
5. check_api_health('BINANCEFTS'):
   a) Verifica si 'BINANCEFTS' está en api_health ✅ SÍ ESTÁ (tras fix)
   b) health = self.api_health['BINANCEFTS']  ✅ Funciona
   c) Retorna True (API está HEALTHY)

6. ejecucion.py rutea orden a handler correcto:
   if evento.bolsa in ['BINANCE', 'BINANCEFTS']:  ✅ True
       self.binance_handler.place_market_order(...)
   
7. Orden colocada exitosamente ✅
```

---

## 📊 Comparación: Antes vs Después

### **Antes del Fix:**

| Exchange | En api_health | check_api_health() | record_api_error() | Resultado |
|----------|---------------|--------------------|--------------------|-----------|
| BINANCE | ✅ Sí | ✅ Funciona | ✅ Funciona | ✅ OK |
| BITSO | ✅ Sí | ✅ Funciona | ✅ Funciona | ✅ OK |
| BINANCEFTS | ❌ No | ❌ KeyError | ❌ KeyError | ❌ CRASH |
| BYBIT | ❌ No | ❌ KeyError | ❌ KeyError | ❌ CRASH |

### **Después del Fix:**

| Exchange | En api_health | check_api_health() | record_api_error() | Resultado |
|----------|---------------|--------------------|--------------------|-----------|
| BINANCE | ✅ Sí | ✅ Funciona | ✅ Funciona | ✅ OK |
| BINANCEFTS | ✅ Sí | ✅ Funciona | ✅ Funciona | ✅ OK |
| BITSO | ✅ Sí | ✅ Funciona | ✅ Funciona | ✅ OK |
| BYBIT | ✅ Sí | ✅ Funciona | ✅ Funciona | ✅ OK |
| OKX | ✅ Sí | ✅ Funciona | ✅ Funciona | ✅ OK |
| NUEVO_EXCHANGE | ❌ No | ✅ Auto-crea entrada | ✅ Auto-crea entrada | ✅ OK (con warning) |

---

## 🔍 Detección de Exchanges Nuevos

Si en el futuro agregas un exchange no inicializado (ej: 'KRAKEN'), verás:

```
[WARNING] Exchange 'KRAKEN' not in api_health dict, adding with default HEALTHY status
Orden recibida en OMS: MKT-buy 100 BTC @ 50000 en KRAKEN
```

Esto te alerta para agregar la entrada manualmente en `__init__()`, pero **no causa crash**.

---

## ✅ Ventajas de la Solución Implementada

### **1. Multi-Exchange Support:**
- ✅ Soporta Binance Spot ('BINANCE')
- ✅ Soporta Binance Perpetuals ('BINANCEFTS')
- ✅ Soporta Bitso ('BITSO')
- ✅ Preparado para Bybit ('BYBIT')
- ✅ Preparado para OKX ('OKX')

### **2. Robustez:**
- ✅ No crashea con exchanges no inicializados
- ✅ Auto-crea entradas con estado HEALTHY
- ✅ Logs de warning para debugging
- ✅ Fallback en 2 métodos críticos

### **3. Circuit Breaker Independiente:**
- ✅ Cada exchange tiene su propio circuit breaker
- ✅ Si BINANCEFTS falla, BINANCE sigue operando
- ✅ Tracking separado de errores por exchange

### **4. Future-Proof:**
- ✅ Fácil agregar nuevos exchanges
- ✅ Defensive programming previene crashes
- ✅ Escalable a docenas de exchanges

---

## 🧪 Testing

### **Test 1: Orden BINANCEFTS (principal use case)**
```python
# Setup
orden = EventoOrden('LINK', 'MKT', 100, 'sell', 13.50, 'BINANCEFTS')

# Verificación
assert orden.bolsa == 'BINANCEFTS'
health_check = ejecucion.check_api_health('BINANCEFTS')
assert health_check == True  # ✅ No KeyError
assert 'BINANCEFTS' in ejecucion.api_health  # ✅ Existe
```

### **Test 2: Exchange no inicializado (defensive fallback)**
```python
# Setup
orden = EventoOrden('BTC', 'MKT', 1, 'buy', 50000, 'KRAKEN')

# Verificación
health_check = ejecucion.check_api_health('KRAKEN')
# ✅ No crashea
# ✅ Crea entrada automática
assert 'KRAKEN' in ejecucion.api_health
assert ejecucion.api_health['KRAKEN']['status'] == 'HEALTHY'
```

### **Test 3: Circuit breaker independiente**
```python
# Simular errores en BINANCEFTS
for i in range(5):
    ejecucion.record_api_error('BINANCEFTS', 'Connection timeout')

# Verificar estado
assert ejecucion.api_health['BINANCEFTS']['status'] == 'CIRCUIT_OPEN'  # ✅ Abierto
assert ejecucion.api_health['BINANCE']['status'] == 'HEALTHY'  # ✅ BINANCE no afectado
```

---

## 📝 Resumen de Archivos Modificados

1. **`src/ejecucion.py`**
   - Líneas 155-187: Agregados 'BINANCEFTS', 'BYBIT', 'OKX' a `self.api_health`
   - Líneas 308-331: Fallback defensivo en `check_api_health()`
   - Líneas 354-371: Fallback defensivo en `record_api_error()`
   - Actualizada documentación de métodos

---

## 🎯 Resultado Esperado

### **Log de ejecución exitosa:**
```
📊 LINK: 500 candles, last=13.38 | AVAX: 500 candles, last=13.99
Último Z-score: 2.5664690026504924
Último Spread: 0.00865786341343977 = y:13.3795 - hr:0.9559819923917035 * x:13.9865
📋 Señal 'CORTO' → Orden 'sell' para LINK
📊 Calculando unidades para LINK , frecuencia : 1m
✅ Retrieved 48 historical candles for BINANCEFTS_PERP_LINK_USDT
🔢 Unidades calculadas para LINK: 1636.66 unidades
📝 Order logged: LINK sell 1636.66 @ 13.3795
P Venta: 13.3795
Orden recibida en OMS: MKT-sell 1636.66 LINK @ 13.3795 en BINANCEFTS  ✅
[ORDER PERP] Placing MARKET SELL 1636.66 LINKUSDT
✅ Order placed: ID 12345678
```

**Ya no verás:**
```
Error en Ciclo de trading: 'BINANCEFTS'  ❌
```

---

**Fecha:** 7 de enero de 2026  
**Issue:** `KeyError: 'BINANCEFTS'` en `ejecucion.py`  
**Fix:** Agregados exchanges faltantes + fallback defensivo (Option 1 + Option 4)  
**Estado:** ✅ Completado y listo para testing
