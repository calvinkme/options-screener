"""Chart indicators, computed from daily bars.

Answering the question behind the build: all of these are closed-form arithmetic over OHLCV
data, so none of them is subjective. The single genuinely judgemental item -- "retracement to
support on an uptrend" -- is reduced here to two measured numbers (distance to the nearest
support level in ATR units, and how many times that level has been touched) and reported as
advisory. The tool measures; the trader still judges.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from backend.app import config
from backend.app.domain import Bar

# ---------------------------------------------------------------------------
# Moving averages
# ---------------------------------------------------------------------------


def sma(values: Sequence[float], period: int) -> float | None:
    if len(values) < period or period <= 0:
        return None
    return float(np.mean(np.asarray(values[-period:], dtype=float)))


def ema_series(values: Sequence[float], period: int) -> np.ndarray | None:
    """Standard Wilder-free EMA with a 2/(n+1) multiplier, seeded on the first SMA."""
    if len(values) < period or period <= 0:
        return None
    arr = np.asarray(values, dtype=float)
    k = 2.0 / (period + 1.0)
    out = np.empty_like(arr)
    out[: period - 1] = np.nan
    out[period - 1] = arr[:period].mean()
    for i in range(period, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out


def ema(values: Sequence[float], period: int) -> float | None:
    series = ema_series(values, period)
    if series is None:
        return None
    tail = series[-1]
    return None if np.isnan(tail) else float(tail)


# ---------------------------------------------------------------------------
# Oscillators
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Stochastic:
    k: float
    d: float
    oversold: bool
    overbought: bool
    bullish_cross: bool
    bearish_cross: bool


def stochastic(bars: Sequence[Bar]) -> Stochastic | None:
    """Slow stochastic (14, 3, 3) -- CLAUDE.md momentum layer."""
    cfg = config.STOCHASTIC
    need = cfg["k"] + cfg["k_smooth"] + cfg["d"]
    if len(bars) < need:
        return None

    highs = np.asarray([b.high for b in bars], dtype=float)
    lows = np.asarray([b.low for b in bars], dtype=float)
    closes = np.asarray([b.close for b in bars], dtype=float)

    n = cfg["k"]
    raw_k = np.full(len(bars), np.nan)
    for i in range(n - 1, len(bars)):
        hh = highs[i - n + 1 : i + 1].max()
        ll = lows[i - n + 1 : i + 1].min()
        raw_k[i] = 50.0 if hh == ll else (closes[i] - ll) / (hh - ll) * 100.0

    k_line = _rolling_mean(raw_k, cfg["k_smooth"])
    d_line = _rolling_mean(k_line, cfg["d"])

    if np.isnan(k_line[-1]) or np.isnan(d_line[-1]) or np.isnan(k_line[-2]) or np.isnan(d_line[-2]):
        return None

    k_now, d_now = float(k_line[-1]), float(d_line[-1])
    k_prev, d_prev = float(k_line[-2]), float(d_line[-2])

    return Stochastic(
        k=k_now,
        d=d_now,
        oversold=k_now < cfg["oversold"],
        overbought=k_now > cfg["overbought"],
        bullish_cross=k_prev <= d_prev and k_now > d_now,
        bearish_cross=k_prev >= d_prev and k_now < d_now,
    )


def _rolling_mean(arr: np.ndarray, window: int) -> np.ndarray:
    out = np.full_like(arr, np.nan)
    for i in range(len(arr)):
        chunk = arr[max(0, i - window + 1) : i + 1]
        if len(chunk) == window and not np.isnan(chunk).any():
            out[i] = chunk.mean()
    return out


@dataclass(slots=True)
class Macd:
    macd: float
    signal: float
    histogram: float
    bullish_cross: bool
    bearish_cross: bool


def macd(closes: Sequence[float]) -> Macd | None:
    """MACD 12/26/9 -- CLAUDE.md momentum layer."""
    cfg = config.MACD
    if len(closes) < cfg["slow"] + cfg["signal"]:
        return None

    fast = ema_series(closes, cfg["fast"])
    slow = ema_series(closes, cfg["slow"])
    if fast is None or slow is None:
        return None

    line = fast - slow
    valid = line[~np.isnan(line)]
    if len(valid) < cfg["signal"] + 1:
        return None

    signal_series = ema_series(valid.tolist(), cfg["signal"])
    if signal_series is None or np.isnan(signal_series[-1]) or np.isnan(signal_series[-2]):
        return None

    m_now, m_prev = float(valid[-1]), float(valid[-2])
    s_now, s_prev = float(signal_series[-1]), float(signal_series[-2])

    return Macd(
        macd=m_now,
        signal=s_now,
        histogram=m_now - s_now,
        bullish_cross=m_prev <= s_prev and m_now > s_now,
        bearish_cross=m_prev >= s_prev and m_now < s_now,
    )


# ---------------------------------------------------------------------------
# Volatility
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Bollinger:
    upper: float
    middle: float
    lower: float
    bandwidth: float
    tagged_upper: bool
    tagged_lower: bool
    squeeze: bool


def bollinger(bars: Sequence[Bar]) -> Bollinger | None:
    """Bollinger (20, 2 sigma). A squeeze is bandwidth in the lowest quartile of the last year."""
    cfg = config.BOLLINGER
    period = cfg["period"]
    if len(bars) < period:
        return None

    closes = np.asarray([b.close for b in bars], dtype=float)
    window = closes[-period:]
    middle = float(window.mean())
    sd = float(window.std(ddof=0))
    upper = middle + cfg["std"] * sd
    lower = middle - cfg["std"] * sd
    bandwidth = (upper - lower) / middle if middle else 0.0

    history = []
    for i in range(period, len(closes) + 1):
        w = closes[i - period : i]
        m, s = w.mean(), w.std(ddof=0)
        if m:
            history.append((2 * cfg["std"] * s) / m)

    squeeze = bool(history) and bandwidth <= float(np.percentile(history, 25))
    last = bars[-1]

    return Bollinger(
        upper=upper,
        middle=middle,
        lower=lower,
        bandwidth=bandwidth,
        tagged_upper=last.high >= upper,
        tagged_lower=last.low <= lower,
        squeeze=squeeze,
    )


def atr(bars: Sequence[Bar], period: int | None = None) -> float | None:
    """Average true range -- the unit we express support distance in."""
    period = period or config.ATR_PERIOD
    if len(bars) < period + 1:
        return None

    trs = []
    for prev, cur in zip(bars[:-1], bars[1:], strict=True):
        trs.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    return float(np.mean(trs[-period:]))


# ---------------------------------------------------------------------------
# Structure: support / resistance
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Level:
    price: float
    touches: int
    kind: str  # "support" or "resistance"


@dataclass(slots=True)
class Structure:
    levels: list[Level]
    nearest_support: Level | None
    nearest_resistance: Level | None
    distance_to_support_atr: float | None
    distance_to_resistance_atr: float | None
    at_level: bool


def find_levels(bars: Sequence[Bar]) -> list[Level]:
    """Pivot highs/lows clustered into levels, counting how many times each was touched.

    CLAUDE.md asks for "a support/resistance level, >=3 prior touches". A pivot is a bar whose
    high (or low) is the extreme of a window PIVOT_LOOKBACK bars either side; pivots within
    SR_CLUSTER_ATR of each other are the same level, and the touch count is the cluster size.
    """
    lb = config.PIVOT_LOOKBACK
    if len(bars) < 2 * lb + 1:
        return []

    band = atr(bars)
    if not band:
        return []
    tolerance = band * config.SR_CLUSTER_ATR

    pivot_highs: list[float] = []
    pivot_lows: list[float] = []
    for i in range(lb, len(bars) - lb):
        window = bars[i - lb : i + lb + 1]
        if bars[i].high >= max(b.high for b in window):
            pivot_highs.append(bars[i].high)
        if bars[i].low <= min(b.low for b in window):
            pivot_lows.append(bars[i].low)

    return _cluster(pivot_lows, tolerance, "support") + _cluster(
        pivot_highs, tolerance, "resistance"
    )


def _cluster(prices: list[float], tolerance: float, kind: str) -> list[Level]:
    levels: list[Level] = []
    for price in sorted(prices):
        for existing in levels:
            if abs(existing.price - price) <= tolerance:
                total = existing.price * existing.touches + price
                existing.touches += 1
                existing.price = total / existing.touches
                break
        else:
            levels.append(Level(price=price, touches=1, kind=kind))
    return [lv for lv in levels if lv.touches >= config.SR_MIN_TOUCHES]


def structure(bars: Sequence[Bar]) -> Structure | None:
    if not bars:
        return None
    band = atr(bars)
    levels = find_levels(bars)
    price = bars[-1].close

    supports = [lv for lv in levels if lv.kind == "support" and lv.price <= price]
    resistances = [lv for lv in levels if lv.kind == "resistance" and lv.price >= price]

    nearest_support = max(supports, key=lambda lv: lv.price) if supports else None
    nearest_resistance = min(resistances, key=lambda lv: lv.price) if resistances else None

    d_sup = abs(price - nearest_support.price) / band if nearest_support and band else None
    d_res = abs(nearest_resistance.price - price) / band if nearest_resistance and band else None

    at_level = any(d is not None and d <= config.SR_NEAR_ATR for d in (d_sup, d_res))

    return Structure(
        levels=levels,
        nearest_support=nearest_support,
        nearest_resistance=nearest_resistance,
        distance_to_support_atr=d_sup,
        distance_to_resistance_atr=d_res,
        at_level=at_level,
    )


# ---------------------------------------------------------------------------
# Trigger candles -- CLAUDE.md defines these numerically, so they are exact
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CandleSignal:
    pin_bar: bool
    bullish_engulfing: bool
    bearish_engulfing: bool
    detail: str


def candle_signal(bars: Sequence[Bar]) -> CandleSignal | None:
    """Pin bar: "body small, one shadow >= 2/3 of range". Engulfing: body covers the prior body."""
    if len(bars) < 2:
        return None

    prev, cur = bars[-2], bars[-1]
    rng = cur.high - cur.low
    if rng <= 0:
        return CandleSignal(False, False, False, "doji / zero range")

    body = abs(cur.close - cur.open)
    upper_shadow = cur.high - max(cur.open, cur.close)
    lower_shadow = min(cur.open, cur.close) - cur.low

    pin = body / rng <= config.PIN_BAR_MAX_BODY_PCT and (
        upper_shadow / rng >= config.PIN_BAR_MIN_SHADOW_PCT
        or lower_shadow / rng >= config.PIN_BAR_MIN_SHADOW_PCT
    )

    prev_body_low, prev_body_high = sorted((prev.open, prev.close))
    bull_engulf = (
        cur.close > cur.open
        and prev.close < prev.open
        and cur.close >= prev_body_high
        and cur.open <= prev_body_low
    )
    bear_engulf = (
        cur.close < cur.open
        and prev.close > prev.open
        and cur.open >= prev_body_high
        and cur.close <= prev_body_low
    )

    parts = []
    if pin:
        parts.append("pin bar")
    if bull_engulf:
        parts.append("bullish engulfing")
    if bear_engulf:
        parts.append("bearish engulfing")

    return CandleSignal(pin, bull_engulf, bear_engulf, ", ".join(parts) or "no trigger")


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TechnicalPicture:
    ema_fast: float | None
    ema_slow: float | None
    sma: dict[int, float | None]
    stochastic: Stochastic | None
    macd: Macd | None
    bollinger: Bollinger | None
    atr: float | None
    structure: Structure | None
    candles: CandleSignal | None

    @property
    def ema_bullish(self) -> bool | None:
        if self.ema_fast is None or self.ema_slow is None:
            return None
        return self.ema_fast > self.ema_slow

    @property
    def ma_bullish(self) -> bool | None:
        fast = self.sma.get(config.REGIME_FAST_MA)
        slow = self.sma.get(config.REGIME_SLOW_MA)
        if fast is None or slow is None:
            return None
        return fast > slow

    @property
    def fan_bullish(self) -> bool | None:
        """MA fan ordering: 50 > 100 > 150 > 200 -- CLAUDE.md confluence layer 1."""
        values = [self.sma.get(p) for p in config.SMA_PERIODS]
        if any(v is None for v in values):
            return None
        return all(values[i] > values[i + 1] for i in range(len(values) - 1))


def analyse(bars: Sequence[Bar]) -> TechnicalPicture:
    closes = [b.close for b in bars]
    return TechnicalPicture(
        ema_fast=ema(closes, config.EMA_FAST),
        ema_slow=ema(closes, config.EMA_SLOW),
        sma={p: sma(closes, p) for p in config.SMA_PERIODS},
        stochastic=stochastic(bars),
        macd=macd(closes),
        bollinger=bollinger(bars),
        atr=atr(bars),
        structure=structure(bars),
        candles=candle_signal(bars),
    )
