---
name: momentum_strategy
description: >
  Full operational context for the crypto momentum strategy in this repo.
  Covers signal construction (tanh momentum), dollar-neutral daily rebalancing,
  transaction cost modeling, backtest framework (notebooks/Momentum_Backtest.ipynb),
  parameter sensitivity analysis, and production weight inspection.
  Load whenever the user asks to modify, debug, backtest, or extend the momentum
  strategy — even when the skill is not named explicitly.

compatibility:
  tools: [bash, python, git, jupyter]
  dependencies:
    - pandas, numpy, matplotlib, seaborn
    - Binance Public API (daily OHLCV, no auth required)
    - Internal: src/Crypto_momentum.py, notebooks/Momentum_Backtest.ipynb
  language: español (output), código en inglés
---

# SKILL: Crypto Momentum Strategy — Daily Cross-Sectional

**scope:** Dollar-neutral daily-rebalanced momentum on Binance USDT-perpetual spot prices.  
**fuentes de datos:** `src/Crypto_momentum.py` (fetch + signal), `notebooks/Momentum_Backtest.ipynb` (analysis).  
**outputs esperados:** Portfolio weights per asset, equity curve, KPI dashboard, sensitivity heatmaps, monthly P&L heatmap, latest weights snapshot.

---

## CÓMO USAR ESTE SKILL

Carga este archivo cuando el contexto involucre:
- Modificar la señal de momentum o los lookback windows.
- Agregar nuevos activos al universo.
- Correr o extender el backtest.
- Inspeccionar los pesos más recientes para ejecución.
- Agregar ejecución live al sistema de momentum.

```
§0  ARQUITECTURA GENERAL          ← leer primero
§1  SEÑAL & CONSTRUCCIÓN DE PESOS ← núcleo de la estrategia
§2  BACKTEST ENGINE               ← función run_momentum_backtest()
§3  KPIs & MÉTRICAS               ← compute_kpis() y benchmarks
§4  VISUALIZACIONES               ← secciones del notebook
§5  SENSIBILIDAD DE PARÁMETROS    ← grid search short × long
§6  PESOS MÁS RECIENTES           ← snapshot para ejecución
§7  UNIVERSO & DATOS              ← fetch_binance_daily_closes()
§8  EXTENSIONES PLANIFICADAS      ← siguientes fases
§9  HISTORIAL DE DESARROLLO       ← commits y decisiones clave
```

---

## §0 — ARQUITECTURA GENERAL

```
src/Crypto_momentum.py
  └── fetch_binance_daily_closes(symbol, start, end) → pd.Series
  └── signal + backtest engine (inline script, duplicado en notebook)

notebooks/Momentum_Backtest.ipynb
  ├── Cell 1  : Imports & Config (SHORT_WINDOW, LONG_WINDOW, FEE_RATE)
  ├── Cell 2  : Universe definition (sector metadata)
  ├── Cell 3  : Data fetch (loops TOKENS → df_prices)
  ├── Cell 4  : run_momentum_backtest() pure function
  ├── Cell 5  : compute_kpis() helper
  ├── Cell 6  : Equity curve + drawdown chart → outputs/Momentum_equity_curve.png
  ├── Cell 7  : Per-asset contribution table (styled Pandas)
  ├── Cell 8  : KPI dashboard (4-panel tiles)
  ├── Cell 9  : Single-asset signal chart (price / signal+weight / cumul contrib)
  ├── Cell 10 : Parameter sensitivity heatmaps → outputs/Momentum_sensitivity.png
  └── Cell 11 : Monthly P&L heatmap → outputs/Momentum_monthly_heatmap.png
  └── Cell 12 : Latest portfolio weights (table + bar chart)

outputs/
  ├── Momentum_equity_curve.png
  ├── Momentum_sensitivity.png
  └── Momentum_monthly_heatmap.png
```

