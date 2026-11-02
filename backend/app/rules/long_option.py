"""Long call / long put -- CLAUDE.md "Long call / long put".

The one strategy family where low IV is what you want: you are buying premium, so the same
IV percentile that fails a credit spread passes here. That inversion is why the ticker-first
view is useful -- a name can be wrong for selling and right for buying on the same day.
"""

from __future__ import annotations

from backend.app import config
from backend.app.domain import RuleResult, Strategy
from backend.app.rules import common
from backend.app.rules.base import RuleContext, check_range

CFG = config.LONG_OPTION


def rule_delta(ctx: RuleContext) -> RuleResult:
    """Delta 0.50-0.60. A put's delta is negative, so the band is mirrored for long puts --
    comparing a -0.55 put against +0.50..+0.60 would fail every put ever quoted."""
    low, high = CFG["delta"]
    if ctx.strategy is Strategy.LONG_PUT:
        low, high = -high, -low
    delta = ctx.metrics.get("long_delta_per_share")
    return check_range(
        "long_option.delta", "Delta", delta, low, high, places=3, what="long leg delta"
    )


def rule_moneyness(ctx: RuleContext) -> RuleResult:
    """ "ATM or 1 strike ITM" -- 0 or 1 strikes in the money."""
    return check_range(
        "long_option.moneyness",
        "Moneyness",
        ctx.metrics.get("strikes_itm"),
        0,
        1,
        places=0,
        unit=" strikes ITM",
        what="strike ladder",
    )


def rule_dte(ctx: RuleContext) -> RuleResult:
    return common.rule_dte_min(ctx, CFG["min_dte"])


def rule_iv(ctx: RuleContext) -> RuleResult:
    return common.rule_iv_ceiling(ctx, CFG["max_iv_percentile"])


def rule_premium(ctx: RuleContext) -> RuleResult:
    return common.rule_premium_budget(ctx, CFG["max_premium_pct_nlv"])


RULES = [
    *common.UNIVERSAL_RULES,
    rule_iv,
    rule_dte,
    rule_delta,
    rule_moneyness,
    rule_premium,
]
