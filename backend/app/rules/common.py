"""Rules shared by more than one strategy.

Split out so that, for example, the DTE 30-45 gate is defined exactly once and every strategy
that cites it in CLAUDE.md gets literally the same check.
"""

from __future__ import annotations

from decimal import Decimal

from backend.app import config
from backend.app.domain import RuleResult
from backend.app.rules.base import (
    RuleContext,
    check_max,
    check_min,
    check_range,
    missing,
    money,
    num,
    result,
)


def _chart_advisory() -> bool:
    """Chart rules are advisory unless the config flag promotes them to hard gates."""
    return not config.CHART_RULES_ARE_HARD_GATES


# ---------------------------------------------------------------------------
# Universal -- CLAUDE.md "Universal (all strategies)"
# ---------------------------------------------------------------------------


def rule_bid_ask_spread(ctx: RuleContext) -> RuleResult:
    """Bid/ask spread per leg <= $0.50. The widest leg decides."""
    spreads = [(leg.quote.spread, leg.quote) for leg in ctx.legs]
    if any(s is None for s, _ in spreads):
        return missing(
            "universal.spread",
            "Bid/ask spread",
            f"<={money(config.MAX_BID_ASK_SPREAD)} per leg",
            "a two-sided quote on every leg",
        )

    worst, quote = max(spreads, key=lambda pair: pair[0])
    passed = worst <= config.MAX_BID_ASK_SPREAD
    leg_name = f"{quote.strike_text}{quote.right.value}"
    return result(
        "universal.spread",
        "Bid/ask spread",
        passed,
        worst,
        f"<={money(config.MAX_BID_ASK_SPREAD)}",
        f"Bid/ask spread {money(worst)} on {leg_name} leg "
        f"(need <={money(config.MAX_BID_ASK_SPREAD)})",
    )


def rule_stock_price(ctx: RuleContext) -> RuleResult:
    """Underlying > $20. CLAUDE.md scopes this to spreads."""
    return check_min(
        "universal.stock_price",
        "Stock price",
        ctx.underlying.price,
        config.MIN_STOCK_PRICE_SPREADS,
        unit="",
        what="underlying price",
    )


def rule_contract_size(ctx: RuleContext) -> RuleResult:
    """Contract size 100 shares -- guards against mini/adjusted contracts sneaking in."""
    odd = [leg for leg in ctx.legs if abs(leg.qty) % config.CONTRACT_MULTIPLIER not in (0,)]
    passed = not odd
    return result(
        "universal.contract_size",
        "Contract size",
        passed,
        config.CONTRACT_MULTIPLIER,
        f"{config.CONTRACT_MULTIPLIER} shares",
        (
            f"Contract size {config.CONTRACT_MULTIPLIER} shares"
            if passed
            else "Non-standard contract size -- adjusted or mini contract"
        ),
    )


UNIVERSAL_RULES = [rule_bid_ask_spread, rule_contract_size]
UNIVERSAL_SPREAD_RULES = [rule_bid_ask_spread, rule_contract_size, rule_stock_price]


# ---------------------------------------------------------------------------
# Liquidity and sector -- CLAUDE.md credit-spread block
# ---------------------------------------------------------------------------


def rule_market_cap(ctx: RuleContext) -> RuleResult:
    cap = ctx.underlying.market_cap
    required = f">=${float(config.MIN_MARKET_CAP) / 1e9:.0f}bn"
    if cap is None:
        return missing("liquidity.market_cap", "Market cap", required, "market cap")
    passed = cap >= config.MIN_MARKET_CAP
    return result(
        "liquidity.market_cap",
        "Market cap",
        passed,
        cap,
        required,
        f"Market cap ${float(cap) / 1e9:,.1f}bn (need {required})",
    )


def rule_avg_volume(ctx: RuleContext) -> RuleResult:
    return check_min(
        "liquidity.avg_volume",
        "Avg volume",
        ctx.underlying.avg_volume,
        config.MIN_AVG_VOLUME,
        places=0,
        what="average daily volume",
    )


