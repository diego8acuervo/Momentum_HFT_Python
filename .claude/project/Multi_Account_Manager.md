---
name: multi_account_credential_manager
description: >
  Provides full operational context for the multi-account / multi-exchange
  credential system in this repo (src/account_manager.py) and its wiring into
  the live launcher (src/AQM_MR_Live.py). Covers the Account enum, the validated
  registry, the activate_account() bridge that injects credentials into the
  canonical env vars the trader classes read, the --account CLI preset, the
  .env / .env.example / .gitignore hygiene, and the secret-rotation playbook.
  MUST be loaded whenever the user asks to add a trading account or API key,
  switch between live/testnet/paper, debug "API key not found" errors, rotate
  leaked secrets, or wire a new exchange handler to credentials — even when the
  skill is not named explicitly.

compatibility:
  tools: [bash, python, git, dotenv]
  dependencies:
    - python-dotenv
    - Internal: src/account_manager.py, src/AQM_MR_Live.py, src/binance_perp.py,
      src/bitget_perp.py, src/ejecucion.py, src/trading.py
    - .env (runtime secrets, git-ignored), .env.example (template, tracked)
  language: español (output), código en inglés
---

# SKILL: Multi-Account Credential Manager (account_manager)

**scope:** Carga, validación y enrutamiento de credenciales API para trading
multi-cuenta / multi-exchange (Binance USD-M, Bitget USDT-M), incluyendo modos
live / testnet / paper.
**fuentes de datos:** `src/account_manager.py` (núcleo), `src/AQM_MR_Live.py`
(wiring CLI), `src/binance_perp.py` y `src/bitget_perp.py` (consumidores de
credenciales), `.env` (secretos en runtime), `.env.example` (plantilla).
**outputs esperados:** Un `AccountConfig` validado, variables de entorno
canónicas pobladas (`BINANCE_API_KEY`, `BITGET_API_KEY`, …), un reporte de
validación por cuenta, y flags de ruteo (`exchange` / `testnet` / `paper`)
propagados al launcher.

---

## CÓMO USAR ESTE SKILL

Carga este archivo cada vez que el contexto involucre **credenciales, API keys,
selección de cuenta o exchange, o errores de autenticación**. El principio
rector es: **`account_manager.py` es la única fuente de verdad de credenciales.**
Las clases de trading (`BinancePerpetualTrader`, `BitgetPerpetualTrader`) **no**
deben elegir qué llaves usar; solo leen las variables canónicas que
`activate_account()` ya pobló.

```
Multi_Account_Manager.md        ← estás aquí (router + formato de salida)

§0   REGLA FUNDAMENTAL          ← LEER PRIMERO SIEMPRE
§1   MODELO MENTAL              ← qué resuelve y por qué existe
§2   ARQUITECTURA & FLUJO       ← account_manager → env → traders
§3   EL REGISTRY                ← estructura de cada cuenta
§4   activate_account() BRIDGE  ← el puente src_* → env_* (canónicas)
§5   WIRING EN AQM_MR_Live.py   ← flag --account y orden de resolución
§6   AÑADIR UNA CUENTA NUEVA    ← receta paso a paso
§7   USO / CLI                  ← comandos de validación y ejecución
§8   HIGIENE DE SECRETOS        ← .env, .env.example, .gitignore, rotación
§9   MEMORIA INSTITUCIONAL      ← bugs reales y fixes
§10  CROSS-REFERENCE            ← archivos relacionados
§11  CHANGELOG                  ← historial de cambios del skill
```

---

## §0 — REGLA FUNDAMENTAL

**Nunca hardcodees `os.environ.get("BINANCE_API_KEY")` en código de estrategia
o ejecución para *decidir* qué cuenta usar.** Toda selección de cuenta pasa por
`account_manager.activate_account()` **antes** de construir cualquier trader.
Si una credencial falta: **DETENTE**, reporta la cuenta y las variables exactas
que faltan (el `EnvironmentError` ya lo hace), y solicita confirmación antes de
continuar. Jamás imprimas una API key completa en logs — usa `masked_key()`.

