"""Turn an option chain into candidate positions, with their metrics attached.

The builder does not judge -- it only constructs plausible structures and computes numbers.
Every pass/fail decision belongs to the rules engine. It deliberately includes a few contracts
either side of each target delta band so that near misses show up as explained FAIL rows.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from backend.app import config
from backend.app.domain import Leg, OptionQuote, Right, Strategy, UnderlyingSnapshot
from backend.app.metrics import formulas
from backend.app.rules.registry import spec_for


@dataclass(slots=True)
class Candidate:
    strategy: Strategy
    symbol: str
    legs: list[Leg]
    metrics: dict[str, Any]
    dte: int


def strike_ladder(quotes: Sequence[OptionQuote], right: Right) -> list[Decimal]:
    return sorted({q.strike for q in quotes if q.right is right})


def _by_strike(quotes: Sequence[OptionQuote], right: Right) -> dict[Decimal, OptionQuote]:
    return {q.strike: q for q in quotes if q.right is right}


def _target_delta_band(strategy: Strategy) -> tuple[Decimal, Decimal] | None:
    """The band the builder searches around, widened by CANDIDATE_DELTA_TOLERANCE."""
    tol = config.CANDIDATE_DELTA_TOLERANCE
    bands = {
        Strategy.BULL_PUT: config.BULL_PUT["short_delta"],
        Strategy.BEAR_CALL: config.BEAR_CALL["short_delta"],
        Strategy.CASH_SECURED_PUT: (
            config.CASH_SECURED_PUT["delta_wheel"]
            if config.CASH_SECURED_PUT["mode"] == "wheel"
            else config.CASH_SECURED_PUT["delta_standalone"]
        ),
        Strategy.COVERED_CALL: config.COVERED_CALL["delta"],
        Strategy.LONG_CALL: config.LONG_OPTION["delta"],
        Strategy.LONG_PUT: (-config.LONG_OPTION["delta"][1], -config.LONG_OPTION["delta"][0]),
        Strategy.BULL_CALL: config.DEBIT_SPREAD["long_delta"],
        Strategy.BEAR_PUT: (
            -config.DEBIT_SPREAD["long_delta"][1],
            -config.DEBIT_SPREAD["long_delta"][0],
        ),
    }
    band = bands.get(strategy)
    if band is None:
        return None
    return band[0] - tol, band[1] + tol


def _in_band(delta: float | None, band: tuple[Decimal, Decimal]) -> bool:
    if delta is None:
        return False
    d = Decimal(str(delta))
    return band[0] <= d <= band[1]


def _expected_move(underlying: UnderlyingSnapshot, dte: int, iv: float | None) -> Decimal | None:
    """One standard deviation of expected move over the holding period.

    spot * IV * sqrt(dte / 365). Used only for the debit-spread short-strike placement rule.
    """
    if underlying.price is None or iv is None or dte <= 0:
        return None
    return underlying.price * Decimal(str(iv)) * Decimal(str(math.sqrt(dte / 365.0)))


def _shared_metrics(legs: list[Leg], underlying: UnderlyingSnapshot, dte: int) -> dict[str, Any]:
    """Fields every strategy's row carries, named as in the tracker workbooks."""
    first = legs[0].quote
    greeks = formulas.position_greeks(legs)

    long_legs = [leg for leg in legs if not leg.is_short]
    long_delta = formulas.dec(long_legs[0].quote.delta) if long_legs else None

    oi = [leg.quote.open_interest for leg in legs if leg.quote.open_interest is not None]
    vol = [leg.quote.volume for leg in legs if leg.quote.volume is not None]

    legs_text = " / ".join(
        f"{'-' if leg.is_short else '+'}{leg.quote.strike_text}{leg.quote.right.value}"
        for leg in legs
    )

    return {
        **greeks,
        "long_delta_per_share": long_delta,
        "symbol": underlying.symbol,
        "underlying_price": underlying.price,
        "expiry": first.expiry,
        "expiry_yymmdd": first.occ_expiry,
        "dte": dte,
        "dte_annualise": formulas.annualisation_dte(dte),
        "legs_text": legs_text,
        "iv_raw": underlying.iv,
        "iv_percentile": underlying.iv_percentile,
        "open_interest": min(oi) if oi else None,
        "volume": min(vol) if vol else None,
        "earnings_date": underlying.earnings_date,
    }


