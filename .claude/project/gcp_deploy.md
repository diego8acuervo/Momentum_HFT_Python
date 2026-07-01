---
description: "GCP Deployment — Momentum Multi-Asset Trading to Google Cloud Platform. Load whenever the user asks about deploying, scaling, monitoring, or operating the momentum trading system in GCP. Covers: AQM_Momentum_Live.py (single-process daily rebalancer), BigQuery logging, Secret Manager, Artifact Registry, Cloud Build CI/CD, and its independent VM alongside the MR deployment."
---

# SKILL: GCP Deployment — Momentum Trading System

**scope:** Deploying the multi-asset momentum crypto trading system to Google
Cloud Platform. Independent deployment from the MR pairs-trading repo — own
VM, own Docker image, own Secret Manager namespace — sharing only the GCP
project and BigQuery dataset.

**GCP project:** `aqm-trading-prod` (shared with MR) | **region:** `europe-west1` | **zone:** `europe-west1-b`

**Split from MR_HFT_Python:** 2026-07-01 — see the split plan for full file
provenance. This repo's `src/`, `Estrategia.py`/`PortAQMHFT.py`/`trading.py`/
`LiveMonitor.py` base classes, and shared infra (`Datos.py`, `ejecucion.py`,
`account_manager.py`, ...) are duplicated copies of the MR repo's versions at
split time — they will drift independently going forward.

---

## ARCHITECTURE OVERVIEW

Momentum is a **single long-running process**, not a multi-subprocess
orchestrator like MR. `MomentumTrading` (subclass of `LiveTrading`) contains
its own internal daily-rebalance loop — no `momentum_orchestrator.py` exists
or is needed.

```
+-------------------------------------------------------------+
|  GCE e2-medium VM (Container-Optimized OS)                  |
|  Name: aqm-momentum-vm | Zone: europe-west1-b               |
|                                                             |
|  +----------------------------------+                       |
|  |  AQM_Momentum_Live.py            |  <-- direct entrypoint|
|  |  * MomentumStrategy (weights)    |                       |
|  |  * MomentumPortfolio (sizing)    |                       |
|  |  * MomentumTrading (event loop,  |                       |
|  |    daily rebalance @ 00:00 UTC)  |                       |
|  |  * MomentumLiveMonitor           |                       |
|  +--------+--------------------------+                       |
|           v                                                 |
|     bq_logger.py (async flush to BigQuery)                  |
+-------------+-------------------------------------------+---+
              |
    +---------v----------+
    |  BigQuery            |
    |  trading dataset      |  <-- SHARED dataset with MR, momentum_* tables
    |  (momentum_weights,   |
    |   momentum_rebalances,|
    |   momentum_pnl_snap-  |
    |   shots, momentum_    |
    |   alerts)              |
    +---------------------+

    Secret Manager (aqm-momentum-*) --> os.environ (via gcp_secrets.py)
    Artifact Registry (aqm-trading/momentum-hft) --> Docker image (via Cloud Build)
```

---

## S0 — FILE MAP

| File | Status | Purpose |
|------|--------|---------|
| `src/AQM_Momentum_Live.py` | LIVE | CLI entrypoint — direct Dockerfile ENTRYPOINT, no orchestrator |
| `src/MomentumStrategy.py` | LIVE | Signal generation (tanh / turtle N-weight) |
| `src/MomentumPortfolio.py` | LIVE | Weight → order sizing |
| `src/MomentumTrading.py` | LIVE | Event loop + internal daily-rebalance scheduling |
| `src/MomentumLiveMonitor.py` | LIVE | `live_state_momentum.json` snapshot writer |
| `src/bq_logger.py` | LIVE | Async BigQuery writer (duplicated from MR repo) |
| `src/gcp_secrets.py` | LIVE | Secret Manager → os.environ (duplicated from MR repo) |
| `config/momentum_production.yaml` | REFERENCE ONLY | Documents intended params — NOT loaded by code (no `--config` flag exists) |
| `Dockerfile` | LIVE | Python 3.13-slim, ENTRYPOINT `AQM_Momentum_Live.py` directly |
| `cloudbuild.yaml` | LIVE | Build image + push to Artifact Registry + update `aqm-momentum-vm` |
| `infra/*.tf` | NOT YET APPLIED | Terraform: own VM/IAM/secrets/BQ tables, reuses MR's dataset + artifact repo |

---

## S1 — CLI REFERENCE

See `Momentum_Strategy.md` §11.1 for the full flag table. Production defaults
are documented in `config/momentum_production.yaml`'s `cli_equivalent` block
and baked into the Dockerfile `CMD`.

```bash
python src/AQM_Momentum_Live.py \
  --tokens AAVE,AIXBT,AVAX,...  --capital 100000 --variant turtle \
  --short-window 5 --long-window 30 --max-weight 0.10 --stop-loss -0.15 \
  --rebalance-utc 0 --min-rebalance 0.005 --batch-n 3 --batch-interval 600 \
  --limit-offset-bps 2 --exchange binance --account binance_momentum \
  --state-file live_state_momentum.json --interval 1d \
  --gcp --gcp-project aqm-trading-prod --gcp-dataset trading
```