**Por qué existe esta regla — bugs reales encontrados en este repo:**

| Caso | Bug encontrado | Síntoma | Fix |
|------|---------------|---------|-----|
| Typo en testnet key (`binance_perp.py`, commit `9a01ee8`) | `load_testnet_binance_key()` leía `BINANCE__API_KEY` (doble guion bajo) en vez de `BINANCE_TESTNET_API_KEY` | Testnet siempre lanzaba `ValueError: API key not found` | Corregido a `BINANCE_TESTNET_API_KEY` + centralizado en account_manager |
| `.env` con llaves reales en historial git | `.env` commiteado en `abb76f5…bf315be`, removido luego en `4abfcde` | Secretos vivos accesibles en `origin/master` y `origin/claude/recursing-hypatia` | Rotar llaves (§8) + `git filter-repo` |
| Credencial duplicada en `.env` | `BINANCE_API_KEY` == `BINANCE_AQM_API_KEY` (mismas llaves) | Ambigüedad sobre cuál cuenta corre | Registry mapea `src=BINANCE_AQM_*` → `env=BINANCE_API_KEY` |
| Sintaxis `.env` inválida | Comentario con `//` en vez de `#` | dotenv no parsea la línea | Usar siempre `#` para comentarios en `.env` |

> Esta tabla es la **memoria institucional** del skill. Añade cada bug
> nuevo cuando se confirme en producción.

---

## §1 — MODELO MENTAL

`account_manager.py` resuelve tres problemas que antes estaban dispersos:

1. **Correr la misma estrategia contra varias cuentas / API keys** sin tocar el
   código de los traders.
2. **Validar que todas las llaves existen** *antes* de un run en vivo (fail
   fast, no a mitad de una orden).
3. **Cambiar entre live / testnet / paper desde un solo flag** (`--account`).

La pieza clave es que cada cuenta lógica conoce **dos** juegos de variables:

| Rol | Prefijo | Ejemplo | Quién lo usa |
|-----|---------|---------|--------------|
| **Origen** (source of truth en `.env`) | `src_*` | `BINANCE_AQM_API_KEY` | `account_manager` lo lee |
| **Canónica** (la que el trader ya lee) | `env_*` | `BINANCE_API_KEY` | `BinancePerpetualTrader` la lee |

`activate_account()` copia `src_* → env_*`. Por eso **no hubo que modificar la
lógica interna de los traders** y aún así cualquier cuenta puede apuntar a
cualquier juego de llaves.

---

## §2 — ARQUITECTURA & FLUJO

```
                         ┌────────────────────────┐
   .env  (git-ignored)   │  BINANCE_AQM_API_KEY    │  ← src_*  (origen)
                         │  BINANCE_AQM_SECRET_KEY  │
                         │  BITGET_PAPER_* …        │
                         └───────────┬────────────┘
                                     │  load_dotenv()
                                     ▼
                       ┌──────────────────────────────┐
                       │     account_manager.py        │
                       │  Account enum + _REGISTRY      │
                       │  get_account() → valida        │
                       │  activate_account() → inyecta  │
                       └───────────────┬──────────────┘
                                       │  os.environ[env_key] = api_key
                                       ▼
                         ┌────────────────────────┐
   env canónicas         │  BINANCE_API_KEY        │  ← env_*  (canónica)
   (proceso en runtime)  │  BINANCE_SECRET_KEY      │
                         │  BITGET_PAPER_* …        │
                         └───────────┬────────────┘
                                     │  os.environ.get(...)
                                     ▼
        ┌───────────────────────────────────────────────────────┐
        │  AQM_MR_Live.py  →  LiveTrading (trading.py)            │
        │     →  traderPerp (ejecucion.py)                        │
        │        →  BinancePerpetualTrader  (testnet flag)         │
        │        →  BitgetPerpetualTrader   (paper_trading flag)   │
        └───────────────────────────────────────────────────────┘
```