**Principio de diseño:** backtest engine como función pura (`run_momentum_backtest`) — sin estado, sin clase. Facilita el grid-search en Cell 10 y la reutilización en scripts externos.

---

## §1 — SEÑAL & CONSTRUCCIÓN DE PESOS

### 1.1 Momentum signal

```python
# (price[t - short_w] - price[t - long_w]) / price[t - long_w]
df_momo = (df_prices.shift(short_w) - df_prices.shift(long_w)) / df_prices.shift(long_w)
```

- `short_w` (default 5): ventana rápida — precio hace N días.
- `long_w`  (default 30): ventana lenta — precio hace M días.
- La señal es el retorno entre dos puntos del pasado, no el retorno reciente.

### 1.2 Transformación tanh → pesos brutos

```python
df_raw_weights = np.tanh(df_momo)     # bounded en (-1, +1)
df_raw_weights = df_raw_weights.where(df_prices.notna(), 0).fillna(0)
```

`tanh` aplana las señales extremas (outliers de momentum no generan pesos desproporcionados).

### 1.3 Normalización (gross exposure = 1.0)

```python
total_abs  = df_raw_weights.abs().sum(axis=1)
df_weights = df_raw_weights.div(total_abs.replace(0, np.nan), axis=0).fillna(0)
```

Después de normalizar: `df_weights.abs().sum(axis=1) == 1.0` (dólar-neutral, gross exposure = 1).  
**No hay leverage implícito** — la estrategia usa 100% de capital en bruto (long + short = 1x en cada lado = 0.5x neto por lado).

### 1.4 Shift de pesos (anti-lookahead)

```python
df_weights_shifted = df_weights.shift(1).fillna(0)
```

Los pesos calculados en `t` se aplican a los retornos de `t+1`. Sin esto la estrategia tiene lookahead bias.

### 1.5 Costos de transacción

```python
weight_changes     = df_weights_shifted.diff().abs().sum(axis=1)
weight_changes.iloc[0] = df_weights_shifted.iloc[0].abs().sum()   # día 1
transaction_fees   = weight_changes * FEE_RATE    # FEE_RATE = 0.0005
portfolio_net      = portfolio_gross - transaction_fees
```

**Trampa conocida:** `diff()` en el primer día produce NaN → se reemplaza con la suma de pesos absolutos del día 0 (costo de construcción inicial de la cartera).

---

## §2 — BACKTEST ENGINE

### 2.1 Firma de la función

```python
def run_momentum_backtest(
    df_prices: pd.DataFrame,
    short_w:   int   = 5,
    long_w:    int   = 30,
    fee_rate:  float = 0.0005,
    eval_years: int  = 5,
) -> dict:
```

### 2.2 Outputs del dict

| Clave | Tipo | Descripción |
|-------|------|-------------|
| `eval_returns` | `pd.Series` | Net daily returns en ventana de evaluación |
| `cum_returns`  | `pd.Series` | Cumulative NAV (inicia en 1.0) |
| `drawdowns`    | `pd.Series` | Drawdown fraccional (≤ 0) |
| `weights_shifted` | `pd.DataFrame` | Pesos efectivamente usados cada día (lag-1) |
| `df_weights`   | `pd.DataFrame` | Pesos calculados en t (a ejecutar mañana) |
| `df_momo`      | `pd.DataFrame` | Raw momentum signal por asset |
| `df_returns`   | `pd.DataFrame` | Daily returns por asset |
| `weight_changes` | `pd.Series` | Turnover diario (suma |Δw|) |
| `fees`         | `pd.Series` | Costos diarios aplicados |
| `short_w`, `long_w` | `int` | Parámetros del run |

### 2.3 Ventana de evaluación

```python
cutoff       = df_prices.index[-1] - pd.DateOffset(years=eval_years)
eval_returns = port_net.loc[cutoff:]
```

El backtest corre sobre toda la historia disponible, pero las métricas se calculan solo sobre los últimos `eval_years` años. Esto requiere `START_DATE` con padding suficiente (`long_w + max_grid_long` días antes del cutoff).

