"""Bull call / bear put debit spreads -- CLAUDE.md "Debit spreads"."""

from __future__ import annotations

from backend.app import config
from backend.app.domain import RuleResult, Strategy
from backend.app.rules import common
from backend.app.rules.base import (
    RuleContext,
    check_min,
    check_range,
    missing,
    money,
    num,
    result,
)

CFG = config.DEBIT_SPREAD


def rule_dte(ctx: RuleContext) -> RuleResult:
    """ "~30" -- read as 30 +/- tolerance rather than exactly 30."""
    target, tol = CFG["target_dte"], CFG["dte_tolerance"]
    return common.rule_dte_range(ctx, target - tol, target + tol)


def rule_long_delta(ctx: RuleContext) -> RuleResult:
    """Long leg delta 0.50-0.60, mirrored for the bear put's long put (negative delta)."""
    low, high = CFG["long_delta"]
    if ctx.strategy is Strategy.BEAR_PUT:
        low, high = -high, -low
    return check_range(
        "debit_spread.long_delta",
        "Long delta",
        ctx.metrics.get("long_delta_per_share"),
        low,
        high,
        places=3,
        what="long leg delta",
    )


def rule_moneyness(ctx: RuleContext) -> RuleResult:
    """Long leg "ATM or 1 strike ITM" -- the same placement rule as a straight long option."""
    return check_range(
        "debit_spread.moneyness",
        "Long leg moneyness",
        ctx.metrics.get("strikes_itm"),
        0,
        1,
        places=0,
        unit=" strikes ITM",
        what="strike ladder",
    )


def rule_width(ctx: RuleContext) -> RuleResult:
    """ ">= $5 from long (bull call)". Only applies to the call side per the spec."""
    if ctx.strategy is not Strategy.BULL_CALL:
        width = ctx.metrics.get("width")
        return result(
            "debit_spread.width",
            "Spread width",
            True,
            width,
            "n/a for bear put",
            f"Spread width {money(width)} (no minimum on the put side)",
        )
    return check_min(
        "debit_spread.width",
        "Spread width",
        ctx.metrics.get("width"),
        CFG["min_width_bull_call"],
        places=2,
        what="strikes",
    )


def rule_risk_reward(ctx: RuleContext) -> RuleResult:
    """Risk:reward >= 1:2. Boundary (exactly 2.0) is a PASS and is tested."""
    rr = ctx.metrics.get("risk_reward")
    minimum = CFG["min_risk_reward"]
    required = f">={num(minimum, 1)}:1"
    if rr is None:
        return missing("debit_spread.risk_reward", "Risk:reward", required, "net debit")

    passed = rr >= minimum
    return result(
        "debit_spread.risk_reward",
        "Risk:reward",
        passed,
        rr,
        required,
        f"Risk:reward {num(rr, 2)}:1 "
        f"({money(ctx.metrics.get('max_profit'))} profit vs "
        f"{money(ctx.metrics.get('max_loss'))} loss) (need {required})",
    )


def rule_short_strike_placement(ctx: RuleContext) -> RuleResult:
    """ "Short strike at 1 SD or resistance/support".

    Expressed as how many standard deviations of expected move the short strike sits away.
    Advisory: the spec offers two alternative anchors (1 SD *or* a level), and which one
    applies is a chart judgement.
    """
    sd = ctx.metrics.get("short_strike_sd")
    required = ">=1.0 SD or at a level"
    if sd is None:
        return result(
            "debit_spread.short_strike",
            "Short strike",
            None,
            None,
            required,
            "Short strike placement UNKNOWN -- expected move unavailable",
            advisory=True,
        )

    passed = sd >= 1
    return result(
        "debit_spread.short_strike",
        "Short strike",
        passed,
        sd,
        required,
        f"Short strike {num(sd, 2)} SD from spot (need {required})",
        advisory=True,
    )


def rule_premium(ctx: RuleContext) -> RuleResult:
    return common.rule_premium_budget(ctx, CFG["max_premium_pct_nlv"])


RULES = [
    *common.UNIVERSAL_SPREAD_RULES,
    rule_dte,
    rule_long_delta,
    rule_moneyness,
    rule_width,
    rule_risk_reward,
    rule_premium,
    rule_short_strike_placement,
    common.rule_earnings_outside_expiry,
]
