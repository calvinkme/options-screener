# Options Trading Screener — Project Summary

**Personal project · Python, FastAPI, React/TypeScript, SQLite, Interactive Brokers API**

An options screener that connects to Interactive Brokers, scans a watchlist of US stocks, and checks
every candidate trade against a set of rules. For each trade it shows **which rule passed or
failed and why**. It replaced a ThinkorSwim spreadsheet scanner that only printed raw numbers
for me to interpret by hand.

```
AVGO Oct16 340/330 Bull Put   FAIL
  ✗ IV percentile 4 (need ≥35)
  ✗ 20EMA 372.97 < 40EMA 380.42 (need 20 > 40)
  ✓ DTE 37 (30–45)
  ✓ Short delta -0.233 (-0.20 to -0.25)
  ✓ Max loss 4.0x credit (need ≤4x)
```

---

## At a glance

| | |
|---|---|
| **Size** | ~11,700 lines (Python backend + React/TypeScript frontend) |
| **Tests** | 386 automated tests (pytest), all passing |
| **Strategies** | 10: bull put, bear call, cash-secured put, covered call, long call/put, bull call / bear put debit spreads, jade lizard, synthetic long/short |
| **Data sources** | Interactive Brokers (`ib_async` over IB Gateway), Yahoo Finance fallback |
| **Stack** | Python 3.11, FastAPI, SQLAlchemy/SQLite, NumPy, React 18, Vite, Tailwind, TypeScript |

---

## What it does

- **Rules engine.** Each trading rule is its own testable check that returns
  `(passed, actual, required, message)`. Rules are never merged into one true/false result.
  All thresholds live in one config module so they are easy to tune.
- **Financial metrics.** Reproduces the original spreadsheet formulas exactly: annualised
  premium, ROI on cash and on margin, max profit/loss, breakeven, loss ratio and risk:reward
  for credit and debit spreads. All money is stored as `Decimal`.
- **Black-Scholes-Merton pricing.** When the broker sends no Greeks (common on free delayed
  data), the app works out implied volatility and delta, gamma, theta and vega from the
  delayed mid price. These values are clearly labelled as computed, not broker data.
- **IV percentile from stored history.** The broker's API does not provide this field, so the
  app pulls historical implied volatility and calculates it locally. The result was checked
  against IBKR's own 52-week figure.
- **Confluence score (0–5) from daily price bars.** Five layers: moving-average trend,
  support/resistance with 3 or more touches, Stochastic and MACD crosses, Bollinger Band
  tags and squeezes, and pin-bar or engulfing candle patterns. The trend and structure
  layers are required, so if either fails the score is capped at 2.
- **Web interface.** A FastAPI backend serves a dense, sortable React table. It has verdict
  chips for each failed rule, ROI colour bands, filters by strategy, verdict and score, a
  payoff diagram for each trade, and a clear **delayed-data** banner.

## Engineering highlights

- **Working within broker rate limits.** IBKR limits roughly 50 requests per second and
  about 100 open market-data lines. The scanner sends requests one at a time, caches chains
  and quotes in SQLite, and on pacing errors (162/200) it waits and keeps going rather than
  stopping the scan.
- **Missing data fails the rule.** If a price, Greek or earnings date is missing, the value
  stays `None` and the rule fails. The app never fills in a likely-looking number. An unknown
  earnings date shows `UNKNOWN — verify manually` and blocks the trade.
- **Handling messy data sources.** Yahoo Finance is used when IBKR refuses fundamental data
  (error 10358). The app handles known Yahoo problems: pharma companies filed under
  "Drug Manufacturers", and old earnings dates that would otherwise let a trade pass the
  earnings check.
- **Conflicting rules are shown, not hidden.** My trading notes state the volatility rule two
  ways (raw IV vs IV percentile), which can give opposite answers. The app shows both values,
  and a config flag sets which one is used.
- **Easy to add strategies.** A strategy registry defines each strategy's rules, columns and
  direction in one place. The jade lizard and synthetic strategies were each added with one
  rules module, one builder and one registry entry, with no changes to the API or the UI.
- **Read-only by design.** The app never places orders. It only suggests trades, and every
  IBKR request and response is written to a rotating log file for auditing.
- **Test discipline.** Each rule is tested with a passing case, a failing case and a boundary
  case. For example, a "≤ 4.0×" rule is tested at exactly 4.0.

---

## Resume bullets

Pick 3–4 and adjust to the role.

**General software / backend**

- Built a full-stack options screener (Python/FastAPI, React/TypeScript, SQLite) on the
  Interactive Brokers API. It checks 10 options strategies against a configurable rules
  engine and shows the reason for every pass or fail.
- Designed a modular rules engine where each threshold is a separate, unit-tested check,
  backed by 386 automated pytest tests including boundary cases.
- Built a rate-limited market-data pipeline with SQLite caching and automatic back-off on
  broker pacing errors, so scans keep running despite IBKR's ~50 req/s limit.
- Built a strategy registry that let me add new option strategies without changing the API
  or frontend.

**Quant / finance angle**

- Implemented Black-Scholes-Merton implied-volatility solving and Greeks to fill in missing
  broker data, and calculated IV percentile from stored historical volatility.
- Built a 0–5 technical confluence score (moving-average trend, support/resistance,
  Stochastic/MACD, Bollinger Bands, candlestick patterns) that is recorded with each trade so
  scores can later be compared with trade outcomes.
- Moved a ThinkorSwim spreadsheet scanner into tested Python code, reproducing its
  ROI, breakeven and risk formulas exactly using `Decimal` arithmetic.

**One-line version**

> Options screener (Python, FastAPI, React, IBKR API): rules engine with per-rule pass/fail
> verdicts across 10 strategies, Black-Scholes Greeks, IV percentile, and 386 automated tests.

---

## Interview talking points

1. **Why verdicts instead of numbers?** A table of numbers still has to be interpreted, so
   the rules were not really being applied. Showing which rule failed makes the decision
   auditable.
2. **Missing-data policy.** In a trading tool, a guessed value is worse than a blank one,
   because it can let a bad trade through. Every gate treats `None` as a failure.
3. **Working with a constrained API.** IBKR has no REST API key, needs a local logged-in
   gateway, enforces strict rate limits, and Greeks need a paid data feed. Each limit shaped
   the design: sequential requests, caching, local BSM pricing and clear delayed-data labels.
4. **Next planned work.** A portfolio-level correlation check (spotting several correlated
   positions that each pass alone), alerts when held positions hit exit triggers, and a
   trade journal to compare real win rates with the rules' assumptions.
