"""Assembling an UnderlyingSnapshot and an option chain from the gateway.

Request discipline matters here. An unbounded chain on a liquid name is several hundred
contracts, which will exceed IBKR's concurrent market-data-line ceiling and start returning
empty tickers with no error. So strikes are bounded to a window around spot before a single
option is requested, and every line is cancelled after it is read.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ib_async import Contract, Index, Option, Stock

from backend.app import config
from backend.app.domain import Bar, OptionQuote, Right, UnderlyingSnapshot
from backend.app.ib import fundamentals, history
from backend.app.ib.client import IBClient, _is_number
from backend.app.metrics.greeks import greeks_from_mark
from backend.app.metrics.iv_percentile import iv_percentile
from backend.app.sources import yahoo

logger = logging.getLogger(__name__)

# 100 = option volume, 101 = option open interest, 106 = implied volatility.
OPTION_TICKS = "100,101,106"
STOCK_TICKS = "165,456"  # 165 = misc stats (avg volume), 456 = dividends


async def qualify_stock(client: IBClient, symbol: str) -> Contract | None:
    contract = Stock(symbol.upper(), "SMART", "USD")
    qualified = await client.paced(
        lambda: client.ib.qualifyContractsAsync(contract), what=f"qualify {symbol}"
    )
    if not qualified:
        logger.warning("Could not qualify %s -- unknown symbol or no permissions", symbol)
        return None
    return qualified[0]


async def qualify_index(client: IBClient, symbol: str, exchange: str = "CBOE") -> Contract | None:
    contract = Index(symbol.upper(), exchange, "USD")
    qualified = await client.paced(
        lambda: client.ib.qualifyContractsAsync(contract), what=f"qualify index {symbol}"
    )
    return qualified[0] if qualified else None


# ---------------------------------------------------------------------------
# Underlying
# ---------------------------------------------------------------------------


async def underlying_snapshot(
    client: IBClient,
    symbol: str,
    *,
    contract: Contract | None = None,
    shares_owned: int = 0,
    earnings_override: date | None = None,
    fetch_fundamentals: bool = True,
) -> tuple[UnderlyingSnapshot | None, Contract | None]:
    """Everything the rules need about the stock, plus the qualified contract for reuse."""
    contract = contract or await qualify_stock(client, symbol)
    if contract is None:
        return None, None

    ticker = await client.snapshot(contract, STOCK_TICKS)

    price = _pick_price(ticker)
    bars = await history.daily_bars(client, contract)
    ivs = await history.iv_history(client, contract)
    hv = await history.historical_volatility(client, contract)

    current_iv = ivs[-1] if ivs else None
    pctile = iv_percentile(ivs, current_iv)

    market_cap = sector = None
    earnings: date | None = earnings_override
    earnings_source = "manual" if earnings_override else "unknown"

    if fetch_fundamentals and config.FUNDAMENTALS_SOURCE == "yahoo":
        yf_data = await yahoo.fundamentals(symbol)
        market_cap, sector = yf_data.market_cap, yf_data.sector
        if earnings_override is None:
            earnings, earnings_source = yf_data.earnings_date, yf_data.earnings_source
    elif fetch_fundamentals:
        market_cap, sector = await fundamentals.company_snapshot(client, contract)
        if earnings_override is None:
            earnings, earnings_source = await fundamentals.next_earnings_date(client, contract)

    snapshot = UnderlyingSnapshot(
        symbol=symbol.upper(),
        price=price,
        bid=_dec(getattr(ticker, "bid", None)),
        ask=_dec(getattr(ticker, "ask", None)),
        volume=_int(getattr(ticker, "volume", None)),
        avg_volume=_avg_volume(ticker, bars),
        market_cap=market_cap,
        sector=sector,
        dividend_yield=_dividend_yield(ticker, price),
        iv=current_iv,
        iv_percentile=pctile,
        iv_percentile_source="computed:OPTION_IMPLIED_VOLATILITY" if pctile is not None else None,
        historical_vol=hv,
        earnings_date=earnings,
        earnings_source=earnings_source,
        earnings_is_stale=False,
        shares_owned=shares_owned,
        bars=bars,
    )
    return snapshot, contract


async def index_bars(client: IBClient, symbol: str | None = None) -> list[Bar]:
    """Daily bars for the market-regime check. SPY is the default proxy for SPX."""
    symbol = symbol or config.REGIME_SYMBOL
    contract = await qualify_stock(client, symbol)
    if contract is None:
        contract = await qualify_index(client, symbol)
    if contract is None:
        return []
    return await history.daily_bars(client, contract)


# ---------------------------------------------------------------------------
# Chain
# ---------------------------------------------------------------------------


async def chain_parameters(client: IBClient, contract: Contract) -> Any:
    """The SMART chain for the symbol's own trading class, ignoring odd classes."""
    chains = await client.paced(
        lambda: client.ib.reqSecDefOptParamsAsync(
            contract.symbol, "", contract.secType, contract.conId
        ),
        what=f"chain params {contract.symbol}",
    )
    if not chains:
        return None

    exact = [c for c in chains if c.exchange == "SMART" and c.tradingClass == contract.symbol]
    smart = [c for c in chains if c.exchange == "SMART"]
    return (exact or smart or chains)[0]


