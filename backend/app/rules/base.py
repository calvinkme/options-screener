"""Rule primitives.

Each rule is a small, individually testable function producing one RuleResult. Per CLAUDE.md:
"Never collapse rules into a single boolean -- I need to see which specific gate failed."

The `message` on every result is written to read on its own in the terminal output, in the
format the spec's "Core purpose" section demands, e.g.

    IV percentile 4 (need >=35)
    Short delta -0.233 (-0.20 to -0.25)
    Earnings 2026-09-11 inside expiry
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from backend.app import config
from backend.app.domain import (
    Leg,
    RuleResult,
    Strategy,
    UnderlyingSnapshot,
)
from backend.app.metrics.indicators import TechnicalPicture


@dataclass(slots=True)
class RuleContext:
    """Everything a rule is allowed to look at."""

    strategy: Strategy
    symbol: str
    legs: list[Leg]
    metrics: dict[str, Any]
    underlying: UnderlyingSnapshot
    dte: int
    quote_date: date
    technicals: TechnicalPicture | None = None
    regime: TechnicalPicture | None = None
    net_liquidation: Decimal = field(default_factory=lambda: config.settings.net_liquidation)
    cash_available: Decimal = field(default_factory=lambda: config.settings.cash_available)

    @property
    def short_legs(self) -> list[Leg]:
        return [leg for leg in self.legs if leg.is_short]

    @property
    def long_legs(self) -> list[Leg]:
        return [leg for leg in self.legs if not leg.is_short]

    @property
    def expiry(self) -> date | None:
        return self.legs[0].quote.expiry if self.legs else None


Rule = Callable[[RuleContext], RuleResult]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def num(value: Any, places: int = 2) -> str:
    """Human-readable number, or the word that makes a missing value obvious."""
    if value is None:
        return "UNKNOWN"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int,)):
        return f"{value:,}"
    if isinstance(value, (Decimal, float)):
        return f"{float(value):,.{places}f}"
    return str(value)


def pct(value: Decimal | float | None, places: int = 1) -> str:
    if value is None:
        return "UNKNOWN"
    return f"{float(value) * 100:.{places}f}%"


def money(value: Decimal | float | None, places: int = 2) -> str:
    if value is None:
        return "UNKNOWN"
    return f"${float(value):,.{places}f}"


# ---------------------------------------------------------------------------
# Result builders
# ---------------------------------------------------------------------------


def result(
    rule_id: str,
    label: str,
    passed: bool | None,
    actual: Any,
    required: str,
    message: str,
    advisory: bool = False,
) -> RuleResult:
    return RuleResult(
        rule_id=rule_id,
        label=label,
        passed=passed,
        actual=actual,
        required=required,
        message=message,
        advisory=advisory,
    )


def missing(rule_id: str, label: str, required: str, what: str) -> RuleResult:
    """A rule whose input never arrived.

    CLAUDE.md is unambiguous that this is a failure, not a pass: "If data is missing,
    propagate None and fail the rule -- do not default to a plausible-looking number."
    """
    return result(
        rule_id,
        label,
        None,
        None,
        required,
        f"{label} UNKNOWN -- {what} unavailable (need {required})",
    )


def check_range(
    rule_id: str,
    label: str,
    value: Decimal | float | int | None,
    low: Decimal | float | int,
    high: Decimal | float | int,
    *,
    places: int = 2,
    unit: str = "",
    advisory: bool = False,
    what: str = "value",
) -> RuleResult:
    """Inclusive at both ends. Boundary behaviour is deliberate and unit-tested."""
    required = f"{num(low, places)}{unit} to {num(high, places)}{unit}"
    if value is None:
        return missing(rule_id, label, required, what)

    v = Decimal(str(value))
    passed = Decimal(str(low)) <= v <= Decimal(str(high))
    return result(
        rule_id,
        label,
        passed,
        v,
        required,
        f"{label} {num(v, places)}{unit} ({required})",
        advisory=advisory,
    )


def check_min(
    rule_id: str,
    label: str,
    value: Decimal | float | int | None,
    minimum: Decimal | float | int,
    *,
    places: int = 2,
    unit: str = "",
    advisory: bool = False,
    what: str = "value",
) -> RuleResult:
    required = f">={num(minimum, places)}{unit}"
    if value is None:
        return missing(rule_id, label, required, what)

    v = Decimal(str(value))
    passed = v >= Decimal(str(minimum))
    return result(
        rule_id,
        label,
        passed,
        v,
        required,
        f"{label} {num(v, places)}{unit} (need {required})",
        advisory=advisory,
    )


def check_max(
    rule_id: str,
    label: str,
    value: Decimal | float | int | None,
    maximum: Decimal | float | int,
    *,
    places: int = 2,
    unit: str = "",
    advisory: bool = False,
    what: str = "value",
) -> RuleResult:
    required = f"<={num(maximum, places)}{unit}"
    if value is None:
        return missing(rule_id, label, required, what)

    v = Decimal(str(value))
    passed = v <= Decimal(str(maximum))
    return result(
        rule_id,
        label,
        passed,
        v,
        required,
        f"{label} {num(v, places)}{unit} (need {required})",
        advisory=advisory,
    )


def check_bool(
    rule_id: str,
    label: str,
    value: bool | None,
    required: str,
    message_true: str,
    message_false: str,
    *,
    advisory: bool = False,
    what: str = "value",
) -> RuleResult:
    if value is None:
        return missing(rule_id, label, required, what)
    return result(
        rule_id,
        label,
        value,
        value,
        required,
        message_true if value else message_false,
        advisory=advisory,
    )
