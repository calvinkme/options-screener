"""Rule tests: a passing case, a failing case and a boundary case for every gate.

CLAUDE.md: "a rule requiring <=4.0 must be tested at exactly 4.0".
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from backend.app import config
from backend.app.domain import Leg, Strategy
from backend.app.rules import (
    bear_call,
    bull_put,
    common,
    covered_call,
    csp,
    debit_spread,
    jade_lizard,
    long_option,
    synthetic,
)
from backend.tests.conftest import QUOTE_DATE, make_context, make_quote, make_underlying

# ---------------------------------------------------------------------------
# Universal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ask,expected",
    [("2.40", True), ("2.50", True), ("2.51", False)],  # boundary at exactly $0.50
)
def test_bid_ask_spread_boundary(ask, expected):
    leg = Leg(make_quote("95", bid="2.00", ask=ask), -100)
    res = common.rule_bid_ask_spread(make_context(legs=[leg]))
    assert res.passed is expected


def test_bid_ask_spread_missing_quote_fails_not_passes():
    leg = Leg(make_quote("95", bid=None, ask="2.20"), -100)
    res = common.rule_bid_ask_spread(make_context(legs=[leg]))
    assert res.passed is None
    assert res.is_blocking


@pytest.mark.parametrize("price,expected", [("25.00", True), ("20.00", True), ("19.99", False)])
def test_stock_price_boundary(price, expected):
    ctx = make_context(underlying=make_underlying(price=Decimal(price)))
    assert common.rule_stock_price(ctx).passed is expected


# ---------------------------------------------------------------------------
# Liquidity / sector
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cap,expected",
    [("25e9", True), ("20e9", True), ("19.9e9", False)],
)
def test_market_cap_boundary(cap, expected):
    ctx = make_context(underlying=make_underlying(market_cap=Decimal(cap)))
    assert common.rule_market_cap(ctx).passed is expected


@pytest.mark.parametrize("vol,expected", [(1_500_000, True), (1_000_000, True), (999_999, False)])
def test_avg_volume_boundary(vol, expected):
    ctx = make_context(underlying=make_underlying(avg_volume=vol))
    assert common.rule_avg_volume(ctx).passed is expected


@pytest.mark.parametrize(
    "sector,expected",
    [
        ("Technology", True),
        ("Biotechnology", False),
        ("Pharmaceuticals", False),
        ("Major Pharma", False),
    ],
)
def test_sector_exclusions(sector, expected):
    ctx = make_context(underlying=make_underlying(sector=sector))
    assert common.rule_sector(ctx).passed is expected


def test_unknown_sector_does_not_silently_pass():
    ctx = make_context(underlying=make_underlying(sector=None))
    res = common.rule_sector(ctx)
    assert res.passed is None
    assert res.is_blocking


# ---------------------------------------------------------------------------
# Volatility -- and the ambiguity it carries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pctile,expected", [("40", True), ("35", True), ("34.9", False)])
def test_iv_percentile_floor_boundary(pctile, expected):
    ctx = make_context(underlying=make_underlying(iv_percentile=Decimal(pctile)))
    assert common.rule_iv_floor(ctx, Decimal("35")).passed is expected


def test_iv_message_always_shows_both_measures():
    """The AVGO case: raw IV reads rich, percentile reads cheap. Both must be on screen."""
    ctx = make_context(underlying=make_underlying(iv=0.386, iv_percentile=Decimal("5")))
    res = common.rule_iv_floor(ctx, Decimal("35"))
    assert res.passed is False
    assert "38.6%" in res.message
    assert "percentile 5" in res.message


def test_iv_gate_can_switch_to_raw(monkeypatch):
    """With the flag on raw IV, the same AVGO inputs flip from FAIL to PASS."""
    monkeypatch.setattr(config, "IV_GATE_USES", "raw")
    ctx = make_context(underlying=make_underlying(iv=0.386, iv_percentile=Decimal("5")))
    assert common.rule_iv_floor(ctx, Decimal("35")).passed is True


def test_iv_ceiling_for_premium_buyers():
    """Low IV fails a seller and passes a buyer -- the inversion the ticker view relies on."""
    ctx = make_context(underlying=make_underlying(iv=0.386, iv_percentile=Decimal("5")))
    assert common.rule_iv_floor(ctx, Decimal("35")).passed is False
    assert common.rule_iv_ceiling(ctx, Decimal("40")).passed is True


def test_missing_iv_percentile_fails_the_gate():
    ctx = make_context(underlying=make_underlying(iv_percentile=None))
    res = common.rule_iv_floor(ctx, Decimal("35"))
    assert res.passed is None
    assert res.is_blocking


# ---------------------------------------------------------------------------
# Time / earnings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dte,expected", [(37, True), (30, True), (45, True), (29, False), (46, False)]
)
def test_dte_range_boundaries(dte, expected):
    assert common.rule_dte_range(make_context(dte=dte), 30, 45).passed is expected


def test_earnings_inside_expiry_fails():
    legs = [Leg(make_quote("95", dte=37), -100)]
    ctx = make_context(
        legs=legs, underlying=make_underlying(earnings_date=QUOTE_DATE + timedelta(days=10))
    )
    res = common.rule_earnings_outside_expiry(ctx)
    assert res.passed is False
    assert "inside expiry" in res.message


def test_earnings_after_expiry_passes():
    legs = [Leg(make_quote("95", dte=37), -100)]
    ctx = make_context(
        legs=legs, underlying=make_underlying(earnings_date=QUOTE_DATE + timedelta(days=60))
    )
    assert common.rule_earnings_outside_expiry(ctx).passed is True


def test_earnings_exactly_on_expiry_is_inside():
    """Boundary: expiry day itself counts as inside. Assignment risk is real that morning."""
    legs = [Leg(make_quote("95", dte=37), -100)]
    ctx = make_context(
        legs=legs, underlying=make_underlying(earnings_date=QUOTE_DATE + timedelta(days=37))
    )
    assert common.rule_earnings_outside_expiry(ctx).passed is False


def test_unknown_earnings_is_a_failure_never_a_pass():
    """CLAUDE.md is explicit: an unknown earnings date must be treated as a FAIL."""
    legs = [Leg(make_quote("95", dte=37), -100)]
    ctx = make_context(legs=legs, underlying=make_underlying(earnings_date=None))
    res = common.rule_earnings_outside_expiry(ctx)
    assert res.passed is None
    assert res.is_blocking
    assert "verify manually" in res.message


# ---------------------------------------------------------------------------
# Bull put
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "delta,expected",
    [(-0.22, True), (-0.20, True), (-0.25, True), (-0.19, False), (-0.26, False)],
)
def test_bull_put_short_delta_boundaries(delta, expected):
    ctx = make_context(metrics={"short_delta_per_share": Decimal(str(delta))})
    assert bull_put.rule_short_delta(ctx).passed is expected


@pytest.mark.parametrize("ratio,expected", [("3.5", True), ("4.0", True), ("4.01", False)])
def test_bull_put_max_loss_boundary_at_exactly_four(ratio, expected):
    """The spec's stated boundary case: <=4.0 must pass at exactly 4.0."""
    ctx = make_context(
        metrics={
            "loss_ratio": Decimal(ratio),
            "max_loss": Decimal("400"),
            "max_profit": Decimal("100"),
        }
    )
    assert bull_put.rule_max_loss(ctx).passed is expected


