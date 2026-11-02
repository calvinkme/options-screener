"""Synthetic spread / risk reversal -- Synthetic_Spread_Notes.pdf.

Long an ATM call and short an ATM put reproduces the stock's payoff for near-zero premium on
roughly a fifth of the capital. The bearish mirror is long an ATM put and short an ATM call.
One module covers both; `Strategy.SYNTHETIC_LONG` and `Strategy.SYNTHETIC_SHORT` differ only
in which leg is which, and `ctx.strategy` is the only thing the rules branch on.

Three notes on how this strategy sits against the rest of the app:

* There is no IV gate, and that absence is deliberate and visible. The notes' hard rule says
  vega on the two legs cancels, so `common.rule_iv_not_applicable` states that on every row
  and still prints both IV measures rather than leaving a blank.
* Two gates here are NOT in the course notes -- the capital budget and the earnings gate. The
  notes give no sizing rule for what they themselves call the highest-risk structure in the
  course, and they say nothing about earnings. Both are config flags, both default to the
  conservative reading, and both say so in their messages.
* `max_loss` is None on the short side because the loss is genuinely unbounded, not because
  data is missing. That is the one place in this codebase where None does not mean "unknown".
"""

from __future__ import annotations

from backend.app import config
from backend.app.domain import RuleResult, Strategy
from backend.app.rules import common
from backend.app.rules.base import (
    RuleContext,
    missing,
    money,
    num,
    pct,
    result,
)

CFG = config.SYNTHETIC


def _is_bullish(ctx: RuleContext) -> bool:
    return ctx.strategy is Strategy.SYNTHETIC_LONG


def _split_mode() -> bool:
    return CFG["mode"] == "split"


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def rule_strike_placement(ctx: RuleContext) -> RuleResult:
    """ "Strike price: ATM (or near ATM) to have the highest liquidity."

    In `atm` mode both legs must sit on the same strike, within tolerance of spot. In `split`
    mode the notes' variant applies instead -- the legs straddle spot with a flat zone between
    them -- so the rule checks that they really do straddle it rather than checking distance.
    """
    long_strike = ctx.metrics.get("long_strike")
    short_strike = ctx.metrics.get("short_strike")
    spot = ctx.underlying.price

    if long_strike is None or short_strike is None or spot is None:
        return missing(
            "synthetic.strikes",
            "Strike placement",
            "ATM both legs",
            "strikes or underlying price",
        )

    if _split_mode():
        bullish = _is_bullish(ctx)
        # Bullish: short put below spot, long call above. Bearish: the mirror.
        low, high = (short_strike, long_strike) if bullish else (long_strike, short_strike)
        required = "legs straddle spot"
        passed = low <= spot <= high
        return result(
            "synthetic.strikes",
            "Strike placement",
            passed,
            ctx.metrics.get("strike_gap"),
            required,
            f"Flat zone {money(low)}-{money(high)} around spot {money(spot)} "
            f"(gap {money(ctx.metrics.get('strike_gap'))}, need {required})",
        )

    tolerance = CFG["atm_tolerance_pct"]
    required = f"both legs within {pct(tolerance, 1)} of spot"

    if long_strike != short_strike:
        return result(
            "synthetic.strikes",
            "Strike placement",
            False,
            ctx.metrics.get("strike_gap"),
            required,
            f"Legs on different strikes ({money(long_strike)} / {money(short_strike)}) -- "
            f"set SYNTHETIC['mode'] to 'split' to screen that variant (need {required})",
        )

    distance = abs(long_strike - spot) / spot
    passed = distance <= tolerance
    return result(
        "synthetic.strikes",
        "Strike placement",
        passed,
        distance,
        required,
        f"Strike {money(long_strike)} is {pct(distance)} from spot {money(spot)} "
        f"(need {required})",
    )


def rule_net_premium(ctx: RuleContext) -> RuleResult:
    """ "Net premium is zero (or near zero)."

    Measured against spot rather than in dollars, so the same threshold means the same thing
    on a $40 stock and a $400 one. A credit counts as near zero just as a debit does, which is
    why the metric is an absolute value.
    """
    ratio = ctx.metrics.get("net_premium_pct_of_spot")
    maximum = CFG["max_net_premium_pct_of_spot"]
    required = f"<={pct(maximum, 1)} of spot"

    if ratio is None:
        return missing(
            "synthetic.net_premium", "Net premium", required, "a two-sided quote on both legs"
        )

    debit = ctx.metrics.get("net_debit")
    side = "credit" if debit is not None and debit < 0 else "debit"
    passed = ratio <= maximum
    return result(
        "synthetic.net_premium",
        "Net premium",
        passed,
        ratio,
        required,
        f"Net {side} {money(abs(debit) if debit is not None else None)}/share "
        f"= {pct(ratio)} of spot (need {required})",
    )


