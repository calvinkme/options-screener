"""The single evaluation function both views call.

Strategy-first and ticker-first are two loops over this one function, which is what keeps the
two screens from ever disagreeing about the same trade. backend/tests/test_cross_view.py
asserts that property directly.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from backend.app.domain import StrategyVerdict, UnderlyingSnapshot
from backend.app.metrics.indicators import TechnicalPicture
from backend.app.rules.base import RuleContext
from backend.app.rules.registry import spec_for
from backend.app.rules.selection import guide_to_dict, selection_for
from backend.app.scan.builder import Candidate
from backend.app.scoring import confluence


def evaluate(
    candidate: Candidate,
    underlying: UnderlyingSnapshot,
    *,
    quote_date: date,
    technicals: TechnicalPicture | None = None,
    regime: TechnicalPicture | None = None,
    net_liquidation: Decimal | None = None,
    cash_available: Decimal | None = None,
) -> StrategyVerdict:
    """Run every rule for the strategy and score the chart, returning one verdict."""
    spec = spec_for(candidate.strategy)

    ctx_kwargs: dict[str, Any] = {
        "strategy": candidate.strategy,
        "symbol": candidate.symbol,
        "legs": candidate.legs,
        "metrics": candidate.metrics,
        "underlying": underlying,
        "dte": candidate.dte,
        "quote_date": quote_date,
        "technicals": technicals,
        "regime": regime,
    }
    if net_liquidation is not None:
        ctx_kwargs["net_liquidation"] = net_liquidation
    if cash_available is not None:
        ctx_kwargs["cash_available"] = cash_available

    ctx = RuleContext(**ctx_kwargs)

    results = [rule(ctx) for rule in spec.rules]
    score = confluence.score(technicals, bullish=spec.bullish)

    return StrategyVerdict(
        strategy=candidate.strategy,
        symbol=candidate.symbol,
        legs=candidate.legs,
        metrics=candidate.metrics,
        results=results,
        confluence=score,
        underlying=underlying,
    )


# ---------------------------------------------------------------------------
# Serialisation for the API / UI
# ---------------------------------------------------------------------------


def _plain(value: Any) -> Any:
    """Decimals to float, dates to ISO strings, everything else untouched."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def verdict_to_dict(verdict: StrategyVerdict) -> dict[str, Any]:
    metrics = {k: _plain(v) for k, v in verdict.metrics.items()}

    return {
        "strategy": verdict.strategy.value,
        "strategy_label": verdict.strategy.label,
        "symbol": verdict.symbol,
        "verdict": verdict.verdict.value,
        "metrics": metrics,
        "confluence": (
            {
                "score": verdict.confluence.score,
                "raw_score": verdict.confluence.raw_score,
                "capped": verdict.confluence.capped,
                "guidance": verdict.confluence.size_guidance,
                "layers": [
                    {"name": lay.name, "scored": lay.scored, "detail": lay.detail}
                    for lay in verdict.confluence.layers
                ],
            }
            if verdict.confluence
            else None
        ),
        "results": [
            {
                "rule_id": r.rule_id,
                "label": r.label,
                "passed": r.passed,
                "actual": _plain(r.actual),
                "required": r.required,
                "message": r.message,
                "advisory": r.advisory,
                "blocking": r.is_blocking,
            }
            for r in verdict.results
        ],
        # The course-notes rule for each part of the contract, what was picked, and whether
        # the pick obeys it -- so the choice can be audited against the notes row by row.
        "selection": selection_for(verdict.strategy, verdict.results),
        "selection_guide": guide_to_dict(verdict.strategy),
        "failures": [r.message for r in verdict.failures],
        "warnings": [r.message for r in verdict.warnings],
        "legs": [
            {
                "strike": float(leg.quote.strike),
                "right": leg.quote.right.value,
                "qty": leg.qty,
                "expiry": leg.quote.expiry.isoformat(),
                "bid": _plain(leg.quote.bid),
                "ask": _plain(leg.quote.ask),
                "mark": _plain(leg.quote.mark),
                "delta": leg.quote.delta,
                "theta": leg.quote.theta,
                "iv": leg.quote.iv,
                "greeks_source": leg.quote.greeks_source,
                "open_interest": leg.quote.open_interest,
                "volume": leg.quote.volume,
            }
            for leg in verdict.legs
        ],
        "underlying": {
            "symbol": verdict.underlying.symbol,
            "price": _plain(verdict.underlying.price),
            "iv": verdict.underlying.iv,
            "iv_percentile": _plain(verdict.underlying.iv_percentile),
            "iv_percentile_source": verdict.underlying.iv_percentile_source,
            "historical_vol": verdict.underlying.historical_vol,
            "sector": verdict.underlying.sector,
            "market_cap": _plain(verdict.underlying.market_cap),
            "avg_volume": verdict.underlying.avg_volume,
            "dividend_yield": _plain(verdict.underlying.dividend_yield),
            "earnings_date": _plain(verdict.underlying.earnings_date),
            "earnings_source": verdict.underlying.earnings_source,
            "earnings_is_stale": verdict.underlying.earnings_is_stale,
            "shares_owned": verdict.underlying.shares_owned,
        },
    }
