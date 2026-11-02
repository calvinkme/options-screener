"""Formula tests, anchored to real cells in the supplied workbooks.

The CSP block asserts against CSP Template.xlsx row 6 (META .META220902P150) so the maths is
provably identical to the sheet already in use, not merely plausible.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.app import config
from backend.app.domain import Right
from backend.app.metrics import formulas


def approx(value, expected, tol=1e-6):
    assert value is not None, "expected a number, got None"
    assert abs(float(value) - expected) < tol, f"{float(value)} != {expected}"


# ---------------------------------------------------------------------------
# CSP -- parity with CSP Template.xlsx row 6
#   B6 .META220902P150 | strike 150 | qty 1 | mark 4.375 | DTE 33 | last 159.10
#   G6 15000 | I6 437.5 | K6 0.45 | M6 437.05 | N6 0.0291367 | O6 0.3222692
#   P6 0.1870933 (against a manual B.Power of 2336) | U6 145.625 | W6 -0.0846952
# ---------------------------------------------------------------------------


@pytest.fixture
def workbook_dte(monkeypatch):
    """The workbook annualises on a plain DTE; CLAUDE.md adds 1. Pin the workbook's reading."""
    monkeypatch.setattr(config, "DTE_ANNUALISE_PLUS_ONE", False)


def test_csp_matches_workbook_row_6(workbook_dte):
    m = formulas.csp_metrics(
        stock_price=Decimal("159.10"),
        option_mark=Decimal("4.375"),
        strike=Decimal("150"),
        dte=33,
        contracts=1,
        buying_power_override=Decimal("2336"),
    )

    approx(m["cash_secured"], 15000.0)  # G6
    approx(m["premium_total"], 437.5)  # I6
    approx(m["commission"], 0.45)  # K6
    approx(m["max_profit"], 437.05)  # M6
    approx(m["roi_premium"], 0.0291366666, tol=1e-9)  # N6
    approx(m["roi_annual_cash"], 0.3222691919, tol=1e-9)  # O6
    approx(m["roi_bp"], 0.1870933219, tol=1e-9)  # P6
    approx(m["roi_annual_margin"], 2.0693655303, tol=1e-9)  # Q6
    approx(m["assigned_price"], 150.0)  # S6
    approx(m["collected_premium"], 4.375)  # T6
    approx(m["assigned_net_cost"], 145.625)  # U6
    approx(m["discount_vs_price"], -0.0846951603, tol=1e-9)  # W6


def test_csp_cash_basis_uses_strike_not_stock_price():
    """CLAUDE.md says stock_price * 100; the workbook uses strike * 100. The workbook wins.

    This is a real difference: at a 150 strike on a 159.10 stock it is $900 of capital,
    which moves ROI by ~6%.
    """
    kwargs = dict(
        stock_price=Decimal("159.10"), option_mark=Decimal("4.375"), strike=Decimal("150"), dte=33
    )
    assert config.CSP_CASH_BASIS == "strike"
    approx(formulas.csp_metrics(**kwargs)["cash_secured"], 15000.0)


def test_csp_cash_basis_spec_literal(monkeypatch):
    monkeypatch.setattr(config, "CSP_CASH_BASIS", "stock_price")
    m = formulas.csp_metrics(
        stock_price=Decimal("159.10"), option_mark=Decimal("4.375"), strike=Decimal("150"), dte=33
    )
    approx(m["cash_secured"], 15910.0)


def test_dte_plus_one_changes_annualisation(monkeypatch):
    """The +1 ambiguity is real and worth ~3% on a 33-day trade -- so it is surfaced, not hidden."""
    kwargs = dict(
        stock_price=Decimal("159.10"), option_mark=Decimal("4.375"), strike=Decimal("150"), dte=33
    )

    monkeypatch.setattr(config, "DTE_ANNUALISE_PLUS_ONE", False)
    plain = formulas.csp_metrics(**kwargs)["roi_annual_cash"]

    monkeypatch.setattr(config, "DTE_ANNUALISE_PLUS_ONE", True)
    plus_one = formulas.csp_metrics(**kwargs)["roi_annual_cash"]

    approx(plain, 0.3222691919, tol=1e-9)  # 0.0291367 / 33 * 365, the workbook's O6
    approx(plus_one, 0.3127906863, tol=1e-9)  # 0.0291367 / 34 * 365, CLAUDE.md's reading
    assert plus_one < plain


# ---------------------------------------------------------------------------
# Covered call -- CLAUDE.md formulas, transcribed
# ---------------------------------------------------------------------------


