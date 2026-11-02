"""Market cap, sector and earnings date from Yahoo Finance, via `yfinance`.

Used when the IBKR account is refused fundamentals (error 10358). `yfinance` is unofficial
and breaks periodically, so every failure here degrades to None -- and None fails the rule,
exactly as the IBKR path does. Nothing is ever defaulted to a plausible value.

Two traps in Yahoo's data, both handled in `parse`:

* Pharma is filed under industry "Drug Manufacturers - ...", not "Pharmaceuticals". The
  sector string is therefore "<sector> / <industry>" so the exclusion keywords in config can
  see the industry.
* After a report, the earnings date can lag and still show the one just passed. A past date
  would let the earnings gate pass on stale data, so it is treated as unknown.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from backend.app import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class YahooFundamentals:
    market_cap: Decimal | None
    sector: str | None
    earnings_date: date | None
    earnings_source: str  # "yfinance", "yfinance_estimate" or "unknown"


_UNKNOWN = YahooFundamentals(None, None, None, "unknown")
_cache: dict[str, tuple[float, YahooFundamentals]] = {}


async def fundamentals(symbol: str, today: date | None = None) -> YahooFundamentals:
    """Fetch (cached) fundamentals for one symbol. Never raises."""
    symbol = symbol.upper()
    hit = _cache.get(symbol)
    if hit and time.monotonic() - hit[0] < config.YAHOO_CACHE_HOURS * 3600:
        return hit[1]

    try:
        # A cold import (pandas, antivirus scanning the DLLs) can take >10s on Windows.
        # Do it outside the timeout so it cannot eat the budget meant for the network call.
        await asyncio.to_thread(_import_yfinance)
    except Exception as exc:
        logger.warning("yfinance import failed: %s %s", type(exc).__name__, exc)
        return _UNKNOWN

    fetched = None
    for attempt in range(1, config.YAHOO_ATTEMPTS + 1):
        try:
            fetched = await asyncio.wait_for(
                asyncio.to_thread(_fetch, symbol), timeout=config.YAHOO_TIMEOUT_SECONDS
            )
            break
        except Exception as exc:  # network, rate limit, yfinance breakage -- all mean "unknown"
            logger.warning(
                "yfinance %s failed (attempt %d/%d): %s %s",
                symbol,
                attempt,
                config.YAHOO_ATTEMPTS,
                type(exc).__name__,
                exc,
            )
    if fetched is None:
        return _UNKNOWN

    info, calendar = fetched
    result = parse(info, calendar, today or date.today())
    logger.info("yfinance %s -> %s", symbol, result)
    _cache[symbol] = (time.monotonic(), result)
    return result


def _import_yfinance() -> None:
    import yfinance  # noqa: F401 -- imported lazily: only needed when this source is selected


def _fetch(symbol: str) -> tuple[dict[str, Any], dict[str, Any]]:
    import yfinance as yf  # already loaded by _import_yfinance; this is a dict lookup

    ticker = yf.Ticker(symbol)
    logger.info("yfinance request %s", symbol)
    info = ticker.info or {}
    try:
        calendar = ticker.calendar or {}
    except Exception as exc:
        logger.warning("yfinance calendar %s failed: %s", symbol, exc)
        calendar = {}
    return info, calendar


def parse(info: dict[str, Any], calendar: dict[str, Any], today: date) -> YahooFundamentals:
    earnings = _earnings(calendar, info, today)
    source = "unknown"
    if earnings is not None:
        source = "yfinance_estimate" if info.get("isEarningsDateEstimate") else "yfinance"
    return YahooFundamentals(
        market_cap=_market_cap(info.get("marketCap")),
        sector=_sector(info.get("sector"), info.get("industry")),
        earnings_date=earnings,
        earnings_source=source,
    )


def _market_cap(raw: Any) -> Decimal | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    return value if value > 0 else None


def _sector(sector: Any, industry: Any) -> str | None:
    parts = [str(p).strip() for p in (sector, industry) if p and str(p).strip()]
    return " / ".join(parts) or None


def _earnings(calendar: dict[str, Any], info: dict[str, Any], today: date) -> date | None:
    """Earliest upcoming earnings date. A range is reported by its first day (conservative)."""
    candidates: list[date] = []

    raw = calendar.get("Earnings Date")
    for item in raw if isinstance(raw, list | tuple) else [raw]:
        if isinstance(item, datetime):
            candidates.append(item.date())
        elif isinstance(item, date):
            candidates.append(item)

    if not candidates:
        for key in ("earningsTimestampStart", "earningsTimestamp"):
            ts = info.get(key)
            if isinstance(ts, int | float) and not isinstance(ts, bool) and ts > 0:
                candidates.append(datetime.fromtimestamp(ts, UTC).date())
                break

    if not candidates:
        return None
    earliest = min(candidates)
    if earliest < today:
        logger.warning("yfinance earnings date %s is in the past -- treating as unknown", earliest)
        return None
    return earliest