# ---------------------------------------------------------------------------
# Per-strategy construction
# ---------------------------------------------------------------------------


def build_credit_spreads(
    strategy: Strategy,
    quotes: Sequence[OptionQuote],
    underlying: UnderlyingSnapshot,
    quote_date: date,
) -> list[Candidate]:
    """Short leg near the target delta, long leg 1-2 strikes further out."""
    right = Right.PUT if strategy is Strategy.BULL_PUT else Right.CALL
    band = _target_delta_band(strategy)
    ladder = strike_ladder(quotes, right)
    by_strike = _by_strike(quotes, right)
    if band is None or len(ladder) < 2:
        return []

    # A bull put's long leg is *below* the short; a bear call's is above.
    direction = -1 if strategy is Strategy.BULL_PUT else 1
    offsets = range(
        config.BULL_PUT["long_strike_offset"][0], config.BULL_PUT["long_strike_offset"][1] + 1
    )

    out: list[Candidate] = []
    for idx, strike in enumerate(ladder):
        short_q = by_strike[strike]
        if not _in_band(short_q.delta, band):
            continue

        for offset in offsets:
            long_idx = idx + direction * offset
            if not 0 <= long_idx < len(ladder):
                continue
            long_q = by_strike[ladder[long_idx]]

            legs = [
                Leg(short_q, -config.CONTRACT_MULTIPLIER),
                Leg(long_q, config.CONTRACT_MULTIPLIER),
            ]
            dte = formulas.days_to_expiry(short_q.expiry, quote_date)

            metrics = formulas.credit_spread_metrics(
                short_premium=short_q.mark,
                long_premium=long_q.mark,
                short_strike=short_q.strike,
                long_strike=long_q.strike,
                right=right,
                dte=dte,
            )
            metrics.update(_shared_metrics(legs, underlying, dte))
            metrics["strike_offset"] = offset
            metrics["net_entry_price"] = metrics.get("net_credit")

            out.append(Candidate(strategy, underlying.symbol, legs, metrics, dte))

    return out


def build_debit_spreads(
    strategy: Strategy,
    quotes: Sequence[OptionQuote],
    underlying: UnderlyingSnapshot,
    quote_date: date,
) -> list[Candidate]:
    """Long leg near 0.50-0.60 delta, short leg further out of the money."""
    right = Right.CALL if strategy is Strategy.BULL_CALL else Right.PUT
    band = _target_delta_band(strategy)
    ladder = strike_ladder(quotes, right)
    by_strike = _by_strike(quotes, right)
    if band is None or len(ladder) < 2:
        return []

    direction = 1 if strategy is Strategy.BULL_CALL else -1
    min_width = config.DEBIT_SPREAD["min_width_bull_call"]

    # "Short strike: target price at 1 standard deviation" -- so the short leg is placed at
    # the strike nearest spot +/- 1 SD (and one strike either side), not at a fixed offset
    # from the long. A fixed 1-5 strikes never reaches 1 SD on a $1 ladder.
    dte_here = formulas.days_to_expiry(by_strike[ladder[0]].expiry, quote_date)
    move = _expected_move(underlying, dte_here, underlying.iv)
    target_idx = None
    if move and underlying.price is not None:
        target = underlying.price + direction * move
        target_idx = min(range(len(ladder)), key=lambda i: abs(ladder[i] - target))
    atm_index = (
        min(range(len(ladder)), key=lambda i: abs(ladder[i] - underlying.price))
        if underlying.price is not None
        else None
    )

    out: list[Candidate] = []
    for idx, strike in enumerate(ladder):
        long_q = by_strike[strike]
        if not _in_band(long_q.delta, band):
            continue

        if target_idx is not None:
            short_idxs = [target_idx + step for step in (0, -1, 1)]
        else:  # no IV, no expected move: fall back to walking out from the long leg
            short_idxs = [idx + direction * offset for offset in range(1, 6)]

        for short_idx in short_idxs:
            if not 0 <= short_idx < len(ladder) or (short_idx - idx) * direction <= 0:
                continue  # the short leg must sit beyond the long leg
            offset = abs(short_idx - idx)
            short_q = by_strike[ladder[short_idx]]

            width = abs(short_q.strike - long_q.strike)
            if strategy is Strategy.BULL_CALL and width < min_width:
                continue

            legs = [
                Leg(long_q, config.CONTRACT_MULTIPLIER),
                Leg(short_q, -config.CONTRACT_MULTIPLIER),
            ]
            dte = formulas.days_to_expiry(long_q.expiry, quote_date)

            metrics = formulas.debit_spread_metrics(
                long_premium=long_q.mark,
                short_premium=short_q.mark,
                long_strike=long_q.strike,
                short_strike=short_q.strike,
                right=right,
                dte=dte,
            )
            metrics.update(_shared_metrics(legs, underlying, dte))
            metrics["strike_offset"] = offset
            metrics["net_entry_price"] = metrics.get("net_debit")
            metrics["total_premium"] = metrics.get("max_loss")
            if atm_index is not None:
                # In the money is below spot for a call, above for a put.
                metrics["strikes_itm"] = (atm_index - idx) * direction

            move = _expected_move(underlying, dte, underlying.iv)
            if move and underlying.price is not None:
                metrics["short_strike_sd"] = abs(short_q.strike - underlying.price) / move

            out.append(Candidate(strategy, underlying.symbol, legs, metrics, dte))

    return out