---

## §3 — KPIs & MÉTRICAS

### 3.1 compute_kpis()

```python
def compute_kpis(results: dict) -> dict:
    # total_return : cr[-1] - 1
    # cagr         : cr[-1] ^ (365.25/n) - 1
    # max_dd       : drawdowns.min()
    # sortino      : (mean_ret*365) / (downside_std*sqrt(365))
    # sharpe       : (mean_ret*365) / (std_ret*sqrt(365))
    # total_fees   : fees[eval_window].sum()
    # short_w, long_w: propagados para sensitivity analysis
```

### 3.2 Benchmarks de producción (baseline 5d/30d, 26 assets, 5yr eval, Jun 2026)

| KPI | Valor observado | Interpretación |
|-----|-----------------|----------------|
| Total Return | +333% | |
| CAGR | +34% | |
| Max Drawdown | -61% | alta — crypto bull/bear ciclos |
| Sortino | 1.21 | razonable para long/short puro |
| Sharpe | 0.77 | |
| Total Fee Drag | ~25% | 0.05% × alto turnover diario |

### 3.3 Alertas de salud

```python
# Fee drag > 30% del gross return → revisar FEE_RATE o reducir turnover
gross_return = (results['eval_returns'] + results['fees'].loc[eval_idx]).sum()
fee_drag_pct = results['fees'].loc[eval_idx].sum() / gross_return
if fee_drag_pct > 0.30:
    print(f"WARNING: fee drag {fee_drag_pct*100:.1f}% — considerar filtro de turnover mínimo")

# Sortino < 0.5 → señal débil o ventanas mal calibradas
if kpis['sortino'] < 0.5:
    print(f"WARNING: Sortino={kpis['sortino']:.2f} — revisar short_w/long_w en sensitivity cell")

# Max DD > 70% → posible crisis sistémica (crypto 2022)
if kpis['max_dd'] < -0.70:
    print(f"INFO: MaxDD={kpis['max_dd']*100:.1f}% — verificar fechas de drawdown con monthly heatmap")
```

---

## §4 — VISUALIZACIONES (SECCIONES DEL NOTEBOOK)

| Sección | Output | Descripción |
|---------|--------|-------------|
| Cell 6 — Equity Curve | `outputs/Momentum_equity_curve.png` | 2-panel: NAV azul + DD rojo; anotación de retorno final y CAGR |
| Cell 7 — Contribution Table | Display in-notebook | Per-asset: Cum Contrib, Ann %, Avg Weight, Avg Signal, Hit Rate, Sharpe; gradiente RdYlGn |
| Cell 8 — KPI Dashboard | Display in-notebook | 4 tiles grises: Total Return / CAGR / Sortino / Max DD |
| Cell 9 — Signal Chart | Display in-notebook | 3 paneles: precio / señal+peso (twin axes) / cumul contrib por asset |
| Cell 10 — Sensitivity | `outputs/Momentum_sensitivity.png` | Heatmap 3×3 con CAGR, Sortino, MaxDD para cada (short_w, long_w) |
| Cell 11 — Monthly Heatmap | `outputs/Momentum_monthly_heatmap.png` | Año × mes, retorno mensual, anotación anual al margen |
| Cell 12 — Latest Weights | Display in-notebook | Tabla estilizada + barras horizontales: Signal(t) vs Executed(t-1) |

**Paleta de colores estándar** (consistente con `MR_Backtest.ipynb`):
- `#1565C0` — líneas de equity / precios
- `#C62828` — drawdown / pérdidas
- `#2E7D32` — ganancias / KPIs positivos
- `#E65100` — señales / alertas intermedias
- `#F5F5F5` — fondo de tiles KPI

---

## §5 — SENSIBILIDAD DE PARÁMETROS

### 5.1 Grid de búsqueda (Cell 10)

