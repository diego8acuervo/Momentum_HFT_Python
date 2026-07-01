#!/usr/bin/env python3
"""
Turtle Trading Position Limits — Binance Perpetuals
====================================================

Adapts the Original Turtle Trading Rules position sizing to crypto perps
as a **hard inventory constraint** injected into the existing CPPI pipeline.

Public API (used by composition root)
--------------------------------------
    limits = TurtlePositionLimits(account_equity=100_000)
    limits.refresh(pairs)                   # call once at startup
    limits.get_max_qty("BTCUSDT")           # returns max base-token qty
    limits.set_account_equity(new_equity)   # call alongside CPPI equity poll
    limits.maybe_refresh(pairs)             # call daily in trade loop

How it integrates with CPPI
---------------------------
After CPPIRiskManager.compute() returns a CPPIState, the composition root
clamps CPPIState.Qmax_eff with TurtlePositionLimits.get_max_qty():

    cppi_state[sym] = cppi.compute(sym, mid, sigma)
    turtle_max = turtle_limits.get_max_qty(sym)
    if cppi_state[sym].Qmax_eff > turtle_max:
        cppi_state[sym].Qmax_eff = turtle_max
        cppi_state[sym].qty_bid, cppi_state[sym].qty_ask = cppi_quote_sizes(
            cppi_state[sym].inventory,
            turtle_max,
            cppi_state[sym].q_target,
            n_slices=cppi._n_slices,
        )

Turtle Position Sizing (adapted for crypto perps)
--------------------------------------------------
  N  = 20-day EMA of True Range  (Turtle "N" / ATR)
  Unit_Qty = (RISK_PER_UNIT × Account_Equity) / N   (base-token units)
  Max_Qty  = Unit_Qty × MAX_UNITS                   (default: 4 units)

  1 N move on Max_Qty position → risk = MAX_UNITS × 1% equity = 4% per market.
  2 N stop → 2% equity loss per unit → 8% max per market at full position.

Turtle Risk Limits (enforced by portfolio-level checks)
--------------------------------------------------------
  - Single market:           max 4 units  ← enforced here
  - Closely correlated:      max 6 units  ← enforced in check_portfolio()
  - Loosely correlated:      max 10 units
  - Single direction total:  max 12 units

CLI usage (standalone report)
-----------------------------
  python -m Binance_Market_Maker.portfolio.turtle_risk
  python -m Binance_Market_Maker.portfolio.turtle_risk --account 50000 --leverage 10

Dependencies
------------
  pip install requests

References
----------
  - Curtis Faith, "The Original Turtle Trading Rules" (2003)
  - Binance FAPI: https://developers.binance.com/docs/derivatives/usds-margined-futures
"""

import argparse
import json
import logging
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    print("ERROR: 'requests' required. Install: pip install requests")
    sys.exit(1)

logger = logging.getLogger(__name__)

# Re-export from shared so ``from Binance_Market_Maker.portfolio
# .turtle_risk import Candle, calc_n`` still works.
from shared.calibration.data_models import Candle  # noqa: F401, E402
from shared.calibration.estimators import calc_n   # noqa: F401, E402

# ─────────────────────────── Defaults ─────────────────────────────────

DEFAULT_ACCOUNT  = 10_000.0
DEFAULT_LEVERAGE = 20
RISK_PER_UNIT    = 0.01        # 3% of equity per unit (Turtle standard)
ATR_PERIOD       = 20          # 20-day EMA of True Range
MAX_UNITS        = 4           # Turtle: max 4 units per market
STOP_N           = 2.0         # 2N stop → 2% risk per unit
CANDLE_LIMIT     = ATR_PERIOD + 30  # extra bars for EMA warm-up

BINANCE_FAPI = "https://fapi.binance.com"