def build_short_single(
    strategy: Strategy,
    quotes: Sequence[OptionQuote],
    underlying: UnderlyingSnapshot,
    quote_date: date,
) -> list[Candidate]:
    """Cash secured put and covered call -- one short leg."""
    right = Right.PUT if strategy is Strategy.CASH_SECURED_PUT else Right.CALL
    band = _target_delta_band(strategy)
    if band is None:
        return []

    out: list[Candidate] = []
    for q in quotes:
        if q.right is not right or not _in_band(q.delta, band):
            continue

        legs = [Leg(q, -config.CONTRACT_MULTIPLIER)]
        dte = formulas.days_to_expiry(q.expiry, quote_date)

        if strategy is Strategy.CASH_SECURED_PUT:
            metrics = formulas.csp_metrics(
                stock_price=underlying.price, option_mark=q.mark, strike=q.strike, dte=dte
            )
            metrics["capital"] = metrics["cash_secured"]
        else:
            metrics = formulas.covered_call_metrics(
                stock_price=underlying.price,
                option_mark=q.mark,
                strike=q.strike,
                dte=dte,
                dividend_yield=underlying.dividend_yield,
            )
            if underlying.price is not None:
                metrics["capital"] = underlying.price * Decimal(config.CONTRACT_MULTIPLIER)
                metrics["max_loss"] = metrics["capital"]
            metrics["max_profit"] = (
                (q.mark + q.strike - underlying.price) * Decimal(config.CONTRACT_MULTIPLIER)
                if q.mark is not None and underlying.price is not None
                else None
            )

        metrics.update(_shared_metrics(legs, underlying, dte))
        metrics["net_entry_price"] = q.mark
        out.append(Candidate(strategy, underlying.symbol, legs, metrics, dte))

    return out


def build_long_single(
    strategy: Strategy,
    quotes: Sequence[OptionQuote],
    underlying: UnderlyingSnapshot,
    quote_date: date,
) -> list[Candidate]:
    """Long call / long put -- one long leg, ATM or one strike in the money."""
    right = Right.CALL if strategy is Strategy.LONG_CALL else Right.PUT
    band = _target_delta_band(strategy)
    ladder = strike_ladder(quotes, right)
    if band is None or not ladder or underlying.price is None:
        return []

    atm_index = min(range(len(ladder)), key=lambda i: abs(ladder[i] - underlying.price))

    out: list[Candidate] = []
    for q in quotes:
        if q.right is not right or not _in_band(q.delta, band):
            continue

        idx = ladder.index(q.strike)
        # In the money is below spot for a call, above for a put.
        strikes_itm = atm_index - idx if right is Right.CALL else idx - atm_index

        legs = [Leg(q, config.CONTRACT_MULTIPLIER)]
        dte = formulas.days_to_expiry(q.expiry, quote_date)

        metrics = formulas.long_option_metrics(
            premium=q.mark, strike=q.strike, right=right, dte=dte
        )
        metrics.update(_shared_metrics(legs, underlying, dte))
        metrics["strikes_itm"] = strikes_itm
        metrics["net_entry_price"] = q.mark
        out.append(Candidate(strategy, underlying.symbol, legs, metrics, dte))

    return out


