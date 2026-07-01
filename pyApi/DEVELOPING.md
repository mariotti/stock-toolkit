# Developing on Stock Toolkit

A working developer's tour of the codebase. Assumes you've read
[`QUICKSTART.md`](QUICKSTART.md) (install paths) and have a Python
3.10+ on your `PATH`.

---

## 1. Dev environment

```bash
git clone https://gitlab.com/Mariotti/stock-toolkit.git
cd stock-toolkit/pyApi
python3 -m venv venv
source venv/bin/activate
pip install -e .                  # installs the package + every stock-* entry point
pip install ruff pyinstaller      # dev-only — not in pyproject deps
```

That gives you `stock-collect`, `stock-score`, `stock-backtest`,
`stock-ui`, `stock-setup`, `stock-bootstrap`, `stock-gap-fill`,
`stock-inventory`, and `stock-alerts` on `PATH` — all reading the
same `config.env` and the same SQLite databases.

**`config.env`** lives next to the source tree during dev. Run
`stock-setup` once to generate it, or copy `dist/config.env.template`
and edit by hand. `config.env*` is gitignored — your keys never get
committed.

**Data location (v1.17+).** All on-disk state lives under a single
`DATA_DIR`. Defaults:

| Mode                  | `BASE_DIR`               | `DATA_DIR`                  |
|-----------------------|--------------------------|-----------------------------|
| Native dev (no env)   | `os.getcwd()` (= `pyApi/`) | `BASE_DIR/data/`            |
| Docker / Win .exe     | `$STOCK_DIR` (= `/data`)   | same as `BASE_DIR` (no nest) |
| Explicit override     | (unchanged)              | `$OUTPUT_DIR` in `config.env` |

Inside `DATA_DIR` after migration:

```
data/
├── stock_data.db          live DB
├── stock_failures.db
├── portfolio.db           Game state
├── .collector_state.json
├── .alerts_state.json
├── stock_data.csv         CSV mirror (if produced)
├── historical/            bootstrap DBs (was data/ pre-v1.17)
│   └── stock_data_<range>.db
└── logs/
    └── collector.log
```

Upgrading from a pre-v1.17 install? On the first run, `common.py`
auto-detects loose DBs at `BASE_DIR/` and moves them into
`DATA_DIR/` — one stderr line records what was moved. Idempotent.

**Logs** land in `DATA_DIR/logs/collector.log` (rotating, 3 × 1 MB).
Override with `LOG_DIR` in `config.env`.

---

## 2. Tests

```bash
python3 -m unittest discover -s tests        # everything (what CI runs)
python3 tests/test_toolkit.py                # core: collector/analysis/score/backtest/alerts/inventory
python3 tests/test_ui.py                     # Streamlit dashboard via AppTest
python3 tests/test_sources.py                # API fetchers against canned responses
python3 tests/test_collector_units.py        # budgets, safe_get, historical orchestration
python3 tests/test_game.py                   # paper-trading engine + UI render
ruff check .                                  # lint (CI also runs this)
```

**Where to add a new test.** Match the existing topology:

| File                       | What lives here                                            |
|----------------------------|------------------------------------------------------------|
| `tests/test_toolkit.py`    | Score steps, backtest signals, analysis, alerts. Uses a small fixture DB in a temp dir (see `FixtureTestCase`). |
| `tests/test_ui.py`         | Streamlit pages via `streamlit.testing.v1.AppTest`. Use `AppTest.from_file(page_path)`. |
| `tests/test_sources.py`    | API fetchers against pre-recorded JSON/HTML — never hits the network. |
| `tests/test_collector_units.py` | Budgets, retry/backoff, the safe_get wrapper. |
| `tests/test_game.py`       | Game engine (`game.py`) + the Game UI render. Pattern is `GameTestCase` with a tmp `portfolio.db` and a tmp price DB. |
| `tests/test_engine_rust.py`| Dispatcher for `stock-collect --engine rust` (`collector/engine.py`). Mocks `subprocess.run` + `shutil.which` — never spawns the real Rust binary. Covers binary discovery, source allow-list, argv shape, exit-code propagation. |
| `tests/test_audit_log.py`  | v2.4.0 audit log: bootstrap markers, every game.py mutation, destructive recovery-source guarantee (full pre-state in `before_json`), v1→v2 migration audit, atomicity (failed op rolls back the audit row), reader API. |
| `tests/test_backup.py`     | v2.4.1 `stock_toolkit.backup`: snapshot round-trip (`VACUUM INTO` opens as a working DB), manifest method per entry, rotation preserving pre-destructive snapshots, config opt-out, game-level integration (delete + reset hooks fire, audit row links the path, failure isolation). |
| `tests/test_live_apis.py`  | **Network-touching** — only run on demand, NOT in CI. |

**Fast iteration loop.** Run just one class:

```bash
python3 -m unittest tests.test_game.TestRiskStats
```

