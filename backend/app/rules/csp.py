"""Cash secured put -- CLAUDE.md "Cash secured put".

Two modes, because the spec gives two delta bands: a standalone premium sale centred on -0.30,
and the wheel variant at -0.20..-0.30 where assignment is acceptable. config.CASH_SECURED_PUT
["mode"] selects; both are exposed so the screener can be run either way.
"""

from __future__ import annotations

from backend.app import config
from backend.app.domain import RuleResult
from backend.app.rules import common
from backend.app.rules.base import RuleContext, check_min, check_range, missing, money, result

CFG = config.CASH_SECURED_PUT


def _is_wheel() -> bool:
    return CFG["mode"] == "wheel"


def rule_delta(ctx: RuleContext) -> RuleResult:
    low, high = CFG["delta_wheel"] if _is_wheel() else CFG["delta_standalone"]
    return check_range(
        "csp.delta",
        "Delta",
        ctx.metrics.get("short_delta_per_share"),
        low,
        high,
        places=3,
        what="short leg delta",
    )


def rule_dte(ctx: RuleContext) -> RuleResult:
    return common.rule_dte_range(ctx, *CFG["dte"])


def rule_return(ctx: RuleContext) -> RuleResult:
    """Standalone: >=2-3% per month. Wheel: >=2% of strike."""
    if _is_wheel():
        return check_min(
            "csp.return",
            "Return on strike",
            ctx.metrics.get("return_of_strike"),
            CFG["min_wheel_return_of_strike"],
            places=4,
            what="option mark",
        )
    return check_min(
        "csp.return",
        "Monthly return",
        ctx.metrics.get("monthly_return"),
        CFG["min_monthly_return"],
        places=4,
        what="option mark",
    )


def rule_cash_available(ctx: RuleContext) -> RuleResult:
    """strike x 100 must actually be in the account -- otherwise it is not cash secured."""
    required_cash = ctx.metrics.get("cash_secured")
    required = "cash >= strike x 100"
    if required_cash is None:
        return missing("csp.cash", "Cash available", required, "strike")

    passed = ctx.cash_available >= required_cash
    return result(
        "csp.cash",
        "Cash available",
        passed,
        required_cash,
        required,
        f"Needs {money(required_cash)} secured, account has {money(ctx.cash_available)}",
    )


def rule_iv(ctx: RuleContext) -> RuleResult:
    return common.rule_iv_preferred(ctx, CFG["preferred_iv_percentile"])


RULES = [
    *common.UNIVERSAL_RULES,
    rule_delta,
    rule_dte,
    rule_return,
    rule_cash_available,
    common.rule_sector,
    rule_iv,
    *common.trend_rules(bullish=True),
]