def rule_sector(ctx: RuleContext) -> RuleResult:
    """NOT biotech, NOT pharmaceutical -- binary event risk the other rules cannot see."""
    sector = ctx.underlying.sector
    required = "not biotech/pharma"
    if not sector:
        return missing("liquidity.sector", "Sector", required, "sector classification")

    lowered = sector.lower()
    hit = next((k for k in config.EXCLUDED_SECTOR_KEYWORDS if k in lowered), None)
    passed = hit is None
    return result(
        "liquidity.sector",
        "Sector",
        passed,
        sector,
        required,
        f"Sector {sector}" if passed else f"Sector {sector} is excluded ({hit})",
    )


# ---------------------------------------------------------------------------
# Volatility -- the ambiguity CLAUDE.md tells us not to resolve silently
# ---------------------------------------------------------------------------


def _iv_actuals(ctx: RuleContext) -> tuple[Decimal | None, str]:
    """Return whichever IV measure gates, plus a label naming it explicitly."""
    if config.IV_GATE_USES == "raw":
        raw = ctx.underlying.iv
        return (Decimal(str(raw * 100)) if raw is not None else None, "IV (raw)")
    return ctx.underlying.iv_percentile, "IV percentile"


def rule_iv_floor(ctx: RuleContext, minimum: Decimal) -> RuleResult:
    """Premium-selling strategies want volatility rich. Both measures are always reported."""
    value, label = _iv_actuals(ctx)
    res = check_min("volatility.iv_floor", label, value, minimum, places=0, what="IV history")
    return _annotate_iv(ctx, res)


def rule_iv_ceiling(ctx: RuleContext, maximum: Decimal) -> RuleResult:
    """Premium-buying strategies want volatility cheap."""
    value, label = _iv_actuals(ctx)
    res = check_max("volatility.iv_ceiling", label, value, maximum, places=0, what="IV history")
    return _annotate_iv(ctx, res)


def rule_iv_preferred(ctx: RuleContext, minimum: Decimal) -> RuleResult:
    """CLAUDE.md says "above 40 preferred" for CSP and covered calls -- a preference, so advisory."""
    value, label = _iv_actuals(ctx)
    res = check_min(
        "volatility.iv_preferred",
        label,
        value,
        minimum,
        places=0,
        advisory=True,
        what="IV history",
    )
    return _annotate_iv(ctx, res)


def _annotate_iv(ctx: RuleContext, res: RuleResult) -> RuleResult:
    """Append the measure that is *not* gating, so the disagreement is always on screen.

    This is the AVGO case: raw IV 38.6% reads "rich", percentile 5 reads "cheap". Showing one
    without the other is how that trade gets taken by mistake.
    """
    raw = ctx.underlying.iv
    pctile = ctx.underlying.iv_percentile
    raw_txt = f"{raw * 100:.1f}%" if raw is not None else "UNKNOWN"
    pct_txt = num(pctile, 0) if pctile is not None else "UNKNOWN"
    res.message = f"{res.message}  [raw IV {raw_txt} | percentile {pct_txt}]"
    return res


def rule_iv_not_applicable(ctx: RuleContext) -> RuleResult:
    """For structures where volatility genuinely does not gate the decision.

    Synthetic_Spread_Notes.pdf, "Implied volatility effect": "IV has little or no impact in
    this strategy. The long Vega in the call option is essentially offset by the short Vega
    in the put option." The rule exists so the screen states that explicitly and still shows
    both IV measures, rather than leaving a conspicuous blank where every other strategy has
    a volatility gate.
    """
    res = result(
        "volatility.iv_neutral",
        "IV",
        True,
        ctx.underlying.iv_percentile,
        "not applicable",
        "IV does not gate this strategy -- long and short vega offset",
        advisory=True,
    )
    return _annotate_iv(ctx, res)


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


def rule_dte_range(ctx: RuleContext, low: int, high: int) -> RuleResult:
    return check_range("time.dte", "DTE", ctx.dte, low, high, places=0, what="expiry")


def rule_dte_min(ctx: RuleContext, minimum: int) -> RuleResult:
    return check_min("time.dte", "DTE", ctx.dte, minimum, places=0, what="expiry")


