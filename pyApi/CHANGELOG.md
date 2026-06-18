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

## 1.19.0 — Public-API + schema commitment _(this release)_

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
