"""SQLite schema.

The `trades` table matters more than it looks, per CLAUDE.md: the point is to be able to ask
later whether the rules actually worked. The confluence score at entry is stored so score can
be correlated with outcome, and nothing in here is populated automatically -- entries are
recorded by hand after a fill, because the app never places orders.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from backend.app import config


def utcnow() -> datetime:
    """UTC internally; the UI renders in Australia/Sydney."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Watchlist(Base):
    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    sector: Mapped[str | None] = mapped_column(String(120), nullable=True)
    shares_owned: Mapped[int] = mapped_column(Integer, default=0)
    # Manual earnings override -- the documented escape hatch when IBKR has no calendar
    # entitlement. Staleness is derived from earnings_updated_at, never assumed away.
    earnings_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    earnings_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class IvHistory(Base):
    """Daily implied vol per ticker -- this is what makes IV percentile ours, not the broker's."""

    __tablename__ = "iv_history"
    __table_args__ = (UniqueConstraint("ticker", "day", name="uq_iv_ticker_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    implied_vol: Mapped[float] = mapped_column(Float)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ScanResult(Base):
    __tablename__ = "scan_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    strategy: Mapped[str] = mapped_column(String(40), index=True)
    verdict: Mapped[str] = mapped_column(String(8))
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metrics_json: Mapped[str] = mapped_column(Text)
    failures_json: Mapped[str] = mapped_column(Text)


class Trade(Base):
    """Filled positions, entered by hand. The audit trail for whether the rules earned money."""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    strategy: Mapped[str] = mapped_column(String(40))
    legs_json: Mapped[str] = mapped_column(Text)
    credit: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    confluence_score_at_entry: Mapped[int | None] = mapped_column(Integer, nullable=True)


engine = create_engine(config.settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(engine)


DEFAULT_WATCHLIST = [
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "META",
    "AVGO",
    "COST",
    "UNH",
    "WMT",
    "V",
]


def seed_watchlist(tickers: list[str] | None = None) -> None:
    """Seed on first run only -- never overwrites an existing list."""
    from sqlalchemy import select

    with SessionLocal() as session:
        if session.execute(select(Watchlist).limit(1)).first():
            return
        for ticker in tickers or DEFAULT_WATCHLIST:
            session.add(Watchlist(ticker=ticker))
        session.commit()