def _nearest_by_delta(
    quotes: Sequence[OptionQuote],
    right: Right,
    target: Decimal,
    band: tuple[Decimal, Decimal],
    limit: int,
) -> list[OptionQuote]:
    """Contracts inside the widened band, closest to the target delta first."""
    pool = [q for q in quotes if q.right is right and _in_band(q.delta, band)]
    pool.sort(key=lambda q: abs(Decimal(str(q.delta)) - target))
    return pool[:limit]


def _widen(band: tuple[Decimal, Decimal]) -> tuple[Decimal, Decimal]:
    tol = config.CANDIDATE_DELTA_TOLERANCE
    return band[0] - tol, band[1] + tol


def _fan_by_delta(
    quotes: Sequence[OptionQuote],
    right: Right,
    band: tuple[Decimal, Decimal],
    limit: int = 4,
) -> list[OptionQuote]:
    """Contracts spread ACROSS a delta band, rather than clustered at one end of it.

    `_nearest_by_delta` is right when the notes name a delta target: three strikes either side
    of 45 are three readings of the same instruction. It is wrong when the notes name a range
    and something else decides where in that range to sit -- the jade lizard's short put being
    the case, where "~25-delta (or lower)" trades credit against the downside breakeven.

    Clustering there is actively misleading: on a real chain every strike near -0.25 leaves the
    breakeven at 0.7-0.9 SD, so a clustered fan shows nine rows that all fail the same gate and
    never reaches the further-out strikes that clear it. Fanning picks one contract per target
    across the band, so both ends of the trade-off are on screen and the trader picks a corner.
    """
    tol = config.CANDIDATE_DELTA_TOLERANCE
    low, high = band
    mid = (low + high) / 2
    # Aggressive (past the ceiling), at the ceiling, middle, and the far end of the band.
    targets = [low - tol, low, mid, high][:limit]

    pool = [q for q in quotes if q.right is right and _in_band(q.delta, _widen(band))]
    picked: list[OptionQuote] = []
    seen: set[Decimal] = set()
    for target in targets:
        match = min(
            (q for q in pool if q.strike not in seen),
            key=lambda q: abs(Decimal(str(q.delta)) - target),
            default=None,
        )
        if match is not None:
            picked.append(match)
            seen.add(match.strike)
    return picked


def _pick_long_call(
    short_call: OptionQuote,
    short_put: OptionQuote,
    call_ladder: list[Decimal],
    calls_by_strike: dict[Decimal, OptionQuote],
) -> tuple[OptionQuote, Decimal | None] | None:
    """Leg 3: "the point where the entry credit is equal or greater than the (Bear) Call width".

    Walks outwards from the short call and stops at the first strike that satisfies it. When no
    strike does, returns the best attempt rather than nothing, so the row still appears with
    "Upside risk $X" against it instead of vanishing.
    """
    cfg = config.JADE_LIZARD
    min_offset, max_offset = cfg["long_call_offset"]
    sc_index = call_ladder.index(short_call.strike)

    best: tuple[OptionQuote, Decimal | None] | None = None
    for offset in range(min_offset, max_offset + 1):
        idx = sc_index + offset
        if idx >= len(call_ladder):
            break
        long_call = calls_by_strike[call_ladder[idx]]

        width = long_call.strike - short_call.strike
        credit = None
        if None not in (short_put.mark, short_call.mark, long_call.mark):
            credit = short_put.mark + short_call.mark - long_call.mark
        ratio = formulas.div(credit, width)

        if best is None or (ratio is not None and (best[1] is None or ratio > best[1])):
            best = (long_call, ratio)
        if ratio is not None and ratio >= cfg["min_credit_to_call_width"]:
            break

    return best


