# Alerts

`stock_alerts.py` watches your collected data for conditions you define and
notifies you the moment a condition becomes true. It uses edge detection —
it fires once when a condition transitions from false to true, then stays
silent until the condition clears and triggers again. You will not get
repeated notifications for the same ongoing situation.

---

## How it works

1. The script loads the latest bars from `stock_data.db` for each symbol.
2. It computes a full set of indicators (RSI, SMA, EMA, MACD, Bollinger
   Bands, volume, 52-week range) from the price history.
3. Each `--when` condition is evaluated as a Python expression against those
   indicator values.
4. Alert state is stored in `.alerts_state.json`. A notification fires only
   when a condition transitions from `false → true` (rising edge).
5. When a condition returns to `false`, the state resets and the alert can
   fire again next time the condition becomes true.

Run this script on a schedule (cron) at whatever frequency your data is
refreshed. The typical setup is every 30 minutes during market hours.

---

## Conditions

Conditions are readable Python expressions. Use any indicator name from the
table below:

```bash
--when "rsi14 < 30"                  # RSI oversold
--when "rsi14 > 70"                  # RSI overbought
--when "price > sma200"              # price above 200-day MA
--when "price < sma50"               # price below 50-day MA
--when "bbands_squeeze"              # Bollinger Band squeeze (breakout pending)
--when "bbands_pct_b < 0"           # price below lower Bollinger Band
--when "change_pct < -3"             # dropped more than 3% today
--when "change_pct > 3"              # gained more than 3% today
--when "volume_spike"                # volume > 2× 20-day average
--when "near_52w_high"               # within 5% of 52-week high
--when "near_52w_low"                # within 5% of 52-week low
--when "macd_hist > 0"              # MACD histogram turned positive
```

Conditions can be combined with `and` / `or`:

```bash
--when "rsi14 < 30 and price > sma200"    # oversold but still in uptrend
--when "bbands_squeeze and rsi14 < 50"    # squeeze with bearish momentum
--when "change_pct < -5 or rsi14 < 20"   # severe drop or extreme oversold
```

Run `--list-conditions` to see every available indicator name:

```bash
python3 stock_alerts.py --list-conditions
```

### Full indicator reference

| Name | Description |
|---|---|
| `price` | Latest close price |
| `open` | Today's open |
| `high_today` | Today's high |
| `low_today` | Today's low |
| `prev_close` | Previous session's close |
| `change_pct` | % change from previous close |
| `volume` | Today's volume |
| `sma5` `sma10` `sma20` `sma50` `sma100` `sma200` | Simple moving averages |
| `ema5` `ema10` `ema20` `ema50` `ema100` `ema200` | Exponential moving averages |
| `rsi7` `rsi9` `rsi14` `rsi21` | RSI at different periods |
| `macd` | MACD line (EMA12 − EMA26) |
| `macd_signal` | Signal line (EMA9 of MACD) |
| `macd_hist` | Histogram (macd − macd_signal) |
| `bbands_upper` | Upper Bollinger Band (SMA20 + 2σ) |
| `bbands_mid` | Middle band (SMA20) |
| `bbands_lower` | Lower band (SMA20 − 2σ) |
| `bbands_pct_b` | %B: 0 = at lower band, 1 = at upper band |
| `bbands_bw` | Bandwidth as % of mid price |
| `bbands_squeeze` | True when bandwidth is in its bottom 20th percentile |
| `high_52w` | 52-week high |
| `low_52w` | 52-week low |
| `near_52w_high` | True when price is within 5% of the 52-week high |
| `near_52w_low` | True when price is within 5% of the 52-week low |
| `volume_avg20` | 20-bar average volume |
| `volume_spike` | True when today's volume > 2× the 20-bar average |

---

## Notification channels

Pass one or more channels to `--notify`:

```bash
--notify console            # print to terminal (default)
--notify email              # send an email via SMTP
--notify pushover           # Pushover mobile notification
--notify slack              # Slack webhook
--notify console email      # multiple channels at once
```

### Setting up notifications

Add the relevant keys to `config.env`:

```bash
# Email (works with Gmail app passwords or any SMTP server)
ALERT_EMAIL=you@example.com
ALERT_SMTP_HOST=smtp.gmail.com
ALERT_SMTP_PORT=587
ALERT_SMTP_USER=you@gmail.com
ALERT_SMTP_PASS=your_16_char_app_password

# Pushover (https://pushover.net — free tier, excellent mobile notifications)
PUSHOVER_USER_KEY=your_user_key
PUSHOVER_APP_TOKEN=your_app_token

# Slack incoming webhook
# (Slack → Apps → Incoming Webhooks → Add to Workspace)
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../...
```

**Gmail setup:** Go to Google Account → Security → 2-Step Verification →
App passwords. Generate a password for "Mail" and use it as `ALERT_SMTP_PASS`.
Do not use your main Gmail password.

---

## State management

