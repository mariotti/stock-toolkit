# Changelog

Curated highlights. For exact diffs, see the
[GitLab releases page](https://gitlab.com/Mariotti/stock-toolkit/-/releases)
or the per-release commit body.

The toolkit follows [semver](https://semver.org/):
- **Major** — public-API or DB-schema breaking change.
- **Minor** — backward-compatible feature additions.
- **Patch** — bug fixes, performance, docs.

The public surface frozen from 2.x onwards is documented in each
module's `__all__` (see `tests/test_toolkit.py::TestPublicAPIIsStable`).
DB schemas are documented in [`SCHEMA.md`](SCHEMA.md).

---

## 2.2.0 — Rust fetcher (experimental, opt-in)

New top-level workspace `rust-fetcher/` — a concurrent fetcher
written in Rust, schema-compatible with the Python collector's
`prices` table. Coexists with the Python pipeline; you can drive
either independently.

**Why**: the Python collector loops over `(symbol, source)` pairs
sequentially within each source. The Rust fetcher uses tokio +
per-source semaphores so symbols run in parallel — roughly the
time of the slowest single fetch instead of the sum.

**Scope of this release** (first ship, intentionally narrow):
- `Source` async trait + one concrete implementation: Alpha
  Vantage `TIME_SERIES_DAILY`.
- SQLite writer with `INSERT OR IGNORE` dedup against the same
  `UNIQUE(symbol, source, timestamp)` Python uses. WAL journal
  mode so a concurrent Python reader doesn't block.
- `config.env` parser byte-compatible with Python's `load_config`
  (single source of truth for `ALPHAVANTAGE_KEY`, `SYMBOLS`).
- Per-source rate-limit / budget state bookkeeping (`state.rs`).
- CLI binary `stock-fetcher` with `--sources`, `--symbols`,
  `--concurrency`, `--summary`.
- 20 tests across config parsing, schema/dedup, state, the
  AV response parser, and a wiremock end-to-end round trip.

**Verified end-to-end against the live Alpha Vantage API**: 100
AAPL bars fetched on first run, 100 dedupe-skipped on second.

**What's deliberately NOT done yet**:
- The other six sources. Architecture is set; each is a single
  module implementing `Source::fetch_daily`.
- Real per-minute rate limiting (Finnhub 60/min, Polygon 5/min).
  The current semaphore caps concurrency but doesn't pace at a
  fixed RPM. Token bucket goes in the per-source semaphore seam.
- Sharing `.collector_state.json` with Python. Rust keeps its own
  state file until both sides need to coordinate budget.
- CI for cross-platform Rust binaries. Local `cargo build --release`
  only for now.
- Python invocation surface. Run from the shell; no `subprocess`
  shim from `stock-collect` yet.

The Rust DDL in `rust-fetcher/src/db.rs::SCHEMA` is the
cross-language contract — the column-level stability rules in
`SCHEMA.md` apply to both implementations. Touching one means
touching the other in the same change.

No Python behaviour change. No new Python dependencies. The Rust
workspace is gitignored except for source — no pre-built artefacts
in the repo.

## 2.1.0 — News sentiment in the Briefing

New module `stock_toolkit/news.py` — fetches Alpha Vantage's
pre-computed `NEWS_SENTIMENT` per symbol, aggregates per-ticker with
relevance-weighted averaging, and formats a compact text block for
the Briefing prompt. **The LLM never computes the score** — it
receives a finished number plus a few headlines, exactly the same
contract Sharpe / Calmar / Monte Carlo already use.

- Briefing tab gains an "Include news sentiment (Alpha Vantage)"
  checkbox. Defaults on when `ALPHAVANTAGE_KEY` is configured, off
  otherwise. Disabled with a hint if no key is set.
- Only the top-5 scored symbols are fetched per briefing — protects
  the 25-call/day Alpha Vantage budget shared with `stock-collect`.
- 1-hour cache (via `st.cache_data`) so re-clicking Generate within
  an hour hits the cache, not the API.
- All failure modes degrade silently: missing key → empty block,
  throttle → empty block, uncovered ticker → "(no articles — free
  tier is US-biased)" line in the prompt.
- Coverage note in the prompt itself, not just docs — Claude reads
  "non-US tickers often return empty" rather than guessing why
  ENEL.MI / BMW.DE / DOCM.SW are missing.

Tests:
- 16 new offline unit tests (`tests/test_news.py`) cover the
  aggregation pipeline against a committed anonymised fixture
  (`tests/fixtures/news_sentiment_aapl.json`).
- New `TestAlphaVantageNews` in `test_live_apis.py` (under the
  existing `RUN_LIVE=1` gate) exercises the real call. Confirmed
  green against the production AV key.
- `__all__` of `stock_toolkit.news` registered in the public-API
  stability test.

Honest scope: free-tier news coverage is US-heavy. Symbols outside
the US frequently return zero articles even when the ticker is
recognised. The prompt block surfaces this explicitly rather than
faking uniform coverage.

No new dependencies, no schema changes, no breaking changes to the
v1.19 public surface.

## 1.19.1 — DATA_DIR config rename + resurrected live-API smoke

- `OUTPUT_DIR` in config.env renamed to `DATA_DIR`. The old name is
  still honoured with a one-shot `DeprecationWarning` (slated for
  removal in 3.x).
- `tests/test_live_apis.py` (zero bytes since the v1.0 restructure)
  restored from history with the v1.19-compatible import path and
  the `setUpModule` + `SkipTest` discovery pattern.
- Live run found two stale provider keys (FMP, Polygon/Massive) —
  both returning 403. The other five sources are healthy.

## 1.19.0 — Public-API + schema commitment

Stability pass ahead of the 2.0 bump. **No behaviour change.**

- Every public module declares `__all__`: `common`, `game`, `score`,
  `backtest`, `alerts`, `analysis`, `sanity`. A new
  `TestPublicAPIIsStable` test asserts every listed name actually
  exists, so a future refactor can't silently drop a function the
  stability contract promises.
- New `SCHEMA.md` documents the three SQLite schemas (`stock_data.db`,
  `portfolio.db`, `stock_failures.db`) and the column-level
  compatibility commitment for 2.x.
- This file (`CHANGELOG.md`) — curated highlights so the "what's in
  2.0?" announcement has a source of truth.

## 1.18.x — Hardening pass

- **1.18.3** Two new journey tests covering the alerts + collector
  failure-report paths that 1.18.2 touched.
- **1.18.2** Four real bugs: Bollinger NaN leak in `alerts.py`,
  duplicate-except double-write in `collector/failures.py`, missing
  `raise … from err` in `game.py`. `.ruff.toml` now permanently
  selects `B025`, `B904`, `PLW0177` so the same classes can't
  silently regress.
- **1.18.1** Three new journey tests: `stock-sanity` modes, Game full
  lifecycle, Briefing offline.
- **1.18.0** `stock_toolkit.sanity` — opt-in audit of the deterministic
  invariants. Eight check categories, exposed as library + `stock-sanity`
  CLI + Admin button. `Issue`/`Report` dataclasses, `--json` mode for
  cron/CI.

## 1.17.0 — Unified `DATA_DIR`

All on-disk state (DBs, state files, logs, historicals) consolidated
under a single `DATA_DIR`. Auto-migration moves loose files from
pre-v1.17 installs and renames `data/` → `historical/`. Layouts
converge across native dev (`pyApi/data/`), Docker (`/data/`), and
the Windows `.exe` (`./data/` next to the binary).

## 1.16.x — Visual refresh

- **1.16.2** Multiselect chip contrast fix.
- **1.16.1** Light theme palette (palette tokens centralised in
  `ui/theme.py`).
- **1.16.0** Single `setup_page()` helper applied across every
  sidebar page so the theme stays uniform; introduced the central
  app icon (`●`).

## 1.15.0 — UI setup

Admin → 🛠 Settings expander + first-run banner. Click-to-run users
can configure paid-tier flags, the UI collect-source allow-list, and
notification channels (email / Pushover / Slack) without dropping to
a shell.

## 1.14.x — Icon vocabulary

- **1.14.3** GitLab → GitHub mirror set up. Tag pushes now
  auto-publish a GitHub Release with the Windows `.exe` attached.
- **1.14.2** First real Windows `.exe` attached to the release page.
- **1.14.1** `stock_toolkit/ui/icons.py` — two-layer mapping
  (semantic name → concept → glyph). Restyling is a one-file change.
- **1.14.0** Minimalist-geometric icon refresh.

## 1.13.0 — Admin API Keys editor

Free-tier keys editable in the Admin sidebar with eye-toggle reveal.
Paid keys (incl. Anthropic) stay password-style with explicit warnings.

## 1.12.0 — In-app Help page

Static-markdown ❓ Help page in the sidebar nav.

## 1.11.0 — PyInstaller infrastructure

`pyApi/pyinstaller/launcher.py` + `StockToolkit.spec` +
`.github/workflows/build-windows-exe.yml`. Standalone Windows `.exe`
build pipeline (no Docker, no Python install).

## 1.10.x — Distribution polish

- **1.10.4** Windows `.bat` launchers added to the Docker bundle.
- **1.10.2** `make_dist.py --package app` produces
  `stock-app-X.Y.Z.zip` — double-clickable Docker bundle for
  Mac (`.command`) and Linux (`.sh`).
- **1.10.1** Collector logs moved to `OUTPUT_DIR/logs/`.
- **1.10.0** MACD added across `score` + `backtest`.

## 1.9.0 — Sizing helper + diversification warning

Position sizing radio (Fixed CHF / % of cash / % of equity) on the
Game Buy form, with a concentration + correlation warning under the
holdings table.

## 1.8.0 — Risk-adjusted return metrics

CAGR / Sharpe / Sortino / Max DD on every Game strategy header,
computed from the daily mark-to-market curve.

## 1.7.0 — Trade journal

Per-trade `note` field, FIFO outcome stats (win rate, expectancy),
CSV export. Claude proposal reasons archived as the note.

## 1.6.0 — Claude-driven Briefing strategy

"Ask Claude to propose trades" button under the Briefing chat. 0–3
structured proposals rendered as confirm/skip cards. First confirm
auto-creates a dedicated `Briefing strategy` portfolio.

## 1.5.0 — Strategy ergonomics

Inline return % in the strategy selector, per-position Sell-all
buttons, equal-weight buy-and-hold benchmark on the Compare expander.

## 1.4.x — Compare strategies

Dotted equal-weight buy-and-hold overlay; Compare expander overlaying
every portfolio's value curve as % return from inception.

## 1.3.0 — Watchlist benchmark

`benchmark_history()` + dotted overlay on the single-strategy chart.

## 1.2.0 — Briefing → Game inline trade

Paper-trade panel below Claude's response, scoped to the symbols
Claude saw.

## 1.1.0 — Multiple Game strategies

`portfolio.db` v2 schema (portfolios + meta + trades with FK).
Active strategy persisted in `meta('active_portfolio_id')`.

## 1.0.0 — Paper-trading Game

Game page in the sidebar, virtual cash, fractional-share buy/sell at
the latest close +0.1% slippage.

## 0.3.x — Initial public surface

Collector, scoring engine, backtest engine, alerts, Streamlit
dashboard.
