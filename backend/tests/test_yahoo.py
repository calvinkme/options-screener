"""Yahoo Finance fundamentals parsing. No network: `parse` is fed recorded shapes."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from backend.app.rules import common
from backend.app.sources import yahoo
from backend.tests.conftest import make_context, make_underlying

TODAY = date(2026, 9, 30)

AVGO_INFO = {
    "marketCap": 1710487502848,
    "sector": "Technology",
    "industry": "Semiconductors",
    "isEarningsDateEstimate": False,
}


def test_parses_recorded_avgo_shape():
    res = yahoo.parse(AVGO_INFO, {"Earnings Date": [date(2026, 12, 10)]}, TODAY)
    assert res.market_cap == Decimal("1710487502848")
    assert res.sector == "Technology / Semiconductors"
    assert res.earnings_date == date(2026, 12, 10)
    assert res.earnings_source == "yfinance"


def test_empty_response_is_all_unknown():
    res = yahoo.parse({}, {}, TODAY)
    assert res == yahoo.YahooFundamentals(None, None, None, "unknown")


@pytest.mark.parametrize("raw", [None, 0, -5, "n/a", True])
def test_bad_market_cap_is_none_not_a_number(raw):
    assert yahoo.parse({"marketCap": raw}, {}, TODAY).market_cap is None


def test_earnings_range_takes_earliest_day():
    cal = {"Earnings Date": [date(2026, 11, 5), date(2026, 11, 2)]}
    assert yahoo.parse({}, cal, TODAY).earnings_date == date(2026, 11, 2)


@pytest.mark.parametrize(
    "day,expected",
    [
        (date(2026, 10, 1), date(2026, 10, 1)),  # future: kept
        (date(2026, 9, 30), date(2026, 9, 30)),  # boundary: today is still upcoming
        (date(2026, 9, 29), None),  # past: stale, must not clear the gate
    ],
)
def test_past_earnings_date_is_unknown(day, expected):
    res = yahoo.parse({}, {"Earnings Date": [day]}, TODAY)
    assert res.earnings_date == expected
    assert res.earnings_source == ("unknown" if expected is None else "yfinance")


def test_falls_back_to_info_timestamp_when_calendar_empty():
    ts = datetime(2026, 12, 10, 21, tzinfo=UTC).timestamp()
    res = yahoo.parse({"earningsTimestampStart": ts}, {}, TODAY)
    assert res.earnings_date == date(2026, 12, 10)


def test_estimated_date_is_labelled():
    info = {"isEarningsDateEstimate": True}
    res = yahoo.parse(info, {"Earnings Date": [date(2026, 11, 1)]}, TODAY)
    assert res.earnings_source == "yfinance_estimate"


@pytest.mark.parametrize(
    "sector,industry,passes",
    [
        ("Technology", "Semiconductors", True),
        ("Healthcare", "Biotechnology", False),  # VRTX
        ("Healthcare", "Drug Manufacturers - General", False),  # LLY
        ("Healthcare", "Drug Manufacturers - Specialty & Generic", False),
        ("Healthcare", "Medical Devices", True),
    ],
)
def test_yahoo_sector_strings_hit_the_exclusion_rule(sector, industry, passes):
    parsed = yahoo.parse({"sector": sector, "industry": industry}, {}, TODAY)
    ctx = make_context(underlying=make_underlying(sector=parsed.sector))
    assert common.rule_sector(ctx).passed is passes


async def test_fetch_failure_degrades_to_unknown(monkeypatch):
    def boom(symbol):
        raise ConnectionError("yahoo down")

    monkeypatch.setattr(yahoo, "_fetch", boom)
    yahoo._cache.clear()
    res = await yahoo.fundamentals("ZZZZ", today=TODAY)
    assert res.market_cap is None and res.sector is None and res.earnings_date is None


async def test_one_transient_failure_is_retried(monkeypatch):
    calls = []

    def flaky(symbol):
        calls.append(symbol)
        if len(calls) == 1:
            raise TimeoutError
        return AVGO_INFO, {"Earnings Date": [date(2099, 12, 10)]}

    monkeypatch.setattr(yahoo, "_fetch", flaky)
    yahoo._cache.clear()
    res = await yahoo.fundamentals("AVGO", today=TODAY)
    assert len(calls) == 2
    assert res.market_cap == Decimal("1710487502848")


async def test_gives_up_after_configured_attempts(monkeypatch):
    calls = []

    def always_down(symbol):
        calls.append(symbol)
        raise ConnectionError("yahoo down")

    monkeypatch.setattr(yahoo, "_fetch", always_down)
    yahoo._cache.clear()
    res = await yahoo.fundamentals("ZZZZ", today=TODAY)
    assert len(calls) == yahoo.config.YAHOO_ATTEMPTS
    assert res.earnings_date is None and res.earnings_source == "unknown"