**Test-count figures stay fresh automatically.** The `Ran N tests`
number in the README and the suite total in this file are kept in sync
by `bin/update-test-counts`, which runs the canonical commands and
rewrites the figures in place. CI runs it with `--check` (the
`docs-test-count` job) and fails the pipeline if either drifted — so the
counts can't silently rot the way `242` did. After adding tests, run it
and commit the doc bump:

```bash
python3 bin/update-test-counts          # rewrite README + DEVELOPING
python3 bin/update-test-counts --check  # what CI runs (exit 1 if stale)
```

**Streamlit AppTest pattern.** When a new sidebar page is added,
register a `TestXPageRenders` class in `test_ui.py` that drives
the shim through `AppTest.from_file(...)` and asserts
`[e.value for e in at.exception] == []`. See
`TestAdminPageRenders` / `TestHelpPageRenders` for the shape.

---

## 3. Release pipeline

A release is whatever's on `main` at a given `vX.Y.Z` tag.

```bash
# 1. Bump version (single source of truth)
echo "1.17.0" > VERSION

# 2. Build the two distributable shapes
#    Files stage under dist/ (toolkit) and dist-app/ (app); the
#    .tar.gz/.zip bundles for BOTH land in dist-bundles/.
echo y | python3 make_dist.py --package toolkit  # → dist-bundles/stock-toolkit-X.Y.Z.{tar.gz,zip}
echo y | python3 make_dist.py --package app      # → dist-bundles/stock-app-X.Y.Z.{tar.gz,zip}

# 3. Commit + tag + push to BOTH GitLab remotes
cd ..   # repo root
git add -A
git -c commit.gpgsign=false commit -m "vX.Y.Z: <one-line>"
git tag -a vX.Y.Z -m "vX.Y.Z — <one-line>"
git push --follow-tags origin main
GIT_SSH_COMMAND="ssh -o IdentitiesOnly=yes -i ~/.ssh/gitlab_002" \
    git push --follow-tags public main

# 4. Create the GitLab Releases (does the small assets in one shot)
glab release create vX.Y.Z -R Mariotti/stock_py_api \
    --name "vX.Y.Z — <title>" \
    --notes "..." \
    "dist-bundles/stock-toolkit-X.Y.Z.tar.gz#..." \
    "dist-bundles/stock-toolkit-X.Y.Z.zip#..." \
    "dist-bundles/stock-app-X.Y.Z.tar.gz#..." \
    "dist-bundles/stock-app-X.Y.Z.zip#..."

glab release create vX.Y.Z -R Mariotti/stock-toolkit ...
```

**The Windows .exe is automatic from here.** The GitLab→GitHub
push-mirror picks up the tag within ~5 minutes; GitHub Actions runs
`build-windows-exe.yml` on `windows-latest`; the resulting zip is
published as a GitHub Release **automatically** (commit `dc06e4c`
wired this up).

**Attaching the .exe to GitLab — use `bin/relay-windows-zip`.**
Release assets cap at ~100 MB on gitlab.com; the .exe is ~140 MB.
The workaround is "upload to the project's **Generic Package
Registry**, then add a release link." The script bundles all three
steps (download from GitHub, upload to the registry, link onto the
GitLab release) into one idempotent call:

```bash
# After the GitHub Actions build for vX.Y.Z lands a .zip on the
# GitHub release:
pyApi/bin/relay-windows-zip vX.Y.Z
```

Idempotent — safe to re-run if a step failed. Already-linked
releases return GitLab's "has already been taken" and the script
exits cleanly. Pre-v2.4.3 releases used an ad-hoc bash loop; the
script replaces it.

`GH_REPO`, `GL_PROJECT`, `DOWNLOAD_DIR` env vars override the
defaults if you ever fork the mirror layout.

---

## 4. Conventions worth knowing

**Single config writer.** Don't open `config.env` and `f.write()`
anywhere. The Admin page edits config via
`stock_toolkit.common.update_config_value(key, value, config_path)`,
which preserves comments, inline annotations, and any other lines.
After a write, call `stock_toolkit.ui.helpers.reload_config()` so
in-memory `_cfg` consumers (the Briefing prompt, etc.) pick up
the new value on the next render — no Streamlit restart.

**Icons via the registry, not literals.** Every glyph in the UI
flows through `stock_toolkit.ui.icons`:

```python
from stock_toolkit.ui.icons import icon, heading, tab_label

st.markdown(heading("watchlist", "Watchlist"))   # ▪ Watchlist
st.button(f"{icon('save')}  Save")                # ✓ Save
```

Two-layer: `SEMANTIC[name] → token → GLYPHS[token]`. Edit
`GLYPHS["execute"]` once and every Run / Buy / Backtest button
restyles in lockstep. A regression test asserts every render module
calls `setup_page` so a new page can't silently drop the theme.

**Theme via one module.** `stock_toolkit.ui.theme.setup_page(title)`
is the first line of every `render()`. The CSS palette + Plotly
chart palette (`CHART_BG`, `CHART_GRID`, `CHART_INK`, etc.) live in
the same module — `charts.py` and `game.py` import these so a future
theme flip is a single-file change. **Do not** hardcode hex
colours in chart layouts.

