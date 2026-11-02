"""How each strategy's contract is chosen, written as the course notes state it.

One entry per strategy: the steps from the notes' "Steps to..." / "Constructing the trade"
sections, each tied to the rule ids that check that step. The UI renders this beside every
row, so the trader can see the rule, the contract the screener actually picked, and whether
that pick satisfies the rule -- without having to trust that the builder read the notes right.

Numbers are read from `config`, never retyped, so tuning a threshold updates the text too.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from backend.app import config
from backend.app.domain import RuleResult, Strategy


@dataclass(frozen=True, slots=True)
class Step:
    step: str  # what is being chosen: "Expiry", "Short put", ...
    rule: str  # the course-notes rule, with the live config numbers
    rule_ids: tuple[str, ...]  # the rule checks that verify this step
    # True for steps that place a leg (delta, strike offset, ATM). The notes choose the
    # strike by these first and only then check credit/size, so ranking honours them first.
    structural: bool = False


@dataclass(frozen=True, slots=True)
class Guide:
    source: str  # where in the course notes this comes from
    method: str  # what the screener does to pick the contract
    steps: tuple[Step, ...]


def _d(value: Decimal) -> str:
    return f"{value:+.2f}" if value < 0 else f"{value:.2f}"


def _pct(value: Decimal) -> str:
    return f"{float(value) * 100:g}%"


def _credit_spread(bull: bool) -> Guide:
    cfg = config.BULL_PUT if bull else config.BEAR_CALL
    side, leg, where = ("put", "Short put", "below") if bull else ("call", "Short call", "above")
    lo, hi = cfg["short_delta"]
    o_lo, o_hi = cfg["long_strike_offset"]
    d_lo, d_hi = cfg["dte"]
    return Guide(
        source="Course notes §10/§11 — Steps to a bull put / bear call spread",
        method=(
            f"Takes the listed expiries inside {d_lo}–{d_hi} DTE (preferring one that ends "
            f"before earnings), tries every short {side} near the delta band paired with the "
            f"long {side} {o_lo} and {o_hi} strikes {where}, checks each against every rule, "
            "and shows the one that passes most."
        ),
        steps=(
            Step(
                "Expiry",
                f"{d_lo}–{d_hi} days to expiration. Earnings NOT within the expiration date.",
                ("time.dte", "time.earnings"),
            ),
            Step(
                leg,
                f"Delta {_d(lo)} to {_d(hi)} — a 75–80% win probability.",
                (f"{'bull_put' if bull else 'bear_call'}.short_delta",),
                structural=True,
            ),
            Step(
                f"Long {side}",
                f"{o_lo}–{o_hi} strikes {where} the short {side}, same expiry.",
                (f"{'bull_put' if bull else 'bear_call'}.strike_offset",),
                structural=True,
            ),
            Step(
                "Credit vs risk",
                f"Must be a CREDIT. Max loss not more than "
                f"{cfg['max_loss_credit_multiple']}× net credit.",
                (f"{'bull_put' if bull else 'bear_call'}.max_loss",),
            ),
            Step(
                "Size",
                f"{cfg['stop_credit_multiple']}× net credit less than "
                f"{_pct(cfg['position_size_pct_nlv'])} of net liquidation.",
                ("size.position",),
            ),
            Step(
                "Liquidity",
                f"Bid/ask spread no wider than ${config.MAX_BID_ASK_SPREAD} on each leg.",
                ("universal.spread",),
            ),
        ),
    )


def _csp() -> Guide:
    cfg = config.CASH_SECURED_PUT
    wheel = cfg["mode"] == "wheel"
    lo, hi = cfg["delta_wheel"] if wheel else cfg["delta_standalone"]
    d_lo, d_hi = cfg["dte"]
    ret = (
        f"Premium at least {_pct(cfg['min_wheel_return_of_strike'])} of the strike."
        if wheel
        else f"Premium / strike at least {_pct(cfg['min_monthly_return'])} per month."
    )
    return Guide(
        source="Course notes §6 — Cash secured put, Entry strategy"
        + (" (§7 wheel, step 1)" if wheel else ""),
        method=(
            f"Takes the listed expiries inside {d_lo}–{d_hi} DTE, tries every put near the "
            "delta band, checks each against every rule, and shows the one that passes most."
        ),
        steps=(
            Step("Expiry", f"{d_lo}–{d_hi} days to expiration.", ("time.dte",)),
            Step(
                "Short put",
                f"Delta {_d(lo)} to {_d(hi)}"
                + (" (wheel: 20–30)." if wheel else " — usually −0.30."),
                ("csp.delta",),
                structural=True,
            ),
            Step("Return", ret, ("csp.return",)),
            Step("Cash", "Strike × 100 must be in the account.", ("csp.cash",)),
            Step(
                "Volatility",
                f"Best when IV percentile is above {cfg['preferred_iv_percentile']}.",
                ("volatility.iv_preferred",),
            ),
        ),
    )


def _covered_call() -> Guide:
    cfg = config.COVERED_CALL
    lo, hi = cfg["delta"]
    d_lo, d_hi = cfg["dte"]
    return Guide(
        source="Course notes §5 — Covered call, Entry strategy",
        method=(
            f"Takes the listed expiries inside {d_lo}–{d_hi} DTE, tries every call near the "
            "delta band, checks each against every rule, and shows the one that passes most."
        ),
        steps=(
            Step("Expiry", f"{d_lo}–{d_hi} days to expiration.", ("time.dte",)),
            Step(
                "Short call",
                f"Delta {_d(lo)} to {_d(hi)}.",
                ("covered_call.delta",),
                structural=True,
            ),
            Step(
                "Return",
                f"At least {_pct(cfg['min_roi_30d'])}–3% ROI in 30 days.",
                ("covered_call.roi",),
            ),
            Step(
                "Shares",
                f"Own at least {cfg['min_shares_owned']} shares — one call per 100.",
                ("covered_call.shares",),
            ),
        ),
    )


def _long_option(call: bool) -> Guide:
    cfg = config.LONG_OPTION
    lo, hi = cfg["delta"]
    side = "call" if call else "put"
    return Guide(
        source=f"Course notes §{3 if call else 4} — Steps to long a {side}",
        method=(
            f"Takes the nearest listed expiry at {cfg['min_dte']}+ DTE, tries every {side} "
            "near the delta band, and shows the one that passes most."
        ),
        steps=(
            Step("Expiry", f"At least {cfg['min_dte']} days.", ("time.dte",)),
            Step(
                f"Long {side}",
                f"Delta {lo}–{hi}, ATM or 1 strike in the money.",
                ("long_option.delta", "long_option.moneyness"),
                structural=True,
            ),
            Step(
                "Volatility",
                f"IV percentile below {cfg['max_iv_percentile']}.",
                ("volatility.iv_ceiling",),
            ),
            Step(
                "Size",
                f"Total premium under {_pct(cfg['max_premium_pct_nlv'])} of net liquidation.",
                ("size.premium",),
            ),
        ),
    )


def _debit_spread(bull: bool) -> Guide:
    cfg = config.DEBIT_SPREAD
    lo, hi = cfg["long_delta"]
    side = "call" if bull else "put"
    level = "resistance / swing high" if bull else "support / swing low"
    width = f" At least ${cfg['min_width_bull_call']} from the long strike." if bull else ""
    t = cfg["target_dte"]
    tol = cfg["dte_tolerance"]
    return Guide(
        source=f"Course notes §{8 if bull else 9} — Steps to a bull call / bear put spread",
        method=(
            f"Takes the listed expiry nearest {t} DTE (within {t - tol}–{t + tol}), tries "
            f"every long {side} near the delta band, pairs it with the short {side} at the "
            "1-standard-deviation target and one strike either side, and shows the one that "
            "passes most."
        ),
        steps=(
            Step(
                "Expiry",
                f"About {t} days — nearest to {t} is preferred (screened as "
                f"{t - tol}–{t + tol}). Earnings outside the expiry.",
                ("time.dte", "time.earnings"),
            ),
            Step(
                f"Long {side}",
                f"Delta {lo}–{hi}, ATM or 1 strike in the money.",
                ("debit_spread.long_delta", "debit_spread.moneyness"),
                structural=True,
            ),
            Step(
                f"Short {side}",
                f"Target price at 1 standard deviation, or at {level}.{width}",
                ("debit_spread.short_strike", "debit_spread.width"),
                structural=True,
            ),
            Step(
                "Risk:reward",
                f"Max risk : max reward 1:{cfg['min_risk_reward']:g} or better.",
                ("debit_spread.risk_reward",),
            ),
            Step(
                "Size",
                f"Total premium ≤ {_pct(cfg['max_premium_pct_nlv'])} of net liquidation.",
                ("size.premium",),
            ),
        ),
    )


def _jade_lizard() -> Guide:
    cfg = config.JADE_LIZARD
    c_lo, c_hi = cfg["short_call_delta"]
    p_lo, _ = cfg["short_put_delta"]
    d_lo, d_hi = cfg["dte"]
    return Guide(
        source="Course notes §13 — Jade Lizard, Optimal entry rules / Constructing the trade",
        method=(
            f"Takes the listed expiries inside {d_lo}–{d_hi} DTE, tries the short calls "
            "nearest 45Δ with short puts fanned from the ~25Δ ceiling outward, walks the long "
            "call out until the credit covers the call width, and shows the one that passes "
            "most."
        ),
        steps=(
            Step("Expiry", f"4–6 weeks ({d_lo}–{d_hi} days).", ("time.dte",)),
            Step(
                "Short put",
                f"~25 delta or lower — magnitude no more than {abs(p_lo)}.",
                ("jade_lizard.short_put_delta",),
                structural=True,
            ),
            Step(
                "Short call",
                f"~45 delta ({c_lo}–{c_hi}).",
                ("jade_lizard.short_call_delta",),
                structural=True,
            ),
            Step(
                "Long call",
                "Further OTM, at the point where the entry credit ≥ the call width "
                "(no upside risk).",
                ("jade_lizard.upside",),
            ),
            Step(
                "Return",
                f"Max ROI greater than {_pct(cfg['min_roi'])} on buying power.",
                ("jade_lizard.roi",),
            ),
            Step(
                "Downside",
                f"Breakeven at {cfg['min_breakeven_sd']} SD below spot or lower.",
                ("jade_lizard.breakeven_sd",),
            ),
        ),
    )


def _synthetic(long: bool) -> Guide:
    cfg = config.SYNTHETIC
    d_lo, d_hi = cfg["dte"]
    legs = "buy the ATM call, sell the ATM put" if long else "buy the ATM put, sell the ATM call"
    return Guide(
        source="Course notes §12 — Synthetic spread, Optimal entry rules",
        method=(
            f"Takes the listed expiries inside {d_lo}–{d_hi} DTE, builds the pair at the "
            "strike nearest spot and one strike either side, and shows the one that passes most."
        ),
        steps=(
            Step("Expiry", "Your time frame — 1–3 months.", ("time.dte",)),
            Step(
                "Strikes",
                f"Both legs ATM (within {_pct(cfg['atm_tolerance_pct'])} of spot): {legs}.",
                ("synthetic.strikes",),
                structural=True,
            ),
            Step(
                "Net premium",
                f"Zero or near zero (≤ {_pct(cfg['max_net_premium_pct_of_spot'])} of spot).",
                ("synthetic.net_premium",),
            ),
        ),
    )


def guide_for(strategy: Strategy) -> Guide:
    return {
        Strategy.BULL_PUT: lambda: _credit_spread(True),
        Strategy.BEAR_CALL: lambda: _credit_spread(False),
        Strategy.CASH_SECURED_PUT: _csp,
        Strategy.COVERED_CALL: _covered_call,
        Strategy.LONG_CALL: lambda: _long_option(True),
        Strategy.LONG_PUT: lambda: _long_option(False),
        Strategy.BULL_CALL: lambda: _debit_spread(True),
        Strategy.BEAR_PUT: lambda: _debit_spread(False),
        Strategy.JADE_LIZARD: _jade_lizard,
        Strategy.SYNTHETIC_LONG: lambda: _synthetic(True),
        Strategy.SYNTHETIC_SHORT: lambda: _synthetic(False),
    }[strategy]()


def guide_to_dict(strategy: Strategy) -> dict[str, Any]:
    g = guide_for(strategy)
    return {
        "source": g.source,
        "method": g.method,
        "steps": [{"step": s.step, "rule": s.rule, "rule_ids": list(s.rule_ids)} for s in g.steps],
    }


def selection_for(strategy: Strategy, results: list[RuleResult]) -> list[dict[str, Any]]:
    """Each step, with what the screener actually picked and whether the pick obeys the rule.

    `passed` is True only if every check behind the step passed; None if any was missing.
    A step whose check did not run for this row (no matching result) is reported as such
    rather than silently counted as satisfied.
    """
    by_id: dict[str, list[RuleResult]] = {}
    for r in results:
        by_id.setdefault(r.rule_id, []).append(r)

    out: list[dict[str, Any]] = []
    for s in guide_for(strategy).steps:
        matched = [r for rid in s.rule_ids for r in by_id.get(rid, [])]
        if not matched:
            passed: bool | None = None
            chosen = "not checked for this row"
        else:
            states = [r.passed for r in matched]
            passed = None if None in states else all(states)
            chosen = " · ".join(r.message for r in matched)
        out.append({"step": s.step, "rule": s.rule, "chosen": chosen, "passed": passed})
    return out


def structural_failures(strategy: Strategy, results: list[RuleResult]) -> int:
    """How many leg-placement rules (delta, strike offset, ATM) this contract breaks."""
    ids = {rid for s in guide_for(strategy).steps if s.structural for rid in s.rule_ids}
    return sum(1 for r in results if r.rule_id in ids and r.passed is not True)