@pytest.mark.parametrize("offset,expected", [(1, True), (2, True), (0, False), (3, False)])
def test_bull_put_strike_offset_boundaries(offset, expected):
    ctx = make_context(metrics={"strike_offset": offset})
    assert bull_put.rule_long_strike_offset(ctx).passed is expected


@pytest.mark.parametrize(
    "credit,expected",
    [
        ("1.30", True),  # 1.5 x 1.30 x 100 = $195 stop vs $3,000 budget
        ("19.99", True),  # $2,998.50 -- just inside
        ("20.00", False),  # exactly $3,000: the spec says "<", so equality fails
        ("21.00", False),  # $3,150 -- over budget
    ],
)
def test_bull_put_position_size_boundary(credit, expected):
    """1.5x net credit must be strictly under 3% of net liquidation."""
    ctx = make_context(metrics={"net_credit": Decimal(credit)}, net_liquidation=Decimal("100000"))
    assert bull_put.rule_size(ctx).passed is expected


# ---------------------------------------------------------------------------
# Bear call -- the mirror
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "delta,expected",
    [(0.22, True), (0.20, True), (0.25, True), (0.19, False), (0.26, False)],
)
def test_bear_call_short_delta_boundaries(delta, expected):
    ctx = make_context(metrics={"short_delta_per_share": Decimal(str(delta))})
    assert bear_call.rule_short_delta(ctx).passed is expected


