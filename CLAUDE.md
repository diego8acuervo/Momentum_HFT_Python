# Momentum_HFT_Python

Multi-asset daily momentum crypto trading system. Split out of the sibling
`MR_HFT_Python` repo (Mean Reversion pairs trading) on 2026-07-01 — see
`README.md` for the split rationale and repo layout.

## Key facts

- Entry point: `src/AQM_Momentum_Live.py` — a single long-running process
  with an internal daily-rebalance loop (`MomentumTrading`). There is no
  orchestrator/subprocess-per-asset model like the MR repo has.
- `config/momentum_production.yaml` is reference documentation only —
  `AQM_Momentum_Live.py` has no `--config` flag; production params are
  passed as CLI flags (see the YAML's `cli_equivalent` block, or
  `Momentum_Strategy.md` §11.1).
- `src/Estrategia.py`, `PortAQMHFT.py`, `trading.py`, `LiveMonitor.py` are
  duplicated whole from the MR repo, including MR-only classes
  (`PairsTradingHFT`, `CryptoMarketMaker`, `XEMM_BTC` in `Estrategia.py`)
  that are unused dead code here — `MomentumStrategy`/`MomentumPortfolio`/
  `MomentumTrading`/`MomentumLiveMonitor` only use the base classes.
- GCP deployment is independent from MR: own VM (`aqm-momentum-vm`), own
  Secret Manager namespace (`aqm-momentum-*`), but shares the
  `aqm-trading-prod` GCP project, the `trading` BigQuery dataset (via
  momentum-only tables), and the `aqm-trading` Artifact Registry repo (new
  image name `momentum-hft`). See `gcp_deploy.md`.

## Skills / reference docs

- `Momentum_Strategy.md` — strategy logic, signal generation, sizing,
  execution, diagnostics. Load for any strategy-behavior question.
- `Multi_Account_Manager.md` — credential system (`account_manager.py`),
  shared and unmodified from MR.
- `gcp_deploy.md` — deployment architecture, CI/CD, BigQuery schema.

## Conventions inherited from MR_HFT_Python

- Español output / código en inglés for strategy-skill responses (see
  `Momentum_Strategy.md` frontmatter).
- Never hardcode exchange credentials — always route through
  `account_manager.activate_account()`.
- Shared modules here are point-in-time duplicates of the MR repo, not a
  package dependency — changes to shared logic must be applied to both
  repos manually if needed in both.