**Comments: only WHY.** From CLAUDE.md: "default to writing no
comments. Only add one when the WHY is non-obvious." Don't restate
what the code does; explain the constraint that made it non-obvious.

**Test count is meaningful.** Every release commit message includes
the test count after the change (`+8 tests → 590 green`). When you
add a behaviour, add the test that protects it.

---

## 5. Upgrading from a pre-2.0 install

For any user picking up a release from before the 2.0 cutover:

- **The v1.17 layout** (single `DATA_DIR`, historicals under
  `historical/`) is migrated automatically on the first import of
  `stock_toolkit.common` — including from a `stock-*` CLI. One
  stderr line records what moved; idempotent.
- **The v1.13 API-keys flow** is the new way to add free keys via
  Admin; existing `config.env` files keep working.
- **The public API** (the names in each module's `__all__`) is the
  surface 2.x commits to preserve. Anything not listed is
  implementation detail; if you reach for it, expect breakage.
- **DB schemas**: see [`SCHEMA.md`](SCHEMA.md) for the column-level
  compatibility table — what's safe to add / drop / rename inside a
  major.

If anything looks off, `stock-sanity` audits the deterministic
invariants in one shot.

## 6. CI shape

Two pipelines, intentionally on different platforms:

**GitLab CI** (`.gitlab-ci.yml`) — runs on every push to both GitLab
remotes. Free Linux runners. Four jobs in one `test` stage (parallel):
`lint` (ruff), `test` (offline suite under `coverage`), `docs-test-count`
(the `bin/update-test-counts --check` drift guard), and `test-latest-deps`
(scheduled-only, no pip cache).

**Coverage.** The `test` job runs the suite under `coverage`, then:
- `coverage report --fail-under=80` is a **hard floor** — the build fails
  if total line coverage drops below 80%. CI's clean checkout measures
  ~81% (a dev tree with a real `config.env` + populated `data/` covers
  ~1% more, so don't be surprised if local `coverage` reads higher),
- `coverage: '/TOTAL.*\s(\d+%)/'` extracts the % onto the pipeline/MR widget,
- a **Cobertura** artifact (`coverage_report`) drives inline coverage
  annotations on MR diffs (`<source>` resolves to `$CI_PROJECT_DIR/pyApi`,
  so the `stock_toolkit/…` paths match), and
- **Codecov** gets `coverage.xml` via `codecovcli upload-process` for the
  badge + trend history. The upload is best-effort (`|| echo …`) so a
  Codecov outage never reds the build.

  One-time setup (not in code): enable the project on codecov.io and add
  `CODECOV_TOKEN` as a **masked** CI/CD variable. The README badges point
  at the public `Mariotti/stock-toolkit` project (they're plain image URLs,
  so they also render on the GitHub mirror). Because GitHub here is a
  read-only mirror, Codecov's PR comments only fire on GitLab MRs — the
  GitHub side gets the badge only.

**GitHub Actions** (`.github/workflows/build-windows-exe.yml`) —
runs on tag pushes to the GitHub mirror. Three steps:

1. `pyinstaller --noconfirm StockToolkit.spec` — builds the .exe.
   Spec is at `pyApi/pyinstaller/StockToolkit.spec`. If a Streamlit
   internal module is missed, add it to `hiddenimports` there.
2. **Smoke test** — launches the .exe, polls `http://localhost:8501`,
   asserts HTTP 200. This caught the
   `RuntimeError: server.port does not work when global.developmentMode is true`
   in v1.14.2 — fix was a one-liner in `launcher.py`.
3. `softprops/action-gh-release@v2` — auto-publishes a GitHub Release
   with the zip attached. Guarded by `event_name == 'push'` and
   `refs/tags/*` so `workflow_dispatch` runs don't create releases.

**Debugging a failed Windows build.** `gh run view <id> --log-failed`
from the CLI on the GitHub mirror. The smoke-test step prints the
last 50 lines of stdout/stderr from the launched .exe — almost every
failure shows up there as a `ModuleNotFoundError` or a Streamlit
runtime error.

**Triggering manually.** `gh workflow run build-windows-exe.yml
-R mariotti/stock-toolkit --ref main` — useful for iterating on the
spec without pushing tags.

---

## Surfaces, in one diagram

```
config.env            ←  update_config_value() / Admin → 🛠 Settings
   │
   ▼
stock_toolkit.collector  →  stock_data.db
   │                            │
   ▼                            ▼
analysis · score · backtest · alerts · game
   │                            │
   ▼                            ▼
ui.tabs · ui.admin · ui.game · ui.help
   │
   ▼
ui.theme.setup_page()  +  ui.icons.icon()  →  Streamlit
```

Everything writes through `update_config_value`, reads through
`load_config`, and renders through `setup_page` + `icon`. Stay on
those rails and the rest of the system stays consistent.