```python
SHORT_WINDOWS = [5, 7, 14]
LONG_WINDOWS  = [21, 30, 60]
# Combinaciones válidas: sw < lw → 9 de 9 combinaciones
```

### 5.2 Heatmap pivots

```python
def make_heatmap_pivot(df, col):
    return df.pivot(index='long_w', columns='short_w', values=col)
# Filas = long_w (21, 30, 60)
# Columnas = short_w (5, 7, 14)
```

### 5.3 Interpretación del grid

- **CAGR:** buscar celda verde máxima sin que esté aislada (puede ser overfitting).
- **Sortino:** más robusto que CAGR — prefiere la región estable.
- **MaxDD:** evitar celdas con DD > -70% aunque el CAGR sea alto.
- **Regla práctica:** la configuración baseline (5d, 30d) debe estar en la zona "buena" del grid. Si queda en un extremo, replantear.

---

## §6 — PESOS MÁS RECIENTES (Cell 12)

### 6.1 Distinción clave

```python
# df_weights.iloc[-1]       → señal de HOY, a ejecutar mañana al open
# weights_shifted.iloc[-1]  → pesos que se aplicaron AYER (último bar del backtest)
```

### 6.2 Checks de sanidad antes de ejecutar

```python
w = results['df_weights'].iloc[-1]
assert abs(w.abs().sum() - 1.0) < 1e-6, "Gross exposure ≠ 1.0 — bug en normalización"
assert not w.isna().any(), "NaN en pesos — asset sin precio en la fecha más reciente"
assert (w.abs() > 0).sum() > 5, "Menos de 5 assets con peso — revisar datos"

print(f"Net exposure:  {w.sum()*100:+.2f}%  (debe ser ~0% para dólar-neutral)")
print(f"Gross exposure:{w.abs().sum()*100:.2f}%  (debe ser ~100%)")
print(f"Max long:  {w.max()*100:.2f}%  {w.idxmax()}")
print(f"Max short: {w.min()*100:.2f}%  {w.idxmin()}")
```

### 6.3 Conversión a notional (para ejecución futura)

```python
CAPITAL = 100_000   # USD
positions = w * CAPITAL   # USD por asset (positivo=long, negativo=short)
# positions['BTC'] = +8500  → long $8500 BTC
# positions['DOGE'] = -3200 → short $3200 DOGE
```

---

## §7 — UNIVERSO & DATOS

### 7.1 Universo actual (26 activos cargados, Jun 2026)

AAVE, AIXBT, AVAX, BCH, BNB, BTC, COMP, DOGE, DOT, DYDX, EIGEN, ENA, ETH, ETHFI, FORM, INJ, JUP, LTC, NEAR, PNUT, RAY, SOL, SUI, TRX, UNI, WIF

**Skipped (no están en Binance Spot/Futures):** XAU, XAG, NVDA, AMZN, GOOG.

### 7.2 fetch_binance_daily_closes (src/Crypto_momentum.py)

```python
def fetch_binance_daily_closes(symbol, start_date, end_date) -> pd.Series | None:
    url     = "https://api.binance.com/api/v3/klines"
    ticker  = f"{symbol}USDT"
    # Pagina en bloques de 1000 barras
    # Retorna None si HTTP 400 (símbolo no existe)
    # Retorna pd.Series con índice DatetimeIndex y valores float (close price)
```

**Trampa conocida:** símbolos macro (XAU, XAG) devuelven HTTP 400 → `None`. El loop de fetch imprime "Skipped" y los excluye de `df_prices`.

### 7.3 Padding de inicio

```python
# START_DATE debe tener al menos max(LONG_WINDOWS) días antes de la fecha de evaluación
# Con eval_years=5 y TODAY=2026-06-17:
#   cutoff = 2021-06-17
#   necesitas datos desde al menos 2021-06-17 - 60 días = 2021-04-18
# START_DATE = '2018-01-01' provee margen suficiente para todo el grid
```

---