def test_bull_put_and_bear_call_delta_bands_are_opposite_signs():
    assert config.BULL_PUT["short_delta"][1] < 0
    assert config.BEAR_CALL["short_delta"][0] > 0


# ---------------------------------------------------------------------------
# CSP
# ---------------------------------------------------------------------------


def test_csp_cash_gate_blocks_when_account_too_small():
    ctx = make_context(metrics={"cash_secured": Decimal("39000")}, cash_available=Decimal("10000"))
    assert csp.rule_cash_available(ctx).passed is False


def test_csp_cash_gate_passes_at_exact_amount():
    ctx = make_context(metrics={"cash_secured": Decimal("15000")}, cash_available=Decimal("15000"))
    assert csp.rule_cash_available(ctx).passed is True


@pytest.mark.parametrize("ret,expected", [("0.03", True), ("0.02", True), ("0.0199", False)])
def test_csp_monthly_return_boundary(ret, expected):
    ctx = make_context(metrics={"monthly_return": Decimal(ret)})
    assert csp.rule_return(ctx).passed is expected


def test_csp_wheel_mode_uses_return_of_strike(monkeypatch):
    monkeypatch.setitem(config.CASH_SECURED_PUT, "mode", "wheel")
    ctx = make_context(metrics={"return_of_strike": Decimal("0.025")})
    res = csp.rule_return(ctx)
    assert res.passed is True
    assert res.label == "Return on strike"


def test_csp_wheel_mode_widens_the_delta_band(monkeypatch):
    ctx = make_context(metrics={"short_delta_per_share": Decimal("-0.21")})
    monkeypatch.setitem(config.CASH_SECURED_PUT, "mode", "standalone")
    assert csp.rule_delta(ctx).passed is False  # standalone wants -0.25..-0.35
    monkeypatch.setitem(config.CASH_SECURED_PUT, "mode", "wheel")
    assert csp.rule_delta(ctx).passed is True


# ---------------------------------------------------------------------------
# Covered call
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shares,expected", [(200, True), (100, True), (99, False), (0, False)])
def test_covered_call_requires_shares(shares, expected):
    ctx = make_context(underlying=make_underlying(shares_owned=shares))
    assert covered_call.rule_shares_owned(ctx).passed is expected


@pytest.mark.parametrize(
    "delta,expected", [(0.42, True), (0.40, True), (0.45, True), (0.39, False), (0.46, False)]
)
def test_covered_call_delta_boundaries(delta, expected):
    ctx = make_context(metrics={"short_delta_per_share": Decimal(str(delta))})
    assert covered_call.rule_delta(ctx).passed is expected


# ---------------------------------------------------------------------------
# Long option
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dte,expected", [(90, True), (60, True), (59, False)])
def test_long_option_min_dte_boundary(dte, expected):
    assert long_option.rule_dte(make_context(dte=dte)).passed is expected


@pytest.mark.parametrize("itm,expected", [(0, True), (1, True), (2, False), (-1, False)])
def test_long_option_moneyness(itm, expected):
    ctx = make_context(metrics={"strikes_itm": itm})
    assert long_option.rule_moneyness(ctx).passed is expected


@pytest.mark.parametrize("premium,expected", [("1500", True), ("2000", True), ("2001", False)])
def test_long_option_premium_budget_boundary(premium, expected):
    ctx = make_context(
        metrics={"total_premium": Decimal(premium)}, net_liquidation=Decimal("100000")
    )
    assert long_option.rule_premium(ctx).passed is expected


# ---------------------------------------------------------------------------
# Debit spread
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rr,expected", [("2.5", True), ("2.0", True), ("1.99", False)])
def test_debit_spread_risk_reward_boundary_at_exactly_two(rr, expected):
    ctx = make_context(
        strategy=Strategy.BULL_CALL,
        metrics={
            "risk_reward": Decimal(rr),
            "max_profit": Decimal("700"),
            "max_loss": Decimal("300"),
        },
    )
    assert debit_spread.rule_risk_reward(ctx).passed is expected


@pytest.mark.parametrize("width,expected", [("10", True), ("5", True), ("4.99", False)])
def test_bull_call_minimum_width(width, expected):
    ctx = make_context(strategy=Strategy.BULL_CALL, metrics={"width": Decimal(width)})
    assert debit_spread.rule_width(ctx).passed is expected