def select_expiries(
    params: Any,
    *,
    quote_date: date,
    dte_range: tuple[int, int] | None = None,
    dte_windows: Sequence[tuple[int, int]] | None = None,
    limit: int = 2,
) -> list[str]:
    """Expiries to fetch, nearest first.

    With `dte_range`, every expiry in that one span, nearest first -- the original behaviour.

    With `dte_windows`, one expiry per window: the nearest expiry that falls inside each
    strategy's own DTE range. Windows overlap heavily (four strategies want 30-45 days), so
    this usually resolves to two or three distinct dates and costs no more chain requests than
    the flat version -- but it does not leave the 60-day and 90-day strategies with nothing to
    screen just because near-dated weeklies got there first.
    """
    if params is None:
        return []

    dated: list[tuple[int, str]] = []
    for raw in sorted(params.expirations):
        parsed = _parse_expiry(raw)
        if parsed is not None:
            dated.append(((parsed - quote_date).days, raw))
    dated.sort()

    if dte_windows:
        picked: list[str] = []
        for low, high in sorted(dte_windows):
            nearest = next((raw for dte, raw in dated if low <= dte <= high), None)
            if nearest is not None and nearest not in picked:
                picked.append(nearest)
        picked.sort()
        if len(picked) <= limit:
            return picked
        # Over budget: thin the list evenly rather than truncating, which would drop exactly
        # the long-dated expiries the windows were introduced to protect.
        step = (len(picked) - 1) / (limit - 1) if limit > 1 else 0
        return [picked[round(i * step)] for i in range(limit)]

    low, high = dte_range if dte_range else (0, 10_000)
    return [raw for dte, raw in dated if low <= dte <= high][:limit]


def select_strikes(params: Any, spot: Decimal | None) -> list[float]:
    """Strikes within the configured window of spot. Unbounded chains are the enemy here."""
    if params is None or spot is None:
        return []
    window = spot * config.STRIKE_WINDOW_PCT
    low, high = float(spot - window), float(spot + window)
    return sorted(s for s in params.strikes if low <= s <= high)