**Secuencia obligatoria:** `activate_account()` **siempre** corre antes de
construir `LiveTrading`. En `AQM_MR_Live.py` esto está garantizado porque la
resolución de `--account` ocurre justo después de `parser.parse_args()`.

---

## §3 — EL REGISTRY

Cada cuenta es una entrada en `_REGISTRY` (`src/account_manager.py`). Campos:

| Campo | Significado |
|-------|-------------|
| `exchange` | `"binance"` o `"bitget"` — determina qué handler se construye |
| `testnet` | flag de ruteo Binance (`BinancePerpetualTrader(testnet=…)`) |
| `paper` | flag de ruteo Bitget (`BitgetPerpetualTrader(paper_trading=…)`) |
| `label` | nombre legible para logs (p. ej. `Binance-AQM-Live`) |
| `src_key` / `src_secret` / `src_pass` | variables `.env` **origen** (verdad) |
| `env_key` / `env_secret` / `env_pass` | variables **canónicas** que lee el trader |

**Mapa actual (4 presets):**

| `Account` | exchange | testnet | paper | src_* (.env) | env_* (canónica) |
|-----------|----------|---------|-------|--------------|------------------|
| `BINANCE_LIVE` | binance | False | False | `BINANCE_AQM_API_KEY` / `…_SECRET_KEY` | `BINANCE_API_KEY` / `BINANCE_SECRET_KEY` |
| `BINANCE_TESTNET` | binance | True | False | `BINANCE_TESTNET_API_KEY` / `…_SECRET_KEY` | iguales |
| `BITGET_LIVE` | bitget | False | False | `BITGET_API_KEY` / `…_SECRET_KEY` / `…_PASSPHRASE` | iguales |
| `BITGET_PAPER` | bitget | False | True | `BITGET_PAPER_API_KEY` / `…_SECRET_KEY` / `…_PASSPHRASE` | iguales |

> Para los 3 presets cuyo `src_* == env_*`, la inyección es un no-op inofensivo.
> El valor del puente se materializa cuando `src_* ≠ env_*` (p. ej. `BINANCE_LIVE`
> lee `BINANCE_AQM_*` pero el trader lee `BINANCE_API_KEY`), o al añadir cuentas
> nuevas (§6).

---

## §4 — activate_account() BRIDGE

```python
from account_manager import activate_account

cfg = activate_account("binance_live")   # acepta str o Account
# Internamente:
#   1. get_account() lee src_* desde os.getenv y VALIDA (no vacíos)
#   2. os.environ[cfg.env_key]    = cfg.api_key
#      os.environ[cfg.env_secret] = cfg.secret
#      os.environ[cfg.env_passphrase] = cfg.passphrase   (si aplica, Bitget)
#   3. imprime línea enmascarada y devuelve AccountConfig

cfg.exchange   # "binance"        → lista_bolsas en el launcher
cfg.testnet    # False            → BinancePerpetualTrader(testnet=…)
cfg.paper      # False            → BitgetPerpetualTrader(paper_trading=…)
cfg.masked_key()  # "ycGioX…TFh2" → seguro para logs
```

**API pública de `account_manager`:**

| Función | Propósito |
|---------|-----------|
| `coerce_account(x)` | Normaliza `str`/`Account` → `Account` (valida) |
| `get_account(x)` | Resuelve + **valida**; lanza `EnvironmentError` si falta algo |
| `activate_account(x)` | `get_account` + inyecta a env canónicas; devuelve `AccountConfig` |
| `validate_all()` | Chequea las 4 cuentas e imprime estado; devuelve `{Account: bool}` |
| `available_accounts()` | Lista de strings para `argparse(choices=…)` |

---

## §5 — WIRING EN AQM_MR_Live.py

Tres puntos de integración (commit `9a01ee8`):