def test_bear_put_has_no_width_minimum():
    """The $5 floor is scoped to bull calls in CLAUDE.md, so the put side must not inherit it."""
    ctx = make_context(strategy=Strategy.BEAR_PUT, metrics={"width": Decimal("2.50")})
    assert debit_spread.rule_width(ctx).passed is True


def test_debit_spread_short_strike_placement_is_advisory():
    ctx = make_context(strategy=Strategy.BULL_CALL, metrics={"short_strike_sd": Decimal("0.4")})
    res = debit_spread.rule_short_strike_placement(ctx)
    assert res.passed is False
    assert res.advisory is True
    assert res.is_blocking is False  # advisory never blocks


# ---------------------------------------------------------------------------
# Chart rules are advisory by default -- the user's explicit decision
# ---------------------------------------------------------------------------


def test_chart_rules_do_not_block_by_default():
    ctx = make_context(technicals=None, regime=None)
    for rule in common.trend_rules(bullish=True):
        res = rule(ctx)
        assert res.advisory is True
        assert res.is_blocking is False


def test_chart_rules_can_be_promoted_to_hard_gates(monkeypatch):
    monkeypatch.setattr(config, "CHART_RULES_ARE_HARD_GATES", True)
    ctx = make_context(technicals=None, regime=None)
    res = common.rule_ema_trend(ctx, bullish=True)
    assert res.advisory is False
    assert res.is_blocking is True


# ---------------------------------------------------------------------------
# Jade lizard -- Jade_Lizard_Notes.pdf
# ---------------------------------------------------------------------------


def lizard_context(**metrics):
    """A jade lizard whose every gate passes, so one override isolates one rule."""
    base = {
        "credit_to_call_width": Decimal("1.07"),
        "net_credit": Decimal("10.675"),
        "call_width": Decimal("10"),
        "upside_risk": Decimal("0"),
        "short_call_delta": Decimal("0.45"),
        "short_put_delta": Decimal("-0.22"),
        "roi": Decimal("0.127"),
        "buying_power": Decimal("8377"),
        "breakeven": Decimal("219.33"),
        "breakeven_sd": Decimal("1.4"),
        "prob_win_approx": Decimal("0.78"),
        "cash_secured": Decimal("23000"),
    }
    base.update(metrics)
    return make_context(strategy=Strategy.JADE_LIZARD, metrics=base)


@pytest.mark.parametrize(
    "ratio,expected",
    # "Entry credit is equal or greater than the Bear Call width" -- 1.0 exactly passes.
    [("1.07", True), ("1.00", True), ("0.99", False)],
)
def test_jade_lizard_no_upside_risk_boundary(ratio, expected):
    ctx = lizard_context(credit_to_call_width=Decimal(ratio))
    assert jade_lizard.rule_no_upside_risk(ctx).passed is expected


def test_jade_lizard_upside_risk_failure_names_the_shortfall():
    ctx = lizard_context(credit_to_call_width=Decimal("0.80"), upside_risk=Decimal("200"))
    res = jade_lizard.rule_no_upside_risk(ctx)
    assert res.passed is False
    assert "Upside risk" in res.message and "$200.00" in res.message


def test_jade_lizard_upside_rule_is_never_advisory():
    """The rule that defines the structure cannot be downgraded to a warning."""
    assert jade_lizard.rule_no_upside_risk(lizard_context()).advisory is False


def test_jade_lizard_missing_credit_fails_the_upside_gate():
    res = jade_lizard.rule_no_upside_risk(lizard_context(credit_to_call_width=None))
    assert res.passed is None
    assert res.is_blocking


@pytest.mark.parametrize(
    "delta,expected",  # "Short Call at ~45 delta"
    [("0.45", True), ("0.40", True), ("0.50", True), ("0.51", False), ("0.39", False)],
)
def test_jade_lizard_short_call_delta_boundaries(delta, expected):
    ctx = lizard_context(short_call_delta=Decimal(delta))
    assert jade_lizard.rule_short_call_delta(ctx).passed is expected


@pytest.mark.parametrize(
    "delta,expected",
    # "~25 delta (or lower is safer)": -0.25 is a ceiling, so -0.12 passes and -0.30 does not.
    [("-0.25", True), ("-0.12", True), ("-0.26", False), ("-0.30", False)],
)
def test_jade_lizard_short_put_delta_is_a_ceiling_not_a_band(delta, expected):
    ctx = lizard_context(short_put_delta=Decimal(delta))
    assert jade_lizard.rule_short_put_delta(ctx).passed is expected


