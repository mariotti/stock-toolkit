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

## 2.4.3 — Release flow: `bin/relay-windows-zip` (GitHub → GitLab)

Docs-only release. Documents and automates the post-tag relay step
that gets the GitHub-built Windows `.exe` bundle onto the matching
GitLab release.

### Why this exists

GitLab release assets cap at ~100 MB on gitlab.com; the Windows
`.exe` bundle is ~140 MB. The standard workaround — upload to the
project's Generic Package Registry, then link it onto the release —
is three API calls (`gh release download` → `glab api PUT` →
`glab api POST .../assets/links`). For v2.3.0 through v2.4.2 we
ran an ad-hoc bash loop by hand; this release replaces it with a
script.

### What's new

- **`pyApi/bin/relay-windows-zip vX.Y.Z`** — one command that
  downloads from `mariotti/stock-toolkit` (GitHub mirror), uploads
  to `Mariotti/stock-toolkit`'s Package Registry, then adds a
  release link with the same "🪟 Native Windows .exe bundle" label
  the previous tags used.
- **Idempotent.** Re-runs after a botched relay are safe — already-
  linked releases produce GitLab's "has already been taken" and the
  script exits cleanly. The PUT to the registry is a no-op refresh
  if the file's already there.
- **`pyApi/DEVELOPING.md` §3 "Release pipeline"** — replaces the
  fragile "see the shell loop at the bottom of any v1.14.2+ commit
  message" pointer with a real command pointing at the script.
- **Env overrides** for forks / alt layouts: `GH_REPO`,
  `GL_PROJECT`, `DOWNLOAD_DIR`. Defaults match this project.

### Validation

- Smoke-tested against `v2.4.2` (an already-attached release): the
  script ran end-to-end and correctly hit the "already taken"
  branch on the link API.

### Not in scope

- The script doesn't trigger or wait for the GitHub Actions build —
  run it only after the build has published the `.zip` on the
  GitHub release.
- The CHANGELOG-and-script-only nature means no tests changed: **422
  Python tests** (unchanged from v2.4.2), all green. **Rust: 24**,
  unchanged.

No new pip / cargo deps.

## 2.4.2 — Game page History expander (audit log + backup links)

Final slice of the audit + backup arc. v2.4.0 made every mutation
visible (audit_log table). v2.4.1 made every destructive mutation
recoverable (VACUUM INTO snapshot + audit `before_json`). v2.4.2
surfaces both in the UI so you don't need a SQLite client to read
them.

### What's new

- New **History** expander on the Game page, between "Trade history"
  and "Settings". Reads via `get_audit_log()` — fully testable through
  the same path Streamlit uses.
- Three filter selectboxes:
  - **Scope** — "Current strategy only" (joins trade-audits via FK)
    vs "All strategies in this DB".
  - **Operation kind** — All / Portfolio ops / Trade ops / System.
  - **Show** — 50 / 100 / 250 / 1000 rows (newest first).
- Compact table view: When, Actor, Op, Target, truncated Note.
- Per-row expander (capped at 30 to keep the page responsive)
  showing the full `before` / `after` JSON.
- When the row's note carries `pre_destructive_snapshot=<path>`,
  the path is surfaced as plain text with an existence check
  (✓ on disk / ✗ missing) and a copy-paste restore hint:
  `cp <snapshot>/portfolio.db data/portfolio.db`.
- CSV download — full audit log with untruncated notes.

### Tests

- **1 new UI test** (`tests/test_ui.py::TestGameHistoryExpanderRenders`):
  Renders the Game page through its production page-shim
  (`pages/02_🎮_Game.py`) and asserts the History expander label,
  caption, and all three filter selectbox keys are present.
- Underlying audit-log behavior is already exhaustively covered by
  `tests/test_audit_log.py` (21 tests). No duplication.

**Total: 422 Python tests** (was 421 → +1), all green. **Rust: 24**,
unchanged.

### Arc complete

