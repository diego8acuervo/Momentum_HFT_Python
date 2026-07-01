# CoinApiDs period_id AttributeError Fix

## 📋 Problema Original

```python
Error en Ciclo de trading: 'CoinApiDs' object has no attribute 'period_id'
```

### Contexto del Error:

**Flujo completo:**
```
1. Estrategia.calcular_senal_pares() genera señales LARGO/CORTO
2. Portafolio.genera_orden(senal) recibe la señal
3. Portafolio.calcular_unidades(senal) intenta calcular tamaño de posición
4. calcular_unidades() necesita:
   - self.velas.period_id ❌ (línea 250 PortAQMHFT.py)
   - self.velas.get_historic_price() ❌ (línea 252 PortAQMHFT.py)
   - self.velas.get_symbol_id() ❌ (línea 251 PortAQMHFT.py)
```

**Ubicación del error:**
- **Archivo**: `src/PortAQMHFT.py`
- **Línea**: 250
- **Método**: `calcular_unidades()`
- **Código problemático**:
  ```python
  print(f"📊 Calculando unidades para {nemo}, frecuencia: {self.velas.period_id}")
  symbol_id = self.velas.get_symbol_id(nemo, 'BINANCEFTS', 'PERP')   
  candles = self.velas.get_historic_price(symbol_id,'1HRS', self.fecha_inicial, self.fecha_final, limit=10000)
  ```

### Causa Raíz:

`CoinApiDs` fue diseñado solo para **streaming en vivo** (WebSocket), NO incluía:
- ✅ Atributo `period_id`
- ✅ Método `get_historic_price()` (REST API)
- ✅ Método `get_symbol_id()` (constructor de symbol IDs)

Estos métodos son necesarios para que `PortAQMHFT.calcular_unidades()` pueda:
1. Obtener datos históricos de 1 hora
2. Calcular el ATR (Average True Range) de 14 períodos
3. Dimensionar las órdenes basadas en el riesgo del portafolio

---

## ✅ Solución Implementada

### Cambio 1: Agregar atributo `period_id`

**Ubicación**: `src/Datos.py`, clase `CoinApiDs.__init__()`, línea ~2486

**Antes**:
```python
# Basic attributes
self.eventos = eventos
self.lista_bolsas = lista_bolsas
self.lista_libros = lista_libros
self.lista_nemos = lista_nemos
self.base_asset = 'USDT'
self.interval = interval
```

**Después**:
```python
# Basic attributes
self.eventos = eventos
self.lista_bolsas = lista_bolsas
self.lista_libros = lista_libros
self.lista_nemos = lista_nemos
self.base_asset = 'USDT'
self.interval = interval
self.period_id = interval  # Add period_id for compatibility with PortAQMHFT
```

**Resultado**: `self.velas.period_id` ahora retorna `'1MIN'` (o el interval configurado).

---

### Cambio 2: Agregar método `get_symbol_id()`

**Ubicación**: `src/Datos.py`, clase `CoinApiDs`, línea ~3008

**Código agregado**:
```python
def get_symbol_id(self, nemo, bolsa=None, libro=None):
    """
    Build CoinAPI symbol ID from components
    
    Parameters:
    -----------
    nemo : str
        Base asset symbol (e.g., 'BTC', 'LINK')
    bolsa : str, optional
        Exchange ID (e.g., 'BINANCEFTS'). Uses first from lista_bolsas if None
    libro : str, optional
        Book type (e.g., 'PERP'). Uses first from lista_libros if None
    
    Returns:
    --------
    str : Symbol ID (e.g., 'BINANCEFTS_PERP_BTC_USDT')
    """
    bolsa = bolsa or self.lista_bolsas[0]
    libro = libro or self.lista_libros[0]
    return f"{bolsa}_{libro}_{nemo}_{self.base_asset}"
```

**Resultado**: 
- `get_symbol_id('LINK', 'BINANCEFTS', 'PERP')` → `'BINANCEFTS_PERP_LINK_USDT'`

---

### Cambio 3: Agregar método `_format_time_for_coinapi()`

**Ubicación**: `src/Datos.py`, clase `CoinApiDs`, línea ~3029

**Código agregado**:
```python
def _format_time_for_coinapi(self, time_input):
    """
    Format time for CoinAPI REST API
    
    Parameters:
    -----------
    time_input : datetime or str
        Time to format
    
    Returns:
    --------
    str : ISO 8601 format without microseconds (e.g., '2025-01-06T12:00:00Z')
    """
    if isinstance(time_input, datetime):
        # Remove microseconds and add Z for UTC
        return time_input.replace(microsecond=0).isoformat() + 'Z'
    elif isinstance(time_input, str):
        # Assume already formatted or parse if needed
        return time_input
    else:
        raise ValueError(f"Unsupported time format: {type(time_input)}")
```