@pytest.mark.parametrize(
    "roi,expected",
    # "Max ROI is greater than 10%" -- strictly greater, so exactly 10% fails.
    [("0.127", True), ("0.1001", True), ("0.10", False), ("0.09", False)],
)
def test_jade_lizard_roi_boundary_is_strictly_greater(roi, expected):
    ctx = lizard_context(roi=Decimal(roi))
    assert jade_lizard.rule_roi(ctx).passed is expected


def test_jade_lizard_roi_message_flags_the_buying_power_as_an_estimate():
    """The broker's real margin cannot be derived from the chain; the row must not pretend."""
    assert "estimated" in jade_lizard.rule_roi(lizard_context()).message


@pytest.mark.parametrize(
    "sd,expected",  # "Break-even on the downside is at the 1-SD point (or lower)"
    [("1.40", True), ("1.00", True), ("0.99", False)],
)
def test_jade_lizard_breakeven_one_sd_boundary(sd, expected):
    ctx = lizard_context(breakeven_sd=Decimal(sd))
    assert jade_lizard.rule_breakeven_at_one_sd(ctx).passed is expected


def test_jade_lizard_missing_expected_move_fails_the_breakeven_gate():
    res = jade_lizard.rule_breakeven_at_one_sd(lizard_context(breakeven_sd=None))
    assert res.passed is None
    assert res.is_blocking


@pytest.mark.parametrize(
    "prob,expected",  # ">70% probability of winning"
    [("0.78", True), ("0.70", True), ("0.69", False)],
)
def test_jade_lizard_win_probability_boundary(prob, expected):
    ctx = lizard_context(prob_win_approx=Decimal(prob))
    assert jade_lizard.rule_win_probability(ctx).passed is expected


def test_jade_lizard_win_probability_is_advisory_because_it_is_approximated():
    res = jade_lizard.rule_win_probability(lizard_context(prob_win_approx=Decimal("0.40")))
    assert res.passed is False
    assert res.advisory is True
    assert res.is_blocking is False
    assert "approximated" in res.message


def test_jade_lizard_assignment_capacity_blocks_when_the_account_is_too_small():
    ctx = lizard_context()
    ctx.cash_available = Decimal("10000")  # needs 230 x 100
    assert jade_lizard.rule_assignment_capacity(ctx).passed is False


def test_jade_lizard_assignment_capacity_passes_at_the_exact_amount():
    ctx = lizard_context()
    ctx.cash_available = Decimal("23000")
    assert jade_lizard.rule_assignment_capacity(ctx).passed is True


def test_jade_lizard_allows_earnings_inside_the_expiry():
    """The one documented exception: "JL can be used during the earnings period"."""
    underlying = make_underlying(earnings_date=QUOTE_DATE + timedelta(days=10))
    ctx = make_context(strategy=Strategy.JADE_LIZARD, metrics={}, underlying=underlying, dte=37)
    res = jade_lizard.rule_earnings_exception(ctx)
    assert res.passed is True
    assert res.is_blocking is False
    assert "inside expiry" in res.message
    assert "gap down" in res.message  # the exception is one-sided and says so


def test_jade_lizard_earnings_exception_does_not_leak_to_the_credit_spreads():
    """Same underlying, same expiry -- the bull put must still fail on it."""
    underlying = make_underlying(earnings_date=QUOTE_DATE + timedelta(days=10))
    ctx = make_context(metrics={}, underlying=underlying, dte=37)
    assert common.rule_earnings_outside_expiry(ctx).passed is False


def test_jade_lizard_unknown_earnings_is_still_shown_not_silently_cleared():
    underlying = make_underlying(earnings_date=None)
    ctx = make_context(strategy=Strategy.JADE_LIZARD, metrics={}, underlying=underlying, dte=37)
    res = jade_lizard.rule_earnings_exception(ctx)
    assert res.passed is None
    assert "UNKNOWN" in res.message


def test_jade_lizard_iv_preference_never_blocks():
    """ "Ideally, enter when IV is high -- but it's not a must"."""
    underlying = make_underlying(iv_percentile=Decimal("5"))
    ctx = make_context(strategy=Strategy.JADE_LIZARD, metrics={}, underlying=underlying, dte=37)
    res = jade_lizard.rule_iv(ctx)
    assert res.passed is False
    assert res.is_blocking is False