TOKENS = [
     "AAVE",
    "AIXBT",
    "AVAX",
    "BCH",
    "BNB",
    "BTC",
    "COMP",
    "DOGE",
    "DOT",
    "DYDX",
    "EIGEN",
    "ENA",
    "ETH",
    "ETHFI",
    "FORM",
    "INJ",
    "JUP",
    "LTC",
    "NEAR",
    "PNUT",
    "RAY",
    "SOL",
    "SUI",
    "TRX",
    "UNI",
    "XAU",
    "XAG",
    "BZ",
    "XPT",
    "XPD",
    "CL",
    "NATGAS",
    "COPPER",
    "TSLA",
    "INTC",
    "MSTR",
    "COIN",
    "HOOD",
    "AMZN",
    "PLTR",
    "CRCL",
    "LLY",
    "NVO",
    "BBX",
    "NOK",
    "ASTS",
    "IBM",
    "NOW",
    "CRM",
    "IREN",
    "ONDS",
    "GOOGL",
    "AAPL",
    "META",
    "MSFT",
    "QQQ",
    "SPY",
    "EWY",
    "EWJ",
    "EWT"
        # may not exist on Binance perps
]

SKIPPED = ["SKHYNIX", "SAMSUNG", "HYUNDAI"]

CORRELATION_GROUPS = {
    "layer1_major":  {"BTC", "ETH"},
    "layer1_alt":    {"SOL", "AVAX", "NEAR", "APT", "SUI", "DOT", "ADA", "HBAR"},
    "meme":          {"DOGE", "PNUT", "WLD"},
    "defi":          {"AAVE", "LINK"},
    "legacy":        {"LTC", "BCH", "XLM", "XTZ"},
    "exchange":      {"BNB"},
}

# ─────────────────────────── Data ─────────────────────────────────────

@dataclass
class TurtleUnit:
    symbol: str
    price: float
    atr_20: float          # N in USD
    n_pct: float           # N / price
    unit_notional: float   # 1 unit USD
    unit_qty: float        # 1 unit tokens
    max_pos_usd: float     # 4 units USD
    max_pos_qty: float     # 4 units tokens
    margin: float          # max_pos / leverage
    margin_pct: float      # margin / account
    stop_usd: float        # 2N USD
    stop_pct: float        # 2N %
    risk_usd: float        # risk per unit at 2N
    half_n: float          # ½N add interval
    group: str
    qty_prec: int = 3

# ─────────────────────────── Binance helpers ──────────────────────────

def get_klines(s: requests.Session, token: str) -> list[Candle]:
    r = s.get(
        f"{BINANCE_FAPI}/fapi/v1/klines",
        params={"symbol": f"{token}USDT", "interval": "1d", "limit": CANDLE_LIMIT},
        timeout=10,
    )
    r.raise_for_status()
    return [
        Candle(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5]))
        for x in r.json()
    ]

def get_sym_info(s: requests.Session) -> dict:
    r = s.get(f"{BINANCE_FAPI}/fapi/v1/exchangeInfo", timeout=10)
    r.raise_for_status()
    out = {}
    for sym in r.json().get("symbols", []):
        out[sym["symbol"]] = {
            "qp": sym.get("quantityPrecision", 3),
            "pp": sym.get("pricePrecision", 2),
        }
    return out

# ─────────────────────────── Unit Calc ────────────────────────────────

def corr_group(sym: str) -> str:
    for name, members in CORRELATION_GROUPS.items():
        if sym in members:
            return name
    return "ungrouped"

def calc_unit(
    sym: str,
    candles: list[Candle],
    acct: float,
    lev: int,
    info: dict,
) -> TurtleUnit:
    n = calc_n(candles)
    price = candles[-1].c
    n_pct = n / price
    si = info.get(f"{sym}USDT", {})
    qp = si.get("qp", 3)

    unit_not = (RISK_PER_UNIT * acct * price) / n
    unit_qty = unit_not / price

    max_not = unit_not * MAX_UNITS
    max_qty = unit_qty * MAX_UNITS
    margin = max_not / lev

    return TurtleUnit(
        symbol=sym, price=round(price, si.get("pp", 2)),
        atr_20=round(n, 4), n_pct=round(n_pct, 6),
        unit_notional=round(unit_not, 2), unit_qty=round(unit_qty, qp),
        max_pos_usd=round(max_not, 2), max_pos_qty=round(max_qty, qp),
        margin=round(margin, 2), margin_pct=round(margin / acct * 100, 2),
        stop_usd=round(STOP_N * n, 4), stop_pct=round(STOP_N * n_pct * 100, 2),
        risk_usd=round(RISK_PER_UNIT * acct * STOP_N, 2),
        half_n=round(n / 2, 4), group=corr_group(sym), qty_prec=qp,
    )