def build_jade_lizard(
    strategy: Strategy,
    quotes: Sequence[OptionQuote],
    underlying: UnderlyingSnapshot,
    quote_date: date,
) -> list[Candidate]:
    """Short put + short call + long call, one expiry.

    Leg 3 is not chosen by a fixed strike offset the way a vertical's long leg is. The notes
    define it functionally -- "Buy a further OTM Call option at the point where the entry
    credit is equal or greater than the (Bear) Call width" -- so `_pick_long_call` walks
    outwards from the short call and stops at the first strike that satisfies it.

    Legs 1 and 2 are fanned out across the delta band instead, three strikes each, and the
    rules judge them. That is deliberate, and it is the opposite of what the notes' wording
    suggests: "Sell a Put option at ~25-delta (or lower)" reads like an instruction to place
    the put whereever the 1-SD breakeven rule needs it, which would make that rule satisfied
    by construction and never worth reading.

    The reason not to do that is that the three conditions pull against each other. Pushing
    the put further out drops the breakeven, which is what the 1-SD rule wants, but it also
    gives away the credit that the no-upside-risk rule needs. A -0.30 put breaches the delta
    ceiling and frequently produces the only structure on the chain whose credit actually
    covers the call width. Auto-placing the put would silently resolve that trade-off in one
    direction and delete the better trade from the table.

    So the table shows the alternatives with their costs attached -- "Short put delta -0.300
    (-0.250 to -0.080)" next to "No upside risk -- credit $6.00 vs call width $5.00" -- and
    the choice of which compromise to take stays with the trader.
    """
    cfg = config.JADE_LIZARD
    calls_by_strike = _by_strike(quotes, Right.CALL)
    call_ladder = strike_ladder(quotes, Right.CALL)
    if len(call_ladder) < 2:
        return []

    put_band = cfg["short_put_delta"]
    call_band = cfg["short_call_delta"]
    short_calls = _nearest_by_delta(
        quotes, Right.CALL, (call_band[0] + call_band[1]) / 2, _widen(call_band), 3
    )
    # Fanned across the band, not clustered at the ceiling: the aggressive strike carries the
    # most credit and breaches the -0.25 ceiling, the far strike has the safest breakeven and
    # the thinnest credit, and the rows in between are the compromises. All are shown with
    # their costs named; none is silently discarded.
    short_puts = _fan_by_delta(quotes, Right.PUT, put_band, limit=4)

    out: list[Candidate] = []
    for short_call in short_calls:
        for short_put in short_puts:
            if short_put.strike >= short_call.strike:
                continue  # not a lizard: the put must sit below the call side

            picked = _pick_long_call(short_call, short_put, call_ladder, calls_by_strike)
            if picked is None:
                continue
            long_call, _ratio = picked

            legs = [
                Leg(short_put, -config.CONTRACT_MULTIPLIER),
                Leg(short_call, -config.CONTRACT_MULTIPLIER),
                Leg(long_call, config.CONTRACT_MULTIPLIER),
            ]
            dte = formulas.days_to_expiry(short_call.expiry, quote_date)

            metrics = formulas.jade_lizard_metrics(
                short_put_premium=short_put.mark,
                short_call_premium=short_call.mark,
                long_call_premium=long_call.mark,
                short_put_strike=short_put.strike,
                short_call_strike=short_call.strike,
                long_call_strike=long_call.strike,
                dte=dte,
            )
            metrics.update(_shared_metrics(legs, underlying, dte))
            metrics["net_entry_price"] = metrics.get("net_credit")
            metrics["short_put_delta"] = formulas.dec(short_put.delta)
            metrics["short_call_delta"] = formulas.dec(short_call.delta)
            metrics["strike_offset"] = call_ladder.index(long_call.strike) - call_ladder.index(
                short_call.strike
            )

            # "Break-even on the downside is at the 1-SD point (or lower)" -- expressed as how
            # many standard deviations of expected move separate spot from the breakeven.
            move = _expected_move(underlying, dte, underlying.iv)
            breakeven = metrics.get("breakeven")
            if move and breakeven is not None and underlying.price is not None:
                metrics["breakeven_sd"] = (underlying.price - breakeven) / move
            metrics["expected_move_1sd"] = move

            # Delta as a stand-in for P(finish out of the money). An approximation, and a
            # conservative one: the trade does not start losing at the put strike but at the
            # breakeven below it. Labelled everywhere it surfaces -- see rule_win_probability.
            if short_put.delta is not None:
                metrics["prob_win_approx"] = Decimal(1) - abs(Decimal(str(short_put.delta)))

            out.append(Candidate(strategy, underlying.symbol, legs, metrics, dte))

    return out


