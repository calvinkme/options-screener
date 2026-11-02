"""Trade maths, transcribed from CLAUDE.md and cross-checked against the workbooks.

Every formula here is either quoted from CLAUDE.md's "Metric formulas" section or read
directly out of a live cell in CSP Template.xlsx. Where the two disagree the discrepancy is
called out in a comment and resolved by a flag in config.py -- never silently.

All money is Decimal. Division that would be by zero, or that depends on a missing input,
returns None and lets the calling rule fail. Nothing here invents a number.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from backend.app import config
from backend.app.domain import Leg, Right

DAYS_PER_YEAR = Decimal("365")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def div(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    """Division that refuses to guess: None in, None out; zero denominator, None out."""
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return None
    try:
        return numerator / denominator
    except (InvalidOperation, ZeroDivisionError):
        return None


def dec(value: float | int | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def days_to_expiry(expiry: date, quote_date: date) -> int:
    """Plain calendar difference. Drives the 30-45 DTE gates.

    Matches both trackers (`EXPIRATION_DAY - TODAY()`), not CLAUDE.md's `+ 1`, which is
    applied only inside annualisation -- see `annualisation_dte`.
    """
    return (expiry - quote_date).days


def annualisation_dte(dte: int) -> int:
    """CLAUDE.md: `dte = expiry_date - quote_date + 1`. Flagged, not assumed."""
    return dte + 1 if config.DTE_ANNUALISE_PLUS_ONE else dte


def annualise(value: Decimal | None, dte: int) -> Decimal | None:
    """`x / dte * 365`, the annualisation used throughout both the spec and the workbook.

    Guards on the *input* DTE, not the +1-adjusted one: an option expiring today has no
    meaningful annualised return, and letting `DTE_ANNUALISE_PLUS_ONE` turn dte=0 into a
    divisor of 1 would report a 1095% figure with a straight face.
    """
    if value is None or dte <= 0:
        return None
    effective = annualisation_dte(dte)
    if effective <= 0:
        return None
    return value / Decimal(effective) * DAYS_PER_YEAR


def commission_for(contracts: int, legs: int = 1) -> Decimal:
    """CSP Template.xlsx K6: `=0.45*D6`, i.e. per contract, per leg."""
    if not config.DEDUCT_COMMISSION_FROM_ROI:
        return Decimal(0)
    return config.COMMISSION_PER_CONTRACT * Decimal(abs(contracts)) * Decimal(legs)


# ---------------------------------------------------------------------------
# Covered call -- CLAUDE.md "Covered call"
# ---------------------------------------------------------------------------


def covered_call_metrics(
    *,
    stock_price: Decimal | None,
    option_mark: Decimal | None,
    strike: Decimal,
    dte: int,
    dividend_yield: Decimal | None = None,
) -> dict[str, Any]:
    """
        premium_pct       = option_mark / (stock_price - option_mark)
        premium_annual    = premium_pct / dte * 365
        prem_plus_div     = premium_annual + (dividend_yield * 0.70)
        diff_to_strike    = strike - stock_price
        diff_pct          = diff_to_strike / stock_price
        diff_annual       = diff_pct / dte * 365
        max_roi_annual    = prem_plus_div + diff_annual

    Note the unusual `premium_pct` denominator: it is the net cost basis of a buy-write,
    not the share price. Transcribed as written.
    """
    net_basis = None
    if stock_price is not None and option_mark is not None:
        net_basis = stock_price - option_mark

    premium_pct = div(option_mark, net_basis)
    premium_annual = annualise(premium_pct, dte)

    weighted_div = None
    if dividend_yield is not None:
        weighted_div = dividend_yield * config.DIVIDEND_WEIGHT

    prem_plus_div = premium_annual
    if premium_annual is not None and weighted_div is not None:
        prem_plus_div = premium_annual + weighted_div

    diff_to_strike = strike - stock_price if stock_price is not None else None
    diff_pct = div(diff_to_strike, stock_price)
    diff_annual = annualise(diff_pct, dte)

    max_roi_annual = None
    if prem_plus_div is not None and diff_annual is not None:
        max_roi_annual = prem_plus_div + diff_annual

    roi_30d = None
    if premium_pct is not None and dte > 0:
        roi_30d = premium_pct / Decimal(annualisation_dte(dte)) * Decimal(30)

    return {
        "premium_pct": premium_pct,
        "premium_annual": premium_annual,
        "weighted_dividend": weighted_div,
        "prem_plus_div": prem_plus_div,
        "diff_to_strike": diff_to_strike,
        "diff_pct": diff_pct,
        "diff_annual": diff_annual,
        "max_roi_annual": max_roi_annual,
        "roi_30d": roi_30d,
    }


# ---------------------------------------------------------------------------
# Cash secured put -- CLAUDE.md "Cash secured put" + CSP Template.xlsx row 6
# ---------------------------------------------------------------------------


def csp_metrics(
    *,
    stock_price: Decimal | None,
    option_mark: Decimal | None,
    strike: Decimal,
    dte: int,
    contracts: int = 1,
    buying_power_override: Decimal | None = None,
) -> dict[str, Any]:
    """Workbook cell references are given per line; these are the authority.

        cash_secured   = STRIKE * qty * 100                    (G6)
        premium_total  = MARK * 100 * qty                      (I6, J6)
        max_profit     = premium_total - commission            (M6 = J6 - L6)
        roi_premium    = max_profit / cash_secured             (N6)
        roi_annual     = roi_premium / dte * 365               (O6)
        roi_bp         = max_profit / buying_power             (P6)
        roi_bp_annual  = roi_bp / dte * 365                    (Q6)
        assigned_net   = strike - premium_per_share            (U6)
        discount       = (assigned_net - last_price) / last    (W6)

    CLAUDE.md says `cash_secured = stock_price * 100`; the workbook uses the strike, which is
    what is actually escrowed. config.CSP_CASH_BASIS decides, defaulting to the workbook.
    """
    qty = abs(contracts) or 1

    if config.CSP_CASH_BASIS == "strike":
        cash_basis = strike
    else:
        cash_basis = stock_price if stock_price is not None else strike

    cash_secured = cash_basis * Decimal(qty) * Decimal(config.CONTRACT_MULTIPLIER)

    premium_total = None
    if option_mark is not None:
        premium_total = option_mark * Decimal(config.CONTRACT_MULTIPLIER) * Decimal(qty)

    commission = commission_for(qty, legs=1)
    max_profit = premium_total - commission if premium_total is not None else None

    roi_premium = div(max_profit, cash_secured)
    roi_annual_cash = annualise(roi_premium, dte)

    buying_power = (
        buying_power_override
        if buying_power_override is not None
        else cash_secured * config.BUYING_POWER_PCT
    )
    roi_bp = div(max_profit, buying_power)
    roi_annual_margin = annualise(roi_bp, dte)

    assigned_net = strike - option_mark if option_mark is not None else None
    discount_vs_price = None
    if assigned_net is not None and stock_price not in (None, 0):
        discount_vs_price = div(assigned_net - stock_price, stock_price)

    monthly_return = None
    if roi_premium is not None and dte > 0:
        monthly_return = roi_premium / Decimal(annualisation_dte(dte)) * Decimal(30)

    return {
        "cash_secured": cash_secured,
        "premium_total": premium_total,
        "commission": commission,
        "max_profit": max_profit,
        "max_loss": cash_secured - (max_profit or Decimal(0)),
        "roi_premium": roi_premium,
        "roi_annual_cash": roi_annual_cash,
        "buying_power": buying_power,
        "roi_bp": roi_bp,
        "roi_annual_margin": roi_annual_margin,
        "assigned_price": strike,
        "collected_premium": option_mark,
        "assigned_net_cost": assigned_net,
        "discount_vs_price": discount_vs_price,
        "monthly_return": monthly_return,
        "return_of_strike": div(option_mark, strike),
    }


# ---------------------------------------------------------------------------
# Vertical credit spread -- CLAUDE.md "Vertical credit spread"
# ---------------------------------------------------------------------------


def credit_spread_metrics(
    *,
    short_premium: Decimal | None,
    long_premium: Decimal | None,
    short_strike: Decimal,
    long_strike: Decimal,
    right: Right,
    dte: int,
    contracts: int = 1,
) -> dict[str, Any]:
    """
    net_credit        = short_premium - long_premium
    max_profit        = net_credit * 100
    max_loss          = (abs(short_strike - long_strike) - net_credit) * 100
    breakeven         = short_strike -/+ net_credit
    roi               = max_profit / max_loss
    loss_ratio        = max_loss / max_profit          # must be <= 4.0
    stop_loss_amount  = net_credit * 1.5 * 100
    """
    qty = abs(contracts) or 1
    mult = Decimal(config.CONTRACT_MULTIPLIER) * Decimal(qty)
    width = abs(short_strike - long_strike)

    net_credit = None
    if short_premium is not None and long_premium is not None:
        net_credit = short_premium - long_premium

    commission = commission_for(qty, legs=2)

    max_profit = None
    if net_credit is not None:
        max_profit = net_credit * mult - commission

    max_loss = None
    if net_credit is not None:
        max_loss = (width - net_credit) * mult + commission

    breakeven = None
    if net_credit is not None:
        breakeven = short_strike - net_credit if right is Right.PUT else short_strike + net_credit

    roi = div(max_profit, max_loss)
    loss_ratio = div(max_loss, max_profit)
    stop_loss_amount = (
        net_credit * config.BULL_PUT["stop_credit_multiple"] * mult
        if net_credit is not None
        else None
    )

    return {
        "width": width,
        "net_credit": net_credit,
        "max_profit": max_profit,
        "max_loss": max_loss,
        "breakeven": breakeven,
        "roi": roi,
        "roi_annual": annualise(roi, dte),
        "loss_ratio": loss_ratio,
        "stop_loss_amount": stop_loss_amount,
        "commission": commission,
        "capital": max_loss,
    }


# ---------------------------------------------------------------------------
# Vertical debit spread -- CLAUDE.md "Vertical debit spread"
# ---------------------------------------------------------------------------


def debit_spread_metrics(
    *,
    long_premium: Decimal | None,
    short_premium: Decimal | None,
    long_strike: Decimal,
    short_strike: Decimal,
    right: Right,
    dte: int,
    contracts: int = 1,
) -> dict[str, Any]:
    """
    net_debit         = long_premium - short_premium
    max_loss          = net_debit * 100
    max_profit        = (abs(short_strike - long_strike) - net_debit) * 100
    breakeven         = long_strike +/- net_debit
    risk_reward       = max_profit / max_loss          # must be >= 2.0
    """
    qty = abs(contracts) or 1
    mult = Decimal(config.CONTRACT_MULTIPLIER) * Decimal(qty)
    width = abs(short_strike - long_strike)

    net_debit = None
    if long_premium is not None and short_premium is not None:
        net_debit = long_premium - short_premium

    commission = commission_for(qty, legs=2)

    max_loss = net_debit * mult + commission if net_debit is not None else None
    max_profit = (width - net_debit) * mult - commission if net_debit is not None else None

    breakeven = None
    if net_debit is not None:
        breakeven = long_strike + net_debit if right is Right.CALL else long_strike - net_debit

    risk_reward = div(max_profit, max_loss)

    return {
        "width": width,
        "net_debit": net_debit,
        "max_loss": max_loss,
        "max_profit": max_profit,
        "breakeven": breakeven,
        "risk_reward": risk_reward,
        "roi": risk_reward,
        "roi_annual": annualise(risk_reward, dte),
        "commission": commission,
        "capital": max_loss,
    }


# ---------------------------------------------------------------------------
# Jade lizard -- Jade_Lizard_Notes.pdf
# ---------------------------------------------------------------------------


def jade_lizard_metrics(
    *,
    short_put_premium: Decimal | None,
    short_call_premium: Decimal | None,
    long_call_premium: Decimal | None,
    short_put_strike: Decimal,
    short_call_strike: Decimal,
    long_call_strike: Decimal,
    dte: int,
    contracts: int = 1,
) -> dict[str, Any]:
    """Short put + short call + long call, one expiry.

        call_width    = long_call_strike - short_call_strike
        net_credit    = short_put + short_call - long_call
        max_profit    = net_credit * 100                     (price between the short strikes)
        upside_risk   = max(call_width - net_credit, 0) * 100 -- zero is the whole point
        breakeven     = short_put_strike - net_credit
        max_loss      = (short_put_strike - net_credit) * 100 (the stock at zero)

    Checked against the notes' TSLA worked example: sell 230P @ 6.975, sell 265C @ 13.225,
    buy 275C @ 9.525 -> credit 10.675, max profit $1,067, call width $10.00, breakeven
    $219.33. backend/tests/test_formulas.py asserts exactly those figures.
    """
    qty = abs(contracts) or 1
    mult = Decimal(config.CONTRACT_MULTIPLIER) * Decimal(qty)
    call_width = long_call_strike - short_call_strike

    net_credit = None
    if None not in (short_put_premium, short_call_premium, long_call_premium):
        net_credit = short_put_premium + short_call_premium - long_call_premium

    commission = commission_for(qty, legs=3)

    max_profit = net_credit * mult - commission if net_credit is not None else None

    # The defining property. Anything above zero means the structure does have upside risk
    # and is therefore not a jade lizard as the notes define one.
    upside_risk = None
    if net_credit is not None:
        shortfall = call_width - net_credit
        upside_risk = shortfall * mult + commission if shortfall > 0 else Decimal(0)

    credit_to_call_width = div(net_credit, call_width)

    breakeven = short_put_strike - net_credit if net_credit is not None else None

    # "Max loss = (Put Strike - Entry Credit) x 100" -- the stock at zero. The short put is
    # unhedged, so this is a real exposure and not a theoretical one.
    max_loss = breakeven * mult + commission if breakeven is not None else None

    cash_secured = short_put_strike * mult

    # See config.JADE_LIZARD for why this is an estimate.
    buying_power = cash_secured * config.JADE_LIZARD["buying_power_pct_of_put_strike"]
    if upside_risk is not None:
        buying_power = buying_power + upside_risk

    roi = div(max_profit, buying_power)

    tp_low, tp_high = config.JADE_LIZARD["take_profit_fractions"]

    return {
        "call_width": call_width,
        "width": call_width,
        "net_credit": net_credit,
        "max_profit": max_profit,
        "max_loss": max_loss,
        "upside_risk": upside_risk,
        "credit_to_call_width": credit_to_call_width,
        "breakeven": breakeven,
        "cash_secured": cash_secured,
        "buying_power": buying_power,
        "capital": buying_power,
        "roi": roi,
        "roi_annual": annualise(roi, dte),
        "commission": commission,
        # Exit triggers, for the journal and (later) alerting -- notes, "Optimal exit rules".
        "take_profit_low": max_profit * tp_low if max_profit is not None else None,
        "take_profit_high": max_profit * tp_high if max_profit is not None else None,
        "stop_trigger_price": short_put_strike,
        "close_above_price": long_call_strike,
    }


# ---------------------------------------------------------------------------
# Synthetic spread / risk reversal -- Synthetic_Spread_Notes.pdf
# ---------------------------------------------------------------------------


def synthetic_metrics(
    *,
    long_premium: Decimal | None,
    short_premium: Decimal | None,
    long_strike: Decimal,
    short_strike: Decimal,
    stock_price: Decimal | None,
    bullish: bool,
    dte: int,
    contracts: int = 1,
) -> dict[str, Any]:
    """Long a call and short a put (bullish), or long a put and short a call (bearish).

        net_debit  = long_premium - short_premium      (negative when it opens for a credit)
        breakeven  = long_strike +/- net_debit
        max_loss   = (short_strike + net_debit) * 100  -- bullish, the stock at zero
        max_profit = unlimited                         -- bullish

    The bearish side mirrors it: profit is bounded only by the stock reaching zero, and the
    loss from the short call is genuinely unbounded, so `max_loss` is None. None here means
    "no finite number exists", not "we failed to compute it" -- the UI says unlimited rather
    than showing it as missing data.

    The notes' HD example: buy Jul 190C @ 6.05, sell Jul 190P @ 5.17 -> net debit $0.88,
    max loss (190 + 0.88) x 100 = $19,088.
    """
    qty = abs(contracts) or 1
    mult = Decimal(config.CONTRACT_MULTIPLIER) * Decimal(qty)

    net_debit = None
    if long_premium is not None and short_premium is not None:
        net_debit = long_premium - short_premium

    commission = commission_for(qty, legs=2)
    net_cost = net_debit * mult + commission if net_debit is not None else None

    breakeven = None
    if net_debit is not None:
        breakeven = long_strike + net_debit if bullish else long_strike - net_debit

    max_loss: Decimal | None = None
    max_profit: Decimal | None = None
    if bullish:
        # "Max loss = (Put Strike + Debit) x 100 -- similar to owning the shares."
        if net_debit is not None:
            max_loss = (short_strike + net_debit) * mult + commission
        max_profit = None  # unlimited
    else:
        max_loss = None  # unbounded: the short call has no ceiling
        if net_debit is not None:
            max_profit = (long_strike - net_debit) * mult - commission

    # Split strikes leave a flat zone between the legs where the position does nothing.
    flat_zone_low = min(long_strike, short_strike)
    flat_zone_high = max(long_strike, short_strike)

    # Opened for a credit, the far side of the flat zone crosses zero as well.
    secondary_breakeven = None
    if net_debit is not None and net_debit < 0:
        secondary_breakeven = short_strike + net_debit if bullish else short_strike - net_debit

    abs_debit = abs(net_debit) if net_debit is not None else None
    net_premium_pct_of_spot = div(abs_debit, stock_price)

    notional = stock_price * mult if stock_price is not None else None
    buying_power = None
    if notional is not None:
        buying_power = notional * config.SYNTHETIC["buying_power_pct"]

    return {
        "net_debit": net_debit,
        "net_cost": net_cost,
        "breakeven": breakeven,
        "secondary_breakeven": secondary_breakeven,
        "max_loss": max_loss,
        "max_profit": max_profit,
        "flat_zone_low": flat_zone_low,
        "flat_zone_high": flat_zone_high,
        "strike_gap": flat_zone_high - flat_zone_low,
        "net_premium_pct_of_spot": net_premium_pct_of_spot,
        "notional": notional,
        "buying_power": buying_power,
        "capital": buying_power,
        # What the leverage actually is: 100 shares of exposure per unit of blocked capital.
        "leverage": div(notional, buying_power),
        # The short leg is an obligation, not a premium sale -- notes, "Hard rule".
        "assignment_obligation": short_strike * mult if bullish else None,
        "commission": commission,
    }


# ---------------------------------------------------------------------------
# Long single option
# ---------------------------------------------------------------------------


def long_option_metrics(
    *,
    premium: Decimal | None,
    strike: Decimal,
    right: Right,
    dte: int,
    contracts: int = 1,
) -> dict[str, Any]:
    qty = abs(contracts) or 1
    mult = Decimal(config.CONTRACT_MULTIPLIER) * Decimal(qty)
    commission = commission_for(qty, legs=1)

    total_premium = premium * mult + commission if premium is not None else None
    breakeven = None
    if premium is not None:
        breakeven = strike + premium if right is Right.CALL else strike - premium

    return {
        "premium": premium,
        "total_premium": total_premium,
        "max_loss": total_premium,
        "max_profit": None,  # unbounded for a call; strike-limited for a put
        "breakeven": breakeven,
        "commission": commission,
        "capital": total_premium,
    }


# ---------------------------------------------------------------------------
# Shared position summary -- mirrors the tracker's Delta/Theta/EV columns
# ---------------------------------------------------------------------------


def position_greeks(legs: list[Leg]) -> dict[str, Any]:
    """Aggregate the per-leg Greeks the way the IBKR tracker's U/W/Y columns do.

    A single missing Greek makes the aggregate None rather than a partial sum, because a
    partial delta silently understates exposure.
    """
    total_delta: Decimal | None = Decimal(0)
    total_theta: Decimal | None = Decimal(0)
    total_ev: Decimal | None = Decimal(0)

    for leg in legs:
        q = leg.quote
        if total_delta is not None:
            d = dec(q.delta)
            total_delta = None if d is None else total_delta + d * Decimal(leg.qty)
        if total_theta is not None:
            t = dec(q.theta)
            total_theta = None if t is None else total_theta + t * Decimal(leg.qty)
        if total_ev is not None:
            ev = q.extrinsic
            total_ev = None if ev is None else total_ev + ev * Decimal(leg.qty)

    short_legs = [leg for leg in legs if leg.is_short]
    short_delta_per_share = dec(short_legs[0].quote.delta) if short_legs else None

    return {
        "total_delta": total_delta,
        "total_theta": total_theta,
        "extrinsic_value": total_ev,
        "short_delta_per_share": short_delta_per_share,
    }