| | v2.4.0 | v2.4.1 | v2.4.2 |
|---|---|---|---|
| Mutation visibility | ✅ audit_log table | ✅ unchanged | ✅ surfaced in UI |
| Destructive recovery | `before_json` snapshot | + VACUUM INTO snapshot | + UI restore hint |
| User-facing | API only | + `stock-backup` CLI | + Game page History |

No new pip / cargo deps.

## 2.4.1 — `stock-backup` CLI + pre-destructive auto-snapshot

Second slice of the audit + backup arc. v2.4.0 made every mutation
visible; this release makes every mutation *recoverable* via two
independent safety nets.

### What's new

- **New module `stock_toolkit/backup.py`** + entry point `stock-backup`.
  Uses SQLite ``VACUUM INTO`` for live DBs (consistent snapshot
  even with the DB open under WAL — `cp` of an open DB is NOT safe)
  and `shutil.copy2` for the tiny JSON state files.
- **Manual snapshots** land in `data/backups/<timestamp>/` and are
  **rotated** (keep last 30 by default, `--keep N` to override).
- **Pre-destructive auto-snapshots** land in
  `data/backups/pre-destructive/<timestamp>-pre-<op>-portfolio-<id>/`
  and are **never** rotated — destructive history outranks disk
  pressure. Opt-out: `AUTO_BACKUP_BEFORE_DESTRUCTIVE=false` in
  config.env.
- Each destructive op (`delete_portfolio`, `reset_portfolio`) now
  takes the snapshot *before* opening its write transaction, then
  records the snapshot path inside the audit row's `note` field —
  one click in the History view (v2.4.2) will reveal where to
  recover from.
- Backup failures are caught, logged to stderr, and **do not block
  the destructive op**: the user explicitly asked for it, and the
  audit log's `before_json` is still the second safety net. (Tested
  end-to-end with a `RuntimeError("disk full")` injection.)
- Each snapshot directory ships a `manifest.json` listing every file,
  its source path, method (`VACUUM INTO` / `copy`), and byte size.

### CLI

```
stock-backup                  # snapshot now + rotate to last 30
stock-backup --keep 7         # keep just 7 manual snapshots
stock-backup --list           # list everything, manual + pre-destructive
stock-backup --dry-run        # show what would happen, no writes
stock-backup --reason "tag"   # custom manifest tag
```

### Tests

- **19 new backup tests** (`tests/test_backup.py`):
  round-trip integrity (snapshot opens and reads identically),
  manifest method per entry, missing-file tolerance, same-minute
  collision handling, `list_snapshots` partitioning, `rotate`
  preserves pre-destructive snapshots, config opt-out
  (true/false/0/1/yes/no/on/off), game-level integration (delete +
  reset hook fire, audit row links the path, opt-out path, failure
  isolation).
- All 38 game + 21 audit tests still green — no behavior drift on
  the public surface.

**Total: 421 Python tests** (was 402 → +19), all green. **Rust: 24**,
unchanged.

### Not yet (deliberately)

- UI History tab — still slated for v2.4.2.
- Restore CLI (`stock-backup --restore PATH`) — the layout is
  trivial to restore by hand (`cp data/backups/.../portfolio.db
  data/portfolio.db`), so this waits until v2.4.2 unless someone
  actually needs it.
- Auto-snapshot for collector ops (suppression, failure DB) — the
  pattern is portable; will land in v2.5.x if the game's experience
  validates it.

No new pip / cargo deps.

## 2.4.0 — Game audit log

First slice of the audit + backup work prompted by 2 paper-trading
strategies "missing" from `portfolio.db` (turned out they were never
saved — but we had no way to tell). v2.4.0 makes every mutation
visible; v2.4.1 will add the backup CLI; v2.4.2 the UI History tab.

### What's new

- New table `audit_log` in `portfolio.db`:
  `(id, timestamp, actor, op_type, target_kind, target_id,
    before_json, after_json, note)`. Schema-CREATE-IF-NOT-EXISTS,
  so existing DBs gain it on next open with a
  `system.audit_log.initialised` marker row.
