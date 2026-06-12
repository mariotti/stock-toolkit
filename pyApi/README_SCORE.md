# Stock Scorer

`stock_toolkit/score.py` runs all seven analysis steps against one or more symbols
and produces a ranked investment score from 0 to 100. Designed to answer a
single question: **right now, for my time horizon, which of these symbols
looks best to buy?**

The score is a data summary — not a buy recommendation. Always review the
full 7-step output before committing capital.

---

## Table of contents

- [Quick start](#quick-start)
- [How the score works](#how-the-score-works)
- [The horizon flag](#the-horizon-flag)
- [Weight profiles by horizon](#weight-profiles-by-horizon)
- [Output explained](#output-explained)
- [All flags](#all-flags)
- [Examples](#examples)
- [Interpreting the results](#interpreting-the-results)
- [Limitations](#limitations)

---

## Quick start

```bash
# Score all symbols in the database, default horizon (quarter)
stock-score

# Score specific symbols
stock-score -s AAPL MSFT GOOGL CSMIB.MI TSLA ENEL.MI

# With a date range and a horizon
stock-score \
    -s AAPL MSFT GOOGL CSMIB.MI TSLA ENEL.MI \
    --from 2023-01-01 \
    --horizon quarter

# Show top 3 with per-metric breakdown
stock-score --from 2023-01-01 --top 3 --detail
```

---

## How the score works

Every symbol is evaluated across seven components, each worth a fixed number
of points. The total always sums to 100. A penalty system can reduce the
score below the raw component total.

### The nine components

| Component | What it measures | Source step |
|---|---|---|
| Sharpe ratio | Annualised return per unit of risk | Step 2 summary |
| Calmar ratio | Annualised return per unit of max drawdown | Step 4 drawdown |
| R² (regression) | How consistently the price has trended | Step 3 regression |
| Annualised trend | The slope of the trend as %/year | Step 3 regression |
| RSI entry | Is the current RSI a good entry point? | Step 5 RSI |
| %B entry | Where is price in the Bollinger Band? | Step 5 BBands |
| Prob(gain) | Monte Carlo probability of finishing above current price | Step 7 Monte Carlo |
| Momentum | 12-1 month and 3-month price momentum (daily bars) | Step 8 momentum |
| Hurst persistence | Do this symbol's return trends persist or mean-revert? | Step 9 Hurst |

### Component scoring logic

**Sharpe** — linear scale from 0 to 2.0. Sharpe=2.0 earns the full weight;
negative Sharpe earns 0.

**Calmar** — linear scale from 0 to 20. Calmar=20 earns the full weight.
Calmar above 20 is capped. Negative Calmar earns 0.

**R²** — direct: R²=0.90 earns 90% of the weight. R²=0.30 earns 30%.

**Annualised trend** — linear from 0 to 50%/year. A 50%/yr trend earns
the full weight. Negative trends earn 0.

**RSI entry** — scored on where RSI sits:

| RSI range | Score | Rationale |
|---|---|---|
| Below 30 | 75% | Oversold — good entry but can fall further |
| 30 – 50 | 75–100% | Sweet spot: recovering from oversold |
| 50 – 60 | 50% | Neutral |
| 60 – 70 | 25% | Getting extended |
| Above 70 | 0% | Overbought — poor entry timing |

**%B entry** — scored on Bollinger Band position:

| %B range | Score | Rationale |
|---|---|---|
| Below 0 | 100% | Below lower band — rare high-quality entry |
| 0 – 0.2 | 90% | Near lower band — strong entry zone |
| 0.2 – 0.4 | 70% | Lower half — good |
| 0.4 – 0.6 | 40% | Mid-band — neutral |
| 0.6 – 0.8 | 20% | Upper half — extended |
| Above 0.8 | 0% | Near upper band — overbought |

A ⚡ symbol in the %B column means a Bollinger Band squeeze is active —
bandwidth is in the bottom 20th percentile of its own history, which often
precedes a significant move.

**Prob(gain)** — direct: 89% probability of gain earns 89% of the weight.

**Momentum** — the classic cross-sectional predictor. The 12-1 return
(last ~12 months excluding the most recent month, so short-term reversal
doesn't pollute it) is scaled linearly from −30% → 0 points to +60% →
full points and blended 60/40 with the 3-month return (−15% → +30%
scale). With under ~13 months of history the 12-1 window falls back to
available-history-minus-last-month and is flagged `partial` in the
detail output.

**Hurst persistence** — the Hurst exponent of daily *log returns*
(computed on returns, not prices — price-level R/S saturates near 1 for
anything uptrending). H=0.5 is a random walk; above it, moves tend to
continue; below it, they tend to reverse. Scaled linearly from H=0.40
(0 points) to H=0.65 (full points), so a random-walk symbol earns 40%
of the weight and a strongly trending one earns all of it. This
effectively weights the trend components by how trustworthy trends are
for that particular symbol.

### Optional: valuation adjustment (`--fundamentals`)

`stock-score --fundamentals` fetches a per-symbol valuation snapshot via
yfinance (network call, no API key) and applies a transparent adjustment
of at most ±5 points after the 100-point score:

| Signal | Adjustment |
|---|---|
| Forward P/E below 15 | +2 |
| Forward P/E above 40 | −2 |
| Forward P/E below trailing P/E (earnings expected to grow) | +1 |
| Revenue growth above +10% YoY | +2 |
| Revenue shrinking (negative YoY) | −2 |

The adjustment and its reasons appear in the `--detail` notes as a
`VALUATION:` line. Symbols with no fundamentals data are left untouched.

### Penalties

Penalties are subtracted from the raw score and can push it below zero
(floored at 0):

| Condition | Penalty | Rationale |
|---|---|---|
| Drawdown not yet recovered | −20 pts | Price still below its worst peak |
| Annualised vol > 50% | −10 pts | Too volatile for most position sizes |
| Max drawdown worse than −40% | −10 pts | Severe historical loss |
| Fewer bars than horizon minimum | −30 pts | Insufficient data to trust metrics |

---

## The horizon flag

```bash
--horizon week | month | quarter | year | life
```

The horizon reshapes three things simultaneously:

1. **Weight profile** — how much each component contributes to the score
2. **Granularity** — what bar size is used for indicator computation
3. **Monte Carlo horizon** — how many bars forward the simulation runs

This means the same symbol can rank very differently depending on your
intended holding period. A stock with poor short-term RSI but an excellent
5-year trend will score low for `week` and high for `life`.

---

## Weight profiles by horizon

The key insight: **entry timing dominates for short horizons; trend quality
and risk dominate for long horizons.**

```
            entry timing   trend quality   risk-adjusted   MC prob   momentum   hurst
            (RSI + %B)     (R² + trend)    (Sharpe+Calmar)
──────────────────────────────────────────────────────────────────────────────────────
week           60 pts          8 pts           8 pts         8 pts      8 pts    8 pts
month          40 pts         12 pts          16 pts        12 pts     12 pts    8 pts
quarter        24 pts         16 pts          32 pts         8 pts     14 pts    6 pts
year            8 pts         28 pts          40 pts         4 pts     14 pts    6 pts
life            4 pts         34 pts          48 pts         0 pts      8 pts    6 pts
──────────────────────────────────────────────────────────────────────────────────────
```

**Why entry timing goes to zero for `life`:**
When you're buying a stock to hold for 5+ years, whether you enter at
RSI=32 vs RSI=48 this week is almost irrelevant. The quality and
consistency of the long-term trend (R², slope) and the risk profile
(Sharpe, Calmar, max drawdown) are what matter. Monte Carlo also gets
0 weight for `life` — a 5-year GBM simulation calibrated on recent
bars says almost nothing useful about a decade-long holding.

**Why entry timing dominates for `week`:**
Over 5 trading days, trend quality is largely irrelevant. The return is
dominated by whether you bought at an oversold extreme vs an overbought
peak. The bulk of the points (60) goes to RSI and %B.

**Why momentum peaks at quarter/year:**
The 12-1 momentum effect is documented at 3–12 month holding periods —
it gets its largest weight (14 pts) for those horizons, less for `week`
(too short for momentum to play out) and `life` (momentum decays and
eventually reverses at multi-year horizons).

**Granularity by horizon:**

| Horizon | Granularity | MC bars | Min bars needed |
|---|---|---|---|
| week | Daily (D) | 5 | 30 |
| month | Daily (D) | 21 | 60 |
| quarter | Weekly (W-FRI) | 63 | 60 |
| year | Weekly (W-FRI) | 252 | 100 |
| life | Monthly (ME) | 60 months | 120 |

For `week` and `month`, daily bars are used so RSI and Bollinger Bands
reflect short-term momentum. For `life`, monthly bars are used so the
Sharpe and regression are computed on 10+ years of monthly returns,
not weeks — a fundamentally different signal.

---

## Output explained

```
Horizon:  QUARTER  —  Next quarter (~63 trading days)
Weights:  entry 30pts  trend 20pts  risk-adj 40pts  MC 10pts
Scoring:  6 symbol(s)  [2023-01-01 → now, MC 500p × 63b, gran=W-FRI]

#  Symbol    Score /100            bar           Sharpe  Calmar  Vol    Max DD   RSI  %B     P(gain)
─  ────────  ────────────────────────────────    ──────  ──────  ─────  ───────  ───  ─────  ───────
1  CSMIB.MI  74.3  ██████████████░░░░░░        1.38    14.19   16.5%  -12.8%   49   0.13   89%
2  GOOGL     68.1  █████████████░░░░░░░         1.32    16.04   30.7%  -28.6%   44   -0.09  85%
3  ENEL.MI   61.4  ████████████░░░░░░░░         1.25    17.37   19.2%  -10.9%   54   0.44   86%
4  AMZN      54.2  ██████████░░░░░░░░░░         1.05     8.90   28.8%  -28.1%   39   0.19   79%
5  TSLA      31.7  ██████░░░░░░░░░░░░░░         0.91     9.77   56.6%  -47.7%   39   0.07   66%
6  MSFT      12.0  ██░░░░░░░░░░░░░░░░░░         0.77     3.38   23.0%  -31.6%   25   0.10   73%

  Top pick:  CSMIB.MI  (74.3/100)
  MC:        89% prob of gain  |  P50=268.69  P5=195.73
  Entry:     RSI=49  %B=0.13
  Risk:      max DD=-12.8%  Calmar=14.19  (recovered)

  Best pair: CSMIB.MI + GOOGL  (scores: 74.3 + 68.1)
  Rationale: different market families → likely low correlation
```

**Column guide:**

| Column | Meaning |
|---|---|
| Score /100 | Total score with visual bar |
| Sharpe | Annualised risk-adjusted return |
| Calmar | Annualised return / max drawdown |
| Vol | Latest rolling annualised volatility |
| Max DD | Worst peak-to-trough decline |
| RSI | RSI-14 at the current bar |
| %B | Bollinger Band position (⚡ = squeeze active) |
| P(gain) | Monte Carlo probability of being above current price at horizon |

**Top pick summary** — printed automatically for the #1 ranked symbol:
the key MC numbers, current entry timing, and risk metrics in one line.

**Best pair** — the scorer identifies the highest-ranked symbol from a
different exchange family (US vs European/other) as the natural
diversification partner, since cross-family correlation tends to be low.

---

## All flags

```
--horizon     Investment horizon (default: quarter)
              week     — next 5 trading days   (entry timing 70%)
              month    — next 21 trading days  (entry timing 50%)
              quarter  — next 63 trading days  (balanced, default)
              year     — next 252 trading days (trend quality 60%)
              life     — 5+ years buy-and-hold (trend + risk 95%)

-s / --symbols   Symbols to score (default: all available in the database)
--from           Start of date range for analysis (YYYY-MM-DD)
--to             End of date range for analysis (YYYY-MM-DD)
--top N          Show only the top N ranked symbols
--mc-paths N     Number of Monte Carlo paths (default: 500)
                 MC horizon is set automatically by --horizon
--detail         Show the per-metric point breakdown for every symbol
--json           Output full results as JSON (for scripting or logging)
```

---

## Examples

### Morning scan — what looks interesting today?

```bash
# Quick weekly scan on your watchlist
stock-score \
    -s AAPL ARM AMD AMZN MSFT GOOGL CSMIB.MI TSLA ENEL.MI \
    --from 2023-01-01 \
    --horizon week
```

### Quarterly investment decision

```bash
# Balanced score — entry timing + trend + risk
stock-score \
    -s AAPL ARM AMD AMZN MSFT GOOGL CSMIB.MI TSLA ENEL.MI \
    --from 2023-01-01 \
    --horizon quarter \
    --top 3 \
    --detail
```

### Long-term portfolio review

```bash
# Ignore entry timing completely — score on 10-year trend quality and risk
stock-score \
    -s AAPL MSFT GOOGL CSMIB.MI ENEL.MI \
    --horizon life \
    --detail
```

### Score all symbols in the database

```bash
# Discover and score everything automatically
stock-score --horizon quarter --top 5
```

### Compare the same symbols across all horizons

```bash
# Run once per horizon to see how the ranking shifts
for h in week month quarter year life; do
    echo "=== $h ==="
    stock-score \
        -s AAPL GOOGL CSMIB.MI ENEL.MI TSLA \
        --from 2023-01-01 \
        --horizon $h \
        --top 3
done
```

### High-precision run before investing

```bash
# More Monte Carlo paths for a more stable probability estimate
stock-score \
    -s AAPL GOOGL CSMIB.MI ENEL.MI \
    --from 2023-01-01 \
    --horizon quarter \
    --mc-paths 2000 \
    --detail
```

### Machine-readable output for logging or scripting

```bash
# Full JSON output — pipe to jq or save to file
stock-score \
    -s AAPL GOOGL CSMIB.MI TSLA \
    --from 2023-01-01 \
    --horizon quarter \
    --json | jq '.[0:3] | .[] | {symbol, score, horizon}'

# Save to a dated file for tracking scores over time
stock-score --from 2023-01-01 --json \
    > scores_$(date +%Y-%m-%d).json
```

### Integrate with the alert system

```bash
# Collect fresh data, then score immediately
stock-collect && \
stock-score \
    -s AAPL GOOGL CSMIB.MI ENEL.MI TSLA \
    --from 2023-01-01 \
    --horizon quarter \
    --top 3
```

### Use from the shell wrapper

If you have the `analyse` shell wrapper in `~/bin`:

```bash
# The scorer is a standalone script — call it directly or wrap it yourself
python3 /path/to/stock_toolkit/score.py --horizon quarter --top 3
```

---

## Interpreting the results

### What a high score means

A score above 60 means the symbol passes most of the eleven checks in the
decision scorecard: good Sharpe, good Calmar, consistent trend, reasonable
drawdown that has recovered, current RSI/BBands suggesting a decent entry,
and a strong Monte Carlo probability of gain. It does not mean the stock
will go up.

### What a low score means

A score below 30 typically means one or more penalties fired (unrecovered
drawdown, high volatility, deep max drawdown) or multiple components scored
poorly simultaneously. MSFT scoring 12 in the example above is primarily
driven by an unrecovered drawdown (−20) and a weak Sharpe.

### How the ranking shifts with horizon

Running the scorer on the same symbols across all five horizons is one of
the most useful exercises. A symbol that ranks #1 for `week` (strong RSI
entry signal) but #5 for `life` (mediocre long-term trend) tells you it
may be a good short-term trade but not a long-term compounder. The reverse
pattern (poor weekly entry, excellent long-term fundamentals) often
describes a good buy-and-hold candidate that is just temporarily extended.

### The best pair suggestion

The scorer heuristically identifies a diversification partner by finding
the highest-ranked symbol from a different exchange family (US vs
European/other). This is a coarse proxy for low correlation — the proper
check is always to run:

```bash
stock-analyse -s SYM1 SYM2 --from 2023-01-01 --analysis correlation
```

before committing to a two-position split.

### Score stability

Re-running the scorer on different days will produce different scores as
prices move and the RSI/%B signals shift. This is intentional — the entry
timing components are designed to be sensitive to current conditions.
The trend and risk components (Sharpe, Calmar, R²) are more stable since
they are computed over the full date range.

---

## Limitations

**The score is backward-looking.** Every metric is computed from historical
data. A high score describes what has happened, not what will happen.

**Entry timing is daily-data dependent.** RSI and %B are computed on the
most recently collected bars. If the collector has not run today, the entry
signals may be stale. Always run `stock_toolkit/collector/` before `stock_toolkit/score.py`
on a day you plan to trade.

**Monte Carlo assumes constant drift and volatility.** The GBM model
underestimates crash risk and overestimates drift when calibrated on a
bull-market period. The P5 downside is more trustworthy than the expected
return. See `ANALYSIS.md` for a full discussion of Monte Carlo limitations.

**The `life` horizon needs long history.** Monthly granularity requires at
least 120 bars (10 years) to avoid the thin-data penalty. If you have only
2–3 years of data (e.g. ARM), the `life` score will be penalised heavily
and is not meaningful.

**Scores are not comparable across different `--from` dates.** A score
computed from 2020 onwards includes the COVID crash and recovery. The same
symbol scored from 2023 onwards does not. Always use a consistent date range
when comparing symbols or tracking scores over time.

**The best pair suggestion is a heuristic.** Exchange family (US vs
European) is a reasonable proxy for low correlation but is not a substitute
for the actual correlation matrix. Two European symbols can be highly
correlated; a US and a European symbol can occasionally be highly correlated
during global market stress events.


