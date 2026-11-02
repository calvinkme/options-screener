"""Bear call spread -- CLAUDE.md: "Mirror of the above."

Deliberately written as its own module rather than a flag on bull_put, so that when the two
inevitably diverge (they already do on delta sign and strike direction) the divergence is
visible rather than buried in a conditional.
"""

from __future__ import annotations

from backend.app import config
from backend.app.domain import RuleResult
from backend.app.rules import common
from backend.app.rules.base import RuleContext, check_range, missing, money, num, result

CFG = config.BEAR_CALL


def rule_short_delta(ctx: RuleContext) -> RuleResult:
    """Short call delta +0.20 to +0.25."""
    low, high = CFG["short_delta"]
    delta = ctx.metrics.get("short_delta_per_share")
    return check_range(
        "bear_call.short_delta",
        "Short delta",
        delta,
        low,
        high,
        places=3,
        what="short leg delta",
    )


def rule_long_strike_offset(ctx: RuleContext) -> RuleResult:
    """Long call 1-2 strikes above the short."""
    low, high = CFG["long_strike_offset"]
    offset = ctx.metrics.get("strike_offset")
    return check_range(
        "bear_call.strike_offset",
        "Long strike offset",
        offset,
        low,
        high,
        places=0,
        unit=" strikes",
        what="strike ladder",
    )


def rule_max_loss(ctx: RuleContext) -> RuleResult:
    ratio = ctx.metrics.get("loss_ratio")
    limit = CFG["max_loss_credit_multiple"]
    required = f"<={num(limit, 1)}x credit"
    if ratio is None:
        return missing("bear_call.max_loss", "Max loss", required, "net credit")

    credit = ctx.metrics.get("net_credit")
    if credit is not None and credit <= 0:
        # A non-positive credit makes the ratio zero or negative, which would sail under
        # the 4x ceiling. Usually a wide leg whose mid is meaningless.
        return result(
            "bear_call.max_loss",
            "Max loss",
            False,
            ratio,
            required,
            f"Net credit {money(credit)} -- the spread collects nothing (need a credit, "
            f"max loss {required})",
        )

    passed = ratio <= limit
    return result(
        "bear_call.max_loss",
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
    *common.trend_rules(bullish=False),
]
