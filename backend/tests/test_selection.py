"""Contract selection follows the course notes, and the written guide matches the checks."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from backend.app import config
from backend.app.domain import Strategy
from backend.app.rules import debit_spread, long_option
from backend.app.rules.registry import ALL_STRATEGIES
from backend.app.rules.selection import guide_for, selection_for
from backend.app.scan import builder
from backend.app.scan.evaluate import evaluate
from backend.tests.conftest import QUOTE_DATE, make_context, make_underlying, model_chain

SPOT = Decimal("254.85")


def _chain():
    """One expiry per strategy window, so every builder has something to work with."""
    return (
        model_chain(spot=SPOT, dte=30, step=Decimal("2.5"))
        + model_chain(spot=SPOT, dte=37, step=Decimal("2.5"))
        + model_chain(spot=SPOT, dte=74, step=Decimal("2.5"))
    )


# -- the written guide and the rule checks must agree --------------------------------------


@pytest.mark.parametrize("strategy", ALL_STRATEGIES)
def test_every_selection_step_is_backed_by_a_check_that_runs(strategy):
    underlying = make_underlying(price=SPOT, iv=0.45, iv_percentile=Decimal("60"))
    candidates = builder.build(strategy, _chain(), underlying, QUOTE_DATE)
    assert candidates, f"{strategy} built nothing"
    verdict = evaluate(candidates[0], underlying, quote_date=QUOTE_DATE)
    emitted = {r.rule_id for r in verdict.results}
    for step in guide_for(strategy).steps:
        assert set(step.rule_ids) & emitted, f"{strategy} step {step.step!r} has no live check"
    for row in selection_for(strategy, verdict.results):
        assert row["chosen"] != "not checked for this row"


# -- puts: a negative delta must be judged against a mirrored band --------------------------


@pytest.mark.parametrize(
    "delta,expected",
    [("-0.55", True), ("-0.50", True), ("-0.60", True), ("-0.45", False), ("-0.61", False)],
)
def test_long_put_delta_band_is_mirrored(delta, expected):
    ctx = make_context(strategy=Strategy.LONG_PUT, metrics={"long_delta_per_share": Decimal(delta)})
    assert long_option.rule_delta(ctx).passed is expected


@pytest.mark.parametrize("delta,expected", [("-0.55", True), ("-0.448", False), ("0.55", False)])
def test_bear_put_long_delta_band_is_mirrored(delta, expected):
    ctx = make_context(strategy=Strategy.BEAR_PUT, metrics={"long_delta_per_share": Decimal(delta)})
    assert debit_spread.rule_long_delta(ctx).passed is expected


def test_long_call_delta_unchanged():
    ctx = make_context(
        strategy=Strategy.LONG_CALL, metrics={"long_delta_per_share": Decimal("0.55")}
    )
    assert long_option.rule_delta(ctx).passed is True


# -- debit spreads: long leg ATM or 1 ITM, short leg at the 1-SD target ---------------------


@pytest.mark.parametrize("itm,expected", [(0, True), (1, True), (2, False), (-1, False)])
def test_debit_long_leg_moneyness(itm, expected):
    ctx = make_context(strategy=Strategy.BULL_CALL, metrics={"strikes_itm": itm})
    assert debit_spread.rule_moneyness(ctx).passed is expected


@pytest.mark.parametrize("strategy", [Strategy.BULL_CALL, Strategy.BEAR_PUT])
def test_debit_short_leg_sits_at_one_sd(strategy):
    underlying = make_underlying(price=SPOT, iv=0.45)
    chain = model_chain(spot=SPOT, dte=30, step=Decimal("1"))
    candidates = builder.build(strategy, chain, underlying, QUOTE_DATE)
    assert candidates
    for c in candidates:
        assert Decimal("0.75") <= c.metrics["short_strike_sd"] <= Decimal("1.25")


# -- expiry: prefer one that ends before earnings -----------------------------------------


def test_expiry_before_earnings_is_preferred():
    chain = model_chain(spot=SPOT, dte=32) + model_chain(spot=SPOT, dte=39)
    earnings = QUOTE_DATE + timedelta(days=35)
    groups = builder.quotes_for_window(chain, (30, 45), QUOTE_DATE, avoid_earnings=earnings)
    assert [(g[0].expiry - QUOTE_DATE).days for g in groups] == [32]


def test_all_expiries_span_earnings_keeps_them_for_the_rule_to_fail():
    chain = model_chain(spot=SPOT, dte=32) + model_chain(spot=SPOT, dte=39)
    earnings = QUOTE_DATE + timedelta(days=10)
    groups = builder.quotes_for_window(chain, (30, 45), QUOTE_DATE, avoid_earnings=earnings)
    assert len(groups) == 2


def test_bull_put_builds_on_the_pre_earnings_expiry():
    underlying = make_underlying(price=SPOT, earnings_date=QUOTE_DATE + timedelta(days=35))
    chain = model_chain(spot=SPOT, dte=32) + model_chain(spot=SPOT, dte=39)
    candidates = builder.build(Strategy.BULL_PUT, chain, underlying, QUOTE_DATE)
    assert candidates and {c.dte for c in candidates} == {32}


# -- the candidate cap keeps the contracts the rule asks for -------------------------------


def test_cap_keeps_in_band_contracts(monkeypatch):
    monkeypatch.setattr(config, "MAX_CANDIDATES_PER_STRATEGY", 4)
    underlying = make_underlying(price=SPOT)
    chain = model_chain(spot=SPOT, dte=37, step=Decimal("1"))  # many strikes in the wide band
    candidates = builder.build(Strategy.BULL_PUT, chain, underlying, QUOTE_DATE)
    low, high = config.BULL_PUT["short_delta"]
    assert len(candidates) == 4
    for c in candidates:
        assert low <= c.metrics["short_delta_per_share"] <= high


# -- ranking: the notes place the legs first, then check credit and size ------------------


def _verdict(strategy, results, dte=37):
    from backend.app.domain import RuleResult, StrategyVerdict

    return StrategyVerdict(
        strategy=strategy,
        symbol="TEST",
        legs=[],
        metrics={"dte": dte},
        results=[RuleResult(rid, rid, ok, None, "", rid) for rid, ok in results],
        confluence=None,
        underlying=make_underlying(),
    )


def test_in_band_short_strike_outranks_fewer_other_failures():
    from backend.app.scan.runner import _rank_key

    wrong_delta = _verdict(Strategy.BEAR_CALL, [("bear_call.short_delta", False)])
    in_band = _verdict(
        Strategy.BEAR_CALL,
        [("bear_call.short_delta", True), ("universal.spread", False), ("size.position", False)],
    )
    assert sorted([wrong_delta, in_band], key=_rank_key)[0] is in_band


def test_debit_spread_prefers_expiry_nearest_thirty_days():
    from backend.app.scan.runner import _rank_key

    near = _verdict(Strategy.BULL_CALL, [("time.earnings", False)], dte=32)
    far = _verdict(Strategy.BULL_CALL, [("time.earnings", False)], dte=25)
    assert sorted([far, near], key=_rank_key)[0] is near


def test_cap_does_not_starve_a_later_in_window_expiry(monkeypatch):
    monkeypatch.setattr(config, "MAX_CANDIDATES_PER_STRATEGY", 3)
    underlying = make_underlying(price=SPOT, iv=0.45)
    chain = model_chain(spot=SPOT, dte=25, step=Decimal("1")) + model_chain(
        spot=SPOT, dte=32, step=Decimal("1")
    )
    candidates = builder.build(Strategy.BULL_CALL, chain, underlying, QUOTE_DATE)
    assert {c.dte for c in candidates} == {25, 32}
