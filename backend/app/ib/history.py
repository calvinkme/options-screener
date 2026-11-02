"""Historical bars: price for the indicators, implied volatility for the IV percentile.

The IV series is the interesting one. The TWS socket API has no IV-percentile field, but it
will serve a year of daily implied-volatility bars for the underlying via
`whatToShow="OPTION_IMPLIED_VOLATILITY"`. That is the input ThinkorSwim's percentile is
computed from, so we get the same measure with no extra subscription -- and we accumulate the
history locally as a side effect, which CLAUDE.md lists as a wanted upgrade.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from ib_async import Contract

from backend.app.domain import Bar
from backend.app.ib.client import IBClient

logger = logging.getLogger(__name__)

ONE_YEAR = "1 Y"
DAILY = "1 day"


async def _bars(
    client: IBClient,
    contract: Contract,
    what_to_show: str,
    duration: str = ONE_YEAR,
    bar_size: str = DAILY,
) -> list[Any]:
    result = await client.paced(
        lambda: client.ib.reqHistoricalDataAsync(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=True,
            formatDate=1,
        ),
        what=f"{what_to_show} {contract.symbol}",
    )
    return list(result or [])


async def daily_bars(client: IBClient, contract: Contract, duration: str = ONE_YEAR) -> list[Bar]:
    """OHLCV daily bars, used by every indicator."""
    raw = await _bars(client, contract, "TRADES", duration)
    out: list[Bar] = []
    for b in raw:
        day = _as_date(b.date)
        if day is None:
            continue
        out.append(
            Bar(
                day=day,
                open=float(b.open),
                high=float(b.high),
                low=float(b.low),
                close=float(b.close),
                volume=float(b.volume) if b.volume is not None else 0.0,
            )
        )
    return out


async def iv_history(client: IBClient, contract: Contract, duration: str = ONE_YEAR) -> list[float]:
    """Daily implied volatility closes for the underlying, as fractions.

    Returns an empty list rather than a partial guess when the request fails -- the percentile
    function then reports None, and the IV rule fails rather than passing on nothing.
    """
    raw = await _bars(client, contract, "OPTION_IMPLIED_VOLATILITY", duration)
    values: list[float] = []
    for b in raw:
        try:
            close = float(b.close)
        except (TypeError, ValueError):
            continue
        if close > 0:  # IBKR emits 0 / -1 for days with no computable IV
            values.append(close)
    return values


async def historical_volatility(
    client: IBClient, contract: Contract, duration: str = "3 M"
) -> float | None:
    """IBKR's own realised-volatility series, reported next to IV for context."""
    raw = await _bars(client, contract, "HISTORICAL_VOLATILITY", duration)
    for b in reversed(raw):
        try:
            value = float(b.close)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def average_volume(bars: Sequence[Bar], days: int = 30) -> int | None:
    """Average daily share volume over the trailing window."""
    recent = [b.volume for b in bars[-days:] if b.volume]
    if not recent:
        return None
    return int(sum(recent) / len(recent))


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        for fmt in ("%Y%m%d", "%Y-%m-%d"):
            try:
                return datetime.strptime(value[:10], fmt).date()
            except ValueError:
                continue
    return None
