# Docker

A self-contained stack: dashboard + scheduled collector, sharing one
host data directory for `config.env`, the SQLite databases, and runtime
state. Runs identically on macOS, Linux, and ARM (NAS / Raspberry Pi)
because the image is multi-arch.

## Quick start

```bash
# Run from the REPO ROOT (where compose.yaml lives), not from pyApi/
cd stock-toolkit                               # or stock_py_api, whichever you cloned
mkdir -p data                                  # host directory for everything
docker compose run --rm ui stock-setup         # creates ./data/config.env interactively
docker compose run --rm ui stock-bootstrap     # one-time historical seed via yfinance
docker compose up -d                           # dashboard + collector, in background
open http://localhost:8501                     # dashboard
```

| Action | Command |
|---|---|
| Follow dashboard logs | `docker compose logs -f ui` |
| Follow scheduler logs | `docker compose logs -f collector` |
| Run a collection manually | `docker compose run --rm ui stock-collect` |
| Open a shell in the image | `docker compose run --rm ui /bin/bash` |
| Stop the stack | `docker compose down` |
| Update after `git pull` | `docker compose build && docker compose up -d` |

## What's inside

| Service | Purpose | Entrypoint |
|---|---|---|
| `ui` | Streamlit dashboard on port 8501 | `stock-ui` |
| `collector` | [supercronic](https://github.com/aptible/supercronic) running `docker/crontab` | `supercronic /app/docker/crontab` |

The schedule in `docker/crontab` mirrors `pyApi/crontab.demo` and the
macOS launchd plists — weekday tiers at 08:00 / 13:00 / 23:30 UTC plus
weekly DB maintenance on Sunday 00:30 UTC.

## Layout on the host

The compose file mounts a single host directory (`./data` by default,
override with `HOST_DATA_DIR` in `.env`) to `/data` inside the container.
That directory holds:

```
data/
├── config.env              # API keys (created by stock-setup; keep it safe)
├── stock_data.db           # the live OHLCV store
├── stock_failures.db       # failure tracker
├── data/                   # historical DBs (--historical flag)
├── collector.log
└── .alerts_state.json
```

The image itself contains only code. Wipe the image and re-build any
time; the host `data/` directory is what matters — back it up.

## Multi-arch build (Apple Silicon, ARM NAS)

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
    -f docker/Dockerfile -t stock-toolkit:latest .
```

The Dockerfile reads `TARGETARCH` and pulls the matching supercronic
binary, so the same definition produces both architectures.

## Designed for a future native Mac wrapper

The Python code knows nothing about Docker. Everything Docker-specific
lives in this directory, and the runtime contract is just:

- entry points (`stock-ui`, `stock-collect`, …) provided by the package
- a data directory the app reads/writes from
- `$STOCK_DIR` env var pointing at it

A future Mac `.app` can reuse the existing `bin/` wrappers and launchd
plists directly against a venv, or wrap `docker compose up` — either
way, no Python changes needed.
