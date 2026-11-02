"""Market cap, sector and earnings date.

CLAUDE.md is blunt about earnings: "There is no clean free source" and "an unknown earnings
date must be treated as a FAIL, not a pass." This module therefore returns None freely and
never substitutes a plausible date. The manual override in the watchlist table is the
documented escape hatch, with a staleness warning attached.

`reqFundamentalData` needs a Reuters/WSH entitlement. Where it is absent, everything here
degrades to None and the corresponding gates fail loudly rather than passing quietly.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

from ib_async import Contract

from backend.app.ib.client import IBClient

logger = logging.getLogger(__name__)


async def _report(client: IBClient, contract: Contract, report_type: str) -> str | None:
    return await client.paced(
        lambda: client.ib.reqFundamentalDataAsync(contract, report_type),
        what=f"fundamental {report_type} {contract.symbol}",
    )


async def company_snapshot(
    client: IBClient, contract: Contract
) -> tuple[Decimal | None, str | None]:
    """(market cap, sector). Market cap arrives in millions and is scaled here."""
    xml = await _report(client, contract, "ReportSnapshot")
    if not xml:
        return None, None

    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        logger.warning("Could not parse ReportSnapshot for %s: %s", contract.symbol, exc)
        return None, None

    market_cap = _market_cap(root)
    sector = _sector(root)
    return market_cap, sector


def _market_cap(root: ET.Element) -> Decimal | None:
    for ratio in root.iter("Ratio"):
        if ratio.get("FieldName") == "MKTCAP":
            try:
                # IBKR reports MKTCAP in millions of the reporting currency.
                return Decimal(str(ratio.text).strip()) * Decimal(1_000_000)
            except (InvalidOperation, TypeError, AttributeError):
                return None
    return None


def _sector(root: ET.Element) -> str | None:
    """Prefer the TRBC industry string; it is the one that names biotech explicitly."""
    best: str | None = None
    for industry in root.iter("Industry"):
        text = (industry.text or "").strip()
        if not text:
            continue
        if industry.get("type") == "TRBC":
            return text
        best = best or text
    return best


async def next_earnings_date(client: IBClient, contract: Contract) -> tuple[date | None, str]:
    """(date, source). Returns (None, "unknown") whenever there is any doubt.

    Deliberately does not fall back to yfinance here: adding an unofficial network dependency
    inside the scan loop trades a loud failure for a flaky one. The watchlist's manual field
    is the supported override.
    """
    xml = await _report(client, contract, "CalendarReport")
    if not xml:
        return None, "unknown"

    for match in re.finditer(r'[Dd]ate="?(\d{4}-\d{2}-\d{2})', xml):
        try:
            return datetime.strptime(match.group(1), "%Y-%m-%d").date(), "ibkr_calendar"
        except ValueError:
            continue

    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None, "unknown"

    for node in root.iter():
        text = (node.text or "").strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            try:
                return datetime.strptime(text, "%Y-%m-%d").date(), "ibkr_calendar"
            except ValueError:
                continue

    return None, "unknown"