@pytest.mark.parametrize(
    "dte,expected", [(35, True), (28, True), (42, True), (27, False), (43, False)]
)
def test_jade_lizard_dte_boundaries(dte, expected):
    """ "DTE: 4-6 weeks"."""
    ctx = make_context(strategy=Strategy.JADE_LIZARD, metrics={}, dte=dte)
    assert jade_lizard.rule_dte(ctx).passed is expected


# ---------------------------------------------------------------------------
# Synthetic spread -- Synthetic_Spread_Notes.pdf
# ---------------------------------------------------------------------------


def synthetic_context(strategy=Strategy.SYNTHETIC_LONG, **metrics):
    base = {
        "long_strike": Decimal("100"),
        "short_strike": Decimal("100"),
        "strike_gap": Decimal("0"),
        "net_debit": Decimal("0.50"),
        "net_premium_pct_of_spot": Decimal("0.005"),
        "assignment_obligation": Decimal("10000"),
        "buying_power": Decimal("2500"),
        "leverage": Decimal("4"),
    }
    base.update(metrics)
    return make_context(strategy=strategy, metrics=base, dte=59)


@pytest.mark.parametrize(
    "strike,expected",  # "Strike price: ATM (or near ATM)" -- tolerance is 3% of spot
    [("100", True), ("103", True), ("103.01", False), ("97", True), ("96.9", False)],
)
def test_synthetic_atm_strike_boundary(strike, expected):
    ctx = synthetic_context(long_strike=Decimal(strike), short_strike=Decimal(strike))
    assert synthetic.rule_strike_placement(ctx).passed is expected


def test_synthetic_atm_mode_rejects_split_strikes_and_says_how_to_screen_them():
    ctx = synthetic_context(long_strike=Decimal("105"), short_strike=Decimal("95"))
    res = synthetic.rule_strike_placement(ctx)
    assert res.passed is False
    assert "split" in res.message


def test_synthetic_split_mode_accepts_legs_that_straddle_spot(monkeypatch):
    monkeypatch.setitem(config.SYNTHETIC, "mode", "split")
    ctx = synthetic_context(
        long_strike=Decimal("105"), short_strike=Decimal("95"), strike_gap=Decimal("10")
    )
    res = synthetic.rule_strike_placement(ctx)
    assert res.passed is True
    assert "Flat zone" in res.message


def test_synthetic_split_mode_rejects_legs_entirely_on_one_side(monkeypatch):
    monkeypatch.setitem(config.SYNTHETIC, "mode", "split")
    ctx = synthetic_context(
        long_strike=Decimal("115"), short_strike=Decimal("105"), strike_gap=Decimal("10")
    )
    assert synthetic.rule_strike_placement(ctx).passed is False


@pytest.mark.parametrize(
    "ratio,expected",  # "Net premium is zero (or near zero)" -- capped at 1% of spot
    [("0.005", True), ("0.01", True), ("0.0101", False)],
)
def test_synthetic_net_premium_boundary(ratio, expected):
    ctx = synthetic_context(net_premium_pct_of_spot=Decimal(ratio))
    assert synthetic.rule_net_premium(ctx).passed is expected


def test_synthetic_net_credit_counts_as_near_zero_too():
    ctx = synthetic_context(net_debit=Decimal("-0.40"), net_premium_pct_of_spot=Decimal("0.004"))
    res = synthetic.rule_net_premium(ctx)
    assert res.passed is True
    assert "credit" in res.message


def test_synthetic_missing_quote_fails_the_premium_gate():
    ctx = synthetic_context(net_premium_pct_of_spot=None)
    res = synthetic.rule_net_premium(ctx)
    assert res.passed is None
    assert res.is_blocking


@pytest.mark.parametrize(
    "dte,expected", [(59, True), (30, True), (90, True), (29, False), (91, False)]
)
def test_synthetic_dte_boundaries(dte, expected):
    """ "DTE: at your time frame -- e.g. 1-3 months"."""
    ctx = synthetic_context()
    ctx.dte = dte
    assert synthetic.rule_dte(ctx).passed is expected


