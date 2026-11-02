"""Scan orchestration -- the loop that turns a watchlist into verdicts.

Both views run through here. `scan_strategy` loops tickers for one strategy; `scan_ticker`
loops strategies for one ticker. Both call `evaluate()` on candidates built from exactly the
same cached market data, which is what guarantees the two screens can never disagree.

Fetch discipline: one underlying snapshot, one set of daily bars, one IV history and one chain
per ticker per run, cached in memory for the duration. Adding a seventh strategy costs no extra
IBKR requests.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from backend.app import config
from backend.app.domain import OptionQuote, Strategy, StrategyVerdict, UnderlyingSnapshot
from backend.app.ib import marketdata
from backend.app.ib.client import IBClient, get_client
from backend.app.metrics import indicators
from backend.app.metrics.indicators import TechnicalPicture
from backend.app.rules.registry import ALL_STRATEGIES
from backend.app.rules.selection import structural_failures
from backend.app.scan import builder
from backend.app.scan.evaluate import evaluate

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TickerData:
    """Everything fetched for one ticker in one run."""

    symbol: str
    underlying: UnderlyingSnapshot
    quotes: list[OptionQuote]
    technicals: TechnicalPicture | None
    error: str | None = None


@dataclass(slots=True)
class ScanReport:
    verdicts: list[StrategyVerdict] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    quote_date: date = field(default_factory=date.today)

    @property
    def passes(self) -> list[StrategyVerdict]:
        return [v for v in self.verdicts if v.verdict.value == "PASS"]


class Scanner:
    """Holds the connection and the per-run cache."""

    def __init__(self, client: IBClient | None = None) -> None:
        self.client = client or get_client()
        self._tickers: dict[str, TickerData] = {}
        self._regime: TechnicalPicture | None = None
        self._regime_loaded = False

    async def connect(self) -> None:
        await self.client.connect()

    def reset_cache(self) -> None:
        self._tickers.clear()
        self._regime = None
        self._regime_loaded = False

    # -- data ---------------------------------------------------------------

    async def regime(self) -> TechnicalPicture | None:
        """SPY 50MA vs 150MA. Fetched once per run, not once per ticker."""
        if self._regime_loaded:
            return self._regime
        self._regime_loaded = True
        try:
            bars = await marketdata.index_bars(self.client)
            self._regime = indicators.analyse(bars) if bars else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load market regime: %s", exc)
            self._regime = None
        return self._regime

    async def load_ticker(
        self,
        symbol: str,
        *,
        quote_date: date,
        shares_owned: int = 0,
        earnings_override: date | None = None,
    ) -> TickerData | None:
        symbol = symbol.upper()
        if symbol in self._tickers:
            return self._tickers[symbol]

        try:
            snapshot, contract = await marketdata.underlying_snapshot(
                self.client,
                symbol,
                shares_owned=shares_owned,
                earnings_override=earnings_override,
            )
        except Exception as exc:  # noqa: BLE001 -- one bad ticker must not end the scan
            logger.warning("Failed to load %s: %s", symbol, exc)
            return None

        if snapshot is None or contract is None:
            return None

        quotes = await self._load_chain(contract, snapshot, quote_date)
        technicals = indicators.analyse(snapshot.bars) if snapshot.bars else None

        data = TickerData(symbol, snapshot, quotes, technicals)
        self._tickers[symbol] = data
        return data

    async def _load_chain(
        self, contract, snapshot: UnderlyingSnapshot, quote_date: date
    ) -> list[OptionQuote]:
        params = await marketdata.chain_parameters(self.client, contract)
        if params is None:
            return []

        dte_span = marketdata.dte_span_for(ALL_STRATEGIES)
        # One expiry per strategy window, not the three nearest across the merged span --
        # otherwise the 60-day and 90-day strategies never see a chain. Same request count.
        expiries = marketdata.select_expiries(
            params,
            quote_date=quote_date,
            dte_windows=marketdata.dte_windows_for(ALL_STRATEGIES),
            limit=3,
        )
        strikes = marketdata.select_strikes(params, snapshot.price)

        if not expiries or not strikes:
            logger.info(
                "%s: no expiries in %s DTE or no strikes near spot", snapshot.symbol, dte_span
            )
            return []

        quotes: list[OptionQuote] = []
        for expiry in expiries:
            quotes.extend(
                await marketdata.option_quotes(
                    self.client,
                    contract,
                    expiry=expiry,
                    strikes=strikes,
                    trading_class=getattr(params, "tradingClass", None),
                    multiplier=getattr(params, "multiplier", None) or "100",
                    underlying_price=snapshot.price,
                    dividend_yield=snapshot.dividend_yield,
                    quote_date=quote_date,
                )
            )
        return quotes

    # -- evaluation ---------------------------------------------------------

    def _verdicts_for(
        self, data: TickerData, strategy: Strategy, quote_date: date, regime
    ) -> list[StrategyVerdict]:
        candidates = builder.build(strategy, data.quotes, data.underlying, quote_date)
        return [
            evaluate(
                candidate,
                data.underlying,
                quote_date=quote_date,
                technicals=data.technicals,
                regime=regime,
            )
            for candidate in candidates
        ]

    async def scan_strategy(
        self,
        strategy: Strategy,
        symbols: Sequence[str],
        *,
        quote_date: date | None = None,
        shares: dict[str, int] | None = None,
        earnings: dict[str, date] | None = None,
    ) -> ScanReport:
        """View A -- one strategy across the watchlist."""
        quote_date = quote_date or date.today()
        report = ScanReport(quote_date=quote_date)
        regime = await self.regime()

        for symbol in symbols:
            data = await self.load_ticker(
                symbol,
                quote_date=quote_date,
                shares_owned=(shares or {}).get(symbol.upper(), 0),
                earnings_override=(earnings or {}).get(symbol.upper()),
            )
            if data is None:
                report.skipped[symbol.upper()] = "could not load market data"
                continue
            if not data.quotes:
                report.skipped[symbol.upper()] = "no option chain in the required DTE window"
                continue

            verdicts = self._verdicts_for(data, strategy, quote_date, regime)
            if not verdicts:
                report.skipped[symbol.upper()] = "no contracts near the target delta band"
            report.verdicts.extend(verdicts)

        report.verdicts.sort(key=_rank_key)
        return report

    async def scan_ticker(
        self,
        symbol: str,
        *,
        strategies: Iterable[Strategy] | None = None,
        quote_date: date | None = None,
        shares_owned: int = 0,
        earnings_override: date | None = None,
    ) -> ScanReport:
        """View B -- every strategy against one ticker."""
        quote_date = quote_date or date.today()
        report = ScanReport(quote_date=quote_date)
        regime = await self.regime()

        data = await self.load_ticker(
            symbol,
            quote_date=quote_date,
            shares_owned=shares_owned,
            earnings_override=earnings_override,
        )
        if data is None:
            report.skipped[symbol.upper()] = "could not load market data"
            return report

        for strategy in strategies or ALL_STRATEGIES:
            verdicts = self._verdicts_for(data, strategy, quote_date, regime)
            if not verdicts:
                report.skipped[f"{symbol.upper()}:{strategy.value}"] = (
                    "no contracts near the target delta band"
                )
                continue
            # One row per strategy in this view: the best candidate represents it.
            verdicts.sort(key=_rank_key)
            report.verdicts.append(verdicts[0])

        return report


def _rank_key(verdict: StrategyVerdict):
    """PASS first; then contracts whose legs sit where the notes place them; then fewest
    failed gates; then (debit spreads) the expiry nearest the ~30-day ideal; then higher
    confluence; then better ROI.

    Leg placement comes before the failure count because the notes choose the strike by delta
    first and only then check credit and size -- a 0.33-delta short call must not represent a
    0.20-0.25 strategy just because it happens to break one fewer of the other gates.
    """
    roi = verdict.metrics.get("roi") or verdict.metrics.get("roi_premium") or Decimal(0)
    try:
        roi_f = float(roi)
    except (TypeError, ValueError):
        roi_f = 0.0
    return (
        0 if verdict.verdict.value == "PASS" else 1,
        structural_failures(verdict.strategy, verdict.results),
        len(verdict.failures),
        _distance_from_ideal_dte(verdict),
        -(verdict.confluence.score if verdict.confluence else 0),
        -roi_f,
    )


def _distance_from_ideal_dte(verdict: StrategyVerdict) -> int:
    """Debit spreads: "30 days is ideal". Other strategies accept their whole window."""
    if verdict.strategy not in (Strategy.BULL_CALL, Strategy.BEAR_PUT):
        return 0
    dte = verdict.metrics.get("dte")
    return abs(int(dte) - config.DEBIT_SPREAD["target_dte"]) if dte is not None else 99
