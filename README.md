# Options Screener

Local options screener. Pulls live option chains from IBKR, applies your per-strategy rules,
and reports **a verdict with the specific gate that failed** — not a table of numbers to
re-interpret.

It replaces a ThinkorSwim spreadsheet scanner that only printed raw numbers. The rules live in
`backend/app/rules/` and every threshold lives in `backend/app/config.py`. Position trackers
record trades *after* entry; this tool sits upstream and picks the trade.

**Read-only. The IBKR socket is opened with `readonly=True`, so the gateway itself refuses
order submission. `placeOrder` is never imported.**

---

## Two ways to use it

**Strategy → stocks.** Pick a strategy, scan the watchlist, get ranked candidates with the
failed gates attached to each row.

**Ticker → strategies.** Type a ticker; all eleven strategies are evaluated against it, so you
see what the name *is* good for rather than only that it failed the thing you checked.

Both run through one `evaluate()` call, and a test asserts the two views can never disagree
about the same trade.

---

## Setup

```powershell

python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"     # or: pip install -r the deps in pyproject.toml
cd frontend; npm install; npm run build; cd ..

copy .env.example .env                     # then edit net liquidation / cash / port
```

### IB Gateway

There is no cloud REST API with an API key — a local process must be running and logged in.

1. Start **IB Gateway** (lighter than TWS) or TWS and log in.
2. Global Configuration → API → Settings → **Enable ActiveX and Socket Clients**.
3. Confirm the socket port: **7497 paper**, **7496 live**. Start on paper.
4. Set `IB_PORT` in `.env` to match.

### Market data subscriptions — what is free and what is not

Market data is billed by subscription, not by which API you use. Everything the rules need
about the *stock* is free: streaming price (Cboe One / IEX), daily bars for every indicator,
`OPTION_IMPLIED_VOLATILITY` history for IV percentile, and the basic fundamentals snapshot
(market cap, sector). Options quotes are the exception:

| Subscription | Monthly | Waived at commissions of | Needed for |
|---|---|---|---|
| US Securities Snapshot and Futures Value Bundle | USD 10.00 | USD 30 | prerequisite for OPRA |
| OPRA Top of Book (L1) | USD 1.50 | USD 20 | live options bid/ask **and** IBKR's Greeks |

Total **USD 11.50/month**, both waived once the account generates ~USD 50/month in
commissions. Minimum equity to hold any subscription is USD 500. Subscribe in Client Portal →
Settings → Market Data Subscriptions; the Value Bundle must be active before OPRA will accept.

**Without OPRA** set `IB_MARKET_DATA_TYPE=3`. The socket then serves 15-minute delayed
bid/ask but no `modelGreeks`. Rather than fail every delta gate on missing data, the app backs
delta/IV/theta out of the delayed mid with Black-Scholes-Merton
(`backend/app/metrics/greeks.py`) and tags each such quote `greeks_source="computed_bsm"`.
The banner says so, the probe prints `computed` next to those rows, and the per-leg source is
in every scan result. Expect small differences from TWS (European model, delayed price,
`RISK_FREE_RATE` in config). Flip `COMPUTE_GREEKS_WHEN_MISSING = False` to insist on broker
Greeks once OPRA is active.

Earnings dates are the other soft spot: `CalendarReport` needs a Wall Street Horizon
entitlement that is patchy over the API even when the TWS calendar shows it. The watchlist's
manual `earnings_date` is the reliable path.

---

## Running

**Phase 1 — prove the pipeline first.** Do not trust anything downstream until the chain
matches what TWS shows on screen.

```bash
.venv\Scripts\python -m backend.app.scripts.probe AVGO
```

Compare bid / ask / delta / IV against the same chain in TWS. Check that the computed IV
percentile is sane — for AVGO during development, IBKR's own 52-week figure was 5.

**Phase 2 — rules in the terminal.**

```bash
.venv\Scripts\python -m backend.app.scripts.scan_cli --strategy bull_put
.venv\Scripts\python -m backend.app.scripts.scan_cli --ticker AVGO --all
```

**Phase 3 — the web page.**

```bash
.venv\Scripts\python -m uvicorn backend.app.main:app --port 8000
```

Open <http://localhost:8000>. For UI work, `cd frontend && npm run dev` proxies to the same API.

**Tests.**

```bash
.venv\Scripts\python -m pytest backend/tests -q
```

---

## Strategies

Eight core strategies: bull put, bear call, cash secured put, covered call, long call, long
put, bull call, bear put.

Three more (jade lizard and the synthetic long and short) break the usual rules in ways worth
knowing before you read their rows.

### Jade lizard — the earnings exception

Short put + short call + long call on one expiry. The defining rule is structural rather than
statistical: **collect a credit at least as large as the call spread's width and the upside
cannot lose**, whatever the stock does. The `Upside Risk` column reads `$0.00` when that holds.

That single property is what licenses the one deliberate exception in this codebase: the jade
lizard is **allowed to be held through earnings**, where every other credit strategy fails on
it. The exception is one-sided and the row says so — a gap *up* is harmless, a gap *down* goes
straight through the short put. Which is why the downside breakeven must sit at or beyond one
standard deviation, and that gate is hard.

