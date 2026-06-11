# Backtesting

`stock_toolkit/backtest.py` replays a trading strategy against historical price data
and measures how it would have performed. Every run automatically compares
the strategy against a buy-and-hold benchmark so you can see whether the
strategy actually adds value.

---

## How it works

1. Daily price data is loaded from the SQLite databases created by
   `stock_toolkit/collector/`.
2. A signal function scans the price series and emits `BUY` or `SELL` at
   each bar. Signals are generated at bar close — there is no lookahead.
3. Trades execute at the same bar's close price, adjusted for slippage.
4. Commission is deducted as a percentage of each trade's value.
5. One position at a time — no pyramiding.
6. Performance metrics are computed for both the strategy and buy-and-hold.

**Execution assumptions:**
- Long-only (no short selling).
- Fractional shares allowed.
- Default starting capital: $10,000.
- Default commission: 0.1% per trade (`--commission 0.001`).
- Default slippage: 0.1% per fill (`--slippage 0.001`).

---

## Strategies

### `rsi` — RSI reversal

Buys when the RSI crosses *down* through the buy threshold (oversold entry),
sells when the RSI crosses *up* through the sell threshold (overbought exit).
Uses Wilder smoothing (EMA with α = 1/window).

```
BUY  when RSI crosses below --buy-at   (default: 30)
SELL when RSI crosses above --sell-at  (default: 70)
```

Best suited for: range-bound or mean-reverting assets. Check `hurst` in
`stock_toolkit/analysis.py` first — RSI signals work better when H < 0.5.

```bash
stock-backtest -s AAPL --strategy rsi --window 14 --plot
stock-backtest -s AAPL --strategy rsi --window 14 --buy-at 25 --sell-at 65 --plot
stock-backtest -s AAPL MSFT TSLA --strategy rsi --window 14
```

---

### `sma_cross` — Moving average crossover

Buys on the golden cross (fast SMA crosses above slow SMA) and sells on the
death cross (fast SMA crosses below slow SMA).

```
BUY  when fast SMA crosses above slow SMA  (golden cross)
SELL when fast SMA crosses below slow SMA  (death cross)
```

Best suited for: trending assets (H > 0.5). Produces few trades —
typically 2–6 per year on daily data with 20/50 windows. Generates many
false signals in sideways markets.

```bash
stock-backtest -s AAPL --strategy sma_cross --fast 20 --slow 50 --plot
stock-backtest -s AAPL --strategy sma_cross --fast 50 --slow 200 --plot
stock-backtest -s AAPL MSFT NVDA --strategy sma_cross --fast 20 --slow 50
```

---

### `bbands` — Bollinger Band mean-reversion

Buys when price crosses *below* the lower Bollinger Band (oversold extreme),
sells when price crosses *above* the middle band (mean reversion complete).

```
BUY  when price crosses below lower band  (SMA − 2σ)
SELL when price crosses above middle band (SMA)
```

Best suited for: low-volatility, mean-reverting assets. In strongly trending
assets the lower band is rarely touched, producing very few trades. Combine
with `--analysis bbands` in `stock_toolkit/analysis.py` to check current band width
before backtesting.

```bash
stock-backtest -s AAPL --strategy bbands --window 20 --plot
stock-backtest -s ENEL.MI --strategy bbands --window 20 --plot
```

---

### `breakout` — N-bar high/low breakout

Buys when the close breaks above the highest close of the past N bars
(momentum entry), sells when the close breaks below the lowest close of the
past N bars (stop-out).

```
BUY  when close > highest close of past N bars
SELL when close < lowest close of past N bars
```

Best suited for: trending assets with clear momentum phases. Can produce
many trades in choppy markets as price repeatedly crosses the rolling
high/low.

```bash
stock-backtest -s AAPL --strategy breakout --window 20 --plot
stock-backtest -s TSLA --strategy breakout --window 10 --plot
```

---

## Walk-forward validation

Split the data into a training period and an out-of-sample test period to
check whether the strategy generalises beyond the data it was observed on.

```bash
# Train on 2018–2022, test on 2023 onwards
stock-backtest -s AAPL \
    --strategy sma_cross --fast 20 --slow 50 \
    --from 2018-01-01 \
    --test-from 2023-01-01 \
    --plot

# RSI walk-forward
stock-backtest -s AAPL \
    --strategy rsi --window 14 \
    --from 2015-01-01 \
    --test-from 2022-01-01 \
    --plot
```