def build_synthetic(
    strategy: Strategy,
    quotes: Sequence[OptionQuote],
    underlying: UnderlyingSnapshot,
    quote_date: date,
) -> list[Candidate]:
    """Long call + short put (bullish), or long put + short call (bearish).

    Strikes are chosen by distance from spot rather than by delta: the notes pick the legs
    for liquidity ("ATM ... to have the highest liquidity"), not for a delta target, and an
    ATM contract's delta is a consequence of that choice rather than the reason for it.

    In `atm` mode the two legs share a strike and the pair either side of the money is built
    as well, so a chain whose nearest strike sits outside the tolerance produces an explained
    FAIL row rather than an empty table.
    """
    cfg = config.SYNTHETIC
    bullish = strategy is Strategy.SYNTHETIC_LONG
    long_right = Right.CALL if bullish else Right.PUT
    short_right = Right.PUT if bullish else Right.CALL

    by_right = {
        Right.CALL: _by_strike(quotes, Right.CALL),
        Right.PUT: _by_strike(quotes, Right.PUT),
    }
    ladder = sorted(set(by_right[Right.CALL]) & set(by_right[Right.PUT]))
    if not ladder or underlying.price is None:
        return []

    atm_index = min(range(len(ladder)), key=lambda i: abs(ladder[i] - underlying.price))

    pairs: list[tuple[int, int]] = []
    if cfg["mode"] == "split":
        # The notes' variant: short leg below the money, long leg above, flat zone between.
        low, high = cfg["split_offset"]
        for long_off in range(low, high + 1):
            for short_off in range(low, high + 1):
                long_idx = atm_index + (long_off if bullish else -long_off)
                short_idx = atm_index + (-short_off if bullish else short_off)
                pairs.append((long_idx, short_idx))
    else:
        for step in (0, -1, 1):
            pairs.append((atm_index + step, atm_index + step))

    out: list[Candidate] = []
    for long_idx, short_idx in pairs:
        if not (0 <= long_idx < len(ladder) and 0 <= short_idx < len(ladder)):
            continue

        long_q = by_right[long_right][ladder[long_idx]]
        short_q = by_right[short_right][ladder[short_idx]]

        legs = [
            Leg(long_q, config.CONTRACT_MULTIPLIER),
            Leg(short_q, -config.CONTRACT_MULTIPLIER),
        ]
        dte = formulas.days_to_expiry(long_q.expiry, quote_date)

        metrics = formulas.synthetic_metrics(
            long_premium=long_q.mark,
            short_premium=short_q.mark,
            long_strike=long_q.strike,
            short_strike=short_q.strike,
            stock_price=underlying.price,
            bullish=bullish,
            dte=dte,
        )
        metrics.update(_shared_metrics(legs, underlying, dte))
        metrics["net_entry_price"] = metrics.get("net_debit")
        metrics["long_strike"] = long_q.strike
        metrics["short_strike"] = short_q.strike

        out.append(Candidate(strategy, underlying.symbol, legs, metrics, dte))

    return out


_BUILDERS = {
    "credit": build_credit_spreads,
    "debit": build_debit_spreads,
    "csp": build_short_single,
    "covered_call": build_short_single,
    "long": build_long_single,
    "jade_lizard": build_jade_lizard,
    "synthetic": build_synthetic,
}


def build(
    strategy: Strategy,
    quotes: Sequence[OptionQuote],
    underlying: UnderlyingSnapshot,
    quote_date: date,
) -> list[Candidate]:
    """Dispatch to the right constructor, capped so one wide chain cannot flood the table.

    The constructors index the chain by strike, so they must only ever see one expiry --
    handed a mixed chain, the last expiry silently overwrites the others and a spread's legs
    can come from different dates. Each expiry is built separately, and only expiries inside
    the strategy's own DTE window are used. If none is listed there, the nearest one is built
    anyway so the row still appears, failing on DTE with the real number attached.
    """
    builder = _BUILDERS[spec_for(strategy).kind]
    avoid_earnings = underlying.earnings_date if _has_earnings_gate(strategy) else None
    candidates: list[Candidate] = []
    for expiry_quotes in quotes_for_window(
        quotes, dte_window(strategy), quote_date, avoid_earnings=avoid_earnings
    ):
        built = builder(strategy, expiry_quotes, underlying, quote_date)
        # Before the cap: candidates whose key leg sits inside the rule's own delta band
        # first. Builders emit in strike order, so without this a widened search could fill
        # the cap with near misses and drop the contracts the rule actually asks for.
        built.sort(key=lambda c: _distance_from_rule(strategy, c))
        # Capped per expiry, not overall -- otherwise the nearest expiry fills every slot and
        # a later in-window expiry (the debit spread's ideal 30 days, say) is never judged.
        candidates.extend(built[: config.MAX_CANDIDATES_PER_STRATEGY])
    return candidates


def _has_earnings_gate(strategy: Strategy) -> bool:
    from backend.app.rules import common  # local: rules import the builder's metrics names

    return common.rule_earnings_outside_expiry in spec_for(strategy).rules


