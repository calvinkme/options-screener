"""Strategy registry: rules, columns and direction, in one place.

This is the extension point. Adding calendar, diagonal, iron condor or any of the other
structures in the IBKR tracker means adding one rules module, one builder and one entry here
-- no changes to the API or the UI, which read the strategy list and its columns from here.
The jade lizard and the synthetic spread were both added exactly that way.

Column keys and labels deliberately mirror the tracker workbooks (Net Entry Price, Capital/
B. Pwr, Max Loss, Delta per share, Extrinsic Value, Remaining DTE, % ROI) so the vocabulary
carries over from the spreadsheets you already read.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.app import config
from backend.app.domain import Strategy
from backend.app.rules import (
    bear_call,
    bull_put,
    covered_call,
    csp,
    debit_spread,
    jade_lizard,
    long_option,
    synthetic,
)


@dataclass(frozen=True, slots=True)
class Column:
    key: str
    label: str
    fmt: str = "num"  # num | int | money | pct | pct1 | text | date | delta
    band: str | None = None  # "roi_annual" | "csp_roi_cash"


# Columns every strategy shows, named as in the trackers.
BASE_COLUMNS: tuple[Column, ...] = (
    Column("verdict", "Verdict", "text"),
    Column("confluence", "Confl.", "int"),
    Column("symbol", "Ticker", "text"),
    Column("underlying_price", "Underlying", "money"),
    Column("expiry_yymmdd", "Expiry (YYMMDD)", "text"),
    Column("dte", "DTE", "int"),
    Column("legs_text", "Legs", "text"),
    Column("net_entry_price", "Net Entry Price", "money"),
    Column("capital", "Capital/B. Pwr", "money"),
    Column("max_loss", "Max Loss", "money"),
    Column("max_profit", "Max Profit", "money"),
    Column("short_delta_per_share", "Delta per share", "delta"),
    Column("total_theta", "Theta", "num"),
    Column("extrinsic_value", "Extrinsic Value", "money"),
    Column("iv_raw", "IV", "pct1"),
    Column("iv_percentile", "IV %ile", "int"),
    Column("open_interest", "OI", "int"),
    Column("volume", "Vol", "int"),
    Column("earnings_date", "Earnings", "date"),
)

CREDIT_COLUMNS: tuple[Column, ...] = (
    Column("net_credit", "Net Credit", "money"),
    Column("width", "Width", "money"),
    Column("breakeven", "Breakeven", "money"),
    Column("roi", "ROI", "pct"),
    Column("roi_annual", "ROI Annualised", "pct", band="roi_annual"),
    Column("loss_ratio", "Loss Ratio", "num"),
    Column("stop_loss_amount", "Stop @1.5x", "money"),
)

# Straight out of CSP Template.xlsx, columns G through W.
CSP_COLUMNS: tuple[Column, ...] = (
    Column("cash_secured", "Capital (Cash)", "money"),
    Column("buying_power", "B. Power", "money"),
    Column("roi_premium", "Max ROI (Cash) %", "pct", band="csp_roi_cash"),
    Column("roi_annual_cash", "Annualized (Cash)", "pct", band="roi_annual"),
    Column("roi_bp", "Max ROI (BP) %", "pct"),
    Column("roi_annual_margin", "Annualized (BP)", "pct", band="roi_annual"),
    Column("assigned_price", "Assigned Price", "money"),
    Column("collected_premium", "Collected Premium", "money"),
    Column("assigned_net_cost", "Net Cost", "money"),
    Column("discount_vs_price", "Discount vs. Price", "pct"),
)

COVERED_CALL_COLUMNS: tuple[Column, ...] = (
    Column("premium_pct", "Premium %", "pct"),
    Column("premium_annual", "Premium Annualised", "pct"),
    Column("weighted_dividend", "Dividend (70%)", "pct"),
    Column("prem_plus_div", "Prem + Div", "pct"),
    Column("diff_to_strike", "Diff to Strike", "money"),
    Column("diff_pct", "Diff %", "pct"),
    Column("diff_annual", "Diff Annualised", "pct"),
    Column("max_roi_annual", "Max ROI Annualised", "pct", band="roi_annual"),
    Column("roi_30d", "ROI (30d)", "pct"),
)

DEBIT_COLUMNS: tuple[Column, ...] = (
    Column("net_debit", "Net Debit", "money"),
    Column("width", "Width", "money"),
    Column("breakeven", "Breakeven", "money"),
    Column("risk_reward", "Risk:Reward", "num"),
    Column("short_strike_sd", "Short Strike (SD)", "num"),
)

LONG_COLUMNS: tuple[Column, ...] = (
    Column("premium", "Premium", "money"),
    Column("total_premium", "Total Premium", "money"),
    Column("breakeven", "Breakeven", "money"),
    Column("long_delta_per_share", "Long Delta", "delta"),
    Column("strikes_itm", "Strikes ITM", "int"),
)


# Jade_Lizard_Notes.pdf. The first three columns are the ones that decide the trade: a credit
# at or above the call width is the whole strategy, and Upside Risk reads $0.00 when it holds.
JADE_LIZARD_COLUMNS: tuple[Column, ...] = (
    Column("net_credit", "Net Credit", "money"),
    Column("call_width", "Call Width", "money"),
    Column("credit_to_call_width", "Credit / Width", "num"),
    Column("upside_risk", "Upside Risk", "money"),
    Column("short_put_delta", "Short Put Delta", "delta"),
    Column("short_call_delta", "Short Call Delta", "delta"),
    Column("breakeven_sd", "B/E in SD", "num"),
    Column("prob_win_approx", "P(win) approx", "pct"),
    Column("buying_power", "B. Power (est.)", "money"),
    Column("roi", "Max ROI", "pct", band="csp_roi_cash"),
    Column("roi_annual", "ROI Annualised", "pct", band="roi_annual"),
    Column("take_profit_low", "TP (1/2)", "money"),
    Column("take_profit_high", "TP (2/3)", "money"),
    Column("stop_trigger_price", "Cut Below", "money"),
    Column("close_above_price", "Close Above", "money"),
)

# Synthetic_Spread_Notes.pdf. No ROI column: profit is unbounded on the long side, so an ROI
# figure would be either meaningless or invented. Leverage is the honest substitute -- how much
# stock exposure each dollar of blocked buying power is carrying.
SYNTHETIC_COLUMNS: tuple[Column, ...] = (
    Column("net_debit", "Net Debit", "money"),
    Column("net_premium_pct_of_spot", "Net Prem % Spot", "pct"),
    Column("breakeven", "Breakeven", "money"),
    Column("secondary_breakeven", "B/E (down)", "money"),
    Column("strike_gap", "Flat Zone", "money"),
    Column("long_delta_per_share", "Long Delta", "delta"),
    Column("buying_power", "B. Power (est.)", "money"),
    Column("leverage", "Leverage", "num"),
    Column("notional", "Stock Notional", "money"),
    Column("assignment_obligation", "Assignment Cost", "money"),
)


@dataclass(frozen=True, slots=True)
class StrategySpec:
    strategy: Strategy
    rules: list[Callable[..., Any]]
    columns: tuple[Column, ...]
    bullish: bool
    kind: str  # credit | debit | csp | covered_call | long
    legs: int


REGISTRY: dict[Strategy, StrategySpec] = {
    Strategy.BULL_PUT: StrategySpec(
        Strategy.BULL_PUT, bull_put.RULES, BASE_COLUMNS + CREDIT_COLUMNS, True, "credit", 2
    ),
    Strategy.BEAR_CALL: StrategySpec(
        Strategy.BEAR_CALL, bear_call.RULES, BASE_COLUMNS + CREDIT_COLUMNS, False, "credit", 2
    ),
    Strategy.CASH_SECURED_PUT: StrategySpec(
        Strategy.CASH_SECURED_PUT, csp.RULES, BASE_COLUMNS + CSP_COLUMNS, True, "csp", 1
    ),
    Strategy.COVERED_CALL: StrategySpec(
        Strategy.COVERED_CALL,
        covered_call.RULES,
        BASE_COLUMNS + COVERED_CALL_COLUMNS,
        True,
        "covered_call",
        1,
    ),
    Strategy.LONG_CALL: StrategySpec(
        Strategy.LONG_CALL, long_option.RULES, BASE_COLUMNS + LONG_COLUMNS, True, "long", 1
    ),
    Strategy.LONG_PUT: StrategySpec(
        Strategy.LONG_PUT, long_option.RULES, BASE_COLUMNS + LONG_COLUMNS, False, "long", 1
    ),
    Strategy.BULL_CALL: StrategySpec(
        Strategy.BULL_CALL, debit_spread.RULES, BASE_COLUMNS + DEBIT_COLUMNS, True, "debit", 2
    ),
    Strategy.BEAR_PUT: StrategySpec(
        Strategy.BEAR_PUT, debit_spread.RULES, BASE_COLUMNS + DEBIT_COLUMNS, False, "debit", 2
    ),
    # Neutral to slightly bullish, so it scores against the bullish confluence layers.
    Strategy.JADE_LIZARD: StrategySpec(
        Strategy.JADE_LIZARD,
        jade_lizard.RULES,
        BASE_COLUMNS + JADE_LIZARD_COLUMNS,
        True,
        "jade_lizard",
        3,
    ),
    Strategy.SYNTHETIC_LONG: StrategySpec(
        Strategy.SYNTHETIC_LONG,
        synthetic.LONG_RULES,
        BASE_COLUMNS + SYNTHETIC_COLUMNS,
        True,
        "synthetic",
        2,
    ),
    Strategy.SYNTHETIC_SHORT: StrategySpec(
        Strategy.SYNTHETIC_SHORT,
        synthetic.SHORT_RULES,
        BASE_COLUMNS + SYNTHETIC_COLUMNS,
        False,
        "synthetic",
        2,
    ),
}

# The order the ticker-first view lists strategies in.
ALL_STRATEGIES: tuple[Strategy, ...] = (
    Strategy.BULL_PUT,
    Strategy.BEAR_CALL,
    Strategy.CASH_SECURED_PUT,
    Strategy.COVERED_CALL,
    Strategy.LONG_CALL,
    Strategy.LONG_PUT,
    Strategy.BULL_CALL,
    Strategy.BEAR_PUT,
    Strategy.JADE_LIZARD,
    Strategy.SYNTHETIC_LONG,
    Strategy.SYNTHETIC_SHORT,
)


def spec_for(strategy: Strategy) -> StrategySpec:
    return REGISTRY[strategy]


def columns_for(strategy: Strategy) -> list[dict[str, Any]]:
    return [
        {"key": c.key, "label": c.label, "fmt": c.fmt, "band": c.band}
        for c in REGISTRY[strategy].columns
    ]


def band_thresholds() -> dict[str, list[float]]:
    """Colour bands from CLAUDE.md's frontend section, handed to the UI as plain numbers."""
    return {
        "roi_annual": [float(x) for x in config.ROI_ANNUAL_BANDS],
        "csp_roi_cash": [float(x) for x in config.CSP_ROI_CASH_BANDS],
    }
