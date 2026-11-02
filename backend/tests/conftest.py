"""Shared fixtures. Deliberately hand-built objects -- no IBKR connection in any test."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from backend.app import config
from backend.app.domain import Bar, Leg, OptionQuote, Right, Strategy, UnderlyingSnapshot
from backend.app.metrics import greeks
from backend.app.rules.base import RuleContext

QUOTE_DATE = date(2026, 9, 10)


def make_quote(
    strike: str | Decimal,
    right: Right = Right.PUT,
    *,
    bid: str | None = "2.00",
    ask: str | None = "2.20",
    delta: float | None = -0.22,
    theta: float | None = -0.05,
    iv: float | None = 0.30,
    dte: int = 37,
    underlying: str | None = "100.00",
    open_interest: int | None = 2500,
    volume: int | None = 400,
    symbol: str = "TEST",
) -> OptionQuote:
    return OptionQuote(
        symbol=symbol,
        expiry=QUOTE_DATE + timedelta(days=dte),
        strike=Decimal(str(strike)),
        right=right,
        bid=Decimal(bid) if bid is not None else None,
        ask=Decimal(ask) if ask is not None else None,
        delta=delta,
        theta=theta,
        iv=iv,
        open_interest=open_interest,
        volume=volume,
        underlying_price=Decimal(underlying) if underlying is not None else None,
    )


def make_underlying(**overrides) -> UnderlyingSnapshot:
    base = {
        "symbol": "TEST",
        "price": Decimal("100.00"),
        "volume": 3_000_000,
        "avg_volume": 2_500_000,
        "market_cap": Decimal("50000000000"),
        "sector": "Technology",
        "dividend_yield": Decimal("0.01"),
        "iv": 0.30,
        "iv_percentile": Decimal("55"),
        "earnings_date": QUOTE_DATE + timedelta(days=120),
        "shares_owned": 100,
    }
    base.update(overrides)
    return UnderlyingSnapshot(**base)


def make_context(
    *,
    strategy: Strategy = Strategy.BULL_PUT,
    legs: list[Leg] | None = None,
    metrics: dict | None = None,
    underlying: UnderlyingSnapshot | None = None,
    dte: int = 37,
    **kwargs,
) -> RuleContext:
    return RuleContext(
        strategy=strategy,
        symbol="TEST",
        legs=legs if legs is not None else [Leg(make_quote("95"), -100)],
        metrics=metrics or {},
        underlying=underlying or make_underlying(),
        dte=dte,
        quote_date=QUOTE_DATE,
        **kwargs,
    )


def model_chain(
    *,
    spot: Decimal = Decimal("254.85"),
    iv: float = 0.45,
    dte: int = 35,
    step: Decimal = Decimal("5"),
    low_pct: float = 0.80,
    high_pct: float = 1.15,
    symbol: str = "TEST",
    spread: Decimal = Decimal("0.10"),
) -> list[OptionQuote]:
    """A chain priced by the app's own Black-Scholes model.

    `synthetic_chain` in test_scan.py is a hand-drawn ladder: fine for asserting that a builder
    puts its legs in the right order, useless for anything where the premium and the delta have
    to be mutually consistent. A jade lizard is exactly that case -- whether the credit covers
    the call width depends on what a 25-delta put is actually worth, and you cannot make that
    number up.

    Defaults mirror the TSLA example in Jade_Lizard_Notes.pdf: spot ~$255, 35 DTE, $5 ladder.
    """
    t_years = dte / 365.0
    rate = float(config.RISK_FREE_RATE)
    expiry = QUOTE_DATE + timedelta(days=dte)

    strikes: list[Decimal] = []
    strike = (spot * Decimal(str(low_pct)) / step).to_integral_value() * step
    while strike <= spot * Decimal(str(high_pct)):
        strikes.append(strike)
        strike += step

    quotes: list[OptionQuote] = []
    for strike in strikes:
        for right in (Right.PUT, Right.CALL):
            price = greeks.bsm_price(right, float(spot), float(strike), t_years, rate, iv, 0.0)
            g = greeks.bsm_greeks(right, float(spot), float(strike), t_years, rate, iv, 0.0)
            mid = Decimal(str(round(price, 2)))
            if mid <= 0:
                continue
            quotes.append(
                OptionQuote(
                    symbol=symbol,
                    expiry=expiry,
                    strike=strike,
                    right=right,
                    bid=max(mid - spread, Decimal("0.01")),
                    ask=mid + spread,
                    delta=g.delta,
                    theta=g.theta,
                    iv=iv,
                    open_interest=3000,
                    volume=500,
                    underlying_price=spot,
                )
            )
    return quotes


def trending_bars(n: int = 260, *, start: float = 50.0, step: float = 0.35) -> list[Bar]:
    """A clean uptrend -- every moving average stacks in bullish order."""
    bars = []
    for i in range(n):
        close = start + i * step
        bars.append(
            Bar(
                day=date(2025, 1, 1) + timedelta(days=i),
                open=close - 0.2,
                high=close + 0.5,
                low=close - 0.5,
                close=close,
                volume=1_000_000,
            )
        )
    return bars


@pytest.fixture
def quote_date() -> date:
    return QUOTE_DATE
