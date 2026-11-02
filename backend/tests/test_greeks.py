"""Local Black-Scholes-Merton Greeks -- the fallback for a delayed, OPRA-less feed.

Reference values are the textbook case S=100, K=100, T=1y, r=5%, q=0, sigma=20%:
call 10.4506, put 5.5735, call delta 0.6368, put delta -0.3632, gamma 0.01876,
vega 37.524 (per 1.00 of vol), call theta -6.414/yr.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.app import config
from backend.app.domain import OptionQuote, Right
from backend.app.ib import marketdata
from backend.app.metrics import greeks as g

S, K, T, R, Q, SIGMA = 100.0, 100.0, 1.0, 0.05, 0.0, 0.20


# -- pricing --------------------------------------------------------------------------------


def test_call_price_matches_textbook():
    assert g.bsm_price(Right.CALL, S, K, T, R, SIGMA, Q) == pytest.approx(10.4506, abs=1e-4)


def test_put_price_matches_textbook():
    assert g.bsm_price(Right.PUT, S, K, T, R, SIGMA, Q) == pytest.approx(5.5735, abs=1e-4)


def test_put_call_parity_with_dividend():
    q = 0.03
    c = g.bsm_price(Right.CALL, S, K, T, R, SIGMA, q)
    p = g.bsm_price(Right.PUT, S, K, T, R, SIGMA, q)
    assert c - p == pytest.approx(S * math.exp(-q * T) - K * math.exp(-R * T), abs=1e-9)


def test_zero_time_is_intrinsic():
    assert g.bsm_price(Right.CALL, 110, 100, 0.0, R, SIGMA, Q) == 10.0
    assert g.bsm_price(Right.PUT, 110, 100, 0.0, R, SIGMA, Q) == 0.0


# -- greeks ---------------------------------------------------------------------------------


def test_call_greeks_match_textbook():
    gr = g.bsm_greeks(Right.CALL, S, K, T, R, SIGMA, Q)
    assert gr.delta == pytest.approx(0.6368, abs=1e-4)
    assert gr.gamma == pytest.approx(0.01876, abs=1e-5)
    assert gr.vega == pytest.approx(37.524, abs=1e-3)
    assert gr.theta * 365 == pytest.approx(-6.414, abs=1e-3)


def test_put_delta_is_call_delta_minus_one_when_no_dividend():
    call = g.bsm_greeks(Right.CALL, S, K, T, R, SIGMA, Q)
    put = g.bsm_greeks(Right.PUT, S, K, T, R, SIGMA, Q)
    assert put.delta == pytest.approx(call.delta - 1.0, abs=1e-12)
    assert put.delta == pytest.approx(-0.3632, abs=1e-4)


# -- implied vol ----------------------------------------------------------------------------


def test_implied_vol_round_trips():
    price = g.bsm_price(Right.PUT, S, 95, 37 / 365, R, 0.35, 0.01)
    assert g.implied_vol(Right.PUT, price, S, 95, 37 / 365, R, 0.01) == pytest.approx(
        0.35, abs=1e-5
    )


def test_implied_vol_none_when_price_at_or_below_model_floor():
    # Deep ITM call at exactly intrinsic: the European floor S - K*exp(-rT) is above it.
    assert g.implied_vol(Right.CALL, 20.0, 120.0, 100.0, 30 / 365, R, Q) is None
    # Deep ITM put below the discounted floor K*exp(-rT) - S.
    assert g.implied_vol(Right.PUT, 19.0, 80.0, 100.0, 30 / 365, R, Q) is None


def test_implied_vol_none_when_price_absurdly_high():
    assert g.implied_vol(Right.CALL, 150.0, S, K, 30 / 365, R, Q) is None


def test_implied_vol_none_for_expired_or_free_option():
    assert g.implied_vol(Right.CALL, 5.0, S, K, 0.0, R, Q) is None
    assert g.implied_vol(Right.CALL, 0.0, S, K, T, R, Q) is None


# -- greeks_from_mark: the data-layer entry point -------------------------------------------


def test_greeks_from_mark_realistic_short_put():
    # $100 stock, 95 put, 37 DTE, mid $1.60 -- should land in the bull-put delta band.
    gr = g.greeks_from_mark(
        Right.PUT,
        Decimal("1.60"),
        Decimal("100"),
        Decimal("95"),
        37,
        rate=Decimal("0.04"),
        dividend_yield=Decimal("0.005"),
    )
    assert gr is not None
    assert -0.30 < gr.delta < -0.15
    assert 0.20 < gr.iv < 0.40
    assert gr.theta < 0


@pytest.mark.parametrize(
    "mark, spot, strike, dte",
    [
        (None, Decimal("100"), Decimal("95"), 37),
        (Decimal("1.60"), None, Decimal("95"), 37),
        (Decimal("1.60"), Decimal("100"), None, 37),
        (Decimal("1.60"), Decimal("100"), Decimal("95"), None),
        (Decimal("1.60"), Decimal("100"), Decimal("95"), 0),
        (Decimal("0"), Decimal("100"), Decimal("95"), 37),
    ],
)
def test_greeks_from_mark_never_guesses(mark, spot, strike, dte):
    assert (
        g.greeks_from_mark(
            Right.PUT, mark, spot, strike, dte, rate=Decimal("0.04"), dividend_yield=None
        )
        is None
    )


# -- wiring into the quote layer ------------------------------------------------------------

QUOTE_DATE = date(2026, 9, 14)
EXPIRY = QUOTE_DATE + timedelta(days=37)


def _quote(**kw) -> OptionQuote:
    base = dict(
        symbol="XYZ",
        expiry=EXPIRY,
        strike=Decimal("95"),
        right=Right.PUT,
        bid=Decimal("1.55"),
        ask=Decimal("1.65"),
        underlying_price=Decimal("100"),
    )
    base.update(kw)
    return OptionQuote(**base)


def test_fill_greeks_tags_source_and_populates_all_fields():
    q = _quote()
    assert marketdata.fill_greeks_from_mark(q, Decimal("0.005"), QUOTE_DATE) is True
    assert q.greeks_source == "computed_bsm"
    assert all(v is not None for v in (q.delta, q.gamma, q.vega, q.theta, q.iv))
    assert -0.30 < q.delta < -0.15


def test_fill_greeks_leaves_quote_untouched_when_mid_missing():
    q = _quote(bid=None, ask=None, last=None)
    assert marketdata.fill_greeks_from_mark(q, None, QUOTE_DATE) is False
    assert q.delta is None and q.greeks_source is None


def test_fill_greeks_leaves_quote_untouched_when_below_intrinsic():
    q = _quote(strike=Decimal("120"), bid=Decimal("19.0"), ask=Decimal("19.5"))  # intrinsic 20
    assert marketdata.fill_greeks_from_mark(q, None, QUOTE_DATE) is False
    assert q.delta is None and q.greeks_source is None


def _opt(right: str) -> SimpleNamespace:
    return SimpleNamespace(symbol="XYZ", strike=95.0, right=right)


def test_to_quote_tags_broker_greeks():
    ticker = SimpleNamespace(
        bid=1.55,
        ask=1.65,
        last=1.60,
        volume=10,
        putOpenInterest=500,
        modelGreeks=SimpleNamespace(
            delta=-0.22, gamma=0.03, vega=0.1, theta=-0.04, impliedVol=0.31, undPrice=100.0
        ),
    )
    q = marketdata._to_quote(ticker, _opt("P"), EXPIRY)
    assert q.greeks_source == "ibkr_model"
    assert q.delta == pytest.approx(-0.22)


def test_to_quote_treats_nan_broker_delta_as_absent():
    """A delayed feed returns a modelGreeks object full of NaN -- that is not a Greek."""
    ticker = SimpleNamespace(
        bid=1.55,
        ask=1.65,
        last=float("nan"),
        volume=float("nan"),
        putOpenInterest=float("nan"),
        modelGreeks=SimpleNamespace(
            delta=float("nan"),
            gamma=float("nan"),
            vega=float("nan"),
            theta=float("nan"),
            impliedVol=float("nan"),
            undPrice=float("nan"),
        ),
    )
    q = marketdata._to_quote(ticker, _opt("P"), EXPIRY)
    assert q.delta is None
    assert q.greeks_source is None


def test_to_quote_without_greeks_object_is_untagged():
    ticker = SimpleNamespace(bid=1.55, ask=1.65, last=1.60, volume=10, putOpenInterest=500)
    q = marketdata._to_quote(ticker, _opt("P"), EXPIRY)
    assert q.greeks_source is None and q.delta is None


def test_status_exposes_fallback_flag():
    assert config.COMPUTE_GREEKS_WHEN_MISSING in (True, False)
    from backend.app.ib.client import IBClient

    assert IBClient().status()["greeks_fallback"] is config.COMPUTE_GREEKS_WHEN_MISSING
