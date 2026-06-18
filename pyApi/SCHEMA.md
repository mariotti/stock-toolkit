# Database Schemas

This document freezes the on-disk SQLite schemas the toolkit
guarantees backward-compatibility for **reads** from 2.x onwards.
Downstream tooling (custom dashboards, exporters, third-party
analysis scripts) may safely depend on these shapes.

All three DBs live under `DATA_DIR` (see [`DEVELOPING.md`](DEVELOPING.md)).

---

## `stock_data.db` — live market data

Single `prices` table written by the collector. One row per
`(symbol, source, timestamp)` triple.

```sql
CREATE TABLE prices (
    fetched_at  TEXT,
    symbol      TEXT,
    source      TEXT,
    timestamp   TEXT,         -- ISO-8601 with timezone, e.g. 2026-01-02T00:00:00+00:00
    interval    TEXT,         -- '1d', '1h', 'quote', …
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    volume      INTEGER,
    vwap        REAL,
    change_pct  REAL,
    extra       TEXT,         -- JSON blob for source-specific fields
    UNIQUE(symbol, source, timestamp)
);
```

Same schema is used for the bootstrap historical DBs under
`DATA_DIR/historical/stock_data_<range>.db`.

Conventions:
- `interval='quote'` is rewritten to `'1d'` on read so all tools see
  same-day data consistently.
- `source` matches the collector source name (`yfinance`,
  `alphavantage`, `finnhub`, `polygon`, `fmp`, `twelvedata`,
  `marketstack`).
- `NULL` values in OHLC are tolerated but flagged by
  `stock-sanity --strict`.

---

## `portfolio.db` — paper-trading Game

**Schema version 2.** Pre-1.1 portfolios are auto-migrated on first
open (single-portfolio v1 → multi-portfolio v2 with a "Default"
strategy).

```sql
CREATE TABLE portfolios (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT    NOT NULL UNIQUE,
    starting_cash  REAL    NOT NULL,
    cash           REAL    NOT NULL,
    created_at     TEXT    NOT NULL,
    last_reset_at  TEXT    NOT NULL,
    archived_at    TEXT
);

CREATE TABLE trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    timestamp    TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    side         TEXT    NOT NULL CHECK (side IN ('buy', 'sell')),
    qty          REAL    NOT NULL,
    price        REAL    NOT NULL,    -- mid-market quote at trade time
    fill_price   REAL    NOT NULL,    -- price after slippage
    cash_delta   REAL    NOT NULL,    -- signed: negative for buys
    note         TEXT                 -- free text (see v1.7)
);

CREATE TABLE meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
```

`meta('active_portfolio_id')` persists which strategy is selected
in the UI.

Invariants enforced by the layer above and audited by `stock-sanity`:
- `cash + equity == total` within float epsilon
- `closed_count == wins + losses` (FIFO matching of buys → sells)
- `value_history` dates are strictly increasing
- No negative qty in any position; no positive qty with `avg_cost ≤ 0`

---

## `stock_failures.db` — collector failure tracker

Tracks `(symbol, source)` pairs that consistently fail so the
collector can suppress them after `FAILURE_THRESHOLD` hits.

```sql
CREATE TABLE failures (
    symbol      TEXT NOT NULL,
    source      TEXT NOT NULL,
    reason      TEXT,
    hits        INTEGER NOT NULL DEFAULT 1,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    PRIMARY KEY (symbol, source)
);
```

Companion file: `stock_failures_report.csv` is regenerated on every
collector run from this DB. The DB is the authoritative source.

---

## State files

JSON blobs in `DATA_DIR/`. Schemas are best-effort — these are
runtime caches, not promised to be stable.

- `.collector_state.json` — per-source call counts, daily and monthly
- `.alerts_state.json` — edge-detection state for `stock-alerts`

---

## Compatibility commitment

From 2.0 onwards:

| Change kind | Allowed in a minor release? |
|---|---|
| **Add a column** | Yes (with a default). |
| Add a new table | Yes. |
| Add a new constraint | Yes, if the existing data satisfies it. |
| Rename a column or table | **No.** Bump major. |
| Drop a column | **No.** Bump major. |
| Change a column's semantic meaning | **No.** Bump major. |

The toolkit's own readers tolerate `NULL` in any nullable column, so
existing data continues to be readable across all 2.x releases.