# ─────────────────────────── Portfolio check ──────────────────────────

def check_portfolio(units: list[TurtleUnit], acct: float) -> dict:
    total_u = len(units) * MAX_UNITS
    total_m = sum(u.margin for u in units)
    by_g: dict[str, int] = {}
    for u in units:
        by_g[u.group] = by_g.get(u.group, 0) + MAX_UNITS
    warn = []
    for g, c in by_g.items():
        if c > 6:
            warn.append(f"Group '{g}': {c} units (limit: 6)")
    if total_u > 12:
        warn.append(
            f"Total {total_u} units > direction limit (12). "
            "Select top trending markets."
        )
    if total_m > acct:
        warn.append(f"Total margin ${total_m:,.0f} > account ${acct:,.0f}")
    return {
        "total_units": total_u,
        "total_margin": round(total_m, 2),
        "margin_pct": round(total_m / acct * 100, 2),
        "by_group": by_g,
        "warnings": warn,
    }

# ─────────────────────────── Display ──────────────────────────────────

def print_table(units: list[TurtleUnit], pf: dict, acct: float, lev: int):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    w = 120
    print(f"\n{'='*w}")
    print(f"  TURTLE RISK UNIT SHEET — {ts}")
    print(
        f"  Account: ${acct:,.0f} | Leverage: {lev}x | "
        f"Risk/Unit: {RISK_PER_UNIT*100:.0f}% of equity | "
        f"N: {ATR_PERIOD}-day EMA(TR) | Stop: {STOP_N:.0f}N"
    )
    print(f"{'='*w}")
    if SKIPPED:
        print(f"  Skipped (no USDT perp): {', '.join(SKIPPED)}")

    hdr = (
        f"  {'Tkn':<5} {'Price':>10} {'N($)':>9} {'N%':>6} "
        f"{'1Unit$':>9} {'1UnitQty':>10} "
        f"{'MaxPos$':>10} {'Margin$':>9} {'M%':>5} "
        f"{'Stop%':>5} {'½N($)':>8} {'Group':<14}"
    )
    print(f"\n{hdr}")
    print(f"  {'─'*115}")

    for u in units:
        print(
            f"  {u.symbol:<5} "
            f"{u.price:>10,.2f} "
            f"{u.atr_20:>9,.2f} "
            f"{u.n_pct*100:>5.2f}% "
            f"{u.unit_notional:>9,.0f} "
            f"{u.unit_qty:>10,.{min(u.qty_prec, 4)}f} "
            f"{u.max_pos_usd:>10,.0f} "
            f"{u.margin:>9,.0f} "
            f"{u.margin_pct:>4.1f}% "
            f"{u.stop_pct:>4.1f}% "
            f"{u.half_n:>8,.2f} "
            f"{u.group:<14}"
        )

    print(f"\n{'='*w}")
    print(f"  PORTFOLIO SUMMARY (if ALL markets loaded to {MAX_UNITS} units)")
    print(f"  {'─'*60}")
    print(
        f"  Total margin: ${pf['total_margin']:>10,.0f}"
        f"  ({pf['margin_pct']:.1f}% of account)"
    )
    print(f"  Total units:  {pf['total_units']:>10}")
    print(f"\n  Correlation groups:")
    for g, c in sorted(pf["by_group"].items(), key=lambda x: -x[1]):
        f_str = " ⚠>6" if c > 6 else ""
        print(f"    {g:<18} {c:>3} units{f_str}")
    if pf["warnings"]:
        print(f"\n  ⚠ WARNINGS:")
        for w_msg in pf["warnings"]:
            print(f"    • {w_msg}")

    print(f"\n  TURTLE RULES QUICK REFERENCE:")
    print(f"    Entry:    1 unit on breakout (20d or 55d Donchian channel)")
    print(f"    Add:      +1 unit each ½N from last fill (max {MAX_UNITS})")
    print(f"    Stop:     2N from entry; raise all stops to 2N from newest unit")
    print(f"    Exit:     10d low (Sys1) or 20d low (Sys2) for longs")
    print(f"    Drawdown: Account −10% → reduce notional by 20%")
    print(f"    Limits:   4/mkt, 6/correlated, 10/loose, 12/direction")
    print(f"{'='*w}\n")


