# stock-fetcher

Rust companion to the Python `stock_toolkit.collector`. Concurrent
per-source fetching, dedup-on-write, byte-compatible SQLite schema
so anything that reads Python's `prices` table can read this one
too.

**Status (v2.2.0):** experimental. One source implemented end-to-end
(Alpha Vantage). Architecture is set; remaining six sources are
mechanical follow-ons.

## Why a second fetcher

The Python collector is correct and battle-tested, but it loops over
`(symbol, source)` pairs sequentially within each source. For a
20-symbol watchlist on six APIs that's ~120 round-trip latencies in
series. The Rust fetcher runs symbols concurrently per source with
a configurable cap, so the same workload completes in roughly the
time of the slowest single fetch.

The two coexist on purpose. You can drive either; they write the
same schema. A future `stock-sync` step (if you ever want one) is a
single `INSERT OR IGNORE … SELECT` between the two files.

## Quick start

```bash
cd rust-fetcher
cargo run --release -- --sources alphavantage --symbols AAPL,MSFT
cargo run --release -- --summary
```

Reads `../pyApi/config.env` for `ALPHAVANTAGE_KEY` and `SYMBOLS`
(override either via `--config` and `--symbols`). Writes to
`rust-fetcher/data/stock_data.db` by default.

## Layout

| File | What |
|---|---|
| `Cargo.toml` | Workspace + deps |
| `src/lib.rs` | Module re-exports — single import surface |
| `src/config.rs` | `config.env` parser, byte-compatible with Python's `load_config` |
| `src/db.rs` | SQLite writer + the cross-language schema contract |
| `src/state.rs` | Per-source rate-limit / budget bookkeeping |
| `src/sources/mod.rs` | `Source` async trait |
| `src/sources/alphavantage.rs` | First concrete source |
| `src/main.rs` | CLI orchestrator (semaphore per source) |
| `tests/integration_test.rs` | End-to-end through a wiremock HTTP server |

## Tests

```bash
cargo test
```

20 tests across config parsing, schema/dedup, state bookkeeping,
the Alpha Vantage response parser, and a full mock-HTTP →
fetch → parse → persist → dedup round trip.

## What's intentionally NOT done yet

- Other sources (Finnhub, Polygon, FMP, Twelve Data, Marketstack,
  yfinance — the last via either a Rust crate or a Python
  subprocess shim).
- Per-source rate limiting beyond the simple semaphore. Real
  per-minute limits (Finnhub 60/min, Polygon 5/min) need a token
  bucket; the seam is the per-source semaphore in `main.rs`.
- Sharing `.collector_state.json` with the Python collector. Right
  now Rust keeps its own state file (`data/.fetcher_state.json`).
- CI: no GitHub Actions Rust matrix yet. Build is local-only;
  `cargo build --release` produces the binary you want.
- Pre-built binaries in releases. Add once the matrix exists.

## Compatibility commitment

The DDL in `src/db.rs::SCHEMA` is the **cross-language contract**
between the Python collector (`pyApi/stock_toolkit/collector/db.py`)
and this crate. The column-level stability rules in
[`../pyApi/SCHEMA.md`](../pyApi/SCHEMA.md) apply to both sides.
Touching one schema means touching the other in the same change.
