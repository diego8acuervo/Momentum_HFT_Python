# Momentum_HFT_Python

Multi-asset daily momentum crypto trading system (Turtle N-Weight / Tanh
variants) on Binance/Bitget USDT-M Futures.

Split out of `MR_HFT_Python` on 2026-07-01 so the Momentum and Mean
Reversion strategies can evolve, deploy, and be operated independently.
Shared infrastructure (`Eventos.py`, `Datos.py`, `ejecucion.py`,
`account_manager.py`, exchange handlers, base strategy/portfolio/monitor
classes, GCP logging) was duplicated as-is from `MR_HFT_Python` at split
time — the two repos no longer share history and will diverge over time.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
cp .env.example .env        # fill in MOMENTUM_API_KEY / MOMENTUM_SECRET_KEY

python src/account_manager.py                    # validate credentials
python src/AQM_Momentum_Live.py --account binance_momentum --paper
```

See `Momentum_Strategy.md` for full strategy documentation (signal
generation, sizing, execution, diagnostics) and `Multi_Account_Manager.md`
for the credential system. For GCP deployment, see `gcp_deploy.md`.

## Repo layout

| Path | Purpose |
|---|---|
| `src/AQM_Momentum_Live.py` | CLI entrypoint (single long-running process, internal daily rebalance loop) |
| `src/MomentumStrategy.py` / `MomentumPortfolio.py` / `MomentumTrading.py` / `MomentumLiveMonitor.py` | Momentum-specific strategy classes |
| `src/Estrategia.py` / `PortAQMHFT.py` / `trading.py` / `LiveMonitor.py` | Shared base classes (duplicated from `MR_HFT_Python`) |
| `src/Datos.py`, `ejecucion.py`, `account_manager.py`, exchange handlers | Shared infra (duplicated from `MR_HFT_Python`) |
| `notebooks/Momentum_Backtest.ipynb` | Research/backtest notebook |
| `config/momentum_production.yaml` | Reference-only production param doc (not loaded by code) |
| `infra/*.tf`, `Dockerfile`, `cloudbuild.yaml` | Independent GCP deployment (own VM, shared project/dataset with MR) |