def test_covered_call_formulas(workbook_dte):
    m = formulas.covered_call_metrics(
        stock_price=Decimal("100"),
        option_mark=Decimal("2.50"),
        strike=Decimal("105"),
        dte=30,
        dividend_yield=Decimal("0.02"),
    )

    # premium_pct = 2.50 / (100 - 2.50)
    approx(m["premium_pct"], 2.50 / 97.50, tol=1e-9)
    # premium_annual = premium_pct / 30 * 365
    approx(m["premium_annual"], (2.50 / 97.50) / 30 * 365, tol=1e-9)
    # dividend weighted at 70%
    approx(m["weighted_dividend"], 0.014, tol=1e-9)
    approx(m["prem_plus_div"], (2.50 / 97.50) / 30 * 365 + 0.014, tol=1e-9)
    approx(m["diff_to_strike"], 5.0)
    approx(m["diff_pct"], 0.05)
    approx(m["diff_annual"], 0.05 / 30 * 365, tol=1e-9)
    approx(
        m["max_roi_annual"],
        (2.50 / 97.50) / 30 * 365 + 0.014 + 0.05 / 30 * 365,
        tol=1e-9,
    )


def test_covered_call_dividend_weighted_at_70_percent():
    m = formulas.covered_call_metrics(
        stock_price=Decimal("100"),
        option_mark=Decimal("2.00"),
        strike=Decimal("105"),
        dte=30,
        dividend_yield=Decimal("0.10"),
    )
    approx(m["weighted_dividend"], 0.07)


def test_covered_call_missing_price_propagates_none():
    m = formulas.covered_call_metrics(
        stock_price=None, option_mark=Decimal("2.00"), strike=Decimal("105"), dte=30
    )
    assert m["premium_pct"] is None
    assert m["max_roi_annual"] is None


# ---------------------------------------------------------------------------
# Vertical credit spread
# ---------------------------------------------------------------------------


def test_credit_spread_formulas(workbook_dte):
    m = formulas.credit_spread_metrics(
        short_premium=Decimal("2.20"),
        long_premium=Decimal("0.90"),
        short_strike=Decimal("490"),
        long_strike=Decimal("480"),
        right=Right.PUT,
        dte=37,
        contracts=1,
    )

    approx(m["net_credit"], 1.30)
    approx(m["width"], 10.0)
    # max_profit = credit * 100 - commission(2 legs)
    approx(m["max_profit"], 130.0 - 0.90)
    # max_loss = (width - credit) * 100 + commission
    approx(m["max_loss"], 870.0 + 0.90)
    # bull put breakeven = short strike - credit
    approx(m["breakeven"], 488.70)
    approx(m["stop_loss_amount"], 195.0)
    approx(m["loss_ratio"], 870.90 / 129.10, tol=1e-9)


def test_bear_call_breakeven_is_short_strike_plus_credit():
    m = formulas.credit_spread_metrics(
        short_premium=Decimal("2.20"),
        long_premium=Decimal("0.90"),
        short_strike=Decimal("490"),
        long_strike=Decimal("500"),
        right=Right.CALL,
        dte=37,
    )
    approx(m["breakeven"], 491.30)


def test_credit_spread_missing_leg_propagates_none():
    m = formulas.credit_spread_metrics(
        short_premium=Decimal("2.20"),
        long_premium=None,
        short_strike=Decimal("490"),
        long_strike=Decimal("480"),
        right=Right.PUT,
        dte=37,
    )
    assert m["net_credit"] is None
    assert m["max_loss"] is None
    assert m["loss_ratio"] is None


# ---------------------------------------------------------------------------
# Vertical debit spread
# ---------------------------------------------------------------------------


def test_debit_spread_formulas():
    m = formulas.debit_spread_metrics(
        long_premium=Decimal("6.00"),
        short_premium=Decimal("3.00"),
        long_strike=Decimal("100"),
        short_strike=Decimal("110"),
        right=Right.CALL,
        dte=30,
    )

    approx(m["net_debit"], 3.00)
    approx(m["max_loss"], 300.0 + 0.90)
    approx(m["max_profit"], 700.0 - 0.90)
    approx(m["breakeven"], 103.0)
    approx(m["risk_reward"], 699.10 / 300.90, tol=1e-9)


def test_bear_put_breakeven_is_long_strike_minus_debit():
    m = formulas.debit_spread_metrics(
        long_premium=Decimal("6.00"),
        short_premium=Decimal("3.00"),
        long_strike=Decimal("100"),
        short_strike=Decimal("90"),
        right=Right.PUT,
        dte=30,
    )
    approx(m["breakeven"], 97.0)