---

## S2 — SECRET MANAGER

Naming convention: `aqm-momentum-{VARIABLE_NAME}` (separate namespace from
MR's `aqm-trading-*`).

| Secret Name | Maps to os.environ | Status |
|---|---|---|
| `aqm-momentum-MOMENTUM_API_KEY` | `MOMENTUM_API_KEY` | Populate before first deploy |
| `aqm-momentum-MOMENTUM_SECRET_KEY` | `MOMENTUM_SECRET_KEY` | Populate before first deploy |

`account_manager.Account.BINANCE_MOMENTUM` maps these `src_*` vars to the
canonical `BINANCE_API_KEY`/`BINANCE_SECRET_KEY` env vars the trader reads —
see `Multi_Account_Manager.md` §3.

Fallback behavior identical to MR: if Secret Manager is unavailable, falls
back to `load_dotenv()` from `.env`.

---

## S3 — BIGQUERY SCHEMA (momentum-only tables, shared `trading` dataset)

Dataset: `aqm-trading-prod.trading` (same dataset as MR — see `infra/bigquery.tf`,
which references it via a Terraform data source rather than creating it,
avoiding a duplicate-resource conflict with the MR repo's own Terraform state).

### `momentum_weights`
timestamp TIMESTAMP, account STRING, symbol STRING, variant STRING,
target_weight FLOAT64, current_weight FLOAT64, weight_delta FLOAT64,
direction STRING, position_value FLOAT64

### `momentum_rebalances`
timestamp TIMESTAMP, account STRING, variant STRING, n_long INT64,
n_short INT64, n_flat INT64, gross_exposure FLOAT64, net_exposure FLOAT64,
n_orders INT64, turnover FLOAT64, duration_s FLOAT64

### `momentum_pnl_snapshots`
timestamp TIMESTAMP, account STRING, cash FLOAT64, total_equity FLOAT64,
commission_total FLOAT64, unrealized_pnl FLOAT64, realized_pnl FLOAT64

### `momentum_alerts`
timestamp TIMESTAMP, account STRING, alert_type STRING, severity STRING,
message STRING

---

## S4 — DOCKERFILE

```dockerfile
FROM python:3.13-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ gfortran libopenblas-dev git && \
    rm -rf /var/lib/apt/lists/*
COPY requirements-prod.txt .
RUN pip install --no-cache-dir -r requirements-prod.txt
COPY src/ src/
COPY config/ config/
RUN mkdir -p outputs src/outputs notebooks
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src
ENTRYPOINT ["python", "src/AQM_Momentum_Live.py"]
CMD [...production flags, see Dockerfile...]
```

Same production-import-guard pattern as MR: `bkcap.py`, `Datos.py`,
`PortAQMHFT.py` (via `performance.py`) all guard matplotlib/seaborn imports
with `try/except ImportError`.

---

## S5 — CI/CD (Cloud Build)

### Manual Build & Deploy
```bash
gcloud builds submit --config=cloudbuild.yaml --project=aqm-trading-prod \
  --substitutions=_TAG=<tag> .
gcloud compute instances reset aqm-momentum-vm --zone=europe-west1-b \
  --project=aqm-trading-prod
```

Pipeline steps and rollback procedure are identical to MR's (see MR repo's
`gcp_deploy.md` §S6) — same Artifact Registry repo (`aqm-trading`), different
image name (`momentum-hft`) and target VM (`aqm-momentum-vm`).

---

## S6 — FIRST-TIME DEPLOYMENT CHECKLIST

1. `cd infra && terraform init && terraform plan` — review before apply
   (creates: `aqm-momentum-vm`, `aqm-momentum-sa`, `aqm-momentum-*` secrets,
   4 new BQ tables in the *existing* `trading` dataset; does NOT create a new
   BigQuery dataset or Artifact Registry repo — those are referenced from MR's
   stack via data sources).
2. Populate `aqm-momentum-MOMENTUM_API_KEY` / `aqm-momentum-MOMENTUM_SECRET_KEY`
   secret versions from `.env` (same `gcloud secrets versions add` pattern as
   MR's `gcp_deploy.md` §S3).
3. `gcloud builds submit --config=cloudbuild.yaml ...` for the first image push.
4. Set up the Cloud Build trigger (push to this repo's deploy branch) — one-time,
   analogous to MR's setup.
5. Add a "Momentum" page to the existing Looker Studio report (MR's `gcp_deploy.md`
   §S7), pointed at the new `momentum_*` tables.

---

## CROSS-REFERENCE

| When you need | Use |
|---|---|
| Strategy logic, signal generation, sizing | `Momentum_Strategy.md` skill |
| Account presets, credential flow | `Multi_Account_Manager.md` skill |
| Exchange connector internals | `docs/*.md` (shared with MR repo) |
| MR (sibling repo) deployment reference | `MR_HFT_Python/gcp_deploy.md` |
