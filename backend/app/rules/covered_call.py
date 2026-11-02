"""Covered call -- CLAUDE.md "Covered call".

Note the delta band is quoted in the spec as "40-45", i.e. whole deltas. Converted to 0.40-0.45
here; config.COVERED_CALL["delta"] holds the fractional form.
"""

from __future__ import annotations

from backend.app import config
from backend.app.domain import RuleResult
from backend.app.rules import common
from backend.app.rules.base import RuleContext, check_min, check_range, result

CFG = config.COVERED_CALL


def rule_delta(ctx: RuleContext) -> RuleResult:
    low, high = CFG["delta"]
    return check_range(
        "covered_call.delta",
        "Delta",
        ctx.metrics.get("short_delta_per_share"),
        low,
        high,
        places=3,
        what="short call delta",
    )


def rule_dte(ctx: RuleContext) -> RuleResult:
    return common.rule_dte_range(ctx, *CFG["dte"])


def rule_roi(ctx: RuleContext) -> RuleResult:
    """2-3% ROI in 30 days."""
    return check_min(
        "covered_call.roi",
        "ROI (30d)",
        ctx.metrics.get("roi_30d"),
        CFG["min_roi_30d"],
        places=4,
        what="option mark",
    )


def rule_shares_owned(ctx: RuleContext) -> RuleResult:
    """You cannot write a covered call against shares you do not hold."""
    owned = ctx.underlying.shares_owned
    minimum = CFG["min_shares_owned"]
    passed = owned >= minimum
    return result(
        "covered_call.shares",
        "Shares owned",
        passed,
        owned,
        f">={minimum}",
        f"Shares owned {owned:,} (need >={minimum:,})",
    )


def rule_iv(ctx: RuleContext) -> RuleResult:
    return common.rule_iv_preferred(ctx, CFG["preferred_iv_percentile"])


RULES = [
    *common.UNIVERSAL_RULES,
    rule_shares_owned,
    rule_delta,
    rule_dte,
    rule_roi,
    rule_iv,
    common.rule_earnings_outside_expiry,
]