# ---------------------------------------------------------------------------
# Long option
# ---------------------------------------------------------------------------


def test_long_call_breakeven_and_premium():
    m = formulas.long_option_metrics(
        premium=Decimal("5.50"), strike=Decimal("100"), right=Right.CALL, dte=65
    )
    approx(m["breakeven"], 105.50)
    approx(m["total_premium"], 550.0 + 0.45)
    assert m["max_profit"] is None  # unbounded, and not faked


# ---------------------------------------------------------------------------
# Division / annualisation guards
# ---------------------------------------------------------------------------


def test_div_refuses_zero_denominator():
    assert formulas.div(Decimal("1"), Decimal("0")) is None
    assert formulas.div(None, Decimal("1")) is None
    assert formulas.div(Decimal("1"), None) is None


def test_annualise_guards_zero_dte():
    assert formulas.annualise(Decimal("0.03"), 0) is None
    assert formulas.annualise(None, 30) is None


def test_commission_can_be_disabled(monkeypatch):
    monkeypatch.setattr(config, "DEDUCT_COMMISSION_FROM_ROI", False)
    assert formulas.commission_for(3, legs=2) == Decimal(0)


# ---------------------------------------------------------------------------
# Jade lizard -- parity with the worked example in Jade_Lizard_Notes.pdf
#   TSLA at $254.85: sell 230P @ 6.975, sell 265C @ 13.225, buy 275C @ 9.525
#   Net credit $1,067 | call width $10.00 | breakeven $219.33
# ---------------------------------------------------------------------------


@pytest.fixture
def no_commission(monkeypatch):
    """The notes quote figures before commission; pin that so the comparison is like for like."""
    monkeypatch.setattr(config, "DEDUCT_COMMISSION_FROM_ROI", False)


def tsla_lizard(dte=35):
    return formulas.jade_lizard_metrics(
        short_put_premium=Decimal("6.975"),
        short_call_premium=Decimal("13.225"),
        long_call_premium=Decimal("9.525"),
        short_put_strike=Decimal("230"),
        short_call_strike=Decimal("265"),
        long_call_strike=Decimal("275"),
        dte=dte,
    )


def test_jade_lizard_matches_the_worked_example(no_commission):
    m = tsla_lizard()
    approx(m["net_credit"], 10.675)
    approx(m["max_profit"], 1067.5)  # "Max profit = Entry credit = $10.67 x 100 = $1,067"
    approx(m["call_width"], 10.0)  # "Call width = 275 - 265 = $10.00"
    approx(m["breakeven"], 219.325)  # "$230 - $10.67 = $219.33"


def test_jade_lizard_credit_above_call_width_means_no_upside_risk(no_commission):
    m = tsla_lizard()
    # "Entry credit $1,067 > call width $1,000 -> no upside risk"
    assert m["credit_to_call_width"] > 1
    assert m["upside_risk"] == 0


def test_jade_lizard_reports_upside_risk_when_the_credit_falls_short(no_commission):
    m = formulas.jade_lizard_metrics(
        short_put_premium=Decimal("4.00"),
        short_call_premium=Decimal("13.225"),
        long_call_premium=Decimal("9.525"),
        short_put_strike=Decimal("230"),
        short_call_strike=Decimal("265"),
        long_call_strike=Decimal("275"),
        dte=35,
    )
    approx(m["net_credit"], 7.70)
    approx(m["upside_risk"], 230.0)  # (10.00 - 7.70) x 100
    assert m["credit_to_call_width"] < 1


def test_jade_lizard_credit_exactly_equal_to_the_width_is_riskless(no_commission):
    """ "equal or greater" -- the boundary belongs on the safe side."""
    m = formulas.jade_lizard_metrics(
        short_put_premium=Decimal("6.30"),
        short_call_premium=Decimal("13.225"),
        long_call_premium=Decimal("9.525"),
        short_put_strike=Decimal("230"),
        short_call_strike=Decimal("265"),
        long_call_strike=Decimal("275"),
        dte=35,
    )
    approx(m["net_credit"], 10.0)
    assert m["credit_to_call_width"] == 1
    assert m["upside_risk"] == 0


def test_jade_lizard_max_loss_is_the_put_strike_less_the_credit(no_commission):
    """ "Max loss = (Put Strike - Entry Credit) x 100" -- the stock at zero."""
    m = tsla_lizard()
    approx(m["max_loss"], 21932.5)