# ═════════════════════════════════════════════════════════════════════
#  TurtlePositionLimits — hard constraint for the live trading system
# ═════════════════════════════════════════════════════════════════════

class TurtlePositionLimits:
    """
    Computes per-symbol maximum inventory using Original Turtle N-based sizing.
    Provides hard position caps: ``|inventory| <= get_max_qty(symbol)``.

    Designed to slot into the existing CPPI pipeline as an upper bound on
    ``CPPIState.Qmax_eff``.  The composition root applies the clamp after
    ``CPPIRiskManager.compute()`` and before ``strategy.calculate_signals()``.

    Parameters
    ----------
    account_equity : float
        Account equity in USDT used for unit sizing.
    risk_per_unit : float
        Fraction of equity risked per unit (Turtle default: 0.01 = 1%).
    max_units : int
        Maximum units per market (Turtle default: 4).
    atr_period : int
        EMA period for True Range (Turtle N).  Default: 20.
    refresh_interval : float
        Seconds between ATR refreshes.  Default: 86400 (once per day).

    Usage
    -----
        limits = TurtlePositionLimits(account_equity=100_000)
        limits.refresh(["BTCUSDT", "ETHUSDT"])   # blocks; call at startup

        # In trade loop (after CPPI compute):
        max_q = limits.get_max_qty("BTCUSDT")    # float, inf until refreshed
    """

    # Quote suffixes we strip to get the base token
    _QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD")

    def __init__(
        self,
        account_equity: float,
        risk_per_unit: float = RISK_PER_UNIT,
        max_units: int = MAX_UNITS,
        atr_period: int = ATR_PERIOD,
        refresh_interval: float = 86_400.0,
    ) -> None:
        self._equity = account_equity
        self._risk = risk_per_unit
        self._max_units = max_units
        self._atr_period = atr_period
        self._refresh_interval = refresh_interval

        self._max_qty: dict[str, float] = {}   # symbol → max base-token qty
        self._atr:     dict[str, float] = {}   # symbol → last N value
        self._last_refresh: float = 0.0
        self._lock = threading.Lock()
        self._session: requests.Session | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_account_equity(self, equity: float) -> None:
        """Update equity (call alongside CPPI equity poll)."""
        with self._lock:
            self._equity = equity

    def get_max_qty(self, symbol: str) -> float:
        """
        Return max long *or* short qty in base tokens for ``symbol``.
        Returns ``float('inf')`` until the first successful refresh.
        """
        with self._lock:
            return self._max_qty.get(symbol, float("inf"))

    def get_atr(self, symbol: str) -> float:
        """Return last computed N (ATR) for the symbol; 0.0 if unknown."""
        with self._lock:
            return self._atr.get(symbol, 0.0)

    def state_snapshot(self) -> dict:
        """Return a copy of current limits for monitoring / JSON export."""
        with self._lock:
            return {
                sym: {
                    "max_qty": self._max_qty[sym],
                    "atr_n":   self._atr.get(sym, 0.0),
                }
                for sym in self._max_qty
            }

    def maybe_refresh(self, symbols: list[str]) -> None:
        """Refresh if ``refresh_interval`` has elapsed since last call."""
        if time.time() - self._last_refresh >= self._refresh_interval:
            self.refresh(symbols)

    def refresh(self, symbols: list[str], session: requests.Session | None = None) -> None:
        """
        Fetch daily klines from Binance FAPI and recompute N-based limits.

        Logs a warning for each symbol that fails (network error, no perp, etc.)
        and keeps the previous limit for that symbol if one exists.

        Parameters
        ----------
        symbols : list[str]
            Full Binance symbols, e.g. ``["BTCUSDT", "ETHUSDC"]``.
        session : requests.Session, optional
            Reuse an existing session.  Creates one if not supplied.
        """
        s = session or self._session
        if s is None:
            s = requests.Session()
            s.headers["User-Agent"] = "TurtlePositionLimits/1.0"
            self._session = s

        try:
            info = get_sym_info(s)
        except Exception as exc:
            logger.warning("TurtlePositionLimits: exchangeInfo failed: %s", exc)
            info = {}

        with self._lock:
            equity = self._equity
            risk   = self._risk
            m_u    = self._max_units

        for sym in symbols:
            token = self._base_token(sym)
            if token in SKIPPED:
                logger.debug("TurtlePositionLimits: skipping %s (SKIPPED list)", sym)
                continue
            try:
                candles = get_klines(s, token)
                n       = calc_n(candles)
                si      = info.get(f"{token}USDT", info.get(sym, {}))
                qp      = si.get("qp", 3)
                # Unit qty = (risk × equity) / N   (price cancels)
                unit_qty = (risk * equity) / n
                max_qty  = round(unit_qty * m_u, qp)
                with self._lock:
                    self._max_qty[sym] = max_qty
                    self._atr[sym]     = round(n, 6)
                logger.info(
                    "TurtlePositionLimits: %s  N=%.4f  max_qty=%.4f",
                    sym, n, max_qty,
                )
            except Exception as exc:
                logger.warning(
                    "TurtlePositionLimits: refresh failed for %s: %s", sym, exc
                )
            time.sleep(0.08)   # polite rate-limiting

        self._last_refresh = time.time()

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    @classmethod
    def _base_token(cls, symbol: str) -> str:
        """Strip quote suffix to get the base token, e.g. 'BTCUSDC' → 'BTC'."""
        for q in cls._QUOTE_SUFFIXES:
            if symbol.endswith(q):
                return symbol[: -len(q)]
        return symbol


