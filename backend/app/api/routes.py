"""HTTP API. Two screens, one endpoint family, one evaluation path underneath."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.app import config
from backend.app.db.models import SessionLocal, Watchlist, utcnow
from backend.app.domain import Strategy
from backend.app.ib.client import IBConnectionError, get_client
from backend.app.rules.registry import (
    ALL_STRATEGIES,
    band_thresholds,
    columns_for,
    spec_for,
)
from backend.app.rules.selection import guide_to_dict
from backend.app.scan.evaluate import verdict_to_dict
from backend.app.scan.runner import Scanner, ScanReport

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

_scanner: Scanner | None = None


def scanner() -> Scanner:
    global _scanner
    if _scanner is None:
        _scanner = Scanner()
    return _scanner


# ---------------------------------------------------------------------------
# Status and configuration
# ---------------------------------------------------------------------------


@router.get("/status")
def status() -> dict[str, Any]:
    """Drives the data banner. The UI must never imply live data when it is delayed."""
    return get_client().status()


@router.get("/config")
def configuration() -> dict[str, Any]:
    """Strategy list, per-strategy columns, colour bands and the flags that resolve the
    two documented ambiguities -- surfaced so the UI can state which reading is in force."""
    return {
        "strategies": [
            {
                "value": s.value,
                "label": s.label,
                "columns": columns_for(s),
                "bullish": spec_for(s).bullish,
                "kind": spec_for(s).kind,
                "legs": spec_for(s).legs,
                "guide": guide_to_dict(s),
            }
            for s in ALL_STRATEGIES
        ],
        "bands": band_thresholds(),
        "flags": {
            "iv_gate_uses": config.IV_GATE_USES,
            "dte_annualise_plus_one": config.DTE_ANNUALISE_PLUS_ONE,
            "chart_rules_are_hard_gates": config.CHART_RULES_ARE_HARD_GATES,
            "csp_cash_basis": config.CSP_CASH_BASIS,
            "csp_mode": config.CASH_SECURED_PUT["mode"],
        },
        "account": {
            "net_liquidation": float(config.settings.net_liquidation),
            "cash_available": float(config.settings.cash_available),
        },
    }


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------


class WatchlistEntry(BaseModel):
    ticker: str = Field(min_length=1, max_length=16)
    shares_owned: int = 0
    notes: str | None = None
    earnings_date: date | None = None


def _watchlist_rows() -> list[Watchlist]:
    with SessionLocal() as session:
        return list(
            session.execute(
                select(Watchlist).where(Watchlist.active.is_(True)).order_by(Watchlist.ticker)
            )
            .scalars()
            .all()
        )


@router.get("/watchlist")
def get_watchlist() -> list[dict[str, Any]]:
    return [
        {
            "ticker": row.ticker,
            "shares_owned": row.shares_owned,
            "notes": row.notes,
            "sector": row.sector,
            "earnings_date": row.earnings_date.isoformat() if row.earnings_date else None,
            "earnings_is_stale": _is_stale(row),
        }
        for row in _watchlist_rows()
    ]


@router.post("/watchlist")
def add_to_watchlist(entry: WatchlistEntry) -> dict[str, Any]:
    ticker = entry.ticker.upper().strip()
    with SessionLocal() as session:
        row = session.execute(
            select(Watchlist).where(Watchlist.ticker == ticker)
        ).scalar_one_or_none()

        if row is None:
            row = Watchlist(ticker=ticker)
            session.add(row)

        row.active = True
        row.shares_owned = entry.shares_owned
        row.notes = entry.notes
        if entry.earnings_date is not None:
            row.earnings_date = entry.earnings_date
            row.earnings_updated_at = utcnow()

        session.commit()
    return {"ok": True, "ticker": ticker}


@router.delete("/watchlist/{ticker}")
def remove_from_watchlist(ticker: str) -> dict[str, Any]:
    with SessionLocal() as session:
        row = session.execute(
            select(Watchlist).where(Watchlist.ticker == ticker.upper())
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, f"{ticker.upper()} is not on the watchlist")
        row.active = False
        session.commit()
    return {"ok": True}


def _is_stale(row: Watchlist) -> bool:
    """CLAUDE.md asks for a 'stale after 90 days' warning on manually entered dates."""
    if row.earnings_date is None or row.earnings_updated_at is None:
        return False
    age = (utcnow().replace(tzinfo=None) - row.earnings_updated_at).days
    return age > config.EARNINGS_STALE_AFTER_DAYS


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


class ScanRequest(BaseModel):
    strategy: str | None = None
    ticker: str | None = None
    symbols: list[str] | None = None


@router.post("/scan")
async def scan(request: ScanRequest) -> dict[str, Any]:
    """One endpoint, two directions.

    `ticker` set  -> every strategy against that one name (view B).
    `strategy` set -> that strategy across the watchlist or the supplied symbols (view A).
    """
    engine = scanner()

    try:
        await engine.connect()
    except IBConnectionError as exc:
        raise HTTPException(503, str(exc)) from exc

    engine.reset_cache()
    rows = {row.ticker: row for row in _watchlist_rows()}
    shares = {t: r.shares_owned for t, r in rows.items()}
    earnings = {t: r.earnings_date for t, r in rows.items() if r.earnings_date}

    if request.ticker:
        ticker = request.ticker.upper().strip()
        report = await engine.scan_ticker(
            ticker,
            shares_owned=shares.get(ticker, 0),
            earnings_override=earnings.get(ticker),
        )
        return _report_payload(report, mode="ticker", subject=ticker)

    if not request.strategy:
        raise HTTPException(400, "Provide either a strategy or a ticker")

    try:
        strategy = Strategy(request.strategy)
    except ValueError as exc:
        raise HTTPException(400, f"Unknown strategy: {request.strategy}") from exc

    symbols = [s.upper() for s in (request.symbols or list(rows))]
    if not symbols:
        raise HTTPException(400, "Watchlist is empty -- add a ticker first")

    report = await engine.scan_strategy(strategy, symbols, shares=shares, earnings=earnings)
    return _report_payload(report, mode="strategy", subject=strategy.value)


def _report_payload(report: ScanReport, *, mode: str, subject: str) -> dict[str, Any]:
    rows = [verdict_to_dict(v) for v in report.verdicts]
    return {
        "mode": mode,
        "subject": subject,
        "quote_date": report.quote_date.isoformat(),
        "status": get_client().status(),
        "counts": {
            "total": len(rows),
            "pass": sum(1 for r in rows if r["verdict"] == "PASS"),
            "fail": sum(1 for r in rows if r["verdict"] == "FAIL"),
            "skipped": len(report.skipped),
        },
        "skipped": report.skipped,
        "rows": rows,
    }