def test_jade_lizard_exit_triggers_follow_the_notes(no_commission):
    m = tsla_lizard()
    approx(m["take_profit_low"], 533.75)  # "Take profit at 1/2 to 2/3 of max profit"
    approx(m["take_profit_high"], 711.70225)  # 1067.5 x 0.6667
    assert m["stop_trigger_price"] == Decimal("230")  # "price falls below the short put's strike"
    assert m["close_above_price"] == Decimal("275")  # "price exceeds the long call's strike"


def test_jade_lizard_missing_leg_propagates_none():
    m = formulas.jade_lizard_metrics(
        short_put_premium=None,
        short_call_premium=Decimal("13.225"),
        long_call_premium=Decimal("9.525"),
        short_put_strike=Decimal("230"),
        short_call_strike=Decimal("265"),
        long_call_strike=Decimal("275"),
        dte=35,
    )
    for key in ("net_credit", "max_profit", "max_loss", "breakeven", "upside_risk", "roi"):
        assert m[key] is None, f"{key} should be None when a leg has no quote"
    # The strikes are known even when the quotes are not.
    assert m["call_width"] == Decimal("10")


# ---------------------------------------------------------------------------
# Synthetic spread -- parity with Synthetic_Spread_Notes.pdf
#   HD at $191.45: buy Jul 190C @ 6.05, sell Jul 190P @ 5.17, DTE 59
#   Net debit $0.88 | buying power $4,292 (22.4% of $19,145)
# ---------------------------------------------------------------------------


def hd_synthetic(bullish=True):
    return formulas.synthetic_metrics(
        long_premium=Decimal("6.05"),
        short_premium=Decimal("5.17"),
        long_strike=Decimal("190"),
        short_strike=Decimal("190"),
        stock_price=Decimal("191.45"),
        bullish=bullish,
        dte=59,
    )


def test_synthetic_matches_the_worked_example(no_commission):
    m = hd_synthetic()
    approx(m["net_debit"], 0.88)
    approx(m["breakeven"], 190.88)
    # "Max loss = (Put Strike + Debit) x 100"
    approx(m["max_loss"], 19088.0)


def test_long_synthetic_profit_is_unlimited_not_unknown(no_commission):
    """None on this field means no finite number exists -- the UI renders it as unlimited."""
    assert hd_synthetic()["max_profit"] is None


def test_short_synthetic_loss_is_unbounded_and_profit_is_capped_at_zero(no_commission):
    m = hd_synthetic(bullish=False)
    assert m["max_loss"] is None  # the short call has no ceiling
    approx(m["max_profit"], 18912.0)  # (190 - 0.88) x 100, the stock at zero
    approx(m["breakeven"], 189.12)  # long_strike - debit, mirrored


def test_synthetic_buying_power_is_a_fraction_of_the_share_price(no_commission):
    """ "broker should block buying power at approximately 20%-25% of share value"."""
    m = hd_synthetic()
    approx(m["notional"], 19145.0)
    approx(m["buying_power"], 19145.0 * float(config.SYNTHETIC["buying_power_pct"]))
    approx(m["leverage"], 1 / float(config.SYNTHETIC["buying_power_pct"]))


def test_synthetic_opened_for_a_credit_has_a_second_breakeven(no_commission):
    """Split strikes plus a net credit put the flat zone above zero, so both edges cross."""
    m = formulas.synthetic_metrics(
        long_premium=Decimal("4.00"),
        short_premium=Decimal("5.00"),
        long_strike=Decimal("105"),
        short_strike=Decimal("95"),
        stock_price=Decimal("100"),
        bullish=True,
        dte=45,
    )
    approx(m["net_debit"], -1.0)
    approx(m["breakeven"], 104.0)  # long call strike less the credit
    approx(m["secondary_breakeven"], 94.0)  # short put strike less the credit
    approx(m["strike_gap"], 10.0)


def test_synthetic_debit_has_no_second_breakeven(no_commission):
    """Paid a debit, the flat zone sits below zero and never crosses it."""
    assert hd_synthetic()["secondary_breakeven"] is None


def test_synthetic_missing_leg_propagates_none():
    m = formulas.synthetic_metrics(
        long_premium=Decimal("6.05"),
        short_premium=None,
        long_strike=Decimal("190"),
        short_strike=Decimal("190"),
        stock_price=Decimal("191.45"),
        bullish=True,
        dte=59,
    )
    for key in ("net_debit", "breakeven", "max_loss", "net_premium_pct_of_spot"):
        assert m[key] is None, f"{key} should be None when a leg has no quote"