async def option_quotes(
    client: IBClient,
    contract: Contract,
    *,
    expiry: str,
    strikes: Sequence[float],
    rights: Sequence[str] = ("P", "C"),
    trading_class: str | None = None,
    multiplier: str = "100",
    underlying_price: Decimal | None = None,
    dividend_yield: Decimal | None = None,
    quote_date: date | None = None,
) -> list[OptionQuote]:
    """Qualify and quote every strike/right for one expiry, sequentially.

    `underlying_price`, `dividend_yield` and `quote_date` feed the local Greek fallback used
    when the feed is delayed and IBKR sends no modelGreeks. Leave them None to disable it.
    """
    if not strikes or not rights:
        return []

    # The chain's strike list is the union across all expiries, so guessing contracts from it
    # asks for strikes this expiry does not list (error 200 per miss). One contract-details
    # request returns exactly what exists for the expiry, already qualified.
    template = Option(
        contract.symbol,
        expiry,
        exchange="SMART",
        multiplier=multiplier,
        currency="USD",
        tradingClass=trading_class or contract.symbol,
    )
    details = await client.paced(
        lambda: client.ib.reqContractDetailsAsync(template),
        what=f"contract details {contract.symbol} {expiry}",
    )
    qualified = listed_contracts(details, strikes, rights)

    expiry_date = _parse_expiry(expiry)
    if expiry_date is None:
        return []

    to_quote = qualified[: config.MAX_CONCURRENT_MD_LINES]
    tickers = await client.snapshots(to_quote, OPTION_TICKS)

    out: list[OptionQuote] = []
    for opt, ticker in zip(to_quote, tickers, strict=True):
        quote = _to_quote(ticker, opt, expiry_date)
        if quote is None:
            continue
        if quote.underlying_price is None:
            quote.underlying_price = underlying_price
        if quote.greeks_source is None and config.COMPUTE_GREEKS_WHEN_MISSING:
            fill_greeks_from_mark(quote, dividend_yield, quote_date or date.today())
        out.append(quote)
    return out


def listed_contracts(
    details: Sequence[Any] | None, strikes: Sequence[float], rights: Sequence[str]
) -> list[Contract]:
    """The listed contracts among the wanted strikes/rights, ordered by strike then right."""
    wanted = {round(float(s), 4) for s in strikes}
    order = {r: i for i, r in enumerate(rights)}
    found = [
        d.contract
        for d in details or []
        if getattr(d.contract, "conId", 0)
        and d.contract.right in order
        and round(float(d.contract.strike), 4) in wanted
    ]
    return sorted(found, key=lambda c: (c.strike, order[c.right]))


def fill_greeks_from_mark(
    quote: OptionQuote, dividend_yield: Decimal | None, quote_date: date
) -> bool:
    """Back Greeks out of the mid with Black-Scholes-Merton. Returns True if it managed to.

    Only ever called when the broker sent nothing, and always tags the quote so the UI can
    say "computed" rather than let a model number pass for a broker one.
    """
    dte = (quote.expiry - quote_date).days
    greeks = greeks_from_mark(
        quote.right,
        quote.mark,
        quote.underlying_price,
        quote.strike,
        dte,
        rate=config.RISK_FREE_RATE,
        dividend_yield=dividend_yield,
    )
    if greeks is None:
        return False
    quote.delta = greeks.delta
    quote.gamma = greeks.gamma
    quote.vega = greeks.vega
    quote.theta = greeks.theta
    quote.iv = greeks.iv
    quote.greeks_source = "computed_bsm"
    return True


def _to_quote(ticker: Any, opt: Contract, expiry: date) -> OptionQuote | None:
    if ticker is None:
        return None

    greeks = getattr(ticker, "modelGreeks", None)
    # A modelGreeks object with a NaN delta is what a delayed feed actually sends; treat it
    # as absent so the fallback engages rather than leaving a None delta tagged "ibkr_model".
    has_greeks = greeks is not None and _float(getattr(greeks, "delta", None)) is not None
    return OptionQuote(
        symbol=opt.symbol,
        expiry=expiry,
        strike=Decimal(str(opt.strike)),
        right=Right.CALL if opt.right.upper().startswith("C") else Right.PUT,
        bid=_dec(getattr(ticker, "bid", None)),
        ask=_dec(getattr(ticker, "ask", None)),
        last=_dec(getattr(ticker, "last", None)),
        delta=_float(getattr(greeks, "delta", None)) if greeks else None,
        gamma=_float(getattr(greeks, "gamma", None)) if greeks else None,
        vega=_float(getattr(greeks, "vega", None)) if greeks else None,
        theta=_float(getattr(greeks, "theta", None)) if greeks else None,
        iv=_float(getattr(greeks, "impliedVol", None)) if greeks else None,
        open_interest=_open_interest(ticker, opt.right),
        volume=_int(getattr(ticker, "volume", None)),
        underlying_price=_dec(getattr(greeks, "undPrice", None)) if greeks else None,
        greeks_source="ibkr_model" if has_greeks else None,
    )