**Resultado**: Convierte `datetime(2025, 1, 6, 12, 0)` → `'2025-01-06T12:00:00Z'`

---

### Cambio 4: Agregar método `get_historic_price()`

**Ubicación**: `src/Datos.py`, clase `CoinApiDs`, línea ~3050

**Código agregado**:
```python
def get_historic_price(self, symbol_id: str, period_id: str, 
                time_start, time_end, limit: int = None) -> pd.DataFrame:
    """
    Get historical OHLCV data from CoinAPI REST API
    
    Parameters:
    -----------
    symbol_id : str
        CoinAPI symbol ID (e.g., 'BINANCEFTS_PERP_BTC_USDT')
    period_id : str
        Time period (e.g., '1MIN', '5MIN', '1HRS', '1DAY')
    time_start : datetime or str
        Start time (datetime object or ISO 8601 string)
    time_end : datetime or str
        End time (datetime object or ISO 8601 string)
    limit : int, optional
        Maximum number of candles to return
    
    Returns:
    --------
    pd.DataFrame : DataFrame with OHLCV data indexed by time_period_start
                   Columns: open, high, low, close, volume, trades
    
    Note:
    -----
    - Uses CoinAPI REST API (not WebSocket) for historical data
    - Requires COINAPI_KEY environment variable
    """
    import requests
    
    # Format times for CoinAPI
    time_start_str = self._format_time_for_coinapi(time_start)
    time_end_str = self._format_time_for_coinapi(time_end)
    
    url = f"{self.baseUrl}/v1/ohlcv/{symbol_id}/history"
    params = {
        'period_id': period_id,
        'time_start': time_start_str,
        'time_end': time_end_str
    }
    
    if limit is not None:
        params['limit'] = limit
    
    headers = {'X-CoinAPI-Key': self.apiKey}
    
    try:
        logger.info(f"Fetching historical data: {symbol_id} ({period_id}) from {time_start_str} to {time_end_str}")
        response = requests.get(url, headers=headers, params=params, timeout=30)
        
        if response.status_code != 200:
            logger.error(f"CoinAPI REST error: {response.status_code}")
            try:
                error_detail = response.json()
                logger.error(f"Error detail: {error_detail}")
            except:
                logger.error(f"Response text: {response.text[:200]}")
            return pd.DataFrame()
        
        data = response.json()
        
        if not data:
            logger.warning(f"No historical data returned for {symbol_id}")
            return pd.DataFrame()
        
        # Convert to DataFrame
        df = pd.DataFrame(data)
        
        # Rename columns to match expected format
        column_mapping = {
            'price_open': 'open',
            'price_high': 'high',
            'price_low': 'low',
            'price_close': 'close',
            'volume_traded': 'volume',
            'trades_count': 'trades'
        }
        df.rename(columns=column_mapping, inplace=True)
        
        # Convert time columns to datetime
        df['time_period_start'] = pd.to_datetime(df['time_period_start'])
        df['time_period_end'] = pd.to_datetime(df['time_period_end'])
        
        if 'time_open' in df.columns:
            df['time_open'] = pd.to_datetime(df['time_open'])
        if 'time_close' in df.columns:
            df['time_close'] = pd.to_datetime(df['time_close'])
        
        # Set index
        df.set_index('time_period_start', inplace=True)
        
        logger.info(f"✅ Retrieved {len(df)} historical candles for {symbol_id}")
        return df
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error fetching historical data: {e}")
        return pd.DataFrame()
    except Exception as e:
        logger.error(f"Error processing historical data: {e}")
        return pd.DataFrame()
```

**Funcionalidad**:
1. Construye URL: `https://rest.coinapi.io/v1/ohlcv/{symbol_id}/history`
2. Formatea parámetros: `period_id`, `time_start`, `time_end`, `limit`
3. Hace request HTTP GET con API key
4. Convierte respuesta JSON a DataFrame
5. Renombra columnas: `price_open` → `open`, etc.
6. Indexa por `time_period_start`
7. Retorna DataFrame listo para calcular ATR

---

## 🎯 Impacto de los Cambios

### ✅ Beneficios:

1. **Compatibilidad con PortAQMHFT**:
   - ✅ `period_id` disponible
   - ✅ `get_historic_price()` implementado
   - ✅ `get_symbol_id()` funcional

