"""IV percentile tests, anchored to a real IBKR reading.

During planning, IBKR's own 52-week implied-volatility percentile for AVGO was 0.0518 (i.e. 5)
against a raw IV of 38.6% and a 52-week range of roughly 29.0 to 49.5 vol. Any implementation
that does not land near 5 on that shape of input is wrong.
"""

from __future__ import annotations

import random
from decimal import Decimal

import pytest

from backend.app import config
from backend.app.metrics.iv_percentile import iv_percentile, iv_rank


def test_percentile_is_zero_when_current_is_the_lowest():
    history = [0.30 + i * 0.001 for i in range(200)]
    assert iv_percentile(history, 0.10) == Decimal("0")


def test_percentile_is_one_hundred_when_current_is_the_highest():
    history = [0.30 + i * 0.001 for i in range(200)]
    assert iv_percentile(history, 5.0) == Decimal("100")


def test_percentile_of_the_median_is_about_fifty():
    history = [i / 200 for i in range(200)]
    value = iv_percentile(history, 0.5)
    assert value is not None
    assert 49 <= float(value) <= 51


def test_avgo_shape_reproduces_ibkrs_low_percentile():
    """AVGO: IV near the bottom of its yearly range should read ~5, not ~39.

    This is the case that makes the raw-IV / percentile ambiguity matter: 38.6% sounds rich in
    isolation but is cheap against this stock's own year.
    """
    random.seed(7)
    # A year that mostly traded above 40 vol, with a small tail below the current 38.6.
    history = [0.386 - random.uniform(0.001, 0.05) for _ in range(13)]
    history += [0.386 + random.uniform(0.001, 0.11) for _ in range(239)]

    value = iv_percentile(history, 0.386)
    assert value is not None
    assert 3 <= float(value) <= 8, f"expected ~5, got {value}"


def test_percentile_refuses_to_answer_on_thin_history():
    """A confident-looking number from 20 bars would gate real trades on noise."""
    assert iv_percentile([0.3] * 20, 0.35) is None


def test_percentile_returns_none_when_current_is_missing():
    assert iv_percentile([0.3] * 200, None) is None


def test_percentile_window_is_bounded_to_a_trading_year():
    """Only the trailing window counts -- older history must not drag the answer."""
    old_high = [0.90] * 500
    recent_low = [0.10] * config.IV_HISTORY_DAYS
    value = iv_percentile(old_high + recent_low, 0.20)
    assert value == Decimal("100")


def test_rank_and_percentile_are_different_measures():
    """Rank asks 'how far up the range', percentile asks 'how many days were lower'.

    On a skewed distribution they disagree, which is exactly why both are reported.
    """
    history = [0.20] * 240 + [0.80] * 12
    current = 0.25

    pctile = iv_percentile(history, current)
    rank = iv_rank(history, current)

    assert pctile is not None and rank is not None
    assert float(pctile) > 90  # almost every day was lower
    assert float(rank) < 15  # but it is barely off the floor of the range
    assert abs(float(pctile) - float(rank)) > 50


def test_rank_returns_none_on_a_flat_series():
    assert iv_rank([0.3] * 200, 0.3) is None


@pytest.mark.parametrize("bad", [float("nan")])
def test_nan_values_are_dropped_not_counted(bad):
    history = [0.3] * 200 + [bad] * 5
    value = iv_percentile(history, 0.4)
    assert value == Decimal("100")