def rule_earnings_outside_expiry(ctx: RuleContext) -> RuleResult:
    """Earnings must not fall inside the expiry window.

    CLAUDE.md: "an unknown earnings date must be treated as a FAIL, not a pass. Never let a
    missing date silently clear the gate."
    """
    required = "not inside expiry"
    earnings = ctx.underlying.earnings_date
    expiry = ctx.expiry

    if expiry is None:
        return missing("time.earnings", "Earnings", required, "expiry")

    if earnings is None:
        if config.UNKNOWN_EARNINGS_IS_FAIL:
            return result(
                "time.earnings",
                "Earnings",
                None,
                None,
                required,
                "Earnings UNKNOWN -- verify manually (unknown date is not a pass)",
            )
        return result(
            "time.earnings", "Earnings", True, None, required, "Earnings unknown (gate disabled)"
        )

    inside = ctx.quote_date <= earnings <= expiry
    passed = not inside
    stale = " [STALE >90d]" if ctx.underlying.earnings_is_stale else ""
    return result(
        "time.earnings",
        "Earnings",
        passed,
        earnings,
        required,
        (
            f"Earnings {earnings.isoformat()} inside expiry{stale}"
            if inside
            else f"Earnings {earnings.isoformat()} outside expiry{stale}"
        ),
    )


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------


def rule_position_size(ctx: RuleContext, pct_nlv: Decimal, multiple: Decimal) -> RuleResult:
    """CLAUDE.md bull put: "1.5x net credit < 3% of net liquidation"."""
    credit = ctx.metrics.get("net_credit")
    required = f"<{float(pct_nlv) * 100:.0f}% of net liq"
    if credit is None:
        return missing("size.position", "Position size", required, "net credit")
    if credit <= 0:
        return result(
            "size.position",
            "Position size",
            False,
            credit,
            required,
            f"Net credit {money(credit)} -- no credit, so no stop can be sized",
        )

    exposure = credit * multiple * Decimal(config.CONTRACT_MULTIPLIER)
    budget = ctx.net_liquidation * pct_nlv
    passed = exposure < budget
    return result(
        "size.position",
        "Position size",
        passed,
        exposure,
        required,
        f"Stop exposure {money(exposure)} vs {money(budget)} budget "
        f"({float(pct_nlv) * 100:.0f}% of net liq)",
    )


def rule_premium_budget(ctx: RuleContext, pct_nlv: Decimal) -> RuleResult:
    """Long options and debit spreads: total premium capped as a share of net liquidation."""
    premium = ctx.metrics.get("total_premium") or ctx.metrics.get("max_loss")
    required = f"<={float(pct_nlv) * 100:.0f}% of net liq"
    if premium is None:
        return missing("size.premium", "Premium budget", required, "premium")

    budget = ctx.net_liquidation * pct_nlv
    passed = premium <= budget
    return result(
        "size.premium",
        "Premium budget",
        passed,
        premium,
        required,
        f"Total premium {money(premium)} vs {money(budget)} budget "
        f"({float(pct_nlv) * 100:.0f}% of net liq)",
    )


# ---------------------------------------------------------------------------
# Chart-derived rules -- advisory by default (user's decision)
# ---------------------------------------------------------------------------


def rule_market_regime(ctx: RuleContext, bullish: bool) -> RuleResult:
    """SPX regime: 50MA > 150MA for bull setups, < for bear. Proxied by SPY."""
    want = "50MA > 150MA" if bullish else "50MA < 150MA"
    if ctx.regime is None:
        return _advisory_missing("chart.regime", "Market regime", want, "index history")

    actual = ctx.regime.ma_bullish
    if actual is None:
        return _advisory_missing("chart.regime", "Market regime", want, "index moving averages")

    fast = ctx.regime.sma.get(config.REGIME_FAST_MA)
    slow = ctx.regime.sma.get(config.REGIME_SLOW_MA)
    passed = actual is bullish
    return result(
        "chart.regime",
        "Market regime",
        passed,
        actual,
        want,
        f"{config.REGIME_SYMBOL} 50MA {num(fast)} "
        f"{'>' if actual else '<'} 150MA {num(slow)} (need {want})",
        advisory=_chart_advisory(),
    )


