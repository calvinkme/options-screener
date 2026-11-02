"""Expiry selection.

The windows form exists because the merged span does not work: four strategies want 30-45
days, one wants 60+, and the synthetic spread wants 30-90. Asking for the three nearest
expiries across 20-120 days returns three near-dated ones and leaves the long-dated
strategies with no chain at all -- silently, as an empty table rather than an error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from backend.app.ib import marketdata
from backend.app.rules.registry import ALL_STRATEGIES

QUOTE_DATE = date(2026, 9, 10)


@dataclass
class FakeParams:
    """Just the one attribute select_expiries reads."""

    expirations: list[str]


def weekly_chain(weeks: int = 20) -> FakeParams:
    """Weekly expiries out to roughly five months, as a liquid name actually lists them."""
    return FakeParams(
        [(QUOTE_DATE + timedelta(days=7 * (i + 1))).strftime("%Y%m%d") for i in range(weeks)]
    )


def dtes(picked: list[str]) -> list[int]:
    return [(marketdata._parse_expiry(raw) - QUOTE_DATE).days for raw in picked]


def test_merged_span_would_starve_the_long_dated_strategies():
    """The behaviour the windows form replaces, asserted so the regression is visible."""
    span = marketdata.dte_span_for(ALL_STRATEGIES)
    picked = marketdata.select_expiries(
        weekly_chain(), quote_date=QUOTE_DATE, dte_range=span, limit=3
    )
    assert max(dtes(picked)) < 60, "the flat span really does cluster at the near end"


def test_windows_cover_the_long_dated_strategies_too():
    picked = marketdata.select_expiries(
        weekly_chain(),
        quote_date=QUOTE_DATE,
        dte_windows=marketdata.dte_windows_for(ALL_STRATEGIES),
        limit=3,
    )
    chosen = dtes(picked)
    assert any(d >= 60 for d in chosen), f"no expiry for the 60+ DTE strategies: {chosen}"
    assert any(30 <= d <= 45 for d in chosen), f"no expiry for the 30-45 strategies: {chosen}"


def test_windows_cost_no_more_requests_than_the_flat_span():
    """The fix must not quietly triple the number of chains fetched per ticker."""
    picked = marketdata.select_expiries(
        weekly_chain(),
        quote_date=QUOTE_DATE,
        dte_windows=marketdata.dte_windows_for(ALL_STRATEGIES),
        limit=3,
    )
    assert len(picked) <= 3


def test_windows_are_deduplicated():
    """Four strategies share the 30-45 window; that must be one expiry, not four."""
    picked = marketdata.select_expiries(
        weekly_chain(),
        quote_date=QUOTE_DATE,
        dte_windows=[(30, 45), (30, 45), (30, 45), (30, 45)],
        limit=3,
    )
    assert len(picked) == 1


def test_thinning_keeps_the_extremes_rather_than_truncating():
    """Over budget, dropping the tail would undo the whole point of the windows."""
    picked = marketdata.select_expiries(
        weekly_chain(),
        quote_date=QUOTE_DATE,
        dte_windows=[(7, 10), (30, 35), (60, 65), (90, 95), (117, 126)],
        limit=3,
    )
    chosen = dtes(picked)
    assert len(chosen) == 3
    assert chosen[0] <= 10 and chosen[-1] >= 117


def test_a_window_with_no_matching_expiry_is_skipped_not_faked():
    picked = marketdata.select_expiries(
        weekly_chain(weeks=4),  # nothing beyond 28 days
        quote_date=QUOTE_DATE,
        dte_windows=[(7, 14), (200, 240)],
        limit=3,
    )
    assert len(picked) == 1


def test_no_expiries_at_all_returns_empty():
    assert marketdata.select_expiries(None, quote_date=QUOTE_DATE, dte_range=(30, 45)) == []
    assert (
        marketdata.select_expiries(
            FakeParams([]), quote_date=QUOTE_DATE, dte_windows=[(30, 45)], limit=3
        )
        == []
    )


def test_dte_span_still_brackets_every_window():
    low, high = marketdata.dte_span_for(ALL_STRATEGIES)
    for window_low, window_high in marketdata.dte_windows_for(ALL_STRATEGIES):
        assert low <= window_low and window_high <= high
