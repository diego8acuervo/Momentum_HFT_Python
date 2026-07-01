# Runbook: Levantar MR_HFT_Python en Windows desde cero

## Prerequisitos

| Herramienta | Version minima | Notas |
|---|---|---|
| Python | 3.13.x | Descargar de python.org. Marcar "Add to PATH" en el instalador |
| Git | 2.40+ | git-scm.com. Incluir Git Bash |
| VSCode | latest | Con extensiones Python y Jupyter |
| Acceso SSH/HTTPS a GitHub | -- | Para clonar el repo privado `Market_Makers` |

## 1. Clonar el repositorio

```powershell
cd C:\Users\%USERNAME%\Projects   # o la ruta que prefieras
git clone https://github.com/d8aBkcap/MR_HFT_Python.git
cd MR_HFT_Python
```

## 2. Crear y activar el entorno virtual

```powershell
python -m venv .venv
.venv\Scripts\activate
```

Verificar que Python apunta al venv:
```powershell
python --version
# Debe mostrar Python 3.13.x
where python
# Debe apuntar a .venv\Scripts\python.exe
```

## 3. Instalar dependencias

El archivo `requirements.txt` fue generado con `pip freeze` en macOS y contiene rutas locales de conda que no funcionan en Windows. Instalar las dependencias limpias:

```powershell
# Dependencias core (instalar en este orden)
pip install --upgrade pip setuptools wheel

# Ciencia de datos y ML
pip install numpy pandas scipy scikit-learn statsmodels
pip install matplotlib seaborn bokeh panel

# Jupyter
pip install ipykernel ipywidgets ipympl jupyterlab

# Trading y exchanges
pip install python-binance binance-futures-connector
pip install pycryptodome websockets websocket-client
pip install yfinance

# APIs de datos
pip install kaikosdk grpcio grpcio-tools
pip install requests aiohttp

# Utilidades
pip install python-dotenv pydantic pydantic-settings
pip install tqdm joblib sortedcontainers
pip install Flask Jinja2
pip install openpyxl h5py
pip install beautifulsoup4
pip install dateparser pytz tzdata
pip install colorama rich

# Deep Learning (opcional, solo si usas modelos Keras)
pip install keras tensorflow

# Paquete privado Market_Makers
pip install git+https://github.com/diego8acuervo/Market_Makers.git
```

> **Nota sobre `uvloop`:** Este paquete NO funciona en Windows. Los scripts que lo importan funcionaran sin el (asyncio usa el event loop default de Windows). Si algun import falla, agregar al inicio del script:
> ```python
> try:
>     import uvloop
> except ImportError:
>     pass
> ```

## 4. Configurar variables de entorno

```powershell
copy .env.example .env
```

Editar `.env` con tus credenciales reales:

| Variable | Descripcion |
|---|---|
| `KAIKO_API_KEY` | API key de Kaiko (datos de mercado) |
| `COINAPI_KEY` | API key de CoinAPI |
| `BINANCE_AQM_API_KEY` / `BINANCE_AQM_SECRET_KEY` | Binance live |
| `BINANCE_API_KEY` / `BINANCE_SECRET_KEY` | Binance live (compat) |
| `BINANCE_TESTNET_API_KEY` / `BINANCE_TESTNET_SECRET_KEY` | Binance testnet |
| `BITGET_API_KEY` / `BITGET_SECRET_KEY` / `BITGET_PASSPHRASE` | Bitget live |
| `BITGET_PAPER_API_KEY` / `BITGET_PAPER_SECRET_KEY` / `BITGET_PAPER_PASSPHRASE` | Bitget paper trading |

**NUNCA commitear el archivo `.env`** (ya esta en `.gitignore`).

## 5. Configurar Jupyter Kernel

```powershell
python -m ipykernel install --user --name mr_hft --display-name "MR HFT Python"
```

## 6. Crear directorios de salida

