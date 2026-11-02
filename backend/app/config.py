"""Every tunable threshold in the app lives here.

The spec is explicit about this: "Thresholds live in a single config module, not scattered
through the code. I will be tuning these." Nothing below should be duplicated as a literal
anywhere else in the codebase.

Sources are cited per block so that when a number is questioned later it is traceable to
either CLAUDE.md (the rules spec) or to a formula read out of one of the .xlsb/.xlsx trackers.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings (connection + account size)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ib_host: str = "127.0.0.1"
    ib_port: int = 7497
    ib_client_id: int = 17
    ib_market_data_type: int = 1

    net_liquidation: Decimal = Decimal("100000")
    cash_available: Decimal = Decimal("50000")

    database_url: str = "sqlite:///./screener.db"
    log_level: str = "INFO"


settings = Settings()


# ---------------------------------------------------------------------------
# Resolved ambiguities. Both are flagged in the plan; neither is silently decided.
# ---------------------------------------------------------------------------

# CLAUDE.md states the credit-spread volatility gate two ways: "implied volatility above 35"
# and "IV percentile 35 or more". These disagree exactly when raw IV is ~39% but the
# percentile is ~4 (verified live on AVGO: raw IV 38.6%, IV percentile 5).
# Both numbers are always displayed. This flag only decides which one *gates*.
IV_GATE_USES: Literal["percentile", "raw"] = "percentile"

# CLAUDE.md's metric block says `dte = expiry_date - quote_date + 1`, but both trackers use a
# plain `EXPIRATION_DAY - TODAY()`. We keep both: `dte` (plain) drives the 30-45 day gates,
# `dte_annualise` drives ROI annualisation only.
DTE_ANNUALISE_PLUS_ONE: bool = True

# Per the user's decision: chart-derived checks are advisory. They are displayed and they feed
# the confluence score, but they never turn a verdict into FAIL. Flip to True to make the
# trend rules hard gates without touching any rule code.
CHART_RULES_ARE_HARD_GATES: bool = False

# CLAUDE.md says `cash_secured = stock_price * 100`, but CSP Template.xlsx cell G6 computes
# `STRIKE * qty * 100`, which is what is actually escrowed against a short put. The workbook
# is right and the spec line is an error, so "strike" is the default -- but the spec's literal
# reading is available for comparison.
CSP_CASH_BASIS: Literal["strike", "stock_price"] = "strike"

# OPRA (US options quotes) is a paid subscription. Without it the feed is 15-min delayed and
# IBKR sends bid/ask but no modelGreeks, so every delta gate would fail on missing data. When
# True, Greeks are backed out of the delayed mid with Black-Scholes-Merton and the quote is
# tagged greeks_source="computed_bsm" so the substitution is never invisible. Set False to
# insist on broker Greeks (i.e. once OPRA is subscribed and the feed is live).
COMPUTE_GREEKS_WHEN_MISSING: bool = True

# Continuously-compounded risk-free rate used by the local pricer. Delta in the 0.20-0.25
# band moves by well under 0.01 per 1% of rate at 30-45 DTE, so a rough figure is fine --
# but revisit it when short-term Treasury yields move materially.
RISK_FREE_RATE: Decimal = Decimal("0.04")


# ---------------------------------------------------------------------------
# Contract + cost model. Commission figure read from CSP Template.xlsx cell K6 (=0.45*D6).
# ---------------------------------------------------------------------------

CONTRACT_MULTIPLIER: int = 100
COMMISSION_PER_CONTRACT: Decimal = Decimal("0.45")
DEDUCT_COMMISSION_FROM_ROI: bool = True

# CLAUDE.md covered-call block: "the dividend is weighted at 70% (post-tax assumption)".
DIVIDEND_WEIGHT: Decimal = Decimal("0.70")

# CLAUDE.md CSP block: `buying_power = cash_secured * 0.20`. Note this is an approximation --
# the workbook takes buying power as a manual input from the broker (H6=2336 against a 15000
# cash requirement, i.e. 15.6%, not 20%). Real reg-T margin varies by name.
BUYING_POWER_PCT: Decimal = Decimal("0.20")


# ---------------------------------------------------------------------------
# Universal gates -- CLAUDE.md "Universal (all strategies)"
# ---------------------------------------------------------------------------

MAX_BID_ASK_SPREAD: Decimal = Decimal("0.50")
MIN_STOCK_PRICE_SPREADS: Decimal = Decimal("20")


# ---------------------------------------------------------------------------
# Liquidity / fundamental gates -- CLAUDE.md credit-spread block
# ---------------------------------------------------------------------------

MIN_MARKET_CAP: Decimal = Decimal("20000000000")  # $20bn
MIN_AVG_VOLUME: int = 1_000_000
# "drug manufacturers" is how Yahoo Finance files pharma (e.g. LLY: "Drug Manufacturers - General").
EXCLUDED_SECTOR_KEYWORDS: tuple[str, ...] = (
    "biotech",
    "pharmaceutical",
    "pharma",
    "drug manufacturers",
)

# Where market cap, sector and earnings date come from. "ibkr" needs a fundamentals
# entitlement (error 10358 without one); "yahoo" uses yfinance. Either way, missing data is
# None and fails the rule. A manual earnings date in the watchlist always wins.
FUNDAMENTALS_SOURCE: str = "yahoo"  # "yahoo" | "ibkr"
YAHOO_CACHE_HOURS = 12
YAHOO_TIMEOUT_SECONDS = 15.0  # per attempt, network only -- the yfinance import is not timed
YAHOO_ATTEMPTS = 2


# ---------------------------------------------------------------------------
# Per-strategy thresholds. Ranges are inclusive at both ends; boundary cases are tested.
# ---------------------------------------------------------------------------

BULL_PUT = {
    "dte": (30, 45),
    "short_delta": (Decimal("-0.25"), Decimal("-0.20")),
    "long_strike_offset": (1, 2),  # strikes below the short
    "min_iv_percentile": Decimal("35"),
    "min_raw_iv": Decimal("35"),  # used only when IV_GATE_USES == "raw"
    "max_loss_credit_multiple": Decimal("4.0"),
    "position_size_pct_nlv": Decimal("0.03"),
    "stop_credit_multiple": Decimal("1.5"),
}

BEAR_CALL = {
    "dte": (30, 45),
    "short_delta": (Decimal("0.20"), Decimal("0.25")),
    "long_strike_offset": (1, 2),  # strikes above the short
    "min_iv_percentile": Decimal("35"),
    "min_raw_iv": Decimal("35"),
    "max_loss_credit_multiple": Decimal("4.0"),
    "position_size_pct_nlv": Decimal("0.03"),
    "stop_credit_multiple": Decimal("1.5"),
}

CASH_SECURED_PUT = {
    "dte": (30, 45),
    # CLAUDE.md gives two modes: -0.30 standalone, -0.20..-0.30 for the wheel.
    "delta_standalone": (Decimal("-0.35"), Decimal("-0.25")),  # centred on -0.30
    "delta_wheel": (Decimal("-0.30"), Decimal("-0.20")),
    "mode": "standalone",  # or "wheel"
    "min_monthly_return": Decimal("0.02"),  # 2-3% per month standalone
    "min_wheel_return_of_strike": Decimal("0.02"),
    "preferred_iv_percentile": Decimal("40"),
}

COVERED_CALL = {
    "dte": (30, 45),
    "delta": (Decimal("0.40"), Decimal("0.45")),
    "min_roi_30d": Decimal("0.02"),  # 2-3% ROI in 30 days
    "preferred_iv_percentile": Decimal("40"),
    "min_shares_owned": 100,
}

LONG_OPTION = {
    "min_dte": 60,
    "delta": (Decimal("0.50"), Decimal("0.60")),
    "max_iv_percentile": Decimal("40"),
    "max_premium_pct_nlv": Decimal("0.02"),
}

DEBIT_SPREAD = {
    "target_dte": 30,
    "dte_tolerance": 10,  # "~30" -- 20..40
    "long_delta": (Decimal("0.50"), Decimal("0.60")),
    "min_width_bull_call": Decimal("5"),
    "min_risk_reward": Decimal("2.0"),
    "max_premium_pct_nlv": Decimal("0.02"),
}


# ---------------------------------------------------------------------------
# Jade lizard -- Jade_Lizard_Notes.pdf, "Optimal entry rules" and "Constructing the trade".
#
# Short put + short call + long call, one expiry. The defining property is structural: collect
# a credit at least as large as the call spread's width and the upside cannot lose, whatever
# the stock does. That single rule is what earns the strategy its earnings exception.
# ---------------------------------------------------------------------------

JADE_LIZARD = {
    # "DTE: 4-6 weeks."
    "dte": (28, 42),
    # "Short Call at ~45 delta, Short Put at ~25 delta (lower is safer)."
    "short_call_delta": (Decimal("0.40"), Decimal("0.50")),
    # Deliberately not a band centred on -0.25. The notes say "~25 delta (or lower is safer)",
    # so 25 is a ceiling on the magnitude, not a target -- a 15-delta put is a better trade,
    # not a worse one, and a band centred on -0.25 would wrongly reject it.
    "short_put_delta": (Decimal("-0.25"), Decimal("-0.08")),
    # How far above the short call the builder will hunt for the long call. Leg 3 is chosen as
    # "the point where the entry credit is equal or greater than the (Bear) Call width".
    "long_call_offset": (1, 6),
    # "Entry credit is equal or greater than the Bear Call width" -- equality passes. Expressed
    # as a ratio so it is one comparison with a testable boundary at exactly 1.0.
    "min_credit_to_call_width": Decimal("1.0"),
    # "Max ROI is greater than 10% with capital based on margin / buying power at entry."
    # Strictly greater, as written; the boundary at exactly 10% is tested and fails.
    "min_roi": Decimal("0.10"),
    # "Break-even on the downside is at the 1-SD point (or lower)."
    "min_breakeven_sd": Decimal("1.0"),
    # "with >70% probability of winning". Approximated from the short put's delta, which is
    # why the rule carrying it is advisory -- delta is a rough proxy for P(expire OTM).
    "min_win_probability": Decimal("0.70"),
    # "Ideally, enter when IV is high -- but it's not a must." Advisory, never a gate.
    "preferred_iv_percentile": Decimal("50"),
    # "Take profit at 1/2 to 2/3 of max profit."
    "take_profit_fractions": (Decimal("0.50"), Decimal("0.6667")),
    # Buying power for the three legs. The bear call spread is fully hedged by the long call,
    # so it adds capital only when the credit fails to cover the width; the short put dominates
    # and is modelled exactly like the CSP (BUYING_POWER_PCT of the escrowed cash) so the two
    # strategies' ROI columns mean the same thing.
    #
    # Real margin on a three-legged position is broker- and name-specific and cannot be
    # derived from the chain. Read the true figure off the broker before sizing; this column
    # is an estimate and is labelled as one in the UI.
    "buying_power_pct_of_put_strike": BUYING_POWER_PCT,
}


# ---------------------------------------------------------------------------
# Synthetic spread (risk reversal) -- Synthetic_Spread_Notes.pdf.
#
# Long an ATM call, short an ATM put (or the mirror for the bearish side). The payoff is the
# stock's own payoff for near-zero premium on roughly a fifth of the capital -- which is also
# why the notes call it "the highest-risk strategy in the course".
# ---------------------------------------------------------------------------

SYNTHETIC = {
    # "DTE: at your time frame -- e.g. 1-3 months, or even LEAPS." The screener covers the
    # 1-3 month reading; LEAPS sit outside the chain window this app fetches.
    "dte": (30, 90),
    # "Strike price: ATM (or near ATM) to have the highest liquidity", with the notes'
    # "With split strikes" variant as the alternative. Flip to "split" to screen that instead.
    "mode": "atm",  # or "split"
    # How far from spot a strike may sit and still count as ATM.
    "atm_tolerance_pct": Decimal("0.03"),
    # In split mode, how many strikes either side of the money the two legs are placed.
    "split_offset": (1, 3),
    # "Net premium is zero (or near zero)." The notes' own HD example is a $0.88 debit on a
    # $191.45 stock, i.e. 0.46% of spot, so 1% is a generous reading of "near zero".
    "max_net_premium_pct_of_spot": Decimal("0.01"),
    # "broker should block buying power at approximately 20%-25% of share value". The notes'
    # HD example implies 22.4% ($4,292 against $19,145). 25% is the conservative end: it
    # understates ROI rather than overstating it.
    "buying_power_pct": Decimal("0.25"),
    # NOT FROM THE COURSE NOTES. The notes give no sizing rule for what they themselves call
    # the highest-risk structure in the set, and the short leg is a genuine obligation, so the
    # screener applies one: blocked buying power must stay under this share of net liquidation.
    # Tune it, or set it to None to drop the gate entirely.
    "max_capital_pct_nlv": Decimal("0.10"),
    # NOT FROM THE COURSE NOTES EITHER. The notes are silent on earnings for this strategy --
    # unlike the jade lizard, where the exception is stated explicitly. A leveraged directional
    # position through an earnings gap is the conservative default, so the gate is on. Set to
    # False if you decide otherwise; nothing else in the code assumes it either way.
    "earnings_gate": True,
}


# ---------------------------------------------------------------------------
# Indicators -- CLAUDE.md "Confluence scoring"
# ---------------------------------------------------------------------------

EMA_FAST = 20
EMA_SLOW = 40
SMA_PERIODS = (50, 100, 150, 200)
REGIME_FAST_MA = 50
REGIME_SLOW_MA = 150
REGIME_SYMBOL = "SPY"  # proxy for SPX; SPX index data needs a separate subscription

STOCHASTIC = {"k": 14, "k_smooth": 3, "d": 3, "oversold": 20.0, "overbought": 80.0}
MACD = {"fast": 12, "slow": 26, "signal": 9}
BOLLINGER = {"period": 20, "std": 2.0}
ATR_PERIOD = 14

# Support/resistance detection: a pivot needs this many bars either side to count, touches
# are clustered when within this many ATRs of each other, and a level needs this many touches.
PIVOT_LOOKBACK = 5
SR_CLUSTER_ATR = 0.5
SR_MIN_TOUCHES = 3
SR_NEAR_ATR = 1.0  # "at" a level means within this many ATRs

# Candle triggers -- CLAUDE.md: "pin bar (body small, one shadow >= 2/3 of range)".
PIN_BAR_MAX_BODY_PCT = 0.33
PIN_BAR_MIN_SHADOW_PCT = 0.66

CONFLUENCE_MANDATORY_LAYERS = ("trend", "structure")
CONFLUENCE_CAP_IF_MANDATORY_FAILS = 2


# ---------------------------------------------------------------------------
# IV percentile
# ---------------------------------------------------------------------------

IV_HISTORY_DAYS = 252  # one trading year
IV_HISTORY_MIN_BARS = 120  # below this the percentile is reported as None, never guessed


# ---------------------------------------------------------------------------
# Earnings. CLAUDE.md: "an unknown earnings date must be treated as a FAIL, not a pass."
# ---------------------------------------------------------------------------

EARNINGS_STALE_AFTER_DAYS = 90
UNKNOWN_EARNINGS_IS_FAIL = True


# ---------------------------------------------------------------------------
# Scanning / pacing. IBKR throttles ~50 req/s and ~100 concurrent market data lines.
# ---------------------------------------------------------------------------

STRIKE_WINDOW_PCT = Decimal("0.15")  # only request strikes within +/-15% of spot

# Candidate breadth. We deliberately build a few contracts either side of the target delta
# band so near misses appear as FAIL rows with the reason attached rather than being invisible.
# Seeing "Short delta -0.283 (-0.25 to -0.20)" is more useful than an empty table.
CANDIDATE_DELTA_TOLERANCE = Decimal("0.10")
MAX_CANDIDATES_PER_STRATEGY = 12
REQUEST_DELAY_SECONDS = 0.12
MARKET_DATA_TIMEOUT_SECONDS = 6.0
MAX_CONCURRENT_MD_LINES = 90
MARKET_DATA_BATCH_SIZE = 40  # option lines open at once; one shared wait per batch
QUOTE_CACHE_TTL_SECONDS = 60
CHAIN_CACHE_TTL_SECONDS = 60
HISTORY_CACHE_TTL_SECONDS = 86_400


# ---------------------------------------------------------------------------
# UI colour bands -- CLAUDE.md "Frontend", matching the original tool.
# ---------------------------------------------------------------------------

ROI_ANNUAL_BANDS = (Decimal("0.15"), Decimal("0.25"), Decimal("0.40"))
CSP_ROI_CASH_BANDS = (Decimal("0.10"), Decimal("0.20"), Decimal("0.30"))