**1. Import (cabecera):**
```python
from account_manager import (
    Account, activate_account, validate_all, available_accounts,
)
```

**2. Argumento CLI (junto a `--exchange` / `--testnet` / `--paper`):**
```python
parser.add_argument(
    "--account", type=str, default=None,
    choices=available_accounts(),          # ['binance_live', 'binance_testnet', …]
    help="Named account preset (recommended). Overrides --exchange/--testnet/--paper.",
)
```

**3. Resolución (inmediatamente después de `args = parser.parse_args()`):**
```python
if args.account:
    validate_all()                         # imprime estado de TODAS las cuentas
    cfg = activate_account(args.account)   # inyecta llaves a env canónicas
    args.exchange = cfg.exchange           # override determinista
    args.testnet  = cfg.testnet
    args.paper    = cfg.paper
```

**Orden de precedencia:** `--account` **gana** sobre `--exchange`/`--testnet`/
`--paper`. Si no se pasa `--account`, el comportamiento legacy (flags sueltos)
se mantiene intacto → **backward-compatible**.

---

## §6 — AÑADIR UNA CUENTA NUEVA

Receta para, p. ej., una **segunda cuenta Binance live** con llaves distintas:

```text
1. .env  →  añade las llaves nuevas:
       BINANCE_ACCT2_API_KEY=...
       BINANCE_ACCT2_SECRET_KEY=...

2. account_manager.Account  →  añade el miembro del enum:
       BINANCE_LIVE_2 = "binance_live_2"

3. account_manager._REGISTRY  →  registra la cuenta:
       Account.BINANCE_LIVE_2: dict(
           exchange="binance", testnet=False, paper=False,
           label="Binance-Acct2-Live",
           src_key="BINANCE_ACCT2_API_KEY",        # ← llaves nuevas (origen)
           src_secret="BINANCE_ACCT2_SECRET_KEY",
           src_pass=None,
           env_key="BINANCE_API_KEY",              # ← canónica que lee el trader
           env_secret="BINANCE_SECRET_KEY",
           env_pass=None,
       )

4. .env.example  →  documenta las variables nuevas (sin secretos).

5. Verifica:
       python src/account_manager.py            # debe mostrar ✅ binance_live_2
       python src/AQM_MR_Live.py --account binance_live_2 --pair XRP,DOGE
```

**Regla de oro:** mantén `env_key`/`env_secret` apuntando a la variable canónica
que el handler de ese exchange/modo ya lee (Binance live → `BINANCE_API_KEY`;
Binance testnet → `BINANCE_TESTNET_API_KEY`; Bitget live → `BITGET_API_KEY`;
Bitget paper → `BITGET_PAPER_API_KEY`). Así no tocas código de traders.

---

## §7 — USO / CLI

```bash
# Validar credenciales de TODAS las cuentas (no arranca trading)
python src/account_manager.py

# Presets multi-cuenta (recomendado — valida + carga llaves antes de operar)
python src/AQM_MR_Live.py --account bitget_paper                 # default seguro
python src/AQM_MR_Live.py --account binance_testnet
python src/AQM_MR_Live.py --account binance_live  --pair XRP,DOGE
python src/AQM_MR_Live.py --account bitget_live   --pair XRP,DOGE

# Legacy (flags sueltos, aún soportado si NO se pasa --account)
python src/AQM_MR_Live.py --exchange bitget --paper --pair XRP,DOGE
```

**Salida de validación esperada:**
```
Account credential check:
  ✅ Binance-AQM-Live  | binance_live    | key=ycGioX…TFh2 | testnet=False paper=False
  ✅ Binance-Testnet   | binance_testnet | key=dITJfj…dLRv | testnet=True  paper=False
  ❌ bitget_live       | [Bitget-Live] Missing credentials in .env: ['BITGET_API_KEY', …]
  ❌ bitget_paper      | [Bitget-Paper] Missing credentials in .env: ['BITGET_PAPER_API_KEY', …]
```

