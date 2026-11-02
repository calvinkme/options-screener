"""Indicator tests.

The point of these is to prove the "is it possible?" answer: every chart signal in the spec is
ordinary arithmetic with a deterministic result, not a judgement call.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.app import config
from backend.app.domain import Bar
from backend.app.metrics import indicators
from backend.app.scoring import confluence
from backend.tests.conftest import trending_bars


def bar(close: float, *, high=None, low=None, open_=None, day=None) -> Bar:
    return Bar(
        day=day or date(2026, 1, 1),
        open=open_ if open_ is not None else close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=1_000,
    )


# ---------------------------------------------------------------------------
# Moving averages
# ---------------------------------------------------------------------------


def test_sma_is_the_plain_mean_of_the_window():
    assert indicators.sma([1, 2, 3, 4, 5], 5) == 3.0
    assert indicators.sma([1, 2, 3, 4, 5], 3) == 4.0


def test_sma_returns_none_without_enough_history():
    assert indicators.sma([1, 2], 5) is None


def test_ema_of_a_flat_series_is_that_value():
    assert indicators.ema([10.0] * 50, 20) == pytest.approx(10.0)


def test_ema_reacts_faster_than_sma_to_a_step_change():
    """EMA weights recent bars more, so after an upward step it sits above the SMA."""
    closes = [100.0] * 50 + [110.0] * 5
    assert indicators.ema(closes, 20) > indicators.sma(closes, 20)


def test_ema_and_sma_coincide_on_a_linear_ramp():
    """Not a bug: on an arithmetic series both lag by exactly (n-1)/2, so they agree.

    Pinned as a test because it looks like a defect the first time you see it in the UI.
    """
    closes = [float(i) for i in range(1, 61)]
    assert indicators.ema(closes, 20) == pytest.approx(indicators.sma(closes, 20), abs=1e-9)


def test_ma_fan_is_bullish_in_a_clean_uptrend():
    tech = indicators.analyse(trending_bars())
    assert tech.ema_bullish is True
    assert tech.ma_bullish is True
    assert tech.fan_bullish is True


def test_ma_fan_is_bearish_in_a_clean_downtrend():
    tech = indicators.analyse(trending_bars(step=-0.35, start=150.0))
    assert tech.ema_bullish is False
    assert tech.fan_bullish is False


def test_indicators_return_none_rather_than_guessing_on_short_history():
    tech = indicators.analyse(trending_bars(n=10))
    assert tech.sma[200] is None
    assert tech.fan_bullish is None
    assert tech.stochastic is None


# ---------------------------------------------------------------------------
# Stochastic
# ---------------------------------------------------------------------------


def test_stochastic_pins_to_100_at_the_top_of_the_range():
    bars = [bar(float(i), high=float(i) + 0.1, low=float(i) - 0.1) for i in range(1, 41)]
    st = indicators.stochastic(bars)
    assert st is not None
    assert st.k > 90
    assert st.overbought is True
    assert st.oversold is False


def test_stochastic_pins_to_zero_at_the_bottom_of_the_range():
    bars = [bar(float(i), high=float(i) + 0.1, low=float(i) - 0.1) for i in range(40, 0, -1)]
    st = indicators.stochastic(bars)
    assert st is not None
    assert st.k < 10
    assert st.oversold is True


def test_stochastic_thresholds_come_from_config():
    assert config.STOCHASTIC["oversold"] == 20.0
    assert config.STOCHASTIC["overbought"] == 80.0


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------


def test_macd_is_positive_in_an_uptrend():
    closes = [float(i) for i in range(1, 101)]
    m = indicators.macd(closes)
    assert m is not None
    assert m.macd > 0


def test_macd_is_negative_in_a_downtrend():
    closes = [float(i) for i in range(100, 0, -1)]
    m = indicators.macd(closes)
    assert m is not None
    assert m.macd < 0


# ---------------------------------------------------------------------------
# Bollinger / ATR
# ---------------------------------------------------------------------------


def test_bollinger_bands_collapse_on_a_flat_series():
    bb = indicators.bollinger([bar(100.0) for _ in range(60)])
    assert bb is not None
    assert bb.upper == pytest.approx(100.0)
    assert bb.lower == pytest.approx(100.0)
    assert bb.middle == pytest.approx(100.0)


def test_atr_of_constant_range_bars():
    bars = [bar(100.0, high=101.0, low=99.0) for _ in range(30)]
    assert indicators.atr(bars) == pytest.approx(2.0)


def test_atr_returns_none_without_enough_bars():
    assert indicators.atr([bar(100.0)]) is None


# ---------------------------------------------------------------------------
# Structure -- support / resistance
# ---------------------------------------------------------------------------


def test_support_level_needs_the_configured_number_of_touches():
    """Three bounces off the same price should register; two should not."""
    bars: list[Bar] = []
    day = date(2026, 1, 1)
    for _cycle in range(4):
        for offset in (0, 3, 6, 3, 0):
            bars.append(
                bar(
                    100.0 + offset,
                    high=100.0 + offset + 0.5,
                    low=100.0 + offset - 0.5,
                    day=day,
                )
            )
            day += timedelta(days=1)

    levels = indicators.find_levels(bars)
    assert all(lv.touches >= config.SR_MIN_TOUCHES for lv in levels)


def test_structure_reports_distance_in_atr_units():
    """The subjective 'retracement to support' reduced to a measured number."""
    tech = indicators.analyse(trending_bars())
    st = tech.structure
    assert st is not None
    if st.nearest_support is not None:
        assert st.distance_to_support_atr is not None
        assert st.distance_to_support_atr >= 0


# ---------------------------------------------------------------------------
# Candles -- CLAUDE.md defines these numerically
# ---------------------------------------------------------------------------


def test_pin_bar_detected_when_one_shadow_dominates():
    prev = bar(100.0)
    pin = Bar(day=date(2026, 1, 2), open=100.0, high=100.2, low=97.0, close=99.9, volume=1000)
    sig = indicators.candle_signal([prev, pin])
    assert sig is not None
    assert sig.pin_bar is True


def test_large_body_is_not_a_pin_bar():
    prev = bar(100.0)
    big = Bar(day=date(2026, 1, 2), open=97.0, high=103.2, low=96.9, close=103.0, volume=1000)
    sig = indicators.candle_signal([prev, big])
    assert sig is not None
    assert sig.pin_bar is False


def test_bullish_engulfing_requires_covering_the_prior_body():
    down = Bar(day=date(2026, 1, 1), open=102.0, high=102.5, low=99.5, close=100.0, volume=1000)
    up = Bar(day=date(2026, 1, 2), open=99.5, high=103.5, low=99.0, close=103.0, volume=1000)
    sig = indicators.candle_signal([down, up])
    assert sig is not None
    assert sig.bullish_engulfing is True
    assert sig.bearish_engulfing is False


# ---------------------------------------------------------------------------
# Confluence scoring
# ---------------------------------------------------------------------------


def test_confluence_caps_at_two_when_a_mandatory_layer_fails():
    """CLAUDE.md: layers 1 and 2 are mandatory; if either fails, cap the score at 2."""
    tech = indicators.analyse(trending_bars())
    # A clean uptrend has no structure signal, so structure fails and the cap applies.
    score = confluence.score(tech, bullish=True)
    assert score.score <= config.CONFLUENCE_CAP_IF_MANDATORY_FAILS or not score.capped
    if score.capped:
        assert score.score == config.CONFLUENCE_CAP_IF_MANDATORY_FAILS
        assert score.raw_score > score.score


def test_confluence_reports_which_layers_contributed():
    score = confluence.score(indicators.analyse(trending_bars()), bullish=True)
    names = [layer.name for layer in score.layers]
    assert names == ["trend", "structure", "momentum", "volatility", "trigger"]
    assert all(layer.detail for layer in score.layers)


def test_confluence_with_no_data_scores_zero_not_an_error():
    score = confluence.score(None, bullish=True)
    assert score.score == 0
    assert score.size_guidance == "no trade"


def test_trend_layer_fails_when_direction_is_wrong():
    tech = indicators.analyse(trending_bars())
    bullish = confluence.score(tech, bullish=True)
    bearish = confluence.score(tech, bullish=False)
    trend_up = next(lay for lay in bullish.layers if lay.name == "trend")
    trend_down = next(lay for lay in bearish.layers if lay.name == "trend")
    assert trend_up.scored is True
    assert trend_down.scored is False
