"""Jade lizard -- Jade_Lizard_Notes.pdf.

Sell a bear call spread and sell an OTM put on the same expiry, collecting a credit at least
as large as the call spread's width. That one condition is the whole strategy: once the credit
covers the width, no price above the short call can lose money, however far the stock runs.

Two of the rules here deliberately break the house pattern, and both are called out in the
code rather than left to be discovered:

* `rule_earnings_exception` does not gate. Every other credit strategy in this app fails a
  trade with earnings inside the expiry; the notes make the jade lizard an explicit exception
  because a gap *up* cannot hurt a position with no upside risk. A gap *down* still can, which
  is why the 1-SD breakeven rule below is a hard gate and not a preference.
* `rule_short_put_delta` is one-sided. "~25 delta (or lower is safer)" makes 25 a ceiling, not
  a target, so a 12-delta put passes where a band centred on -0.25 would have rejected it.
"""

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
    pct,
    result,
)

CFG = config.JADE_LIZARD


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def rule_no_upside_risk(ctx: RuleContext) -> RuleResult:
    """ "Entry credit is equal or greater than the Bear Call width -- no risk on the upside."

    Equality passes, as the notes state twice ("equal or greater"). The boundary at exactly
    1.0x is tested. This is the rule that makes the structure a jade lizard rather than a
    bear call spread with a short put bolted on, so it is never advisory.
    """
    ratio = ctx.metrics.get("credit_to_call_width")
    minimum = CFG["min_credit_to_call_width"]
    required = f">={num(minimum, 2)}x call width"

    if ratio is None:
        return missing("jade_lizard.upside", "Upside risk", required, "net credit")

    passed = ratio >= minimum
    credit = ctx.metrics.get("net_credit")
    width = ctx.metrics.get("call_width")
    detail = f"credit {money(credit)} vs call width {money(width)}"

    return result(
        "jade_lizard.upside",
        "Upside risk",
        passed,
        ratio,
        required,
        (
            f"No upside risk -- {detail} ({num(ratio, 2)}x)"
            if passed
            else f"Upside risk {money(ctx.metrics.get('upside_risk'))} -- "
            f"{detail} ({num(ratio, 2)}x, need {required})"
        ),
    )


def rule_short_call_delta(ctx: RuleContext) -> RuleResult:
    """ "Short Call at ~45 delta"."""
    low, high = CFG["short_call_delta"]
    return check_range(
        "jade_lizard.short_call_delta",
        "Short call delta",
        ctx.metrics.get("short_call_delta"),
        low,
        high,
        places=3,
        what="short call delta",
    )


def rule_short_put_delta(ctx: RuleContext) -> RuleResult:
    """ "Short Put at ~25 delta (lower is safer)" -- a ceiling on magnitude, not a band.

    Implemented as a range whose far end is a floor against selling a put so far out that it
    contributes no credit; the meaningful edge is the -0.25 ceiling.
    """
    low, high = CFG["short_put_delta"]
    return check_range(
        "jade_lizard.short_put_delta",
        "Short put delta",
        ctx.metrics.get("short_put_delta"),
        low,
        high,
        places=3,
        what="short put delta",
    )


# ---------------------------------------------------------------------------
# Return and downside
# ---------------------------------------------------------------------------


def rule_roi(ctx: RuleContext) -> RuleResult:
    """ "Max ROI is greater than 10% with capital based on margin / buying power at entry."

    Strictly greater, as written -- exactly 10.0% fails, and that boundary is tested. The
    buying-power figure is the app's estimate, not the broker's; see config.JADE_LIZARD.
    """
    roi = ctx.metrics.get("roi")
    minimum = CFG["min_roi"]
    required = f">{pct(minimum, 0)} on buying power"

    if roi is None:
        return missing("jade_lizard.roi", "Max ROI", required, "net credit or buying power")

    passed = roi > minimum
    return result(
        "jade_lizard.roi",
        "Max ROI",
        passed,
        roi,
        required,
        f"Max ROI {pct(roi)} on {money(ctx.metrics.get('buying_power'))} buying power "
        f"(estimated, need {required})",
    )


def rule_breakeven_at_one_sd(ctx: RuleContext) -> RuleResult:
    """ "Break-even on the downside is at the 1-SD point (or lower)."

    Measured as how many standard deviations of expected move sit between spot and the
    breakeven. Further away is better, so this is a minimum and not a band.

    Worth knowing what a failure here means, because it is not like the other gates. The
    breakeven is `put strike - credit`, and the put strike is ours to choose, so this rule can
    always be satisfied by moving leg 1 further out -- at the cost of the credit that
    `rule_no_upside_risk` needs. A row failing here is not disqualified, it is one corner of a
    three-way trade-off between this rule, the call width and the ROI floor. The builder fans
    the put across the delta band precisely so the alternatives are on screen together.
    """
    sd = ctx.metrics.get("breakeven_sd")
    minimum = CFG["min_breakeven_sd"]
    required = f">={num(minimum, 1)} SD below spot"

    if sd is None:
        return missing(
            "jade_lizard.breakeven_sd", "Downside breakeven", required, "IV or spot price"
        )

    passed = sd >= minimum
    return result(
        "jade_lizard.breakeven_sd",
        "Downside breakeven",
        passed,
        sd,
        required,
        (
            f"Breakeven {money(ctx.metrics.get('breakeven'))} is {num(sd, 2)} SD below spot "
            f"(need {required})"
            if passed
            else f"Breakeven {money(ctx.metrics.get('breakeven'))} is only {num(sd, 2)} SD "
            f"below spot (need {required}) -- a further-out put fixes this but costs credit"
        ),
    )