**The 1-SD breakeven is the rule that bites.** It and the no-upside-risk rule pull against
each other: the put has to go far enough out to put the breakeven past one standard deviation,
and by the time it does, the credit often no longer covers the call width. Swept across $1 /
$2.50 / $5 ladders, 35–80% IV and 35/42 DTE, most combinations fail — nearly always on the
breakeven. High IV and the long end of the DTE window is what opens the gap, so "enter when IV
is high" falls out of the arithmetic rather than being asserted.

Because of that, the screener **fans the short put across the delta band instead of picking
one strike** — including strikes past the −0.25 ceiling, which breach the delta gate but carry
the credit that makes the structure riskless. Each row shows its own compromise (`B/E in SD`
against `Credit / Width` against `Max ROI`) with the breached gate named. Which corner to take
is a judgement, so the tool lays them out rather than choosing.

The payoff is lopsided (small wins, large losses), so the win rate does nearly all the work and
the stop is not optional.

The `B. Power (est.)` and `Max ROI` columns are estimates. Real margin on a three-legged
position is broker-specific and cannot be derived from the chain, so the rule message says
`estimated` on every row. Read the true figure off the broker before sizing.

### Synthetic spread — no IV gate, and two extra rules

Long an ATM call and short an ATM put (or the mirror, bearish) reproduces the stock's payoff
for near-zero premium on roughly a fifth of the capital.

**IV does not gate it.** Long vega on the call cancels short vega on the put. Rather than
leave a blank where every other strategy has a volatility gate, the row says so explicitly and
still prints both IV measures.

**Two extra gates apply here**, and both say so in their own failure messages:

| Gate | Why it exists | Switch |
|---|---|---|
| Capital budget ≤ 10% of net liq | It is the highest-risk structure here and has no sizing rule of its own | `SYNTHETIC["max_capital_pct_nlv"]`, `None` to drop |
| Earnings must be outside expiry | Unlike the jade lizard, nothing licenses holding a leveraged directional position through a gap | `SYNTHETIC["earnings_gate"]` |

`Max profit` reads **unlimited** on the long side and `Max loss` reads **unlimited** on the
short side. That is the one place in this codebase where a missing number does not mean
missing data, and the UI renders it as a word rather than a dash.

A split-strike variant is screened by setting `SYNTHETIC["mode"] = "split"`.

---

## Two ambiguities the tool refuses to hide

Both are surfaced in the UI header and switchable in `backend/app/config.py`.

**1. "IV above 35" means two different things.** Raw IV and IV percentile disagree exactly when
it matters. AVGO, measured live: raw IV **38.6%** (reads rich → sell premium) versus IV
percentile **5** (reads cheap → do not sell premium). Both columns are always shown; the gate
runs on percentile. `IV_GATE_USES`.

**2. DTE annualisation.** The written rules say `expiry − quote_date + 1`; the original
spreadsheets use plain `expiry − TODAY()`. On a 33-day trade that is ~3% of annualised ROI.
Both DTE values are carried; `DTE_ANNUALISE_PLUS_ONE` decides which annualises.

A third difference was resolved in the spreadsheet's favour: the written rules say
cash-secured-put capital is `stock_price * 100`, but the spreadsheet uses `strike * qty * 100`,
which is what is actually escrowed. `CSP_CASH_BASIS` defaults to `strike`, and the formula
tests assert exact parity with the spreadsheet formula.

---

## What decides a verdict

**Hard gates** — bid/ask spread, stock price, market cap, average volume, sector, IV, DTE,
earnings, delta, strike offsets, max-loss ratio, risk:reward, position size, premium budget,
and for the two newer structures: no-upside-risk, 1-SD downside breakeven, ATM strike
placement, near-zero net premium, assignment capacity.

**Advisory** — every chart-derived signal: SPX regime, 20/40 EMA, 50/150 MA, stochastic, MACD,
Bollinger, support/resistance proximity, candle triggers. These are displayed as amber chips and
feed the 0–5 confluence score, but **never fail a row**. Flip `CHART_RULES_ARE_HARD_GATES` to
change that without touching rule code.

**Missing data fails, it never passes.** An unknown earnings date shows as
`UNKNOWN — verify manually` and blocks the row, as the ruleset requires. The jade
lizard is the single documented exception, and it still shows the date rather than clearing
the gate quietly.

---

## Adding a strategy

One rules module in `backend/app/rules/`, one builder in `scan/builder.py`, one entry in
`registry.py`. Nothing in the API or the UI changes — both read the strategy list and its
columns from the registry. The jade lizard and the synthetic spread were added exactly that
way; `test_scan.py` asserts that every registered strategy builds candidates, produces a
verdict, and declares no column that nothing fills.

Calendar, diagonal, iron condor and other structures would slot in the same way; they just
need their entry criteria defined first.

---

## Layout

```
backend/app/
  config.py            every threshold, nothing hardcoded elsewhere
  domain.py            the types the rules reason about
  ib/                  gateway connection, pacing, chains, history, fundamentals
  metrics/             trade formulas, indicators, IV percentile, BSM Greeks
  rules/               one module per strategy + shared gates + registry
  scoring/             0-5 confluence, layers 1 and 2 mandatory
  scan/                candidate construction, evaluation, the scan loop
  api/, main.py        FastAPI; serves /api and the built UI on one port
  scripts/             probe (phase 1), scan_cli (phase 2)
frontend/src/          React + Vite + Tailwind, two tabs over one endpoint
backend/tests/         386 tests: pass, fail and boundary for every gate
```