# ─────────────────────────── CLI entry-point ──────────────────────────

def main():
    p = argparse.ArgumentParser(description="Turtle Risk Units — Crypto Perps")
    p.add_argument("--account",  type=float, default=DEFAULT_ACCOUNT)
    p.add_argument("--leverage", type=int,   default=DEFAULT_LEVERAGE)
    p.add_argument("--output",   type=str,   default="turtle_risk_units.json")
    a = p.parse_args()

    print(f"\n  Connecting to Binance FAPI...")
    s = requests.Session()
    s.headers["User-Agent"] = "TurtleRiskCalc/1.0"

    try:
        info = get_sym_info(s)
    except Exception as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    units, errs = [], []
    for tok in TOKENS:
        try:
            cd = get_klines(s, tok)
            u  = calc_unit(tok, cd, a.account, a.leverage, info)
            units.append(u)
            print(
                f"    ✓ {tok:<5} ${u.price:>10,.2f}"
                f"  N={u.atr_20:>8,.2f} ({u.n_pct*100:.2f}%)"
            )
        except Exception as e:
            errs.append(f"{tok}: {e}")
            print(f"    ✗ {tok}: {e}")
        time.sleep(0.08)

    if not units:
        print("  No data. Exiting.")
        sys.exit(1)

    units.sort(key=lambda u: u.margin_pct, reverse=True)
    pf = check_portfolio(units, a.account)
    print_table(units, pf, a.account, a.leverage)

    export = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "config": {
            "account": a.account, "leverage": a.leverage,
            "risk_per_unit_pct": RISK_PER_UNIT * 100,
            "atr_period": ATR_PERIOD, "stop_n": STOP_N, "max_units": MAX_UNITS,
        },
        "tokens": [
            {
                "symbol":          u.symbol,
                "price":           u.price,
                "atr_20":          u.atr_20,
                "n_pct":           round(u.n_pct * 100, 4),
                "unit_notional":   u.unit_notional,
                "unit_qty":        u.unit_qty,
                "max_pos_usd":     u.max_pos_usd,
                "max_pos_qty":     u.max_pos_qty,
                "margin":          u.margin,
                "margin_pct":      u.margin_pct,
                "stop_2n_usd":     u.stop_usd,
                "stop_2n_pct":     u.stop_pct,
                "half_n_usd":      u.half_n,
                "risk_per_unit_usd": u.risk_usd,
                "correlation_group": u.group,
            }
            for u in units
        ],
        "portfolio": pf,
        "skipped": SKIPPED,
        "errors": errs,
    }
    with open(a.output, "w") as f:
        json.dump(export, f, indent=2, default=str)
    print(f"  JSON → {a.output}")


if __name__ == "__main__":
    main()