def rule_win_probability(ctx: RuleContext) -> RuleResult:
    """ ">70% probability of winning".

    Advisory, because the number is approximated from the short put's delta rather than
    measured: delta is a serviceable stand-in for the chance of finishing out of the money,
    but it is not that probability, and the message says so. The 1-SD breakeven rule above
    is the hard gate the notes actually rest this claim on.
    """
    prob = ctx.metrics.get("prob_win_approx")
    minimum = CFG["min_win_probability"]
    required = f">={pct(minimum, 0)}"

    if prob is None:
        return result(
            "jade_lizard.win_prob",
            "P(win)",
            None,
            None,
            required,
            "P(win) UNKNOWN -- short put delta unavailable",
            advisory=True,
        )

    passed = prob >= minimum
    return result(
        "jade_lizard.win_prob",
        "P(win)",
        passed,
        prob,
        required,
        f"P(win) {pct(prob)} approximated from short put delta (need {required})",
        advisory=True,
    )


# ---------------------------------------------------------------------------
# Capital and obligations
# ---------------------------------------------------------------------------


def rule_assignment_capacity(ctx: RuleContext) -> RuleResult:
    """ "you must be willing to own 100 shares at the put strike, with the cash or buying
    power to match. Every caution from the cash secured put section applies unchanged."

    Checked against the same account cash figure the CSP screen uses, so the two strategies
    cannot disagree about whether an assignment is affordable.
    """
    needed = ctx.metrics.get("cash_secured")
    required = "cash >= put strike x 100"

    if needed is None:
        return missing("jade_lizard.assignment", "Assignment capacity", required, "put strike")

    passed = ctx.cash_available >= needed
    return result(
        "jade_lizard.assignment",
        "Assignment capacity",
        passed,
        needed,
        required,
        f"Assignment costs {money(needed)}, account has {money(ctx.cash_available)}",
    )


# ---------------------------------------------------------------------------
# Volatility, time and the earnings exception
# ---------------------------------------------------------------------------


def rule_iv(ctx: RuleContext) -> RuleResult:
    """ "Ideally, enter when IV is high -- but it's not a must." Advisory, exactly as written."""
    return common.rule_iv_preferred(ctx, CFG["preferred_iv_percentile"])


def rule_dte(ctx: RuleContext) -> RuleResult:
    """ "DTE: 4-6 weeks." """
    return common.rule_dte_range(ctx, *CFG["dte"])


def rule_earnings_exception(ctx: RuleContext) -> RuleResult:
    """ "JL can be used during the earnings period."

    The one place in this codebase where earnings inside the expiry is not a failure. It is
    still reported on every row, because the exception is one-sided: the upside is riskless
    so a gap up is harmless, but a gap down goes straight through the short put. An unknown
    date is shown as UNKNOWN rather than quietly treated as clear.
    """
    earnings = ctx.underlying.earnings_date
    expiry = ctx.expiry
    required = "allowed inside expiry"

    if earnings is None:
        return result(
            "jade_lizard.earnings",
            "Earnings",
            None,
            None,
            required,
            "Earnings UNKNOWN -- verify manually (allowed for JL, but a gap down still hurts)",
            advisory=True,
        )

    if expiry is None:
        return result(
            "jade_lizard.earnings",
            "Earnings",
            None,
            None,
            required,
            "Expiry unknown",
            advisory=True,
        )

    inside = ctx.quote_date <= earnings <= expiry
    stale = " [STALE >90d]" if ctx.underlying.earnings_is_stale else ""
    return result(
        "jade_lizard.earnings",
        "Earnings",
        True,
        earnings,
        required,
        (
            f"Earnings {earnings.isoformat()} inside expiry -- allowed for JL "
            f"(no upside risk; a gap down still hits the short put){stale}"
            if inside
            else f"Earnings {earnings.isoformat()} outside expiry{stale}"
        ),
        advisory=True,
    )


RULES = [
    *common.UNIVERSAL_SPREAD_RULES,
    # "Avoid highly speculative stocks / stocks with a good chance of collapse" and "good
    # options liquidity" -- the same two proxies the credit spreads use.
    common.rule_market_cap,
    common.rule_avg_volume,
    # Not named in the jade lizard notes, but "Every caution from the cash secured put
    # section applies unchanged" imports it, and the CSP screen excludes biotech/pharma.
    common.rule_sector,
    rule_dte,
    rule_short_call_delta,
    rule_short_put_delta,
    rule_no_upside_risk,
    rule_roi,
    rule_breakeven_at_one_sd,
    rule_assignment_capacity,
    rule_win_probability,
    rule_iv,
    rule_earnings_exception,
    # "Technical setup: neutral to slightly bullish."
    *common.trend_rules(bullish=True),
]