def _rule_band(strategy: Strategy) -> tuple[str, tuple[Decimal, Decimal]] | None:
    """The metric and the (unwidened) delta band the strategy's rule judges its key leg by."""
    debit = config.DEBIT_SPREAD["long_delta"]
    long_band = config.LONG_OPTION["delta"]
    csp = config.CASH_SECURED_PUT
    bands = {
        Strategy.BULL_PUT: ("short_delta_per_share", config.BULL_PUT["short_delta"]),
        Strategy.BEAR_CALL: ("short_delta_per_share", config.BEAR_CALL["short_delta"]),
        Strategy.CASH_SECURED_PUT: (
            "short_delta_per_share",
            csp["delta_wheel"] if csp["mode"] == "wheel" else csp["delta_standalone"],
        ),
        Strategy.COVERED_CALL: ("short_delta_per_share", config.COVERED_CALL["delta"]),
        Strategy.LONG_CALL: ("long_delta_per_share", long_band),
        Strategy.LONG_PUT: ("long_delta_per_share", (-long_band[1], -long_band[0])),
        Strategy.BULL_CALL: ("long_delta_per_share", debit),
        Strategy.BEAR_PUT: ("long_delta_per_share", (-debit[1], -debit[0])),
    }
    return bands.get(strategy)


def _distance_from_rule(strategy: Strategy, candidate: Candidate) -> Decimal:
    """0 inside the rule's band, else how far outside. Unbanded strategies keep their order."""
    spec = _rule_band(strategy)
    if spec is None:
        return Decimal(0)
    key, (low, high) = spec
    delta = candidate.metrics.get(key)
    if delta is None:
        return Decimal(99)
    d = Decimal(str(delta))
    return max(low - d, d - high, Decimal(0))


def dte_window(strategy: Strategy) -> tuple[int, int]:
    """The DTE range each strategy's own rule accepts, read from config."""
    debit = (
        config.DEBIT_SPREAD["target_dte"] - config.DEBIT_SPREAD["dte_tolerance"],
        config.DEBIT_SPREAD["target_dte"] + config.DEBIT_SPREAD["dte_tolerance"],
    )
    windows = {
        Strategy.BULL_PUT: config.BULL_PUT["dte"],
        Strategy.BEAR_CALL: config.BEAR_CALL["dte"],
        Strategy.CASH_SECURED_PUT: config.CASH_SECURED_PUT["dte"],
        Strategy.COVERED_CALL: config.COVERED_CALL["dte"],
        Strategy.LONG_CALL: (config.LONG_OPTION["min_dte"], 10_000),
        Strategy.LONG_PUT: (config.LONG_OPTION["min_dte"], 10_000),
        Strategy.BULL_CALL: debit,
        Strategy.BEAR_PUT: debit,
        Strategy.JADE_LIZARD: config.JADE_LIZARD["dte"],
        Strategy.SYNTHETIC_LONG: config.SYNTHETIC["dte"],
        Strategy.SYNTHETIC_SHORT: config.SYNTHETIC["dte"],
    }
    return windows[strategy]


def quotes_for_window(
    quotes: Sequence[OptionQuote],
    window: tuple[int, int],
    quote_date: date,
    *,
    avoid_earnings: date | None = None,
) -> list[list[OptionQuote]]:
    """The chain split by expiry, keeping only expiries in `window` (nearest first).

    "Choose date to expiration. 30-45 days is ideal. Earnings NOT within the expiration
    date." -- so when `avoid_earnings` is given and some in-window expiry ends before it,
    only those are kept. If every in-window expiry spans earnings, all are kept and the
    earnings rule fails them with the date attached.

    Falls back to the single expiry closest to the window when none falls inside it.
    """
    by_expiry: dict[date, list[OptionQuote]] = {}
    for q in quotes:
        by_expiry.setdefault(q.expiry, []).append(q)
    if not by_expiry:
        return []

    low, high = window

    def dte(expiry: date) -> int:
        return formulas.days_to_expiry(expiry, quote_date)

    inside = sorted(e for e in by_expiry if low <= dte(e) <= high)
    if inside and avoid_earnings is not None:
        before = [e for e in inside if e < avoid_earnings]
        inside = before or inside
    if not inside:
        inside = [min(by_expiry, key=lambda e: max(low - dte(e), dte(e) - high))]
    return [by_expiry[e] for e in inside]