## §8 — EXTENSIONES PLANIFICADAS

### 8.1 Ejecución live (próxima fase)

La estrategia actualmente es solo backtest. Para conectar a ejecución live:

```python
# Paso 1: calcular pesos del día (ya disponible con run_momentum_backtest o inline)
w_today = results['df_weights'].iloc[-1]   # señal de hoy

# Paso 2: calcular Δweight vs posición actual en exchange
w_prev  = exchange.get_current_weights()   # normalizado igual que df_weights
delta_w = w_today - w_prev

# Paso 3: convertir Δweight a órdenes
for asset, dw in delta_w.items():
    if abs(dw) * CAPITAL > MIN_ORDER_SIZE:
        direction = 'buy' if dw > 0 else 'sell'
        qty = abs(dw) * CAPITAL / price[asset]
        exchange.place_order(asset + 'USDT', direction, qty)
```

### 8.2 Rebalanceo intradía vs. EOD

- **Modelo actual:** rebalanceo al close de cada día (daily).
- **Mejora posible:** ejecutar al open del día siguiente (más realista) — ya implementado via `shift(1)`.
- **Extensión:** rebalanceo semanal para reducir turnover y fees.

### 8.3 Filtros adicionales

| Filtro | Justificación |
|--------|--------------|
| Mínimo de liquidez (volumen > $Xm/día) | Evitar activos con spread amplio |
| Cap de peso por asset (e.g., max 15%) | Evitar concentración en un activo |
| Filtro de volatilidad (excluir si σ > 20%/día) | Evitar activos en crash/spike |
| Momentum de largo plazo (1yr) como filtro de régimen | No tomar momentum en bear market |

### 8.4 Integración con Mean Reversion

La cartera MR y Momentum son complementarias (MR es market-neutral por par, Momentum es cross-sectional). Una asignación conjunta podría:
- Usar MR para el alpha de corto plazo (15m).
- Usar Momentum para el tilt direccional de largo plazo (daily).
- Gestionar el capital total entre ambas con CPPI o target volatility.

---

## §9 — HISTORIAL DE DESARROLLO

| Sesión | Fecha | Hito |
|--------|-------|------|
| Creación de `src/Crypto_momentum.py` | Pre Jun 2026 | Script standalone: fetch → signal → tanh weights → backtest → KPIs |
| `notebooks/Momentum_Backtest.ipynb` | Jun 2026 | Notebook de análisis creado basado en estructura de `MR_Backtest.ipynb` |
| Cell 12 — Latest Weights | Jun 2026 | Snapshot de pesos más recientes: tabla estilizada + bar chart Signal(t) vs Executed(t-1) |
| **Status** | Jun 2026 | Solo backtest. Ejecución live pendiente. Baseline: Short=5d, Long=30d, CAGR=+34%, Sortino=1.21, MaxDD=-61%. |

---

## REFERENCIA RÁPIDA

| Cuando necesites | Archivo / Función |
|-----------------|-------------------|
| Modificar la señal o lookbacks | `notebooks/Momentum_Backtest.ipynb` Cell 1 (`SHORT_WINDOW`, `LONG_WINDOW`) |
| Agregar activos al universo | Cell 1 (`TOKENS` list) + Cell 2 (`UNIVERSE_META` dict) |
| Re-correr el backtest | Cell 4 (`run_momentum_backtest`) → Cell 5 (`compute_kpis`) |
| Ver pesos actuales para ejecución | Cell 12 (`results['df_weights'].iloc[-1]`) |
| Explorar sensibilidad de parámetros | Cell 10 (grid `SHORT_WINDOWS × LONG_WINDOWS`) |
| Ver comportamiento de un activo | Cell 9 (`SHOW_ASSET = 'BTC'`) |
| Función de fetch de datos | `src/Crypto_momentum.py::fetch_binance_daily_closes` |
| Outputs visuales | `notebooks/outputs/Momentum_*.png` |