- Every mutation in `stock_toolkit/game.py` now writes an audit row
  in the same transaction as the change it records — half-committed
  state is impossible. If the op fails (e.g. duplicate name), the
  audit row rolls back too.

### Operations covered

| op_type | Actor | Notes |
|---|---|---|
| `portfolio.create` | user (or system, for init's auto-Default) | `after_json` = new row |
| `portfolio.rename` | user | before/after name; same-name is a no-op |
| `portfolio.set_active` | user OR system | system rows mark rollovers after archive/delete and init adoption |
| `portfolio.archive` / `.unarchive` | user | before/after `archived_at` |
| `portfolio.delete` | user | **`before_json` carries the full portfolio row + all cascaded trades** — recovery source after VACUUM |
| `portfolio.reset` | user | **`before_json` carries the full pre-reset portfolio + wiped trades** |
| `trade.buy` / `trade.sell` | user | `after_json` = trade row + `cash_before` / `cash_after` |
| `system.schema_migrate.v1_to_v2` | system | one-shot when the legacy single-portfolio DB is upgraded |
| `system.audit_log.initialised` | system | one-shot when this table is created on a pre-v2.4.0 DB |

The `init_portfolio` auto-Default path is now flagged `actor=system`
with note "auto-created on first open" — exactly the case that bit
us earlier where a fresh "Default" appeared without an explicit user
click.

### Public API additions

- `get_audit_log(portfolio_id=None, limit=None, op_prefix=None, db=None) -> list[dict]`
  — newest-first; filters by portfolio (joins trade audits via FK)
  and op_type prefix.
- `get_audit_event(audit_id, db=None) -> dict | None` — single-row
  detail view for the upcoming UI History tab.

### Tests

- **21 new audit tests** (`tests/test_audit_log.py`) covering
  bootstrap (idempotent init marker), every mutation, the destructive
  recovery-source guarantee (delete + reset embed full pre-state),
  v1→v2 migration marker, atomicity (failed op leaves no audit row),
  and the reader API (newest-first, filters, limit, unknown id).
- All 38 pre-existing game tests still green — no behavior drift on
  the public surface.

**Total: 402 Python tests** (was 381 → +21), all green. **Rust: 24**,
unchanged.

### Not yet (deliberately)

- No backup CLI — `stock-backup` arrives in v2.4.1 with
  `VACUUM INTO`, rotation, and pre-destructive auto-snapshot.
- No UI History tab — lands in v2.4.2 once the audit data has been
  exercised in real use.
- Audit doesn't yet cover collector ops (record_failure,
  suppression) or the JSON state files. Same shape applies if the
  pattern proves itself here.

No new pip / cargo deps.

## 2.3.2 — v2.3.1 paperwork: docs + cross-language pickup hints

Docs-only follow-up to v2.3.1. No behavior change, no test count
change. The point: make the next engineer's pickup obvious.

