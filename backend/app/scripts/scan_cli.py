"""Phase 2 -- the rules engine in the terminal, no web layer.

    python -m backend.app.scripts.scan_cli --strategy bull_put
    python -m backend.app.scripts.scan_cli --ticker AVGO
    python -m backend.app.scripts.scan_cli --strategy csp --symbols AAPL MSFT --all

Output follows the format CLAUDE.md's "Core purpose" section specifies: a verdict, then the
specific gates that decided it.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import select

from backend.app.db.models import SessionLocal, Watchlist, init_db, seed_watchlist
from backend.app.domain import Strategy, StrategyVerdict
from backend.app.ib.client import IBConnectionError, get_client
from backend.app.scan.runner import Scanner

logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(message)s")

ALIASES = {
    "csp": Strategy.CASH_SECURED_PUT,
    "cc": Strategy.COVERED_CALL,
    "bups": Strategy.BULL_PUT,
    "becs": Strategy.BEAR_CALL,
    "bucs": Strategy.BULL_CALL,
    "beps": Strategy.BEAR_PUT,
    "jl": Strategy.JADE_LIZARD,
    "lizard": Strategy.JADE_LIZARD,
    "synthetic": Strategy.SYNTHETIC_LONG,
    "syn": Strategy.SYNTHETIC_LONG,
    "rr": Strategy.SYNTHETIC_LONG,  # risk reversal, the notes' other name for it
}


def resolve(name: str) -> Strategy:
    key = name.lower().strip()
    if key in ALIASES:
        return ALIASES[key]
    return Strategy(key)


def watchlist_symbols() -> list[str]:
    with SessionLocal() as session:
        return [
            row.ticker
            for row in session.execute(
                select(Watchlist).where(Watchlist.active.is_(True)).order_by(Watchlist.ticker)
            )
            .scalars()
            .all()
        ]


def render(verdict: StrategyVerdict, show_all: bool) -> str:
    lines = [verdict.describe()]
    if verdict.confluence:
        c = verdict.confluence
        cap = " (capped: a mandatory layer failed)" if c.capped else ""
        lines.append(f"  Confluence {c.score}/5{cap} -- {c.size_guidance}")
        if show_all:
            for layer in c.layers:
                mark = "+" if layer.scored else "-"
                lines.append(f"     {mark} {layer.name}: {layer.detail}")
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    init_db()
    seed_watchlist()

    scanner = Scanner()
    try:
        await scanner.connect()
    except IBConnectionError as exc:
        print(f"\n{exc}\n")
        return 1

    status = get_client().status()
    print(f"\nMarket data: {status['market_data_label']}")
    if status["is_delayed"]:
        print("WARNING: DELAYED DATA -- Greeks may be missing and gates will fail accordingly.")

    with SessionLocal() as session:
        rows = {
            r.ticker: r
            for r in session.execute(select(Watchlist).where(Watchlist.active.is_(True)))
            .scalars()
            .all()
        }
    shares = {t: r.shares_owned for t, r in rows.items()}
    earnings = {t: r.earnings_date for t, r in rows.items() if r.earnings_date}

    if args.ticker:
        ticker = args.ticker.upper()
        print(f"\nEvaluating every strategy against {ticker}\n" + "=" * 72)
        report = await scanner.scan_ticker(
            ticker,
            shares_owned=shares.get(ticker, 0),
            earnings_override=earnings.get(ticker),
        )
    else:
        strategy = resolve(args.strategy)
        symbols = [s.upper() for s in (args.symbols or watchlist_symbols())]
        print(f"\n{strategy.label} across {len(symbols)} tickers\n" + "=" * 72)
        report = await scanner.scan_strategy(strategy, symbols, shares=shares, earnings=earnings)

    shown = 0
    for verdict in report.verdicts:
        if not args.all and verdict.verdict.value != "PASS" and shown >= args.max_fails:
            continue
        if verdict.verdict.value != "PASS":
            shown += 1
        print()
        print(render(verdict, args.all))

    print("\n" + "=" * 72)
    passes = sum(1 for v in report.verdicts if v.verdict.value == "PASS")
    print(f"{len(report.verdicts)} candidates | {passes} pass | {len(report.skipped)} skipped")

    for symbol, reason in report.skipped.items():
        print(f"  skipped {symbol}: {reason}")

    await get_client().disconnect()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Screen options and print verdicts")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--strategy",
        help="bull_put, bear_call, csp, covered_call, jade_lizard, synthetic_long, ...",
    )
    group.add_argument("--ticker", help="evaluate every strategy against one ticker")
    parser.add_argument("--symbols", nargs="*", help="override the watchlist")
    parser.add_argument("--all", action="store_true", help="show every row and every layer")
    parser.add_argument("--max-fails", type=int, default=5, help="failing rows to show")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
