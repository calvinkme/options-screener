"""Domain types shared across the data layer, the rules engine and the API.

Deliberately separate from the SQLAlchemy models in db/: these are what the rules engine
reasons about, and they carry `None` wherever the broker gave us nothing. Per the spec:
"Never fabricate a price, a Greek, or a date. If data is missing, propagate None and fail
the rule -- do not default to a plausible-looking number."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any


class Right(StrEnum):
    CALL = "C"
    PUT = "P"


class Verdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class Strategy(StrEnum):
    BULL_PUT = "bull_put"
    BEAR_CALL = "bear_call"
    CASH_SECURED_PUT = "cash_secured_put"
    COVERED_CALL = "covered_call"
    LONG_CALL = "long_call"
    LONG_PUT = "long_put"
    BULL_CALL = "bull_call"
    BEAR_PUT = "bear_put"
    JADE_LIZARD = "jade_lizard"
    SYNTHETIC_LONG = "synthetic_long"
    SYNTHETIC_SHORT = "synthetic_short"

    @property
    def label(self) -> str:
        return _STRATEGY_LABELS[self]


_STRATEGY_LABELS: dict[Strategy, str] = {
    Strategy.BULL_PUT: "Bull Put Spread",
    Strategy.BEAR_CALL: "Bear Call Spread",
    Strategy.CASH_SECURED_PUT: "Cash Secured Put",
    Strategy.COVERED_CALL: "Covered Call",
    Strategy.LONG_CALL: "Long Call",
    Strategy.LONG_PUT: "Long Put",
    Strategy.BULL_CALL: "Bull Call Spread",
    Strategy.BEAR_PUT: "Bear Put Spread",
    Strategy.JADE_LIZARD: "Jade Lizard",
    Strategy.SYNTHETIC_LONG: "Long Synthetic",
    Strategy.SYNTHETIC_SHORT: "Short Synthetic",
}


@dataclass(slots=True)
class OptionQuote:
    """One option contract at one instant. Every market field is optional by design."""

    symbol: str
    expiry: date
    strike: Decimal
    right: Right

    bid: Decimal | None = None
    ask: Decimal | None = None
    last: Decimal | None = None

    delta: float | None = None
    gamma: float | None = None
    vega: float | None = None
    theta: float | None = None
    iv: float | None = None

    open_interest: int | None = None
    volume: int | None = None
    underlying_price: Decimal | None = None

    # "ibkr_model" when the broker sent modelGreeks; "computed_bsm" when we backed them out
    # of the mid ourselves (delayed feed, no OPRA). None means no Greeks at all.
    greeks_source: str | None = None

    @property
    def mark(self) -> Decimal | None:
        """Midpoint. None unless both sides of the market are present.

        Falls back to `last` only when there is no two-sided quote at all -- and never
        invents a value from one side of the book.
        """
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / Decimal(2)
        return self.last

    @property
    def spread(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid

    @property
    def intrinsic(self) -> Decimal | None:
        if self.underlying_price is None:
            return None
        if self.right is Right.CALL:
            return max(self.underlying_price - self.strike, Decimal(0))
        return max(self.strike - self.underlying_price, Decimal(0))

    @property
    def extrinsic(self) -> Decimal | None:
        """Time value. The IBKR tracker calls this Extrinsic Value (EV)."""
        mark, intrinsic = self.mark, self.intrinsic
        if mark is None or intrinsic is None:
            return None
        return mark - intrinsic

    @property
    def strike_text(self) -> str:
        """Strike as a human reads it.

        `Decimal("100").normalize()` is `1E+2`, which is correct and unreadable. Trailing
        zeros still go (107.50 -> 107.5), but the exponent form never reaches the screen.
        """
        trimmed = self.strike.normalize()
        if trimmed.as_tuple().exponent > 0:
            trimmed = trimmed.quantize(Decimal(1))
        return str(trimmed)

    @property
    def occ_expiry(self) -> str:
        """YYMMDD, the format the IBKR tracker's column E uses."""
        return self.expiry.strftime("%y%m%d")


@dataclass(slots=True)
class Leg:
    """A quote plus a signed quantity. Negative is short, matching the trackers' Qty column."""

    quote: OptionQuote
    qty: int

    @property
    def is_short(self) -> bool:
        return self.qty < 0


@dataclass(slots=True)
class UnderlyingSnapshot:
    """Everything we know about the stock itself at scan time."""

    symbol: str
    price: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    volume: int | None = None
    avg_volume: int | None = None
    market_cap: Decimal | None = None
    sector: str | None = None
    dividend_yield: Decimal | None = None  # fraction, e.g. 0.0069 for 0.69%

    iv: float | None = None  # raw 30-day implied vol, as a fraction
    iv_percentile: Decimal | None = None  # 0-100
    iv_percentile_source: str | None = None
    historical_vol: float | None = None

    earnings_date: date | None = None
    earnings_source: str | None = None
    earnings_is_stale: bool = False

    shares_owned: int = 0
    bars: list[Bar] = field(default_factory=list)


@dataclass(slots=True)
class Bar:
    """One daily OHLCV bar."""

    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(slots=True)
class RuleResult:
    """The atomic unit of the rules engine.

    `passed` is tri-state: True, False, or None when the input was missing. None is rendered
    distinctly in the UI and, for hard gates, counts as a failure -- silence is never a pass.
    """

    rule_id: str
    label: str
    passed: bool | None
    actual: Any
    required: str
    message: str
    advisory: bool = False

    @property
    def is_blocking(self) -> bool:
        return not self.advisory and self.passed is not True


@dataclass(slots=True)
class ConfluenceLayer:
    name: str
    scored: bool
    detail: str


@dataclass(slots=True)
class ConfluenceScore:
    score: int
    layers: list[ConfluenceLayer]
    capped: bool
    raw_score: int

    @property
    def size_guidance(self) -> str:
        return {
            0: "no trade",
            1: "no trade",
            2: "no trade",
            3: "minimum size, credit spreads only",
            4: "normal size",
            5: "full size, debit trades allowed",
        }[self.score]


@dataclass(slots=True)
class StrategyVerdict:
    """The output that matters -- a verdict, plus exactly which gate decided it."""

    strategy: Strategy
    symbol: str
    legs: list[Leg]
    metrics: dict[str, Any]
    results: list[RuleResult]
    confluence: ConfluenceScore | None
    underlying: UnderlyingSnapshot

    @property
    def verdict(self) -> Verdict:
        return Verdict.FAIL if any(r.is_blocking for r in self.results) else Verdict.PASS

    @property
    def failures(self) -> list[RuleResult]:
        return [r for r in self.results if r.is_blocking]

    @property
    def warnings(self) -> list[RuleResult]:
        return [r for r in self.results if r.advisory and r.passed is not True]

    @property
    def passes(self) -> list[RuleResult]:
        return [r for r in self.results if r.passed is True]

    def describe(self) -> str:
        """Terminal rendering, in the format the spec's 'Core purpose' section demands."""
        strikes = "/".join(leg.quote.strike_text for leg in self.legs)
        expiry = self.legs[0].quote.expiry.strftime("%b%d") if self.legs else "--"
        head = f"{self.symbol} {expiry} {strikes} {self.strategy.label}   {self.verdict.value}"
        lines = [head]
        for r in self.failures:
            lines.append(f"  X {r.message}")
        for r in self.passes:
            lines.append(f"  v {r.message}")
        for r in self.warnings:
            lines.append(f"  ~ {r.message}  (advisory)")
        return "\n".join(lines)
