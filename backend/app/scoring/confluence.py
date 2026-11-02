"""Confluence scoring -- CLAUDE.md "Confluence scoring".

Five layers, one point each. Layers 1 and 2 are mandatory: if either fails the score is capped
at 2 regardless of the rest, because a momentum signal against the trend at no structural level
is not a setup. The raw (uncapped) score is retained so the cap is visible rather than implied.
"""

from __future__ import annotations

from backend.app import config
from backend.app.domain import ConfluenceLayer, ConfluenceScore
from backend.app.metrics.indicators import TechnicalPicture


def score(tech: TechnicalPicture | None, *, bullish: bool) -> ConfluenceScore:
    layers = [
        _trend(tech, bullish),
        _structure(tech),
        _momentum(tech, bullish),
        _volatility(tech),
        _trigger(tech, bullish),
    ]

    raw = sum(1 for layer in layers if layer.scored)
    mandatory_ok = all(
        layer.scored for layer in layers if layer.name in config.CONFLUENCE_MANDATORY_LAYERS
    )

    capped = not mandatory_ok and raw > config.CONFLUENCE_CAP_IF_MANDATORY_FAILS
    final = config.CONFLUENCE_CAP_IF_MANDATORY_FAILS if capped else raw

    return ConfluenceScore(score=final, layers=layers, capped=capped, raw_score=raw)


def _trend(tech: TechnicalPicture | None, bullish: bool) -> ConfluenceLayer:
    """MA fan ordering: 20EMA vs 40EMA, and 50/100/150/200 stacked in order."""
    if tech is None:
        return ConfluenceLayer("trend", False, "no daily bars")

    ema_ok = tech.ema_bullish
    fan_ok = tech.fan_bullish

    if ema_ok is None or fan_ok is None:
        return ConfluenceLayer("trend", False, "insufficient history for the MA fan")

    scored = (ema_ok is bullish) and (fan_ok is bullish)
    direction = "bullish" if bullish else "bearish"
    detail = (
        f"20/40 EMA {'aligned' if ema_ok is bullish else 'against'} and "
        f"MA fan {'aligned' if fan_ok is bullish else 'against'} {direction}"
    )
    return ConfluenceLayer("trend", scored, detail)


def _structure(tech: TechnicalPicture | None) -> ConfluenceLayer:
    """Price at a level with at least three prior touches."""
    if tech is None or tech.structure is None:
        return ConfluenceLayer("structure", False, "no daily bars")

    st = tech.structure
    if not st.levels:
        return ConfluenceLayer(
            "structure", False, f"no level with >={config.SR_MIN_TOUCHES} touches"
        )

    if not st.at_level:
        nearest = (
            min(
                d
                for d in (st.distance_to_support_atr, st.distance_to_resistance_atr)
                if d is not None
            )
            if any(
                d is not None for d in (st.distance_to_support_atr, st.distance_to_resistance_atr)
            )
            else None
        )
        detail = (
            f"nearest level {nearest:.2f} ATR away (need <={config.SR_NEAR_ATR})"
            if nearest is not None
            else "no level in range"
        )
        return ConfluenceLayer("structure", False, detail)

    level = st.nearest_support or st.nearest_resistance
    return ConfluenceLayer(
        "structure",
        True,
        f"at {level.kind} {level.price:.2f} ({level.touches} touches)",
    )


def _momentum(tech: TechnicalPicture | None, bullish: bool) -> ConfluenceLayer:
    """Stochastic cross from oversold/overbought, or a MACD cross in the trade's direction."""
    if tech is None:
        return ConfluenceLayer("momentum", False, "no daily bars")

    signals: list[str] = []

    st = tech.stochastic
    if st is not None:
        if bullish and st.bullish_cross and st.k < config.STOCHASTIC["overbought"]:
            signals.append(f"stochastic bullish cross at {st.k:.0f}")
        elif not bullish and st.bearish_cross and st.k > config.STOCHASTIC["oversold"]:
            signals.append(f"stochastic bearish cross at {st.k:.0f}")
        elif bullish and st.oversold:
            signals.append(f"stochastic oversold {st.k:.0f}")
        elif not bullish and st.overbought:
            signals.append(f"stochastic overbought {st.k:.0f}")

    mc = tech.macd
    if mc is not None:
        if bullish and mc.bullish_cross:
            signals.append("MACD bullish cross")
        elif not bullish and mc.bearish_cross:
            signals.append("MACD bearish cross")

    if st is None and mc is None:
        return ConfluenceLayer("momentum", False, "insufficient history")

    return ConfluenceLayer(
        "momentum", bool(signals), ", ".join(signals) if signals else "no momentum signal"
    )


def _volatility(tech: TechnicalPicture | None) -> ConfluenceLayer:
    """Bollinger band tag or a squeeze."""
    if tech is None or tech.bollinger is None:
        return ConfluenceLayer("volatility", False, "insufficient history")

    bb = tech.bollinger
    signals = []
    if bb.tagged_lower:
        signals.append("lower band tag")
    if bb.tagged_upper:
        signals.append("upper band tag")
    if bb.squeeze:
        signals.append(f"squeeze (bandwidth {bb.bandwidth:.3f})")

    return ConfluenceLayer(
        "volatility", bool(signals), ", ".join(signals) if signals else "no band signal"
    )


def _trigger(tech: TechnicalPicture | None, bullish: bool) -> ConfluenceLayer:
    """Pin bar or engulfing candle in the trade's direction."""
    if tech is None or tech.candles is None:
        return ConfluenceLayer("trigger", False, "insufficient history")

    c = tech.candles
    directional = c.bullish_engulfing if bullish else c.bearish_engulfing
    scored = c.pin_bar or directional
    return ConfluenceLayer("trigger", scored, c.detail)