Un `❌` no es un crash: indica exactamente qué variables faltan en `.env`.

---

## §8 — HIGIENE DE SECRETOS

**Estado del repo (`.gitignore`, commit `9a01ee8`):**
```gitignore
# Environment / secrets — never commit real credentials
.env
.env.*
!.env.example
```
- `.env` → **ignorado**, nunca se commitea.
- `.env.*` → ignora variantes (`.env.local`, `.env.prod`, …).
- `!.env.example` → la plantilla **sí** se versiona (sin secretos).

**Plantilla:** `.env.example` documenta cada variable requerida con valores
placeholder. Onboarding:
```bash
cp .env.example .env      # luego rellena tus llaves reales
```

**⚠️ Hallazgo de seguridad pendiente:** `.env` con llaves **reales** sigue en el
**historial** de `origin/master` y `origin/claude/recursing-hypatia` (commits
`abb76f5…bf315be`; un commit posterior `4abfcde` borró el archivo, pero los
blobs con secretos permanecen accesibles).

**Playbook de remediación:**
```bash
# 1) ROTAR todas las llaves (única mitigación 100% confiable):
#    Binance (AQM + testnet), Bitget, Bitget Paper, Kaiko, CoinAPI.
#    Actualiza tu .env local con las nuevas.

# 2) (Opcional) Limpiar el historial — DESTRUCTIVO, requiere force-push:
pip install git-filter-repo
git filter-repo --path .env --invert-paths --force
git push origin --force --all
git push origin --force --tags
```
> Limpiar el historial **no** des-filtra secretos ya clonados/forkeados. La
> rotación es obligatoria; el scrub es complementario.

---

## §9 — MEMORIA INSTITUCIONAL (BUGS & FIXES)

| Fecha | Componente | Bug → Fix |
|-------|-----------|-----------|
| 18-Jun-2026 | `binance_perp.py` | `load_testnet_binance_key()` leía `BINANCE__API_KEY` (typo) → corregido a `BINANCE_TESTNET_API_KEY` |
| 18-Jun-2026 | repo / `.env` | Secretos reales en historial `origin/master` → rotación + `git filter-repo` (§8) |
| 18-Jun-2026 | `.env` | Comentario con `//` (sintaxis inválida) y `BINANCE_API_KEY` duplicado de `BINANCE_AQM_*` → registry desambigua vía `src_*`→`env_*` |

> Actualiza esta tabla después de cada sesión significativa.

---

## §10 — CROSS-REFERENCE

| Cuando necesites | Usa |
|-----------------|-----|
| Definir / validar credenciales de cuentas | `src/account_manager.py` |
| Cambiar qué cuenta corre el launcher | `src/AQM_MR_Live.py` (flag `--account`) |
| Documentar variables requeridas | `.env.example` |
| Entender cómo el trader lee llaves Binance | `src/binance_perp.py` (`load_binance_key`, `load_testnet_binance_key`) |
| Entender cómo el trader lee llaves Bitget | `src/bitget_perp.py` (`paper_trading` branch) |
| Ver dónde se construyen los handlers | `src/ejecucion.py` (`traderPerp.__init__`) |
| Propagar `testnet` / `paper` al loop vivo | `src/trading.py` (`LiveTrading`) |
| Contexto de la estrategia que consume todo esto | `Mean_Reversion_Strategy.md` |

---

## §11 — CHANGELOG

| Fecha | Cambio |
|-------|--------|
| 18-Jun-2026 | **Creación.** `account_manager.py` (Account enum, `_REGISTRY`, `get_account`, `activate_account`, `validate_all`, `available_accounts`, `AccountConfig.masked_key`). Wiring de `--account` en `AQM_MR_Live.py`. Fix typo testnet en `binance_perp.py`. Nuevo `.env.example`. `.gitignore` endurecido (`.env`, `.env.*`, `!.env.example`). Commit `9a01ee8`. |
