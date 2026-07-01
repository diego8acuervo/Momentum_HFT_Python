# -*- coding: utf-8 -*-
"""
account_manager.py
==================

Centralized, validated registry for **multi-account / multi-exchange** trading
credentials.

Why this module exists
----------------------
Credentials used to be read ad-hoc with ``os.environ.get("BINANCE_API_KEY")``
scattered across ``binance_perp.py``, ``bitget_perp.py`` and ``ejecucion.py``.
That made it impossible to:

* run the same strategy against several accounts / API keys,
* validate that every required key is present *before* a live run,
* switch between live / testnet / paper trading from a single flag.

This module is the **single source of truth**. Each logical account maps to:

* the ``.env`` variables that hold its credentials (``src_*``),
* the routing flags the downstream traders expect (``exchange`` / ``testnet`` /
  ``paper``),
* the *canonical* environment variables the existing trader classes read
  (``env_*``).

``activate_account()`` copies the resolved credentials into those canonical
variables, so the existing ``BinancePerpetualTrader`` / ``BitgetPerpetualTrader``
code keeps working **unchanged** while still letting you point any account at any
set of keys.

Adding a new account (e.g. a second Binance live key)
-----------------------------------------------------
1. Add an ``Account`` enum member, e.g. ``BINANCE_LIVE_2 = "binance_live_2"``.
2. Add the keys to ``.env``, e.g. ``BINANCE_ACCT2_API_KEY`` /
   ``BINANCE_ACCT2_SECRET_KEY``.
3. Register it in ``_REGISTRY`` below, pointing ``src_key`` / ``src_secret`` at
   the new ``.env`` vars and keeping ``env_key`` / ``env_secret`` as the
   canonical names the trader reads (``BINANCE_API_KEY`` / ``BINANCE_SECRET_KEY``
   for a live Binance account).

CLI
---
    python src/account_manager.py            # validate every account's creds
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

from dotenv import load_dotenv

# Load .env once on import so credentials are available everywhere.
load_dotenv()


# ---------------------------------------------------------------------------
# Account identifiers
# ---------------------------------------------------------------------------
class Account(str, Enum):
    """Named trading accounts. Extend this enum to add more accounts."""

    BINANCE_LIVE = "binance_live"
    BINANCE_MOMENTUM = "binance_momentum"
    BINANCE_TESTNET = "binance_testnet"
    BITGET_LIVE = "bitget_live"
    BITGET_PAPER = "bitget_paper"

    def __str__(self) -> str:  # nicer CLI / log output
        return self.value


# ---------------------------------------------------------------------------
# Resolved credentials
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AccountConfig:
    """Fully-resolved, validated credentials + routing flags for one account."""

    account: Account
    exchange: str  # "binance" | "bitget"
    api_key: str
    secret: str
    passphrase: Optional[str]
    testnet: bool
    paper: bool
    label: str
    # Canonical env-var names the downstream trader classes read from.
    env_key: str
    env_secret: str
    env_passphrase: Optional[str] = None

    def masked_key(self) -> str:
        """API key with the middle redacted, safe for logs."""
        if not self.api_key:
            return "<missing>"
        if len(self.api_key) <= 10:
            return self.api_key[:2] + "…"
        return f"{self.api_key[:6]}…{self.api_key[-4:]}"


# ---------------------------------------------------------------------------
# Registry — maps each Account to its .env source vars + routing + canonical
# trader env vars.  This is the only place you edit to add/modify accounts.
# ---------------------------------------------------------------------------
_REGISTRY: Dict[Account, Dict] = {
    Account.BINANCE_LIVE: dict(
        exchange="binance",
        testnet=False,
        paper=False,
        label="Binance-AQM-Live",
        # Source of truth in .env:
        src_key="BINANCE_AQM_API_KEY",
        src_secret="BINANCE_AQM_SECRET_KEY",
        src_pass=None,
        # Canonical vars the live Binance trader reads:
        env_key="BINANCE_API_KEY",
        env_secret="BINANCE_SECRET_KEY",
        env_pass=None,
    ),
    Account.BINANCE_MOMENTUM: dict(
        exchange="binance",
        testnet=False,
        paper=False,
        label="Binance-Momentum-Live",
        src_key="MOMENTUM_API_KEY",
        src_secret="MOMENTUM_SECRET_KEY",
        src_pass=None,
        env_key="BINANCE_API_KEY",
        env_secret="BINANCE_SECRET_KEY",
        env_pass=None,
    ),
    Account.BINANCE_TESTNET: dict(
        exchange="binance",
        testnet=True,
        paper=False,
        label="Binance-Testnet",
        src_key="BINANCE_TESTNET_API_KEY",
        src_secret="BINANCE_TESTNET_SECRET_KEY",
        src_pass=None,
        env_key="BINANCE_TESTNET_API_KEY",
        env_secret="BINANCE_TESTNET_SECRET_KEY",
        env_pass=None,
    ),
    Account.BITGET_LIVE: dict(
        exchange="bitget",
        testnet=False,
        paper=False,
        label="Bitget-Live",
        src_key="BITGET_API_KEY",
        src_secret="BITGET_SECRET_KEY",
        src_pass="BITGET_PASSPHRASE",
        env_key="BITGET_API_KEY",
        env_secret="BITGET_SECRET_KEY",
        env_pass="BITGET_PASSPHRASE",
    ),
    Account.BITGET_PAPER: dict(
        exchange="bitget",
        testnet=False,
        paper=True,
        label="Bitget-Paper",
        src_key="BITGET_PAPER_API_KEY",
        src_secret="BITGET_PAPER_SECRET_KEY",
        src_pass="BITGET_PAPER_PASSPHRASE",
        env_key="BITGET_PAPER_API_KEY",
        env_secret="BITGET_PAPER_SECRET_KEY",
        env_pass="BITGET_PAPER_PASSPHRASE",
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def coerce_account(account) -> Account:
    """Accept an ``Account`` or its string value and return an ``Account``."""
    if isinstance(account, Account):
        return account
    try:
        return Account(str(account).strip().lower())
    except ValueError as exc:
        valid = ", ".join(a.value for a in Account)
        raise ValueError(
            f"Unknown account '{account}'. Valid accounts: {valid}"
        ) from exc


def get_account(account) -> AccountConfig:
    """
    Resolve and validate the credentials for ``account``.

    Raises
    ------
    EnvironmentError
        If any required credential is missing/empty in the environment.
    """
    acc = coerce_account(account)
    spec = _REGISTRY[acc]

    api_key = os.getenv(spec["src_key"], "").strip()
    secret = os.getenv(spec["src_secret"], "").strip()
    passphrase = (
        os.getenv(spec["src_pass"], "").strip() if spec["src_pass"] else None
    )

    missing = []
    if not api_key:
        missing.append(spec["src_key"])
    if not secret:
        missing.append(spec["src_secret"])
    if spec["src_pass"] and not passphrase:
        missing.append(spec["src_pass"])

    if missing:
        raise EnvironmentError(
            f"[{spec['label']}] Missing credentials in .env: {missing}. "
            f"See .env.example for the expected variables."
        )

    return AccountConfig(
        account=acc,
        exchange=spec["exchange"],
        api_key=api_key,
        secret=secret,
        passphrase=passphrase,
        testnet=spec["testnet"],
        paper=spec["paper"],
        label=spec["label"],
        env_key=spec["env_key"],
        env_secret=spec["env_secret"],
        env_passphrase=spec["env_pass"],
    )


def activate_account(account) -> AccountConfig:
    """
    Resolve ``account`` and publish its credentials into the *canonical*
    environment variables the existing trader classes read.

    This is the bridge that lets any account point at any set of keys without
    modifying the live trader code.  Call this once at startup, **before**
    constructing the traders.

    Returns
    -------
    AccountConfig
        The resolved config, including ``exchange`` / ``testnet`` / ``paper``
        routing flags for the launcher.
    """
    cfg = get_account(account)

    os.environ[cfg.env_key] = cfg.api_key
    os.environ[cfg.env_secret] = cfg.secret
    if cfg.env_passphrase and cfg.passphrase:
        os.environ[cfg.env_passphrase] = cfg.passphrase

    print(
        f"🔐 Activated account '{cfg.account}' → {cfg.label} "
        f"(exchange={cfg.exchange}, testnet={cfg.testnet}, paper={cfg.paper}, "
        f"key={cfg.masked_key()})"
    )
    return cfg


def validate_all() -> Dict[Account, bool]:
    """
    Check every registered account and print a status line for each.

    Returns a mapping ``{Account: ok}`` so callers can act on the result.
    """
    print("Account credential check:")
    results: Dict[Account, bool] = {}
    for acc in Account:
        try:
            cfg = get_account(acc)
            print(
                f"  ✅ {cfg.label:<20s} | {cfg.account.value:<16s} | "
                f"key={cfg.masked_key()} | testnet={cfg.testnet} paper={cfg.paper}"
            )
            results[acc] = True
        except EnvironmentError as exc:
            print(f"  ❌ {acc.value:<16s} | {exc}")
            results[acc] = False
    return results


def available_accounts() -> list[str]:
    """List the string values of every registered account (for argparse)."""
    return [a.value for a in Account]


if __name__ == "__main__":
    ok = validate_all()
    failures = [a.value for a, good in ok.items() if not good]
    if failures:
        print(f"\n⚠️  {len(failures)} account(s) missing credentials: {failures}")
    else:
        print("\n✅ All accounts have credentials configured.")