2. **Cálculo de ATR funcional**:
   - PortAQMHFT puede obtener datos históricos de 1 hora
   - Calcula ATR de 14 períodos correctamente
   - Dimensiona órdenes basadas en volatilidad

3. **Arquitectura híbrida**:
   - **WebSocket**: Datos en tiempo real (OHLCV, trades, quotes, books)
   - **REST API**: Datos históricos para cálculos estadísticos (ATR, backtesting)

4. **No rompe funcionalidad existente**:
   - Streaming WebSocket sigue funcionando igual
   - Métodos existentes sin cambios
   - Backward compatible 100%

### 📊 Uso de APIs:

| Propósito | API | Método | Ejemplo |
|-----------|-----|--------|---------|
| Datos en vivo | WebSocket | `wss://ws.coinapi.io/v1/` | OHLCV streaming |
| Datos históricos | REST | `https://rest.coinapi.io/v1/ohlcv/{symbol}/history` | ATR calculation |

---

## 🧪 Testing

### Verificar que funciona:

```python
# En Python terminal o script de prueba
from Datos import CoinApiDs
import datetime
import queue

# Setup
cola_eventos = queue.Queue()
admin_datos = CoinApiDs(
    eventos=cola_eventos,
    lista_bolsas=['BINANCEFTS'],
    lista_libros=['PERP'],
    lista_nemos=['LINK', 'AVAX'],
    interval='1MIN'
)

# Test 1: period_id attribute
print(f"✅ period_id: {admin_datos.period_id}")  # Should print: 1MIN

# Test 2: get_symbol_id()
symbol_id = admin_datos.get_symbol_id('LINK', 'BINANCEFTS', 'PERP')
print(f"✅ symbol_id: {symbol_id}")  # Should print: BINANCEFTS_PERP_LINK_USDT

# Test 3: get_historic_price()
time_end = datetime.datetime.now()
time_start = time_end - datetime.timedelta(hours=24)

df = admin_datos.get_historic_price(
    symbol_id='BINANCEFTS_PERP_LINK_USDT',
    period_id='1HRS',
    time_start=time_start,
    time_end=time_end,
    limit=100
)

print(f"✅ Historical data: {len(df)} candles retrieved")
print(df.head())
print(f"✅ Columns: {list(df.columns)}")
```

**Output esperado**:
```
✅ period_id: 1MIN
✅ symbol_id: BINANCEFTS_PERP_LINK_USDT
Fetching historical data: BINANCEFTS_PERP_LINK_USDT (1HRS) from 2025-01-05T12:00:00Z to 2025-01-06T12:00:00Z
✅ Retrieved 24 historical candles for BINANCEFTS_PERP_LINK_USDT
✅ Historical data: 24 candles retrieved
                          open    high     low   close    volume  trades
time_period_start                                                          
2025-01-05 12:00:00  25.34  25.45  25.20  25.38  12345.0     150
...
✅ Columns: ['time_period_end', 'open', 'high', 'low', 'close', 'volume', 'trades']
```

---

## 📝 Próximos Pasos

### Ejecutar el sistema completo:

```bash
cd /Users/diegoochoa/Library/CloudStorage/OneDrive-Personal/AQM/MR_HFT_Python
source PairsTrading/bin/activate
python src/AQM_MR_Live.py
```

**Esperado**:
- ✅ Sin errores `'CoinApiDs' object has no attribute 'period_id'`
- ✅ calcular_unidades() funciona correctamente
- ✅ Órdenes se generan con tamaño calculado por ATR
- ✅ Sistema opera normalmente

### Si aparecen nuevos errores:

1. **CoinAPI rate limits**: Limitar requests históricos
2. **Symbol ID inválido**: Verificar formato del exchange/nemo
3. **Sin datos históricos**: Verificar que el símbolo existe en CoinAPI
4. **API key inválida**: Verificar `COINAPI_KEY` environment variable

---

## 📚 Referencias

- **CoinAPI REST API docs**: https://docs.coinapi.io/market-data/rest-api/
- **CoinAPI WebSocket docs**: https://docs.coinapi.io/market-data/websocket/
- **ATR (Average True Range)**: Indicador de volatilidad usado para dimensionar posiciones
- **Kelly Criterion**: Fórmula usada en `calculate_position_size()` para optimizar tamaño

---

**Fecha**: 6 de enero de 2026  
**Issue**: `'CoinApiDs' object has no attribute 'period_id'`  
**Fix**: Agregados `period_id`, `get_symbol_id()`, `get_historic_price()`  
**Estado**: ✅ Completado y listo para testing