Alert state is persisted in `.alerts_state.json`. This file tracks which
conditions are currently active so the script knows whether a condition just
became true (fire) or was already true on the previous run (stay silent).

```bash
# Show current state: which conditions are active and when they last fired
python3 stock_alerts.py --status

# Clear all state (every condition will fire fresh on next run)
python3 stock_alerts.py --reset

# Evaluate conditions and print results without firing or saving state
python3 stock_alerts.py -s AAPL --when "rsi14 < 30" --dry-run
```

---

## Running via cron

The recommended setup is to run the alert script on the same schedule as
the collector, during market hours:

```bash
crontab -e
```

```
# Collect data every 30 minutes, Mon-Fri, 09:00-17:00 local time
*/30 9-17 * * 1-5  /usr/bin/python3 /path/to/stock_collector.py

# Check alerts every 30 minutes on the same schedule
*/30 9-17 * * 1-5  /usr/bin/python3 /path/to/stock_alerts.py \
    -s AAPL MSFT TSLA NVDA \
    --when "rsi14 < 30" \
    --when "price > sma200" \
    --when "bbands_squeeze" \
    --notify pushover
```

Or using the shell wrapper if you set it up:

```
*/30 9-17 * * 1-5  STOCK_DIR=/path/to/stock /home/you/bin/collect
*/30 9-17 * * 1-5  /usr/bin/python3 /path/to/stock_alerts.py -s AAPL --when "rsi14 < 30" --notify email
```

---

## All flags

```
-s / --symbols        Symbols to watch (required unless using --list-conditions,
                      --status, or --reset)
--when EXPR           Condition to watch — can be repeated for multiple conditions
--notify CHANNEL      Notification channels: console, email, pushover, slack
                      (default: console, can specify multiple)
--list-conditions     Print all available indicator names and exit
--dry-run             Evaluate conditions and print results without firing alerts
                      or updating state — useful for testing
--status              Show which conditions are currently active and when they
                      last fired
--reset               Clear all alert state so every edge can fire again
--bars N              Number of historical bars to load for indicator computation
                      (default: 250 — enough for SMA200 and RSI14)
```

---

## Examples

```bash
# Single symbol, single condition
python3 stock_alerts.py -s AAPL --when "rsi14 < 30"

# Multiple symbols, multiple conditions
python3 stock_alerts.py -s AAPL MSFT TSLA NVDA \
    --when "rsi14 < 30" \
    --when "price > sma200" \
    --when "bbands_squeeze"

# Combined condition: oversold AND still in uptrend
python3 stock_alerts.py -s AAPL \
    --when "rsi14 < 30 and price > sma200"

# Big daily move alert
python3 stock_alerts.py -s AAPL MSFT GOOGL AMZN TSLA \
    --when "change_pct < -3" \
    --when "change_pct > 3" \
    --notify console email

# Volume spike with price confirmation
python3 stock_alerts.py -s AAPL \
    --when "volume_spike and change_pct > 2"

# Approaching 52-week high/low
python3 stock_alerts.py -s AAPL MSFT \
    --when "near_52w_high" \
    --when "near_52w_low"

# MACD crossover
python3 stock_alerts.py -s AAPL \
    --when "macd > macd_signal and macd_hist > 0"

# Bollinger Band squeeze — breakout likely
python3 stock_alerts.py -s AAPL MSFT NVDA TSLA \
    --when "bbands_squeeze" \
    --notify pushover

# Test conditions without firing anything (dry run)
python3 stock_alerts.py -s AAPL \
    --when "rsi14 < 30" \
    --when "price > sma200" \
    --dry-run

# Check current alert state
python3 stock_alerts.py --status

# Clear state and start fresh
python3 stock_alerts.py --reset

# Send to multiple channels
python3 stock_alerts.py -s AAPL \
    --when "rsi14 < 30" \
    --notify console email pushover slack

# Italian stocks work the same way
python3 stock_alerts.py -s ENEL.MI CSMIB.MI \
    --when "rsi14 < 30" \
    --when "price < sma50"
```

---

## Limitations

**Data freshness.** The alert fires on the most recently collected bar. If
the collector has not run for several hours, the conditions may be stale.
Keep the collector cron running on the same schedule as the alerts.

**Daily bars only.** Conditions are evaluated on daily bars by default.
For intraday alerts you would need to switch the data source to hourly
bars, which requires more frequent collection and a slightly different
indicator calibration.

**No price-level conditions.** Conditions like `"price > 185.50"` work
fine, but there is no built-in `take_profit` or `stop_loss` abstraction.
You can express these as raw price comparisons in `--when`.

**Edge detection is per run.** If the collector misses a run (e.g. network
issue) and the condition both triggered and cleared within that gap, the
alert will not fire. This is inherent to polling-based systems.

**MACD and RSI need enough history.** The default `--bars 250` is enough
for RSI14, SMA200, and MACD(12,26,9). If you have very little data
collected, some indicators will return `None` and the condition will be
skipped with a warning.


