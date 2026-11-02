"""IV percentile, computed locally.

The TWS socket API does not serve an IV-percentile field, so we derive it the way
ThinkorSwim defines it: the share of trading days in the trailing year on which implied
volatility closed *below* today's. IBKR will serve the input via
`reqHistoricalData(..., whatToShow="OPTION_IMPLIED_VOLATILITY")`, so no extra subscription
is needed, and the history accumulates in SQLite as a by-product.

Sanity anchor from planning: IBKR's own 52-week figure for AVGO was 0.0518, i.e. percentile 5,
against a raw IV of 38.6%. Any implementation here that does not land near 5 for that input
is wrong -- see backend/tests/test_iv_percentile.py.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import numpy as np

from backend.app import config


def iv_percentile(
    history: Sequence[float],
    current: float | None,
    *,
    window: int | None = None,
    min_bars: int | None = None,
) -> Decimal | None:
    """Percentile rank of `current` within `history`, on a 0-100 scale.

    Returns None -- never a guess -- when there is too little history to be meaningful.
    A thin sample would otherwise produce a confident-looking number that gates real trades.
    """
    if current is None:
        return None

    window = window or config.IV_HISTORY_DAYS
    min_bars = min_bars if min_bars is not None else config.IV_HISTORY_MIN_BARS

    series = np.asarray([v for v in history[-window:] if v is not None], dtype=float)
    series = series[~np.isnan(series)]

    if len(series) < min_bars:
        return None

    below = float(np.count_nonzero(series < current))
    pct = below / len(series) * 100.0
    return Decimal(str(round(pct, 2)))


def iv_rank(
    history: Sequence[float],
    current: float | None,
    *,
    window: int | None = None,
    min_bars: int | None = None,
) -> Decimal | None:
    """IV *rank*: where current sits between the year's low and high.

    Distinct from percentile and frequently confused with it -- rank asks "how far up the
    range", percentile asks "how many days were lower". Reported alongside so the difference
    is visible rather than argued about.
    """
    if current is None:
        return None

    window = window or config.IV_HISTORY_DAYS
    min_bars = min_bars if min_bars is not None else config.IV_HISTORY_MIN_BARS

    series = np.asarray([v for v in history[-window:] if v is not None], dtype=float)
    series = series[~np.isnan(series)]

    if len(series) < min_bars:
        return None

    lo, hi = float(series.min()), float(series.max())
    if hi == lo:
        return None
    return Decimal(str(round((current - lo) / (hi - lo) * 100.0, 2)))