The backtester prints the bar counts and date ranges for both periods and
runs the strategy only on the test period. This protects against
overfitting — a strategy that looks great in-sample often fails
out-of-sample.

---

## All flags

```
-s / --symbols      Symbols to backtest (required, one or more)
--strategy          Strategy: rsi, sma_cross, bbands, breakout (required)
--from              Start of date range (YYYY-MM-DD)
--to                End of date range (YYYY-MM-DD)
--test-from         Walk-forward split point (test starts here)
--source            Use only this data source (e.g. yfinance, fmp)

Strategy parameters:
--window            RSI / Bollinger / breakout window (default: 14)
--fast              Fast SMA for sma_cross (default: 20)
--slow              Slow SMA for sma_cross (default: 50)
--buy-at            RSI level to enter (default: 30)
--sell-at           RSI level to exit  (default: 70)

Execution parameters:
--capital           Starting capital in $ (default: 10000)
--commission        Commission as fraction of trade value (default: 0.001 = 0.1%)
--slippage          Slippage as fraction of fill price    (default: 0.001 = 0.1%)

Output:
--plot              Show equity curve, price chart with trade markers,
                    and indicator panel
--show-trades       Print every trade in the log
```

---

## Output metrics

Every run prints a side-by-side comparison of the strategy and buy-and-hold:

| Metric | Description |
|---|---|
| Total return | Percentage gain/loss over the full period |
| CAGR | Compound annual growth rate |
| Sharpe | Annualised risk-adjusted return (risk-free rate = 0) |
| Sortino | Like Sharpe but penalises only downside volatility |
| Max drawdown | Worst peak-to-trough decline |
| Calmar | CAGR / \|max drawdown\| |
| Final value | Portfolio value at end of period |
| Trades | Total number of round trips |
| Win rate | Percentage of trades that were profitable |
| Avg win | Average return on winning trades |
| Avg loss | Average return on losing trades |
| Profit factor | Gross profit / gross loss |

---

## Examples

```bash
# Quick overview: all four strategies on AAPL
stock-backtest -s AAPL --strategy rsi      --window 14 --plot
stock-backtest -s AAPL --strategy sma_cross --fast 20 --slow 50 --plot
stock-backtest -s AAPL --strategy bbands    --window 20 --plot
stock-backtest -s AAPL --strategy breakout  --window 20 --plot

# Compare same strategy across multiple symbols
stock-backtest -s AAPL MSFT GOOGL TSLA NVDA \
    --strategy sma_cross --fast 20 --slow 50

# Use historical data (requires prior --historical collection)
stock-backtest -s AAPL \
    --from 2010-01-01 \
    --strategy sma_cross --fast 50 --slow 200 \
    --plot

# Walk-forward: does the strategy hold out-of-sample?
stock-backtest -s AAPL \
    --strategy rsi --window 14 \
    --from 2015-01-01 \
    --test-from 2022-01-01 \
    --plot

# Higher commission and slippage for more realistic simulation
stock-backtest -s AAPL \
    --strategy sma_cross --fast 20 --slow 50 \
    --commission 0.002 --slippage 0.002 \
    --plot

# Print every trade
stock-backtest -s AAPL --strategy rsi --window 14 --show-trades
```

---

## Limitations

**No shorting.** The backtester is long-only. A SELL signal simply closes the
existing long position; it does not open a short.

**EOD fills.** Trades execute at the close price of the signal bar. In
reality you would typically get the next day's open, which may gap
significantly from the prior close. Set a higher `--slippage` to
compensate for this.

**No position sizing.** The full available cash is always invested on a BUY
signal. Kelly criterion, fixed-fractional, and volatility-adjusted sizing
are not implemented.

**No dividends or splits.** If you use unadjusted price data (Alpha Vantage
free tier), split and dividend events will appear as price gaps that may
trigger false signals. Use `ALPHAVANTAGE_PAID = true` or the FMP source
for adjusted closes.

**Overfitting risk.** Tuning parameters (`--window`, `--buy-at`, `--sell-at`,
`--fast`, `--slow`) to maximise past performance almost always leads to
a strategy that fails on new data. Always validate with `--test-from`.