# ---------------------------------------------------------------------------
# Field coercion. IBKR signals "no data" with NaN and -1; neither becomes a number here.
# ---------------------------------------------------------------------------


def _dec(value: Any) -> Decimal | None:
    if not _is_number(value):
        return None
    return Decimal(str(round(float(value), 6)))


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # drop NaN


def _int(value: Any) -> int | None:
    if not _is_number(value):
        return None
    return int(float(value))


def _pick_price(ticker: Any) -> Decimal | None:
    """Last trade, else the midpoint, else the prior close. Never one side of the book."""
    for attr in ("last", "close"):
        value = _dec(getattr(ticker, attr, None))
        if value is not None:
            return value
    bid, ask = _dec(getattr(ticker, "bid", None)), _dec(getattr(ticker, "ask", None))
    if bid is not None and ask is not None:
        return (bid + ask) / Decimal(2)
    return None


def _open_interest(ticker: Any, right: str) -> int | None:
    attr = "callOpenInterest" if right.upper().startswith("C") else "putOpenInterest"
    return _int(getattr(ticker, attr, None))


def _avg_volume(ticker: Any, bars: Sequence[Bar]) -> int | None:
    """Prefer IBKR's own average; fall back to a 30-day mean of the bars we already have."""
    reported = _int(getattr(ticker, "avVolume", None))
    if reported:
        return reported * 100 if reported < 100_000 else reported
    return history.average_volume(bars)


def _dividend_yield(ticker: Any, price: Decimal | None) -> Decimal | None:
    """Forward 12-month dividend over price, as a fraction."""
    dividends = getattr(ticker, "dividends", None)
    if dividends is None or price in (None, 0):
        return None
    forward = getattr(dividends, "next12Months", None)
    if not _is_number(forward):
        return None
    return Decimal(str(forward)) / price


def _parse_expiry(raw: str) -> date | None:
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def dte_windows_for(strategies: Sequence[Any]) -> list[tuple[int, int]]:
    """Every strategy's own DTE window, as distinct ranges rather than one merged span.

    Kept separate on purpose. Merging them produces 20-120 days, and asking for the three
    nearest expiries in that span returns three near-dated ones -- which serves the 30-45 day
    strategies and silently starves the long-dated ones (long options at 60+, the synthetic
    spread at 30-90). `select_expiries` uses these windows to pick one expiry per window
    instead, which covers every strategy without fetching any more chains than before.
    """
    return [
        config.BULL_PUT["dte"],
        config.BEAR_CALL["dte"],
        config.CASH_SECURED_PUT["dte"],
        config.COVERED_CALL["dte"],
        (
            config.DEBIT_SPREAD["target_dte"] - config.DEBIT_SPREAD["dte_tolerance"],
            config.DEBIT_SPREAD["target_dte"] + config.DEBIT_SPREAD["dte_tolerance"],
        ),
        (config.LONG_OPTION["min_dte"], config.LONG_OPTION["min_dte"] + 60),
        config.JADE_LIZARD["dte"],
        config.SYNTHETIC["dte"],
    ]


def dte_span_for(strategies: Sequence[Any]) -> tuple[int, int]:
    """The outer bounds of every DTE window in play."""
    spans = dte_windows_for(strategies)
    return min(s[0] for s in spans), max(s[1] for s in spans)
