"""Builder, evaluator and cross-view tests, on a synthetic chain -- no IBKR connection.

The cross-view test is the important one: it asserts that the strategy-first and ticker-first
screens produce byte-identical verdicts for the same trade, which is the property that stops
the two views drifting apart as rules get tuned.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from backend.app import config
from backend.app.domain import OptionQuote, Right, Strategy
from backend.app.metrics import indicators
from backend.app.scan import builder
from backend.app.scan.evaluate import evaluate, verdict_to_dict
from backend.app.scan.runner import Scanner, TickerData
from backend.tests.conftest import QUOTE_DATE, make_underlying, model_chain, trending_bars

SPOT = Decimal("100")
DTE = 37
EXPIRY = QUOTE_DATE + timedelta(days=DTE)


def synthetic_chain() -> list[OptionQuote]:
    """A tidy chain around a $100 spot: strikes every $5, deltas that behave sensibly."""
    quotes: list[OptionQuote] = []
    for strike_int in range(85, 121, 5):
        strike = Decimal(strike_int)
        moneyness = float(strike - SPOT) / float(SPOT)

        # Delta runs toward zero as an option goes out of the money: for a put that means
        # strikes *below* spot, which is the opposite direction to a call.
        put_delta = max(-0.95, min(-0.02, -0.5 - moneyness * 4.0))
        call_delta = max(0.02, min(0.95, 0.5 - moneyness * 4.0))

        put_mid = max(0.25, 6.0 - abs(moneyness) * 40) if strike <= SPOT else 6.0 + moneyness * 30
        call_mid = max(0.25, 6.0 - abs(moneyness) * 40) if strike >= SPOT else 6.0 - moneyness * 30

        for right, delta, mid in (
            (Right.PUT, put_delta, put_mid),
            (Right.CALL, call_delta, call_mid),
        ):
            mid = round(max(0.20, mid), 2)
            quotes.append(
                OptionQuote(
                    symbol="TEST",
                    expiry=EXPIRY,
                    strike=strike,
                    right=right,
                    bid=Decimal(str(round(mid - 0.10, 2))),
                    ask=Decimal(str(round(mid + 0.10, 2))),
                    delta=delta,
                    theta=-0.04,
                    iv=0.30,
                    open_interest=3000,
                    volume=500,
                    underlying_price=SPOT,
                )
            )
    return quotes


@pytest.fixture
def chain():
    return synthetic_chain()


@pytest.fixture
def underlying():
    return make_underlying(price=SPOT, earnings_date=QUOTE_DATE + timedelta(days=200))


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def test_bull_put_builder_puts_long_leg_below_the_short(chain, underlying):
    candidates = builder.build(Strategy.BULL_PUT, chain, underlying, QUOTE_DATE)
    assert candidates

    for c in candidates:
        short = next(leg for leg in c.legs if leg.is_short)
        long = next(leg for leg in c.legs if not leg.is_short)
        assert long.quote.strike < short.quote.strike
        assert short.quote.right is Right.PUT
        assert c.metrics["strike_offset"] in (1, 2)


def test_bear_call_builder_puts_long_leg_above_the_short(chain, underlying):
    candidates = builder.build(Strategy.BEAR_CALL, chain, underlying, QUOTE_DATE)
    assert candidates

    for c in candidates:
        short = next(leg for leg in c.legs if leg.is_short)
        long = next(leg for leg in c.legs if not leg.is_short)
        assert long.quote.strike > short.quote.strike
        assert short.quote.right is Right.CALL


def test_credit_spread_metrics_are_populated(chain, underlying):
    c = builder.build(Strategy.BULL_PUT, chain, underlying, QUOTE_DATE)[0]
    for key in ("net_credit", "max_profit", "max_loss", "breakeven", "loss_ratio", "roi"):
        assert c.metrics[key] is not None, f"{key} missing"
    assert c.metrics["dte"] == DTE


def test_csp_builder_produces_single_short_put(chain, underlying):
    candidates = builder.build(Strategy.CASH_SECURED_PUT, chain, underlying, QUOTE_DATE)
    assert candidates
    for c in candidates:
        assert len(c.legs) == 1
        assert c.legs[0].is_short
        assert c.legs[0].quote.right is Right.PUT
        assert c.metrics["cash_secured"] > 0


def test_covered_call_builder_produces_single_short_call(chain, underlying):
    candidates = builder.build(Strategy.COVERED_CALL, chain, underlying, QUOTE_DATE)
    assert candidates
    for c in candidates:
        assert c.legs[0].quote.right is Right.CALL
        assert c.legs[0].is_short


def test_bull_call_builder_respects_minimum_width(chain, underlying):
    candidates = builder.build(Strategy.BULL_CALL, chain, underlying, QUOTE_DATE)
    for c in candidates:
        assert c.metrics["width"] >= Decimal("5")


def test_builder_is_capped_so_one_chain_cannot_flood_the_table(chain, underlying):
    from backend.app import config

    for strategy in (Strategy.BULL_PUT, Strategy.BEAR_CALL, Strategy.CASH_SECURED_PUT):
        candidates = builder.build(strategy, chain, underlying, QUOTE_DATE)
        assert len(candidates) <= config.MAX_CANDIDATES_PER_STRATEGY


def test_builder_returns_nothing_on_an_empty_chain(underlying):
    assert builder.build(Strategy.BULL_PUT, [], underlying, QUOTE_DATE) == []


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def test_evaluate_produces_a_verdict_with_reasons(chain, underlying):
    candidate = builder.build(Strategy.BULL_PUT, chain, underlying, QUOTE_DATE)[0]
    verdict = evaluate(candidate, underlying, quote_date=QUOTE_DATE)

    assert verdict.verdict.value in ("PASS", "FAIL")
    assert verdict.results
    # Every blocking failure must carry a human-readable reason.
    for failure in verdict.failures:
        assert failure.message
        assert failure.advisory is False


def test_failing_earnings_shows_up_as_a_named_failure(chain):
    """The AVGO case from the spec: earnings inside the expiry window."""
    underlying = make_underlying(
        price=SPOT, earnings_date=QUOTE_DATE + timedelta(days=5), iv_percentile=Decimal("60")
    )
    candidate = builder.build(Strategy.BULL_PUT, chain, underlying, QUOTE_DATE)[0]
    verdict = evaluate(candidate, underlying, quote_date=QUOTE_DATE)

    assert verdict.verdict.value == "FAIL"
    assert any("inside expiry" in f.message for f in verdict.failures)


def test_low_iv_percentile_fails_selling_and_passes_buying(chain):
    """The inversion the ticker-first view exists to surface."""
    underlying = make_underlying(
        price=SPOT,
        iv=0.386,
        iv_percentile=Decimal("5"),
        earnings_date=QUOTE_DATE + timedelta(days=200),
    )

    bull_put = builder.build(Strategy.BULL_PUT, chain, underlying, QUOTE_DATE)[0]
    sell_verdict = evaluate(bull_put, underlying, quote_date=QUOTE_DATE)
    assert any("IV percentile" in f.message for f in sell_verdict.failures)

    long_calls = builder.build(Strategy.LONG_CALL, chain, underlying, QUOTE_DATE)
    if long_calls:
        buy_verdict = evaluate(long_calls[0], underlying, quote_date=QUOTE_DATE)
        iv_rules = [r for r in buy_verdict.results if r.rule_id == "volatility.iv_ceiling"]
        assert iv_rules and iv_rules[0].passed is True


def test_advisory_chart_rules_never_flip_a_verdict(chain, underlying):
    """With no bars at all, chart rules are unknown -- and must not fail the row."""
    candidate = builder.build(Strategy.BULL_PUT, chain, underlying, QUOTE_DATE)[0]
    verdict = evaluate(candidate, underlying, quote_date=QUOTE_DATE, technicals=None)

    chart_results = [r for r in verdict.results if r.rule_id.startswith("chart.")]
    assert chart_results
    assert all(not r.is_blocking for r in chart_results)


def test_describe_renders_the_spec_format(chain, underlying):
    candidate = builder.build(Strategy.BULL_PUT, chain, underlying, QUOTE_DATE)[0]
    text = evaluate(candidate, underlying, quote_date=QUOTE_DATE).describe()

    assert "Bull Put Spread" in text
    assert ("PASS" in text) or ("FAIL" in text)
    assert "DTE" in text


def test_verdict_serialises_without_decimals_or_dates(chain, underlying):
    candidate = builder.build(Strategy.BULL_PUT, chain, underlying, QUOTE_DATE)[0]
    payload = verdict_to_dict(evaluate(candidate, underlying, quote_date=QUOTE_DATE))

    import json

    json.dumps(payload)  # must not raise
    assert payload["symbol"] == "TEST"
    assert isinstance(payload["results"], list)
    assert "confluence" in payload


# ---------------------------------------------------------------------------
# Cross-view consistency -- the property that keeps the two screens honest
# ---------------------------------------------------------------------------


@pytest.fixture
def primed_scanner(chain, underlying):
    """A Scanner with its cache pre-filled, so no socket is opened."""
    scanner = Scanner()
    scanner._regime_loaded = True
    scanner._regime = None
    scanner._tickers["TEST"] = TickerData(
        symbol="TEST",
        underlying=underlying,
        quotes=chain,
        technicals=indicators.analyse(trending_bars()),
    )
    return scanner


async def test_both_views_agree_on_the_same_trade(primed_scanner):
    """A verdict must not depend on which screen you arrived from."""
    by_strategy = await primed_scanner.scan_strategy(Strategy.BULL_PUT, ["TEST"])
    by_ticker = await primed_scanner.scan_ticker("TEST", strategies=[Strategy.BULL_PUT])

    assert by_strategy.verdicts
    assert by_ticker.verdicts

    # The ticker view shows the best candidate; it must match the strategy view's top row.
    top_strategy = verdict_to_dict(by_strategy.verdicts[0])
    top_ticker = verdict_to_dict(by_ticker.verdicts[0])

    assert top_strategy["verdict"] == top_ticker["verdict"]
    assert top_strategy["legs"] == top_ticker["legs"]
    assert top_strategy["failures"] == top_ticker["failures"]
    assert top_strategy["confluence"] == top_ticker["confluence"]


async def test_ticker_view_covers_every_registered_strategy(primed_scanner):
    report = await primed_scanner.scan_ticker("TEST")
    seen = {v.strategy for v in report.verdicts} | {
        key.split(":")[1] for key in report.skipped if ":" in key
    }
    assert len(seen) >= 6


async def test_scan_ranks_passes_above_failures(primed_scanner):
    report = await primed_scanner.scan_strategy(Strategy.BULL_PUT, ["TEST"])
    verdicts = [v.verdict.value for v in report.verdicts]
    assert verdicts == sorted(verdicts, key=lambda v: 0 if v == "PASS" else 1)


async def test_unknown_ticker_is_skipped_with_a_reason(primed_scanner):
    """A bad symbol must not abort the run."""
    report = await primed_scanner.scan_strategy(Strategy.BULL_PUT, ["TEST", "NOPE"])
    assert "NOPE" in report.skipped
    assert report.verdicts  # TEST still produced rows


# ---------------------------------------------------------------------------
# Jade lizard builder
# ---------------------------------------------------------------------------


def test_jade_lizard_builder_produces_three_legs_in_the_right_order(chain, underlying):
    candidates = builder.build(Strategy.JADE_LIZARD, chain, underlying, QUOTE_DATE)
    assert candidates

    for c in candidates:
        assert len(c.legs) == 3
        short_put, short_call, long_call = c.legs
        assert short_put.is_short and short_put.quote.right is Right.PUT
        assert short_call.is_short and short_call.quote.right is Right.CALL
        assert not long_call.is_short and long_call.quote.right is Right.CALL
        # A bear call spread with a put sold underneath it, never overlapping.
        assert long_call.quote.strike > short_call.quote.strike
        assert short_put.quote.strike < short_call.quote.strike


def test_jade_lizard_builder_stops_at_the_first_strike_that_removes_upside_risk(chain, underlying):
    """Leg 3 is chosen functionally: the nearest call whose width the credit covers."""
    candidates = builder.build(Strategy.JADE_LIZARD, chain, underlying, QUOTE_DATE)
    riskless = [c for c in candidates if c.metrics["credit_to_call_width"] >= 1]
    assert riskless, "the synthetic chain should support at least one true jade lizard"

    for c in riskless:
        assert c.metrics["upside_risk"] == 0


# ---------------------------------------------------------------------------
# Leg 1 placement. The notes read like an instruction to auto-place the put ("Sell a Put
# option at ~25-delta (or lower)"), which would make the 1-SD breakeven rule satisfied by
# construction. The builder deliberately does not: the three conditions trade off against
# each other, and resolving that trade-off is the trader's call, not the screener's.
# ---------------------------------------------------------------------------


@pytest.fixture
def model_underlying():
    return make_underlying(
        price=Decimal("254.85"), iv=0.45, earnings_date=QUOTE_DATE + timedelta(days=200)
    )


def test_jade_lizard_offers_several_puts_not_one(model_underlying):
    candidates = builder.build(
        Strategy.JADE_LIZARD, model_chain(iv=0.45), model_underlying, QUOTE_DATE
    )
    puts = {c.legs[0].quote.strike for c in candidates}
    assert len(puts) > 1, "the put is a free parameter -- the table must show the alternatives"


def test_jade_lizard_keeps_the_richer_put_past_the_delta_ceiling(chain, underlying):
    """The aggressive put is often the only one whose credit covers the call width.

    On the standard chain the -0.30 put clears the width at 1.20x while the -0.10 put manages
    0.80x. Filtering it out for breaching the -0.25 ceiling would delete the better structure
    from the table; showing it with the ceiling breach attached lets that be a decision.
    """
    ceiling = config.JADE_LIZARD["short_put_delta"][0]
    candidates = builder.build(Strategy.JADE_LIZARD, chain, underlying, QUOTE_DATE)

    aggressive = [c for c in candidates if Decimal(str(c.legs[0].quote.delta)) < ceiling]
    assert aggressive, "a put past the delta ceiling must still be offered"

    best = max(aggressive, key=lambda c: c.metrics["credit_to_call_width"])
    assert best.metrics["credit_to_call_width"] >= 1

    # Offered, but never silently: the breach is a named, blocking failure on the row.
    verdict = evaluate(best, underlying, quote_date=QUOTE_DATE)
    assert "jade_lizard.short_put_delta" in {f.rule_id for f in verdict.failures}


def test_jade_lizard_shows_the_tradeoff_rather_than_resolving_it(model_underlying):
    """Pushing the put out buys downside room and spends credit. Both ends must be visible."""
    candidates = sorted(
        builder.build(Strategy.JADE_LIZARD, model_chain(iv=0.45), model_underlying, QUOTE_DATE),
        key=lambda c: c.legs[0].quote.strike,
    )
    lowest, highest = candidates[0], candidates[-1]

    # The further-out put has the safer breakeven and the thinner credit.
    assert lowest.metrics["breakeven_sd"] > highest.metrics["breakeven_sd"]
    assert lowest.metrics["credit_to_call_width"] < highest.metrics["credit_to_call_width"]


def test_jade_lizard_passes_when_the_chain_can_actually_support_one():
    """The happy path -- and it is a narrow one, which is the point.

    Swept across strike ladders of $1/$2.50/$5, IV from 35% to 80% and 35/42 DTE, almost every
    combination fails, nearly always on the downside breakeven: the put has to go far enough
    out to clear 1 SD, and by the time it does the credit no longer covers the call width. The
    two rules are close to tangent. High IV and the longer end of the DTE window is what opens
    the gap, which is the notes' own "enter when IV is high" arrived at from the maths.

    If this test ever starts failing after a threshold change, that is the finding, not a bug.
    """
    underlying = make_underlying(
        price=Decimal("254.85"), iv=0.60, earnings_date=QUOTE_DATE + timedelta(days=200)
    )
    candidates = builder.build(
        Strategy.JADE_LIZARD, model_chain(iv=0.60, dte=42), underlying, QUOTE_DATE
    )
    assert candidates

    passing = [
        v
        for v in (evaluate(c, underlying, quote_date=QUOTE_DATE) for c in candidates)
        if v.verdict.value == "PASS"
    ]
    assert passing, "no jade lizard passed on a chain rich enough to support one"

    v = passing[0]
    assert v.metrics["upside_risk"] == 0
    assert v.metrics["credit_to_call_width"] >= 1
    assert v.metrics["breakeven_sd"] >= 1


def test_jade_lizard_reports_unknown_breakeven_rather_than_guessing():
    """With no IV there is no expected move, so the 1-SD rule has nothing to measure."""
    blind = make_underlying(
        price=Decimal("254.85"), iv=None, earnings_date=QUOTE_DATE + timedelta(days=200)
    )
    candidates = builder.build(Strategy.JADE_LIZARD, model_chain(iv=0.45), blind, QUOTE_DATE)
    assert candidates

    c = candidates[0]
    assert c.metrics.get("breakeven_sd") is None
    verdict = evaluate(c, blind, quote_date=QUOTE_DATE)
    breakeven = next(r for r in verdict.results if r.rule_id == "jade_lizard.breakeven_sd")
    assert breakeven.passed is None
    assert breakeven.is_blocking, "an unmeasurable breakeven must not clear the gate"


def test_jade_lizard_builder_keeps_candidates_that_fail_rather_than_hiding_them(chain, underlying):
    """A near miss must appear as an explained FAIL row, not vanish from the table."""
    candidates = builder.build(Strategy.JADE_LIZARD, chain, underlying, QUOTE_DATE)
    failing = [c for c in candidates if c.metrics["credit_to_call_width"] < 1]
    assert failing

    verdict = evaluate(failing[0], underlying, quote_date=QUOTE_DATE)
    assert verdict.verdict.value == "FAIL"
    assert any("Upside risk" in f.message for f in verdict.failures)


def test_jade_lizard_metrics_are_populated(chain, underlying):
    c = builder.build(Strategy.JADE_LIZARD, chain, underlying, QUOTE_DATE)[0]
    for key in (
        "net_credit",
        "call_width",
        "credit_to_call_width",
        "max_profit",
        "max_loss",
        "breakeven",
        "breakeven_sd",
        "prob_win_approx",
        "buying_power",
        "roi",
        "short_put_delta",
        "short_call_delta",
    ):
        assert c.metrics[key] is not None, f"{key} missing"
    assert c.metrics["dte"] == DTE


# ---------------------------------------------------------------------------
# Synthetic builder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "strategy,long_right,short_right",
    [
        (Strategy.SYNTHETIC_LONG, Right.CALL, Right.PUT),
        (Strategy.SYNTHETIC_SHORT, Right.PUT, Right.CALL),
    ],
)
def test_synthetic_builder_legs_point_the_right_way(
    chain, underlying, strategy, long_right, short_right
):
    candidates = builder.build(strategy, chain, underlying, QUOTE_DATE)
    assert candidates

    for c in candidates:
        assert len(c.legs) == 2
        long_leg, short_leg = c.legs
        assert not long_leg.is_short and long_leg.quote.right is long_right
        assert short_leg.is_short and short_leg.quote.right is short_right


def test_synthetic_builder_defaults_to_matching_strikes(chain, underlying):
    """ "Strike price: ATM (or near ATM)" -- one strike, both legs."""
    for c in builder.build(Strategy.SYNTHETIC_LONG, chain, underlying, QUOTE_DATE):
        assert c.legs[0].quote.strike == c.legs[1].quote.strike


def test_synthetic_builder_split_mode_straddles_spot(chain, underlying, monkeypatch):
    monkeypatch.setitem(config.SYNTHETIC, "mode", "split")
    candidates = builder.build(Strategy.SYNTHETIC_LONG, chain, underlying, QUOTE_DATE)
    assert candidates

    for c in candidates:
        long_call, short_put = c.legs
        assert long_call.quote.strike > short_put.quote.strike


def test_long_synthetic_reports_unlimited_profit_rather_than_a_number(chain, underlying):
    c = builder.build(Strategy.SYNTHETIC_LONG, chain, underlying, QUOTE_DATE)[0]
    assert c.metrics["max_profit"] is None
    assert c.metrics["max_loss"] is not None


def test_short_synthetic_reports_unbounded_loss(chain, underlying):
    c = builder.build(Strategy.SYNTHETIC_SHORT, chain, underlying, QUOTE_DATE)[0]
    assert c.metrics["max_loss"] is None
    assert c.metrics["max_profit"] is not None


def test_synthetic_serialises_without_choking_on_the_unbounded_fields(chain, underlying):
    """The API must render an unlimited payoff as null, not crash on it."""
    c = builder.build(Strategy.SYNTHETIC_LONG, chain, underlying, QUOTE_DATE)[0]
    payload = verdict_to_dict(evaluate(c, underlying, quote_date=QUOTE_DATE))
    assert payload["metrics"]["max_profit"] is None
    assert payload["strategy"] == "synthetic_long"
    assert payload["strategy_label"] == "Long Synthetic"


# ---------------------------------------------------------------------------
# Registry coverage -- every strategy must be buildable and evaluable
# ---------------------------------------------------------------------------


def test_every_registered_strategy_has_a_builder_and_produces_verdicts(chain, underlying):
    """Adding a strategy to the registry without a builder would fail silently otherwise."""
    from backend.app.rules.registry import ALL_STRATEGIES, spec_for

    for strategy in ALL_STRATEGIES:
        candidates = builder.build(strategy, chain, underlying, QUOTE_DATE)
        assert candidates, f"{strategy.value} built nothing from the standard test chain"

        verdict = evaluate(candidates[0], underlying, quote_date=QUOTE_DATE)
        assert verdict.verdict.value in ("PASS", "FAIL")
        assert len(candidates[0].legs) == spec_for(strategy).legs


def test_every_registered_strategy_is_capped(chain, underlying):
    from backend.app.rules.registry import ALL_STRATEGIES

    for strategy in ALL_STRATEGIES:
        candidates = builder.build(strategy, chain, underlying, QUOTE_DATE)
        assert len(candidates) <= config.MAX_CANDIDATES_PER_STRATEGY


def test_every_column_a_strategy_declares_is_a_key_something_can_fill(chain, underlying):
    """A column whose key no metric ever sets would render as a permanently empty dash."""
    from backend.app.rules.registry import ALL_STRATEGIES, spec_for

    # These three are read off the verdict itself rather than from the metrics dict.
    known = {"verdict", "confluence", "symbol"}
    for strategy in ALL_STRATEGIES:
        candidates = builder.build(strategy, chain, underlying, QUOTE_DATE)
        metrics = candidates[0].metrics

        for column in spec_for(strategy).columns:
            if column.key in known:
                continue
            assert (
                column.key in metrics
            ), f"{strategy.value} declares column '{column.key}' that no metric provides"


# -- expiry selection: one expiry per build, inside the strategy's own DTE window ----------


def _two_expiry_chain():
    """SPGI shape: the same strikes listed at 32 and 74 DTE. The 74-day one comes last."""
    near = model_chain(spot=Decimal("388"), dte=32)
    far = model_chain(spot=Decimal("388"), dte=74)
    return near + far


@pytest.mark.parametrize(
    "strategy", [Strategy.BULL_PUT, Strategy.BEAR_CALL, Strategy.CASH_SECURED_PUT]
)
def test_builds_on_the_in_window_expiry_not_the_last_one_loaded(strategy):
    underlying = make_underlying(price=Decimal("388"))
    candidates = builder.build(strategy, _two_expiry_chain(), underlying, QUOTE_DATE)
    assert candidates
    assert {c.dte for c in candidates} == {32}


def test_spread_legs_never_mix_expiries():
    underlying = make_underlying(price=Decimal("388"))
    for c in builder.build(Strategy.BULL_PUT, _two_expiry_chain(), underlying, QUOTE_DATE):
        assert len({leg.quote.expiry for leg in c.legs}) == 1


def test_long_options_use_the_60_plus_expiry():
    underlying = make_underlying(price=Decimal("388"))
    candidates = builder.build(Strategy.LONG_CALL, _two_expiry_chain(), underlying, QUOTE_DATE)
    assert candidates and {c.dte for c in candidates} == {74}


def test_no_expiry_in_window_falls_back_to_the_nearest():
    chain = model_chain(spot=Decimal("388"), dte=25) + model_chain(spot=Decimal("388"), dte=74)
    groups = builder.quotes_for_window(chain, (30, 45), QUOTE_DATE)
    assert len(groups) == 1
    assert (groups[0][0].expiry - QUOTE_DATE).days == 25  # 5 days short beats 29 days over


@pytest.mark.parametrize("dte,inside", [(29, False), (30, True), (45, True), (46, False)])
def test_window_boundaries(dte, inside):
    # 38 DTE is always present and always inside 30-45; `dte` joins it only if it is inside too.
    chain = model_chain(spot=Decimal("388"), dte=dte) + model_chain(spot=Decimal("388"), dte=38)
    groups = builder.quotes_for_window(chain, (30, 45), QUOTE_DATE)
    got = {(g[0].expiry - QUOTE_DATE).days for g in groups}
    assert got == ({dte, 38} if inside else {38})