def test_synthetic_long_requires_cash_to_take_assignment():
    """ "Treat the short put leg as a genuine obligation to buy 100 shares"."""
    ctx = synthetic_context()
    ctx.cash_available = Decimal("9999")
    assert synthetic.rule_assignment_capacity(ctx).passed is False


def test_synthetic_long_assignment_passes_at_the_exact_amount():
    ctx = synthetic_context()
    ctx.cash_available = Decimal("10000")
    assert synthetic.rule_assignment_capacity(ctx).passed is True


def test_synthetic_short_has_no_assignment_cash_but_says_the_loss_is_unbounded():
    ctx = synthetic_context(strategy=Strategy.SYNTHETIC_SHORT)
    ctx.cash_available = Decimal("0")
    res = synthetic.rule_assignment_capacity(ctx)
    assert res.is_blocking is False
    assert "unbounded" in res.message


@pytest.mark.parametrize(
    "capital,expected",  # 10% of a $100,000 net liq
    [("2500", True), ("10000", True), ("10001", False)],
)
def test_synthetic_capital_budget_boundary(capital, expected):
    ctx = synthetic_context(buying_power=Decimal(capital))
    ctx.net_liquidation = Decimal("100000")
    assert synthetic.rule_capital_budget(ctx).passed is expected


def test_synthetic_capital_budget_says_it_is_not_a_course_rule():
    """An added gate must never read as if it came from the source material."""
    ctx = synthetic_context()
    ctx.net_liquidation = Decimal("100000")
    assert "not a course rule" in synthetic.rule_capital_budget(ctx).message


def test_synthetic_capital_budget_can_be_switched_off(monkeypatch):
    monkeypatch.setitem(config.SYNTHETIC, "max_capital_pct_nlv", None)
    ctx = synthetic_context(buying_power=Decimal("999999"))
    res = synthetic.rule_capital_budget(ctx)
    assert res.is_blocking is False


def test_synthetic_iv_never_gates_but_always_reports_both_measures():
    """The notes' hard rule: long and short vega cancel, so IV is neutral here."""
    underlying = make_underlying(iv=0.386, iv_percentile=Decimal("5"))
    ctx = make_context(strategy=Strategy.SYNTHETIC_LONG, metrics={}, underlying=underlying)
    res = common.rule_iv_not_applicable(ctx)
    assert res.is_blocking is False
    assert "38.6%" in res.message and "percentile 5" in res.message


def test_synthetic_earnings_gate_is_on_by_default_unlike_the_jade_lizard():
    """The notes are silent here, so the app's conservative default applies."""
    underlying = make_underlying(earnings_date=QUOTE_DATE + timedelta(days=10))
    ctx = make_context(strategy=Strategy.SYNTHETIC_LONG, metrics={}, underlying=underlying, dte=37)
    assert synthetic.rule_earnings(ctx).passed is False


def test_synthetic_earnings_gate_can_be_switched_off(monkeypatch):
    monkeypatch.setitem(config.SYNTHETIC, "earnings_gate", False)
    underlying = make_underlying(earnings_date=QUOTE_DATE + timedelta(days=10))
    ctx = make_context(strategy=Strategy.SYNTHETIC_LONG, metrics={}, underlying=underlying, dte=37)
    res = synthetic.rule_earnings(ctx)
    assert res.is_blocking is False
    assert "gate disabled" in res.message


@pytest.mark.parametrize("module", [bull_put, bear_call])
@pytest.mark.parametrize(
    "credit,ratio,expected",
    [
        ("-0.359", "-14.93", False),  # SPGI 420/425C on frozen data: debit, ratio negative
        ("0", "0", False),  # boundary: no credit at all
        ("0.01", "4.0", True),  # smallest real credit at the 4x ceiling
    ],
)
def test_max_loss_requires_a_positive_credit(module, credit, ratio, expected):
    ctx = make_context(
        metrics={
            "net_credit": Decimal(credit),
            "loss_ratio": Decimal(ratio),
            "max_loss": Decimal("535.90"),
            "max_profit": Decimal(credit) * 100,
        }
    )
    assert module.rule_max_loss(ctx).passed is expected


@pytest.mark.parametrize("credit,expected", [("-0.359", False), ("0", False), ("0.01", True)])
def test_position_size_requires_a_positive_credit(credit, expected):
    ctx = make_context(metrics={"net_credit": Decimal(credit)})
    assert common.rule_position_size(ctx, Decimal("0.03"), Decimal("1.5")).passed is expected