```powershell
mkdir outputs 2>nul
mkdir src\outputs 2>nul
mkdir notebooks\outputs 2>nul
```

## 7. Datos de mercado

Los archivos CSV en `data/` (formato `{TICKER}_ohlcv_{YYYYMMDD}.csv`) son datos OHLCV descargados via Kaiko/CoinAPI. Estos archivos no estan en el repo (git-ignored).

Para regenerar datos, ejecutar el notebook:
```
notebooks/kaikoData.ipynb
```
O el script:
```powershell
python src/Datos.py
```

> Requiere API keys configuradas en `.env`.

## 8. Estructura del proyecto

```
MR_HFT_Python/
  src/                    # Codigo fuente principal
    Datos.py              # Descarga de datos (Kaiko/CoinAPI)
    Estrategia.py         # Logica de estrategia mean reversion
    Eventos.py            # Event-driven framework
    MRBacktest.py         # Motor de backtest
    CointegrationMonitor.py  # Monitor de cointegracion
    account_manager.py    # Gestion multi-cuenta
    trading.py            # Trading live
    binance_perp.py       # Trader Binance perpetuos
    binance_spot.py       # Trader Binance spot
    bitget_perp.py        # Trader Bitget perpetuos
    turtle_risk.py        # Position sizing Turtle N
    performance.py        # Metricas de rendimiento
    LiveMonitor.py        # Dashboard live
    AQM_MR_Live.py        # Entry point live trading
  notebooks/
    MR_Backtest.ipynb     # Backtest mean reversion (principal)
    Momentum_Backtest.ipynb  # Backtest momentum
    StatArb_Notebook.ipynb   # Analisis stat arb
    kaikoData.ipynb       # Descarga datos Kaiko
    live_dashboard.ipynb  # Dashboard live
  data/                   # CSVs de mercado (git-ignored)
  outputs/                # Resultados de backtest (git-ignored)
  tests/                  # Tests de integracion
  .env.example            # Template de variables de entorno
  requirements.txt        # Dependencias (pip freeze de macOS)
```

## 9. Verificar instalacion

```powershell
# Test rapido de imports
python -c "import numpy, pandas, statsmodels, sklearn, matplotlib; print('Core OK')"
python -c "from binance.client import Client; print('Binance OK')"
python -c "import kaikosdk; print('Kaiko OK')"
python -c "from dotenv import load_dotenv; load_dotenv(); print('.env loaded OK')"
```

## 10. Ejecutar backtests

Abrir en VSCode y seleccionar el kernel "MR HFT Python":
- **Mean Reversion:** `notebooks/MR_Backtest.ipynb`
- **Momentum:** `notebooks/Momentum_Backtest.ipynb`

O desde terminal:
```powershell
jupyter nbconvert --to notebook --execute notebooks/MR_Backtest.ipynb
```

## 11. Ejecutar trading live (solo cuando estes listo)

```powershell
python src/AQM_MR_Live.py
```

> Asegurate de que las API keys estan configuradas y que entiendes los riesgos antes de ejecutar en modo live.

---

## Troubleshooting Windows

| Problema | Solucion |
|---|---|
| `uvloop` no instala | Normal en Windows. Ignorar, asyncio usa SelectorEventLoop |
| `ModuleNotFoundError: Market_Makers` | Reinstalar: `pip install git+https://github.com/diego8acuervo/Market_Makers.git` |
| Jupyter no encuentra el kernel | `python -m ipykernel install --user --name mr_hft` |
| `grpcio` falla al compilar | Instalar Visual C++ Build Tools o usar `pip install --only-binary :all: grpcio` |
| Paths con espacios fallan | Usar rutas sin espacios o encerrar en comillas |
| `.env` no se carga | Verificar que esta en la raiz del proyecto, no en `src/` |
| `h5py` no instala | `pip install --only-binary :all: h5py` |
| SSL errors con Binance/Bitget | Actualizar certifi: `pip install --upgrade certifi` |