def rule_dte(ctx: RuleContext) -> RuleResult:
    """ "DTE: at your time frame -- e.g. 1-3 months, or even LEAPS."

    Screened over the 1-3 month reading. LEAPS are a deliberate omission: they sit outside the
    DTE window this app fetches chains for, so screening them would mean pulling more data on
    every scan for a trade the notes describe as an alternative rather than the default.
    """
    return common.rule_dte_range(ctx, *CFG["dte"])


# ---------------------------------------------------------------------------
# Risk -- the notes' "Hard rule" section
# ---------------------------------------------------------------------------


def rule_assignment_capacity(ctx: RuleContext) -> RuleResult:
    """ "Treat the short put leg as a genuine obligation to buy 100 shares."

    Only the bullish side has a cash obligation. On the bearish side the short call has no
    ceiling at all, so there is no number to check against and the rule says that instead of
    inventing one -- `rule_capital_budget` is what constrains that side.
    """
    if not _is_bullish(ctx):
        return result(
            "synthetic.assignment",
            "Assignment capacity",
            True,
            None,
            "n/a for the short synthetic",
            "Short call leg -- no assignment cash to check, but the upside loss is unbounded",
            advisory=True,
        )

    needed = ctx.metrics.get("assignment_obligation")
    required = "cash >= put strike x 100"

    if needed is None:
        return missing("synthetic.assignment", "Assignment capacity", required, "put strike")

    passed = ctx.cash_available >= needed
    return result(
        "synthetic.assignment",
        "Assignment capacity",
        passed,
        needed,
        required,
        f"Assignment costs {money(needed)}, account has {money(ctx.cash_available)}",
    )


def rule_capital_budget(ctx: RuleContext) -> RuleResult:
    """NOT FROM THE COURSE NOTES -- a sizing guard the notes do not provide.

    The notes rate this structure's max loss as the highest of the three bullish alternatives
    and then give no position-size rule at all. Leverage without a size limit is the failure
    mode this whole screener exists to prevent, so blocked buying power is capped as a share
    of net liquidation. Set SYNTHETIC['max_capital_pct_nlv'] to None to drop the gate.
    """
    limit = CFG["max_capital_pct_nlv"]
    capital = ctx.metrics.get("buying_power")

    if limit is None:
        return result(
            "synthetic.capital",
            "Capital budget",
            True,
            capital,
            "gate disabled",
            f"Capital {money(capital)} -- budget gate disabled in config",
            advisory=True,
        )

    required = f"<={pct(limit, 0)} of net liq"
    if capital is None:
        return missing("synthetic.capital", "Capital budget", required, "underlying price")

    budget = ctx.net_liquidation * limit
    passed = capital <= budget
    return result(
        "synthetic.capital",
        "Capital budget",
        passed,
        capital,
        required,
        f"Buying power {money(capital)} vs {money(budget)} budget "
        f"({pct(limit, 0)} of net liq, not a course rule -- see config) "
        f"[leverage {num(ctx.metrics.get('leverage'))}x]",
    )


def rule_earnings(ctx: RuleContext) -> RuleResult:
    """NOT FROM THE COURSE NOTES -- the notes are silent on earnings for this strategy.

    Unlike the jade lizard, where the exception is stated outright, nothing here licenses
    holding a leveraged directional position through a gap. The app's default applies, and an
    unknown date fails as it does everywhere else. Set SYNTHETIC['earnings_gate'] to False to
    turn the gate off; nothing else in the code assumes it either way.
    """
    if not CFG["earnings_gate"]:
        earnings = ctx.underlying.earnings_date
        return result(
            "synthetic.earnings",
            "Earnings",
            True,
            earnings,
            "gate disabled",
            f"Earnings {earnings.isoformat() if earnings else 'UNKNOWN'} "
            "-- gate disabled in config",
            advisory=True,
        )
    return common.rule_earnings_outside_expiry(ctx)


RULES = [
    *common.UNIVERSAL_SPREAD_RULES,
    # "Enter when you are bullish on a fundamentally good stock" plus "ATM ... to have the
    # highest liquidity" -- the app's two standing proxies for both.
    common.rule_market_cap,
    common.rule_avg_volume,
    rule_dte,
    rule_strike_placement,
    rule_net_premium,
    rule_assignment_capacity,
    rule_capital_budget,
    rule_earnings,
    # Stated as a hard rule in the notes: vega cancels, so IV is reported and never gates.
    common.rule_iv_not_applicable,
]


def rules_for(bullish: bool) -> list:
    """The rule list with the trend bundle pointed the right way."""
    return [*RULES, *common.trend_rules(bullish=bullish)]


LONG_RULES = rules_for(bullish=True)
SHORT_RULES = rules_for(bullish=False)
