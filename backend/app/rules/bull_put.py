"""Bull put spread -- CLAUDE.md "Bull put spread"."""

from __future__ import annotations

from backend.app import config
from backend.app.domain import RuleResult
from backend.app.rules import common
from backend.app.rules.base import (
    RuleContext,
    check_range,
    missing,
    money,
    num,
    result,
)

CFG = config.BULL_PUT


def rule_short_delta(ctx: RuleContext) -> RuleResult:
    """Short put delta -0.20 to -0.25."""
    low, high = CFG["short_delta"]
    delta = ctx.metrics.get("short_delta_per_share")
    return check_range(
        "bull_put.short_delta",
        "Short delta",
        delta,
        low,
        high,
        places=3,
        what="short leg delta",
    )


def rule_long_strike_offset(ctx: RuleContext) -> RuleResult:
    """Long put 1-2 strikes below the short."""
    low, high = CFG["long_strike_offset"]
    offset = ctx.metrics.get("strike_offset")
    return check_range(
        "bull_put.strike_offset",
        "Long strike offset",
        offset,
        low,
        high,
        places=0,
        unit=" strikes",
        what="strike ladder",
    )


def rule_max_loss(ctx: RuleContext) -> RuleResult:
    """Max loss <= 4x net credit. Boundary case (exactly 4.0) is a PASS and is tested."""
    ratio = ctx.metrics.get("loss_ratio")
    limit = CFG["max_loss_credit_multiple"]
    required = f"<={num(limit, 1)}x credit"
    if ratio is None:
        return missing("bull_put.max_loss", "Max loss", required, "net credit")

    credit = ctx.metrics.get("net_credit")
    if credit is not None and credit <= 0:
        # A non-positive credit makes the ratio zero or negative, which would sail under
        # the 4x ceiling. Usually a wide leg whose mid is meaningless.
        return result(
            "bull_put.max_loss",
            "Max loss",
            False,
            ratio,
            required,
            f"Net credit {money(credit)} -- the spread collects nothing (need a credit, "
            f"max loss {required})",
        )

    passed = ratio <= limit
    return result(
        "bull_put.max_loss",
        "Max loss",
        passed,
        ratio,
        required,
        f"Max loss {num(ratio, 2)}x credit "
        f"({money(ctx.metrics.get('max_loss'))} vs {money(ctx.metrics.get('max_profit'))} credit) "
        f"(need {required})",
    )


def rule_iv(ctx: RuleContext) -> RuleResult:
    minimum = CFG["min_raw_iv"] if config.IV_GATE_USES == "raw" else CFG["min_iv_percentile"]
    return common.rule_iv_floor(ctx, minimum)


def rule_dte(ctx: RuleContext) -> RuleResult:
    return common.rule_dte_range(ctx, *CFG["dte"])


def rule_size(ctx: RuleContext) -> RuleResult:
    return common.rule_position_size(ctx, CFG["position_size_pct_nlv"], CFG["stop_credit_multiple"])


RULES = [
    *common.UNIVERSAL_SPREAD_RULES,
    common.rule_market_cap,
    common.rule_avg_volume,
    common.rule_sector,
    rule_iv,
    rule_dte,
    common.rule_earnings_outside_expiry,
    rule_short_delta,
    rule_long_strike_offset,
    rule_max_loss,
    rule_size,
    *common.trend_rules(bullish=True),
]
