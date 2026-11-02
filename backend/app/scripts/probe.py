"""Phase 1 -- prove the data pipeline before anything else is trusted.

Pulls one option chain and prints it next to the underlying's computed IV percentile. Open the
same chain in TWS and compare: bid, ask, delta and IV must match on screen. Nothing downstream
means anything until they do.

    python -m backend.app.scripts.probe AVGO
    python -m backend.app.scripts.probe AVGO --dte 30 45 --no-fundamentals
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date

from backend.app.ib import history, marketdata
from backend.app.ib.client import IBConnectionError, get_client
from backend.app.metrics.iv_percentile import iv_percentile, iv_rank

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")


def fmt(value, places: int = 2, width: int = 9) -> str:
    if value is None:
        return "--".rjust(width)
    try:
        return f"{float(value):>{width},.{places}f}"
    except (TypeError, ValueError):
        return str(value).rjust(width)


async def probe(symbol: str, dte_range: tuple[int, int], fundamentals: bool) -> int:
    client = get_client()
    try:
        await client.connect()
    except IBConnectionError as exc:
        print(f"\n{exc}\n")
        return 1

    status = client.status()
    print(f"\nConnected. Market data: {status['market_data_label']} (read-only session)")
    if status["is_delayed"]:
        print(
            "WARNING: delayed data. The socket API does not reliably return Greeks on a "
            "delayed feed -- expect blank deltas."
        )

    today = date.today()
    print(f"\nLoading {symbol}...")
    snapshot, contract = await marketdata.underlying_snapshot(
        client, symbol, fetch_fundamentals=fundamentals
    )
    if snapshot is None or contract is None:
        print(f"Could not resolve {symbol}.")
        return 1

    ivs = await history.iv_history(client, contract)
    current_iv = ivs[-1] if ivs else None

    print("\n" + "=" * 78)
    print(f"{symbol.upper()}  underlying")
    print("=" * 78)
    print(f"  Price              {fmt(snapshot.price)}")
    print(f"  Bid / Ask          {fmt(snapshot.bid)} /{fmt(snapshot.ask)}")
    print(f"  Volume             {fmt(snapshot.volume, 0)}")
    print(f"  Avg volume         {fmt(snapshot.avg_volume, 0)}")
    print(f"  Market cap         {fmt(snapshot.market_cap, 0, 15)}")
    print(f"  Sector             {snapshot.sector or '--'}")
    print(f"  Dividend yield     {fmt(snapshot.dividend_yield, 4)}")
    print(f"  Earnings           {snapshot.earnings_date or 'UNKNOWN -- verify manually'}")
    print(f"  Daily bars         {len(snapshot.bars)}")

    print("\n  -- volatility ------------------------------------------------------")
    print(f"  Raw IV (30d)       {fmt(current_iv, 4)}   ({len(ivs)} IV bars)")
    print(f"  Historical vol     {fmt(snapshot.historical_vol, 4)}")
    print(f"  IV percentile      {fmt(iv_percentile(ivs, current_iv), 1)}")
    print(f"  IV rank            {fmt(iv_rank(ivs, current_iv), 1)}")
    print(
        "\n  Cross-check the percentile against TWS or IBKR's own figure. If they disagree\n"
        "  materially, the IV history window is wrong -- do not proceed to phase 2."
    )

    params = await marketdata.chain_parameters(client, contract)
    expiries = marketdata.select_expiries(params, quote_date=today, dte_range=dte_range, limit=1)
    strikes = marketdata.select_strikes(params, snapshot.price)

    if not expiries:
        print(f"\nNo expiry between {dte_range[0]} and {dte_range[1]} DTE.")
        return 1

    expiry = expiries[0]
    print(f"\nChain {expiry}  ({len(strikes)} strikes within the spot window)")
    quotes = await marketdata.option_quotes(
        client,
        contract,
        expiry=expiry,
        strikes=strikes,
        trading_class=getattr(params, "tradingClass", None),
        underlying_price=snapshot.price,
        dividend_yield=snapshot.dividend_yield,
        quote_date=today,
    )

    print("=" * 78)
    print(
        f"{'Strike':>8} {'R':>2} {'Bid':>9} {'Ask':>9} {'Mark':>9} "
        f"{'Delta':>8} {'Theta':>8} {'IV':>7} {'OI':>7} {'Greeks':>9}"
    )
    print("-" * 78)
    for q in sorted(quotes, key=lambda q: (q.right.value, q.strike)):
        print(
            f"{fmt(q.strike, 2, 8)} {q.right.value:>2} {fmt(q.bid)} {fmt(q.ask)} "
            f"{fmt(q.mark)} {fmt(q.delta, 4, 8)} {fmt(q.theta, 4, 8)} "
            f"{fmt(q.iv, 3, 7)} {fmt(q.open_interest, 0, 7)} "
            f"{_greeks_tag(q.greeks_source):>9}"
        )

    missing_greeks = sum(1 for q in quotes if q.delta is None)
    computed = sum(1 for q in quotes if q.greeks_source == "computed_bsm")
    print("-" * 78)
    print(
        f"{len(quotes)} contracts, {computed} with locally computed Greeks, "
        f"{missing_greeks} without Greeks"
    )
    if computed:
        print(
            "'computed' = Black-Scholes from the delayed mid, not IBKR's model. Good enough to\n"
            "screen a delta band; expect small differences from what TWS shows."
        )
    if missing_greeks:
        print(
            "Contracts without Greeks will fail their delta gate rather than pass silently.\n"
            "Usually the mid was missing or below intrinsic; if the count is high, check the\n"
            "OPRA options subscription."
        )

    await client.disconnect()
    return 0


def _greeks_tag(source: str | None) -> str:
    return {"ibkr_model": "ibkr", "computed_bsm": "computed"}.get(source or "", "-")


def main() -> int:
    parser = argparse.ArgumentParser(description="Dump one option chain for eyeball comparison")
    parser.add_argument("symbol")
    parser.add_argument("--dte", nargs=2, type=int, default=[30, 45], metavar=("LOW", "HIGH"))
    parser.add_argument("--no-fundamentals", action="store_true")
    args = parser.parse_args()

    return asyncio.run(
        probe(args.symbol.upper(), (args.dte[0], args.dte[1]), not args.no_fundamentals)
    )


if __name__ == "__main__":
    raise SystemExit(main())