- `rust-fetcher/README.md` — status line updated from "v2.2.0
  experimental" to v2.3.x; layout table now lists `rate_limit.rs`;
  test count 20 → 24; new "From Python (`stock-collect --engine
  rust`)" section pointing at the dispatcher; the "what's NOT
  done" list now spells out *exactly* which two files a new Rust
  source has to touch.
- `pyApi/stock_toolkit/collector/engine.py` — comment above
  `RUST_SUPPORTED_SOURCES` flags the cross-language contract with
  `rust-fetcher/src/main.rs` (and explains the rc=2 failure mode).
- `rust-fetcher/src/main.rs` — matching comment above the
  `match source_name.as_str()` arm pointing the other way.
- `pyApi/DEVELOPING.md` — test files table now lists
  `test_engine_rust.py` (was missing; auto-discovered, just
  invisible to readers).
- `pyApi/CLAUDE.md` — Commands section now shows `--engine rust`
  + the cross-language contract pointer; `test_engine_rust.py`
  added to the test-list block.
- `pyApi/README.md` — stock-collect usage gains a `--engine rust`
  example; new "Rust engine" subsection explaining the opt-in,
  the failure modes, and the binary discovery order.

Also small housekeeping: `make_dist.py --package {toolkit,app}` now
writes the per-version archives into `pyApi/dist-bundles/` (sibling
to `dist/` and `dist-app/`) instead of dropping them at the top of
`pyApi/`. Keeps `git status` quiet as versions accumulate. Gitignore
gained `dist-bundles/` and the long-missing `stock-app-*.{tar.gz,zip}`
patterns (the existing rules only covered `stock-toolkit-*`, so app
bundles kept showing up in `git status` until now).

No production code change. No new pip / cargo deps.

## 2.3.1 — Python `stock-collect --engine rust` shim

Visible payoff for the Rust foundation laid in v2.2.0 + v2.3.0:
existing `stock-collect` users can opt in to the Rust fetcher per-run
without rewriting their cron jobs.

- New `--engine {python,rust}` flag on `stock-collect`. Default is
  `python` (existing behaviour — no observable change for current
  users). `--engine rust` subprocesses out to the Rust
  `stock-fetcher` binary.
- New `stock_toolkit/collector/engine.py` — the dispatcher. Owns
  binary discovery (`STOCK_FETCHER_BIN` → `rust-fetcher/target/release/`
  → `PATH`), the source allow-list (`RUST_SUPPORTED_SOURCES = {alphavantage}`
  — grows as Rust modules ship), argv construction (Rust uses CSV
  for `--sources` / `--symbols`), and exit-code surfacing.
- Symbol resolution stays in Python: `SYMBOLS_IGNORE`,
  DB-discovered extras, staleness-sort all flow through identically.
  Rust sees the same watchlist Python would have used — no
  divergence.
- Honest fallback: if the Rust binary isn't built / on PATH /
  pointed-to by env, the shim exits 127 with a friendly message
  pointing at `rust-fetcher/README.md`. It *does not* silently fall
  back to Python — `--engine rust` is an explicit opt-in.
- Rejecting unsupported sources: `--engine rust --sources yfinance`
  exits 2 with `"Rust currently supports: alphavantage"` instead of
  invoking the binary and letting it fail mid-flight.

13 new Python tests covering: env-override discovery, repo-layout
discovery, PATH fallback, "binary missing" exit, unsupported-source
rejection, argv shape, exit-code propagation, exec-time race. 381
Python tests now (368 + 13), all green. Rust tests unchanged at 24.

No new pip / cargo deps.

## 2.3.0 — Rust fetcher: token-bucket rate limiting

Follow-on to v2.2.0. Adds the per-source rate limiter the architecture
needed before more sources land.

- New `rust-fetcher/src/rate_limit.rs` — token bucket with `per_minute`,
  `per_second`, `per_day` constructors. Composes with the existing
  per-source semaphore (semaphore caps in-flight requests; rate limit
  caps requests per unit time).
- `Source` trait gains `default_rate_limit()` so each source declares
  its own free-tier cap. Default is `None` — opt-in per source.
- `main.rs` wraps the per-symbol fetch in `rl.acquire().await` so the
  bucket gates real HTTP calls.
- Alpha Vantage source declares its limit. Two empirical findings:
  1. The free tier enforces both 25/day AND ~1/sec — the second is
     undocumented but real. Caught it on the first live two-symbol
     fetch; AAPL went through, MSFT got throttled.
  2. 1.0 s spacing still tripped the limiter — needed 1.5 s margin
     for clock skew. `Some(RateLimit::new(1, 1500ms))` clears it.

4 new Rust tests covering: initial burst, refill after sleep, blocking
acquire, concurrent-caller serialisation. 24 total Rust tests now,
all green.

No Python changes. No new pip / cargo deps.

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
