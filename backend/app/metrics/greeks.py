"""Black-Scholes-Merton Greeks, computed locally when the broker sends none.

Why this exists: OPRA (US options quotes) is a paid subscription. Without it the socket API
serves 15-minute delayed bid/ask but usually no `modelGreeks`, so every delta gate fails on
missing data. Given the delayed mid, spot, strike, time and a rate we can back out implied
vol and the Greeks ourselves.

Honesty rules:
- These are *model* Greeks from a *delayed* mid on *American* options priced with a European
  model. They are close enough to screen a 0.20-0.25 delta band; they are not what TWS shows.
  Every quote that receives them is tagged `greeks_source="computed_bsm"` and the UI says so.
- Nothing here guesses. If the mid is missing, at or below intrinsic, or the solver cannot
  bracket a root, the result is None and the rule fails as it would on any missing datum.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from backend.app.domain import Right

_SQRT_2PI = math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


@dataclass(slots=True, frozen=True)
class Greeks:
    iv: float
    delta: float
    gamma: float
    vega: float  # per 1.00 (100 vol points); divide by 100 for per-point, as TWS shows
    theta: float  # per calendar day, negative for long options


def _f(value: Decimal | float | None) -> float | None:
    if value is None:
        return None
    f = float(value)
    return None if (f != f or f <= 0) else f


def bsm_price(
    right: Right, spot: float, strike: float, t_years: float, rate: float, sigma: float, q: float
) -> float:
    """European option price under Black-Scholes-Merton with continuous yield `q`."""
    if t_years <= 0 or sigma <= 0:
        intrinsic = spot - strike if right is Right.CALL else strike - spot
        return max(intrinsic, 0.0)
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (rate - q + 0.5 * sigma * sigma) * t_years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    disc_s = spot * math.exp(-q * t_years)
    disc_k = strike * math.exp(-rate * t_years)
    if right is Right.CALL:
        return disc_s * _norm_cdf(d1) - disc_k * _norm_cdf(d2)
    return disc_k * _norm_cdf(-d2) - disc_s * _norm_cdf(-d1)


def bsm_greeks(
    right: Right, spot: float, strike: float, t_years: float, rate: float, sigma: float, q: float
) -> Greeks:
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (rate - q + 0.5 * sigma * sigma) * t_years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    eq = math.exp(-q * t_years)
    er = math.exp(-rate * t_years)
    pdf = _norm_pdf(d1)

    if right is Right.CALL:
        delta = eq * _norm_cdf(d1)
        theta_yr = (
            -spot * eq * pdf * sigma / (2 * sqrt_t)
            - rate * strike * er * _norm_cdf(d2)
            + q * spot * eq * _norm_cdf(d1)
        )
    else:
        delta = -eq * _norm_cdf(-d1)
        theta_yr = (
            -spot * eq * pdf * sigma / (2 * sqrt_t)
            + rate * strike * er * _norm_cdf(-d2)
            - q * spot * eq * _norm_cdf(-d1)
        )

    gamma = eq * pdf / (spot * sigma * sqrt_t)
    vega = spot * eq * pdf * sqrt_t
    return Greeks(iv=sigma, delta=delta, gamma=gamma, vega=vega, theta=theta_yr / 365.0)


def implied_vol(
    right: Right,
    price: float,
    spot: float,
    strike: float,
    t_years: float,
    rate: float,
    q: float,
    *,
    lo: float = 0.01,
    hi: float = 5.0,
    tol: float = 1e-6,
    max_iter: int = 100,
) -> float | None:
    """Bisection for sigma. Monotone in sigma, so bracketing is enough and never diverges.

    Returns None when the price sits at or below the model's zero-vol floor (intrinsic) or
    above its sigma=`hi` ceiling -- both mean the quote cannot be explained by the model.
    """
    if t_years <= 0 or price <= 0:
        return None
    floor = bsm_price(right, spot, strike, t_years, rate, lo, q)
    ceil = bsm_price(right, spot, strike, t_years, rate, hi, q)
    if price <= floor or price >= ceil:
        return None

    a, b = lo, hi
    for _ in range(max_iter):
        mid = 0.5 * (a + b)
        diff = bsm_price(right, spot, strike, t_years, rate, mid, q) - price
        if abs(diff) < tol:
            return mid
        if diff > 0:
            b = mid
        else:
            a = mid
    return 0.5 * (a + b)


def greeks_from_mark(
    right: Right,
    mark: Decimal | float | None,
    spot: Decimal | float | None,
    strike: Decimal | float | None,
    days_to_expiry: int | None,
    *,
    rate: Decimal | float,
    dividend_yield: Decimal | float | None,
) -> Greeks | None:
    """The one entry point the data layer uses. Any missing input yields None, not a guess."""
    p, s, k = _f(mark), _f(spot), _f(strike)
    if p is None or s is None or k is None or not days_to_expiry or days_to_expiry <= 0:
        return None
    t = days_to_expiry / 365.0
    r = float(rate)
    q = float(dividend_yield) if dividend_yield is not None else 0.0

    sigma = implied_vol(right, p, s, k, t, r, q)
    if sigma is None:
        return None
    return bsm_greeks(right, s, k, t, r, sigma, q)