def rule_ema_trend(ctx: RuleContext, bullish: bool) -> RuleResult:
    """20EMA vs 40EMA. The spec's example failure line comes from this rule."""
    want = "20 > 40" if bullish else "20 < 40"
    if ctx.technicals is None:
        return _advisory_missing("chart.ema", "EMA trend", want, "daily bars")

    actual = ctx.technicals.ema_bullish
    if actual is None:
        return _advisory_missing("chart.ema", "EMA trend", want, "enough daily bars")

    fast, slow = ctx.technicals.ema_fast, ctx.technicals.ema_slow
    passed = actual is bullish
    return result(
        "chart.ema",
        "EMA trend",
        passed,
        actual,
        want,
        f"20EMA {num(fast)} {'>' if actual else '<'} 40EMA {num(slow)} (need {want})",
        advisory=_chart_advisory(),
    )


def rule_ma_trend(ctx: RuleContext, bullish: bool) -> RuleResult:
    want = "50MA > 150MA" if bullish else "50MA < 150MA"
    if ctx.technicals is None:
        return _advisory_missing("chart.ma", "MA trend", want, "daily bars")

    actual = ctx.technicals.ma_bullish
    if actual is None:
        return _advisory_missing("chart.ma", "MA trend", want, "enough daily bars")

    fast = ctx.technicals.sma.get(config.REGIME_FAST_MA)
    slow = ctx.technicals.sma.get(config.REGIME_SLOW_MA)
    passed = actual is bullish
    return result(
        "chart.ma",
        "MA trend",
        passed,
        actual,
        want,
        f"50MA {num(fast)} {'>' if actual else '<'} 150MA {num(slow)} (need {want})",
        advisory=_chart_advisory(),
    )


def rule_setup_structure(ctx: RuleContext, bullish: bool) -> RuleResult:
    """The only genuinely subjective item in CLAUDE.md, reduced to measurements.

    "Retracement to support on an uptrend" is not something a screener can assert. What it can
    do is state how far price sits from the nearest multi-touch level, in ATR units, and how
    many times that level has held. Always advisory -- the judgement stays with the trader.
    """
    want = "near support" if bullish else "near resistance"
    if ctx.technicals is None or ctx.technicals.structure is None:
        return _advisory_missing("chart.structure", "Setup", want, "daily bars")

    st = ctx.technicals.structure
    level = st.nearest_support if bullish else st.nearest_resistance
    distance = st.distance_to_support_atr if bullish else st.distance_to_resistance_atr

    if level is None or distance is None:
        return result(
            "chart.structure",
            "Setup",
            False,
            None,
            want,
            f"No {('support' if bullish else 'resistance')} level with "
            f">={config.SR_MIN_TOUCHES} touches found",
            advisory=True,
        )

    passed = distance <= config.SR_NEAR_ATR
    return result(
        "chart.structure",
        "Setup",
        passed,
        distance,
        want,
        f"{'Support' if bullish else 'Resistance'} {num(level.price)} "
        f"({level.touches} touches), {distance:.2f} ATR away "
        f"(near = <={config.SR_NEAR_ATR} ATR)",
        advisory=True,
    )


def _advisory_missing(rule_id: str, label: str, required: str, what: str) -> RuleResult:
    """Missing chart data is advisory-missing, so it never silently fails a row."""
    return result(
        rule_id,
        label,
        None,
        None,
        required,
        f"{label} UNKNOWN -- {what} unavailable",
        advisory=_chart_advisory(),
    )


def trend_rules(bullish: bool) -> list:
    """The chart bundle cited by both credit-spread strategies."""
    return [
        lambda ctx: rule_market_regime(ctx, bullish),
        lambda ctx: rule_ema_trend(ctx, bullish),
        lambda ctx: rule_ma_trend(ctx, bullish),
        lambda ctx: rule_setup_structure(ctx, bullish),
    ]
