# Analysis tools reference

This document covers every tool available in `stock_toolkit/analysis.py` — what it
measures, when to use it, how to interpret the output, known limitations, and
practical examples.

For CLI reference and flags see `README.md`.

---

## Table of contents

- [How data is prepared](#how-data-is-prepared)
- [summary](#summary)
- [regression](#regression)
- [returns](#returns)
- [volatility](#volatility)
- [correlation](#correlation)
- [sma](#sma)
- [drawdown](#drawdown)
- [rsi](#rsi)
- [bbands](#bbands)
- [montecarlo](#montecarlo)
- [hurst](#hurst)
- [Combining tools](#combining-tools)
- [Choosing granularity](#choosing-granularity)
- [Common pitfalls](#common-pitfalls)

---

## How data is prepared

Before any analysis runs, the pipeline does three things:

1. **Source deduplication** — multiple APIs often report the same date. One
   source is kept per `(symbol, date)` using a priority order:
   `alphavantage → fmp → yfinance → finnhub → twelvedata → polygon → marketstack`.
   Override with `--source NAME`.

2. **Interval filter** — `--interval 1d` loads daily bars (default),
   `--interval 1h` loads hourly bars. The interval affects which rows are
   read from the database before resampling.

3. **Resampling** — daily bars can be resampled up to weekly, monthly, or
   quarterly (`--granularity`). Intraday bars can be resampled to 2h or 4h
   buckets. Resampling uses OHLCV aggregation: open=first, high=max, low=min,
   close=last, volume=sum.

All analyses operate on the `close` field by default. Use `--field` to
change this to `open`, `high`, `low`, `volume`, `vwap`, or `change_pct`.

---

## summary

**What it measures:**
A broad descriptive overview of a symbol over the loaded date range. Reports
first and last price, total return, min/max, mean, standard deviation,
annualised volatility, Sharpe ratio, and total bar count.

**Output columns:**

| Column | Description |
|---|---|
| First | Price at the first bar in the range |
| Last | Price at the last bar in the range |
| Total ret | Percentage change from first to last |
| Min / Max | Lowest and highest close in the range |
| Mean | Simple average of all closes |
| Std | Standard deviation of closes |
| Ann.vol | Annualised volatility: `std(returns) × √annualisation_factor × 100` |
| Sharpe | `mean(returns) / std(returns) × √annualisation_factor` (risk-free rate = 0) |
| Bars | Number of data points loaded |

**When to use it:**
Always. Run `summary` first on any symbol you haven't looked at before.
It gives you an immediate orientation: is the stock up or down over the
period, how volatile is it, and is the return adequate for the risk taken
(Sharpe).

**Interpreting Sharpe ratio:**
- `< 0` — negative return, losing money in absolute terms
- `0 – 0.5` — poor risk-adjusted return
- `0.5 – 1.0` — moderate
- `1.0 – 2.0` — good
- `> 2.0` — very strong (may indicate a short or unusual period)

Note: Sharpe here uses risk-free rate = 0. For a true Sharpe you'd subtract
the prevailing T-bill rate from returns. For relative comparison between
symbols this doesn't matter.

**When it can mislead:**
- If the range starts at a trough and ends at a peak (or vice versa), total
  return will look unusually good or bad.
- Annualised volatility from a short range (e.g. 30 days) is noisy. Use
  `volatility` with a rolling window for a more stable estimate.
- Mean and std of raw prices (not returns) have little statistical meaning
  for trending assets.

**Example:**
```bash
stock-analyse -s AAPL MSFT GOOGL --analysis summary
stock-analyse -s AAPL --from 2022-01-01 --to 2022-12-31 --analysis summary
```

---

## regression

**What it measures:**
Fits a straight line through price vs elapsed time using ordinary least squares.
Answers the question: "what is the average rate of price change per day, and
how consistent is that trend?"

**Output columns:**

| Column | Description |
|---|---|
| Slope | Price change per bar (e.g. `+0.38/day`) |
| Ann.trend | Slope annualised as a % of starting price |
| R² | Proportion of variance explained by the trend (0–1) |
| p-value | Probability of observing this slope by chance if the true slope is zero |
| Sig(5%) | Yes if p-value < 0.05 |

**With `--plot`:**
Renders the price series, the regression line, and a shaded 95% confidence
interval band around the line.

**When to use it:**
- To quantify whether a visual trend is statistically real.
- To compare the trend strength of two symbols over the same period.
- To check whether a recovery after a crash has resumed a prior trend.
- For macro analysis: annual or multi-year views with `--granularity 1M`.

**Interpreting R²:**
- `0.0 – 0.3` — weak trend, price moves are mostly noise
- `0.3 – 0.7` — moderate trend, consistent directional drift
- `0.7 – 1.0` — strong trend, price tracks the line closely

**When it fails or misleads:**
- Linear regression assumes a constant rate of change. Real stocks
  accelerate and decelerate — a strong R² over a long period may hide
  structural breaks.
- A low p-value does not mean the trend will continue. It only says the
  trend in the historical data is unlikely to be random.
- Very short ranges (< 20 bars) produce unreliable statistics.
- Stocks with high volatility will have wide confidence bands that make
  the regression less actionable.
- For cyclical or mean-reverting assets, a linear fit is the wrong model.
  Use `hurst` first to check whether the series is trending at all.

**Good ranges:**
- `1–5 years` of daily data gives a meaningful medium-term trend.
- `10+ years` of monthly data (`--granularity 1M`) gives a macro view.
- Intraday (`--interval 1h`) regression on a few days tells you the
  direction of the current session.

**Example:**
```bash
stock-analyse -s AAPL --analysis regression --plot
stock-analyse -s AAPL MSFT --from 2020-01-01 --analysis regression --plot
stock-analyse -s AAPL --from 2015-01-01 --granularity 1M --analysis regression --plot
```

---

## returns

**What it measures:**
The distribution of periodic returns — the percentage change from one bar to
the next. Answers: "how big are the typical daily moves, and in which
direction?"

**Output columns:**

| Column | Description |
|---|---|
| Mean ret | Average periodic return |
| Std | Standard deviation of returns (raw, not annualised) |
| Worst | Largest single-period loss |
| Best | Largest single-period gain |
| % positive | Percentage of bars where price went up |
| Sharpe | Annualised Sharpe ratio (same as in `summary`) |

**With `--plot`:**
Histogram of returns per symbol, with a vertical line at zero and at the mean.

**When to use it:**
- To understand the shape of the return distribution: is it symmetric, or
  skewed toward losses?
- To compare the risk profiles of two assets at the same granularity.
- To check whether returns are roughly normal (required by many models).
- Before running `montecarlo` — the simulation uses the mean and std you
  see here.

**Interpreting the histogram:**
- A symmetric bell curve around zero is what most models assume.
- A distribution skewed to the left (fat left tail) means crashes are more
  common than rallies of the same size — typical of equities.
- Very high kurtosis (fat tails, narrow peak) means more extreme events
  than a normal distribution predicts.

**When it fails or misleads:**
- Returns computed at different granularities are not directly comparable.
  Weekly returns have different scaling than daily returns.
- `% positive` above 50% does not mean a stock is a good investment —
  a stock can have many small gains and one catastrophic loss.
- Mean return close to zero does not mean low risk — it could mean many
  equal wins and losses that cancel out.
- Autocorrelation in returns (trending or mean-reverting behaviour) violates
  the independence assumption used by Sharpe and most risk models. Check
  with `hurst`.

**Good ranges:**
- 1–5 years of daily data gives a stable distribution.
- Fewer than 50 bars produces an unreliable histogram.
- Weekly or monthly granularity smooths out noise but loses information
  about intraday/daily extremes.

**Example:**
```bash
stock-analyse -s AAPL --analysis returns --plot
stock-analyse -s AAPL MSFT --granularity 1w --analysis returns --plot
stock-analyse -s TSLA --from 2020-01-01 --analysis returns --plot
```

---

## volatility

**What it measures:**
Rolling annualised volatility: the standard deviation of periodic returns
over a sliding window, scaled to annual terms. This captures how the
*riskiness* of a stock changes over time.

**Output:**
For each symbol: latest rolling volatility, mean over the full range, minimum
and maximum. With `--plot`: a line chart showing how volatility evolved.

**Annualisation factors by granularity:**

| Granularity | Factor |
|---|---|
| `1h` | 252 × 6.5 ≈ 1638 |
| `1d` | 252 |
| `1w` | 52 |
| `1M` | 12 |
| `1Q` | 4 |

**When to use it:**
- To spot volatility regimes: low volatility periods often precede large moves.
- To compare two assets and decide which carries more risk.
- To check whether current volatility is historically elevated or suppressed.
- After a market event (earnings, macro shock) to see if volatility has reverted.

**Choosing `--window`:**
- `--window 10` — very reactive, shows individual spikes clearly
- `--window 20` — standard for daily data (≈ 1 trading month)
- `--window 30` — default, slightly smoother
- `--window 60` — longer-term view, hides short spikes

For intraday data, scale the window by the number of bars per day:
e.g. `--window 12` for a 12-hour window on hourly bars.

**When it fails or misleads:**
- The first `window - 1` bars always produce `NaN` (not enough history for
  the first window). The "latest" value is only reliable if the last window
  is fully populated.
- A short total range with a large window will produce very few valid
  data points.
- Volatility is backward-looking. A low current volatility does not mean
  future volatility will be low.
- Volatility clustering (quiet periods followed by turbulent periods) is
  normal in equities. The `garch` tool (not yet implemented) models this
  explicitly.

**Good ranges:**
- At least `3 × window` total bars for the rolling estimate to be meaningful.
- 1–3 years of daily data with `--window 20` gives a clean picture.

**Example:**
```bash
stock-analyse -s AAPL --analysis volatility --window 20 --plot
stock-analyse -s AAPL MSFT TSLA --analysis volatility --window 30 --plot
stock-analyse -s AAPL --interval 1h --analysis volatility --window 12 --plot
```

---

## correlation

**What it measures:**
The Pearson correlation coefficient between the *returns* (not prices) of
every pair of symbols in the request. A value of +1 means the two assets
move together perfectly; -1 means perfectly opposite; 0 means no linear
relationship.

**Output:**
A symmetric matrix printed to the console, and a colour-coded heatmap with
`--plot` (green = positive, red = negative, values printed in each cell).

**When to use it:**
- **Portfolio construction**: low or negative correlation between positions
  reduces overall portfolio volatility through diversification.
- **Sector analysis**: high correlation within a sector is expected;
  surprises (e.g. two supposedly different assets moving together) can
  reveal hidden factor exposure.
- **Hedge effectiveness**: checking whether a hedge (e.g. a short position
  or an inverse ETF) actually offsets the long position.
- **Risk concentration**: if all positions are highly correlated, a single
  bad event affects everything.

**Interpreting values:**

| Range | Interpretation |
|---|---|
| `0.8 – 1.0` | Strongly correlated — largely the same bet |
| `0.4 – 0.8` | Moderate correlation — partial diversification |
| `0.0 – 0.4` | Low correlation — meaningful diversification |
| `-0.4 – 0.0` | Mild negative — partial natural hedge |
| `< -0.4` | Strong negative — good hedge candidate |

**When it fails or misleads:**
- Correlation measures *linear* relationships only. Two assets can have
  zero correlation but still behave similarly in crashes (tail dependence).
- Correlation is not stable — it can change dramatically in different
  market regimes. Always compute it over multiple sub-periods.
- Requires at least 2 symbols. With only 2 symbols the matrix is trivial
  (1.0 on diagonal, one off-diagonal value).
- Price correlation is meaningless — always compute on returns, which this
  tool does automatically.
- Short ranges can produce spuriously high or low values. Use 1+ year of
  daily data for reliable estimates.

**Good ranges:**
- 1–3 years of daily data (`1d`) for medium-term portfolio decisions.
- `--granularity 1M` over 5+ years for long-term structural relationships.
- Repeat the analysis over different sub-periods to check stability.

**Example:**
```bash
stock-analyse -s AAPL MSFT GOOGL AMZN TSLA NVDA --analysis correlation --plot
stock-analyse -s AAPL MSFT --from 2020-01-01 --to 2022-12-31 --analysis correlation --plot
stock-analyse -s AAPL MSFT --from 2023-01-01 --analysis correlation --plot
# compare the two periods above to check if correlation has changed
```

---

## sma

**What it measures:**
Simple moving averages of the close price over multiple window lengths.
Smooths out noise to reveal the underlying trend direction at different
time scales.

**Output:**
For each symbol and each window: current SMA value, current price, and
whether price is above (↑) or below (↓) the SMA. With `--plot`: price
chart with SMA lines overlaid.

**Configuring windows with `--sma-windows`:**
- Default: `20 50 200`
- `20` — short-term trend (≈ 1 trading month of daily data)
- `50` — medium-term trend (≈ 2.5 months)
- `200` — long-term trend (≈ 10 months); the "200-day MA" is widely
  watched by traders

**When to use it:**
- **Trend identification**: price above the 200-day SMA is broadly
  considered a bullish regime; below is bearish.
- **Support and resistance**: SMA lines often act as dynamic support
  (price bounces off from below) or resistance (price fails to break
  through from above).
- **Crossovers**: when the 50-day SMA crosses above the 200-day SMA
  ("golden cross") it is considered a bullish signal. The reverse
  ("death cross") is bearish.
- **Intraday**: shorter windows (6, 12, 24) on `--interval 1h` data
  serve as dynamic intraday trend indicators.

**When it fails or misleads:**
- SMAs are *lagging* indicators — they confirm trends after they have
  started, not before. In sideways markets, SMA crossovers generate
  many false signals.
- The window length must be shorter than the total data range. With
  only 30 bars of data, a 200-period SMA cannot be computed at all.
- A price above the 200-day SMA does not mean the stock will keep rising.
  It only describes the current positioning relative to the average.
- Whipsawing: in choppy markets, price repeatedly crosses the SMA
  without establishing a trend, generating misleading signals.

**Good ranges:**
- For the `200` window: need at least 250+ daily bars (≈ 1 year).
- For intraday: `--sma-windows 6 12 24` with `--interval 1h`.
- For long-term macro view: `--granularity 1M --sma-windows 6 12 24`
  (months instead of days).

**Example:**
```bash
stock-analyse -s AAPL --analysis sma --sma-windows 20 50 200 --plot
stock-analyse -s AAPL --from 2023-01-01 --analysis sma --plot
stock-analyse -s AAPL --interval 1h --analysis sma --sma-windows 6 12 24 --plot
```

---

## drawdown

**What it measures:**
How far a price has fallen from its most recent peak (the "high-water mark").
Reports the worst such decline, how long it lasted, and how long recovery took.

**Output columns:**

| Column | Description |
|---|---|
| Max DD | Largest peak-to-trough decline as a percentage |
| DD duration | Number of bars from peak to trough |
| Recovery | Number of bars from trough back to the prior peak price |
| Ann. return | Annualised return over the full loaded range |
| Calmar | Ann. return / |Max DD| — return per unit of drawdown risk |

**With `--plot`:**
The "underwater chart" — a filled area showing the drawdown at every point
in time. The deeper and wider the filled area, the more painful that period
was for a buy-and-hold investor.

**When to use it:**
- **Risk assessment**: max drawdown tells you the worst case a long-only
  investor would have experienced. It is often more intuitive than
  volatility.
- **Strategy evaluation**: a strategy with high returns but a 60% max
  drawdown is unusable in practice (most investors would have sold
  at the bottom).
- **Calmar ratio**: compares strategies or assets on a return-per-drawdown
  basis. Higher is better. A Calmar above 1.0 is generally acceptable;
  above 3.0 is very strong.
- **Recovery analysis**: knowing how long it took to recover from the
  worst drawdown tells you whether you could have held through it.

**Interpreting max drawdown:**

| Range | Character |
|---|---|
| `< 10%` | Conservative asset or very short time range |
| `10 – 25%` | Normal correction territory for large-cap equities |
| `25 – 50%` | Severe bear market territory |
| `> 50%` | Catastrophic loss — high-volatility asset or crash period |

**When it fails or misleads:**
- Max drawdown is heavily dependent on the time range. A range that
  includes 2008 or 2020 will produce much larger drawdowns than a range
  that avoids them.
- "Not yet recovered" means the end of the loaded data is still below
  the peak — not necessarily that recovery never happened.
- Drawdown computed on resampled data (e.g. weekly bars) may understate
  the true intraday drawdown that occurred within bars.

**Good ranges:**
- Use the full available history to capture worst-case scenarios.
- Compare drawdown across different market regimes (bull/bear periods)
  by varying `--from` and `--to`.

**Example:**
```bash
stock-analyse -s AAPL --analysis drawdown --plot
stock-analyse -s AAPL MSFT TSLA --from 2020-01-01 --analysis drawdown --plot
stock-analyse -s TSLA --analysis summary drawdown  # return + worst case together
```

---

## rsi

**What it measures:**
The Relative Strength Index — a momentum oscillator that measures the
speed and magnitude of recent price changes, normalised to a 0–100 scale.
Uses Wilder smoothing (exponential moving average with α = 1/window).

**Formula:**
```
RSI = 100 − (100 / (1 + RS))
RS  = avg_gain / avg_loss  (Wilder EMA over the window)
```

**Output columns:**

| Column | Description |
|---|---|
| RSI | Latest RSI value (0–100) |
| Signal | overbought (≥70) / oversold (≤30) / neutral |
| Bars ≥70 | Number of bars where RSI was in overbought territory |
| Bars ≤30 | Number of bars where RSI was in oversold territory |

**With `--plot`:**
Dual panel: price on top, RSI below with dashed lines at 70 and 30 and
shaded overbought/oversold zones.

**Standard window:** `--window 14` (RSI-14 is the industry standard).
The default window in the script is 30 — change it with `--window 14`
for the classic interpretation.

**When to use it:**
- **Identifying extremes**: RSI > 70 suggests a stock may be overbought
  (due for a pullback); RSI < 30 suggests oversold (potential bounce).
- **Divergence**: price makes a new high but RSI does not — this
  "bearish divergence" can signal a trend weakening.
- **Trend confirmation**: in a strong uptrend RSI oscillates between
  40 and 80 without touching 30; in a downtrend it oscillates between
  20 and 60 without reaching 70.
- **Intraday**: RSI on hourly data with `--window 14` gives short-term
  momentum signals within a trading session.

**When it fails or misleads:**
- In a strong trend RSI can stay overbought (>70) for extended periods
  without a reversal. RSI > 70 alone is not a sell signal.
- In very volatile assets (TSLA, crypto) the 70/30 thresholds may need
  to be widened to 80/20.
- RSI is computed from close prices and ignores gaps between sessions.
- Short windows (< 7) produce an RSI that is too reactive to single bars.
  Long windows (> 30) produce a very smooth RSI that reacts slowly to
  real momentum shifts.
- Requires at least `2 × window` bars to produce a meaningful reading.

**Good ranges:**
- 6 months – 2 years of daily data with `--window 14`.
- `--interval 1h --window 14` for intraday sessions.

**Example:**
```bash
stock-analyse -s AAPL --analysis rsi --window 14 --plot
stock-analyse -s AAPL TSLA --from 2023-01-01 --analysis rsi --window 14 --plot
stock-analyse -s AAPL --interval 1h --analysis rsi --window 14 --plot
```

---

## bbands

**What it measures:**
Bollinger Bands: a moving average (the middle band) surrounded by an upper
and lower band at ±2 standard deviations. As volatility increases the bands
widen; as volatility decreases they contract ("squeeze").

**Output columns:**

| Column | Description |
|---|---|
| Lower | Current lower band value (SMA − 2σ) |
| Mid (SMA) | Current middle band (simple moving average) |
| Upper | Current upper band value (SMA + 2σ) |
| %B | Position of price within the bands: 0 = lower band, 1 = upper band, 0.5 = mid |
| Bandwidth | (Upper − Lower) / Mid × 100 — measures band width as % of price |
| Squeeze | Yes if bandwidth is in the bottom 20th percentile of its own history |

**With `--plot`:**
Price chart with the three band lines and a shaded fill between upper and lower.

**Standard settings:** `--window 20` (20-day SMA, 20-day standard deviation).

**When to use it:**
- **Volatility compression**: when the bands are very narrow (squeeze = yes),
  a large move is likely approaching. Direction is not indicated.
- **Mean reversion**: in range-bound markets, price tends to revert to the
  middle band after touching either outer band.
- **Trend following**: in a strong uptrend, price "walks the upper band" —
  repeatedly touching or exceeding it without a reversal.
- **%B as an oscillator**: %B above 1.0 means price is above the upper band
  (strong momentum); below 0.0 means below the lower band (potential support).

**When it fails or misleads:**
- Bollinger Bands are based on standard deviation, which assumes normally
  distributed returns. Equity returns have fat tails — price breaks the
  bands more often than the 2σ rule implies.
- The squeeze signal tells you *that* a move is coming, not *which direction*.
  Always combine with a directional indicator (RSI, trend, support/resistance).
- In very low-volatility periods the bands can become so narrow that minor
  noise appears to be a breakout.
- Window too short (< 10): bands are noisy and not meaningful.
- Window too long (> 50): bands react slowly and signal late.

**Good ranges:**
- 6 months – 3 years of daily data with `--window 20`.
- For intraday: `--interval 1h --window 20`.
- For long-term structural view: `--granularity 1w --window 20`.

**Example:**
```bash
stock-analyse -s AAPL --analysis bbands --window 20 --plot
stock-analyse -s AAPL --analysis rsi bbands --window 14 --plot  # combine
stock-analyse -s AAPL --interval 1h --analysis bbands --window 20 --plot
```

---

## montecarlo

**What it measures:**
Simulates future price paths using Geometric Brownian Motion (GBM). The
drift (μ) and volatility (σ) are estimated from the historical returns in
the loaded data. N paths are simulated forward for a given horizon, and
the distribution of terminal prices is reported.

**Formula:**
```
S(t+1) = S(t) × exp((μ − σ²/2) + σ × ε)

where ε ~ N(0,1) iid
```

**Output:**
For each symbol: estimated μ (annualised drift), σ (annualised vol), current
price S₀, then the P5/P25/P50/P75/P95 fan at the horizon, expected return,
and probability of finishing above S₀. With `--plot`: a fan chart of sampled
paths plus a terminal price histogram.

**Key parameters:**
- `--mc-paths N` — number of simulated paths (default: 1000). More paths
  give a smoother distribution. 5000–10000 for final analysis; 500 for quick runs.
- `--mc-horizon BARS` — how many bars forward to simulate (default: 252 ≈
  1 trading year of daily data). Adjust for your granularity and horizon.

**Common horizons:**

| Goal | Bars |
|---|---|
| 1 week of daily bars | 5 |
| 1 month | 21 |
| 1 quarter | 63 |
| 6 months | 126 |
| 1 year | 252 |

**When to use it:**
- **Range estimation**: "where might this stock be in 6 months?" — the P5–P95
  band gives a probabilistic answer.
- **Scenario planning**: the P5 is a plausible pessimistic scenario; P95 is
  optimistic; P50 is the median outcome.
- **Option pricing intuition**: the terminal distribution is the same
  distribution that underpins Black-Scholes pricing.
- **Risk sizing**: the probability of finishing below a stop-loss level tells
  you the probability of that outcome under the GBM model.

**When it fails or misleads:**
- GBM assumes returns are normally distributed and independent. Real stock
  returns have fat tails, volatility clustering, and occasional jumps. The
  simulation underestimates crash risk.
- The simulation is only as good as the historical μ and σ. If the loaded
  range is short or covers an unusual period, the estimates will be biased.
  Check what `returns` shows before running `montecarlo`.
- A high `prob(price > S₀)` does not mean buying the stock is a good idea —
  the downside tail (P5) tells you the other side of the trade.
- Results are stochastic — re-running produces slightly different values.
  The seed is fixed at 42 for reproducibility.
- The model does not account for dividends, splits, or macro regime changes.

**Good ranges:**
- 1–3 years of daily data to estimate μ and σ from a representative period.
- Avoid using a range that is all bull market — it will overestimate drift.

**Example:**
```bash
stock-analyse -s AAPL --analysis montecarlo --plot
stock-analyse -s AAPL --analysis montecarlo --mc-paths 5000 --mc-horizon 63 --plot
stock-analyse -s AAPL --from 2020-01-01 --analysis summary montecarlo --plot
```

---

## hurst

**What it measures:**
The Hurst exponent H, estimated via Rescaled Range (R/S) analysis. It
characterises the long-range memory of the price series:

| H | Regime | Meaning |
|---|---|---|
| `< 0.45` | Mean-reverting | Past moves tend to reverse. Overshoots correct. |
| `0.45 – 0.55` | Random walk | No memory. Past prices carry no predictive information. |
| `> 0.55` | Trending | Past moves tend to continue. Momentum persists. |

**Method:**
The series is divided into overlapping segments of increasing length. For
each length, the range of cumulative deviations from the mean (R) is computed
and divided by the standard deviation (S). `log(R/S)` is then regressed
against `log(segment length)` — the slope is H.

**With `--plot`:**
A log-log scatter of R/S vs segment length with the fitted regression line.
A slope of exactly 0.5 is the random walk baseline.

**When to use it:**
- **Before choosing a strategy**: a trending asset (H > 0.55) favours
  momentum strategies, moving average crossovers, and trend-following.
  A mean-reverting asset (H < 0.45) favours RSI oversold/overbought
  signals, Bollinger Band mean reversion, and pairs trading.
- **Regime detection**: run on different sub-periods to check if the
  character of an asset has changed.
- **Model validation**: if you plan to use GBM (montecarlo), H ≈ 0.5
  validates the random-walk assumption. H far from 0.5 means GBM
  is a poor model.

**When it fails or misleads:**
- R/S analysis requires at least 40–100 bars to produce a reliable
  estimate. On short ranges the result is meaningless.
- H is an average over the entire loaded range. If the series switches
  between regimes (trending in 2020, mean-reverting in 2022), a single
  H hides this.
- H > 1.0 indicates a computation problem (possibly caused by non-stationary
  prices rather than returns). The tool operates on raw prices — for a
  more theoretically correct version, apply it to log returns.
- H near 0.5 is ambiguous — the series could be any of the three regimes
  within the noise band of the estimator.
- Very high or very low H (e.g. 0.98 or 0.12) from a short range should
  be treated with scepticism.

**Good ranges:**
- At least 200 daily bars (≈ 1 year) for a stable estimate.
- 500+ bars is better.
- Compare across multiple sub-periods to check regime stability.

**Example:**
```bash
stock-analyse -s AAPL --analysis hurst --plot
stock-analyse -s AAPL MSFT TSLA NVDA --analysis hurst --plot
stock-analyse -s AAPL --from 2015-01-01 --analysis hurst regression --plot
```

---

## Combining tools

Most analyses are more informative together than alone. Some useful combinations:

**Complete risk snapshot:**
```bash
stock-analyse -s AAPL \
    --analysis summary volatility drawdown \
    --window 20 --plot
```

**Technical analysis dashboard:**
```bash
stock-analyse -s AAPL \
    --analysis sma rsi bbands \
    --window 14 --sma-windows 20 50 200 \
    --plot
```

**Strategy selection workflow** — start with Hurst to pick the right tools:
```bash
# 1. Check the regime
stock-analyse -s TSLA --analysis hurst

# If H > 0.55 (trending) → use these:
stock-analyse -s TSLA --analysis regression sma --plot

# If H < 0.45 (mean-reverting) → use these instead:
stock-analyse -s TSLA --analysis rsi bbands --window 14 --plot
```

**Portfolio analysis:**
```bash
stock-analyse -s AAPL MSFT GOOGL AMZN TSLA \
    --analysis summary correlation drawdown \
    --from 2022-01-01 \
    --plot
```

**Forward-looking:**
```bash
stock-analyse -s AAPL \
    --from 2022-01-01 \
    --analysis returns hurst montecarlo \
    --mc-paths 5000 --mc-horizon 126 \
    --plot
```

**Intraday session:**
```bash
stock-analyse -s AAPL \
    --interval 1h \
    --analysis summary rsi bbands sma \
    --window 14 --sma-windows 6 12 24 \
    --plot
```

---

## Choosing granularity

The `--granularity` flag resamples the loaded bars before analysis. Choosing
the wrong granularity doesn't break the tools but can make results
meaningless.

| Data range | Recommended granularity | Why |
|---|---|---|
| < 1 month | `raw` or `1h` (intraday) | Daily bars are too few for most tools |
| 1–12 months | `1d` | Standard daily analysis |
| 1–5 years | `1d` or `1w` | Weekly reduces noise for longer ranges |
| 5–20 years | `1w` or `1M` | Monthly gives stable estimates |
| > 20 years | `1M` or `1Q` | Quarterly for macro trends |

Rules of thumb:
- `regression` and `hurst` both need at least 30–40 data points — match
  granularity to your date range accordingly.
- `sma` with `--sma-windows 200` needs at least 200 bars at the chosen
  granularity.
- `montecarlo` and `returns` are more reliable with 100+ bars.
- `correlation` benefits from at least 60 bars per symbol.

---

## Common pitfalls

**Survivor bias:**
If you only collect data for currently successful companies, historical
analysis will look rosier than reality. This is inherent to the data
sources used.

**Look-ahead bias:**
All tools in this toolkit are retrospective — they describe what happened.
Do not use their outputs to make forward-looking claims without adding
proper out-of-sample testing.

**Multiple comparisons:**
Running 11 tools on 10 symbols produces 110 outputs. Some will look
significant by chance. Be especially cautious with p-values from `regression`
when running many symbols simultaneously.

**Granularity mismatch:**
Comparing volatility or Sharpe across symbols that were run at different
granularities is not meaningful. Always use the same `--granularity`
when comparing multiple symbols.

**Short date ranges:**
Almost every tool becomes unreliable below 30–50 bars. Check the "Bars"
column in `summary` before interpreting any output.

**Data quality:**
yfinance (unofficial) can return incorrect or missing bars, especially
for non-US symbols. For critical analysis, cross-check against a second
source using `--source fmp` or `--source alphavantage` and compare results.
The `--source` flag lets you restrict to a single trusted source.


