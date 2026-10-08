# AlgoEarning

Algo trading SaaS for Indian markets: build and deploy options strategies on your own broker account.

New here? Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how it fits together and
[CONTRIBUTING.md](CONTRIBUTING.md) for setup, commands, tests and the branch workflow.

## Repository layout

```
apps/
  web/          Next.js web app (dashboard, builder, Monitor), light and dark themes
  landing/      static marketing page for algoearning.com (no build step)
  api/          FastAPI: HTTP + realtime API          (Python package ae_api)
  engine/       trading engine: per-user sessions     (ae_engine, phase 9)
  worker/       background jobs                       (ae_worker)
packages/
  ui/           design system: theme tokens + React components
  shared/       TypeScript utilities (formatting, P&L maths)
  api-types/    TypeScript types generated from the API's OpenAPI spec
  config/       shared tsconfig / ESLint
  py-core/      trading domain (phases 8-9)
  py-brokers/   broker adapters, Zerodha first (phase 7)
  py-marketdata/ platform market-data feed (phase 10)
  py-db/        models, migrations, repositories (phase 3)
infra/docker/   Dockerfiles + compose
docs/adr/       architecture decision records
docs/home-hosting.md  running production on this Mac (Cloudflare Tunnel)
```

## Quick start

Needs Node 22, [pnpm](https://pnpm.io) and [uv](https://docs.astral.sh/uv/) (`brew install pnpm uv`).

Needs Postgres 17 + Redis for local runs: `brew install postgresql@17 redis` (no Docker required).

```bash
make dev        # or: npm run dev / pnpm dev / scripts/dev.sh
```

One command does everything: checks tools, creates `.env` files from the examples on first run, installs
dependencies when the lockfiles change, starts Postgres + Redis (`.data/`), applies migrations, then runs the
API (http://localhost:8000, docs at /docs) and the web app (http://localhost:3000) until you press Ctrl-C.
Options: `--kill-ports` (free 3000/8000 first, also `make dev-fresh`), `--stop-db` (stop Postgres + Redis on exit).

```bash
make check      # lint + typecheck + tests (what CI runs)
make db-down    # stop Postgres + Redis
```

With Docker installed, `make up` runs the whole stack (web, api, engine, worker, Postgres, Redis).

`make help` lists every target. Moving to another computer: [docs/setup.md](docs/setup.md).

## Commands

Python commands read their settings from `.env` (`DATABASE_URL`, `REDIS_URL`, broker keys), so run them as
`uv run --env-file .env ...` from the repo root. On the home-hosted Mac, `.env` points at the production database.

### Shortcuts (`pnpm <name>`, or `npm run <name>`)

Extra words after the name are passed on, e.g. `pnpm history:show NIFTY 2025-01-02` (with npm, put `--` before them).

| Shortcut                                       | Runs                                                            |
| ---------------------------------------------- | --------------------------------------------------------------- |
| `config:push` / `list` / `restore`             | `scripts/config-backup.sh` (the `.env` files to Google Drive)   |
| `db:backup`, `db:backup:history`               | `scripts/backup.py push full` / `push history` (database to R2) |
| `db:backup:list`, `db:backup:pull`             | `backup.py list` / `pull <latest-full\|name>`                   |
| `db:restore .data/backups/<file>.dump`         | `backup.py restore <file>` (stop the services first)            |
| `history:show [KEY DAY]`                       | `scripts/show-history.py`                                       |
| `history:backfill NIFTY --from DATE`           | `python -m ae_worker backfill` (download history)               |
| `host:deploy`, `host:status`, `host:logs`, ... | `scripts/home-host.sh <command>` (production on this Mac)       |

### Develop

```bash
make dev                 # everything: Postgres + Redis, migrations, API :8000, web :3000
make dev-fresh           # same, first freeing ports 3000/8000
make dev-api             # only the API, with reload
make dev-web             # only the web app
make db-up / db-down     # local Postgres + Redis (data in .data/), also scripts/local-db.sh up|down|status
make setup               # install JS + Python dependencies
make help                # every make target
```

### Check

```bash
make check               # lint + typecheck + tests (what CI runs)
make test                # all tests (test-py: pytest, test-js: vitest)
make lint / typecheck    # ruff + eslint + prettier / mypy (strict) + tsc
make format              # auto-format Python and JS
uv run pytest apps/api/tests/test_market.py -q   # one test file
uv run python scripts/check-contrast.py          # every theme colour passes WCAG AA, light and dark
```

### Database and API types

```bash
make migrate                         # apply migrations (alembic upgrade head)
make migration m="add foo"           # new migration from model changes
make types                           # regenerate web API types from the FastAPI OpenAPI spec
uv run --env-file .env alembic -c packages/py-db/alembic.ini current   # revision the database is at
```

### Historical data (backtests)

1-minute candles live in the `history_candles` table. The backfill fetches from the feed's provider (Breeze, or
the platform Kite account), so it needs that provider's keys and today's session from Monitor > Market data.
Breeze allows 5,000 REST calls a day, shared with the live feed: run it after market hours. A rerun continues
where the last one stopped.

```bash
# download: the index first, then the option contracts in reach each day
uv run --env-file .env python -m ae_worker backfill NIFTY --from 2026-01-01 --to 2026-10-08
uv run --env-file .env python -m ae_worker backfill NIFTY --from 2026-01-01 --dry-run      # plan + count calls only
uv run --env-file .env python -m ae_worker backfill SENSEX --from 2026-01-01 --index-only  # index candles only
#   more options: --reserve 500 (calls kept for the live feed), --buffer 4 (strikes beyond each day's range)

# import an existing DuckDB file (from algo-trading-claude)
uv run --env-file .env --with duckdb python -m ae_worker import-history ~/git/algo-trading-claude/data/market_data.duckdb

# look at what is stored
uv run --env-file .env python scripts/show-history.py                          # summary per underlying
uv run --env-file .env python scripts/show-history.py NIFTY 2026-01-02         # the index's candles that day
uv run --env-file .env python scripts/show-history.py NIFTY 2026-01-02 --options              # contracts that day
uv run --env-file .env python scripts/show-history.py NIFTY:2026-01-02:23600:CE 2025-01-02    # one contract

# the 3 x 3 SMC backtests as a JSON report
uv run --env-file .env python -m ae_worker smc-report NIFTY,BANKNIFTY,SENSEX --from 2025-01-01 --out smc.json
```

Keys: an index is its code (`NIFTY`), an option is `UNDERLYING:EXPIRY:STRIKE:RIGHT`. Times are IST.

### Backups and a new computer

Manual backups of the database to Cloudflare R2, restoring them, and setting up another computer from a fresh
clone step by step: [docs/setup.md](docs/setup.md).

```bash
uv run --env-file .env --with boto3 python scripts/backup.py push full          # or: push history
uv run --env-file .env --with boto3 python scripts/backup.py list
uv run --env-file .env --with boto3 python scripts/backup.py pull latest-full   # into .data/backups/
uv run --env-file .env python scripts/backup.py restore .data/backups/full-<date>.dump
```

### Backing up the config files (.env and the tunnel login)

These files are never in git, so back them up yourself after changing any of them: `.env`, `.env.production`,
`apps/web/.env.local`, `apps/web/.env.production.local` and the `~/.cloudflared/` folder. The command copies them as
they are into Google Drive, `My Drive/AlgoEarning/config/` (same paths as in the repo, `~/.cloudflared` as
`cloudflared/`), where you can open them directly. Drive keeps older versions of each file (right-click > Manage
versions).

One-time setup: `brew install --cask google-drive`, open Google Drive and sign in. The script finds the Drive folder
by itself.

```bash
scripts/config-backup.sh push      # after any change: copy the files into Google Drive
scripts/config-backup.sh list      # what the Drive copy holds
scripts/config-backup.sh restore   # copy them back, e.g. on a new computer; a differing file is kept as .bak-<date>
```

`APP_ENCRYPTION_KEY` in `.env` matters most: without it the broker logins in the database backups cannot be read,
and unlike the other keys it cannot be re-created.

### Background processes (run by hand)

```bash
uv run --env-file .env python -m ae_marketdata           # the market-data feed (MARKET_DATA_SOURCE=breeze|kite|simulated|auto)
uv run --env-file .env python -m ae_engine               # the trading engine (--once: start, report ready, exit)
uv run --env-file .env python -m ae_signals  # the Telegram signal reader (read-only; ADR 0025)
uv run --env-file .env python -m ae_worker               # scheduled jobs (--once: every job once, then exit)
uv run --env-file .env python -m ae_worker refresh-instruments   # one job, then exit
```

Never run these, or `make dev`, while production runs on the same Mac: they share the ports and the database.

### Production on this Mac (docs/home-hosting.md)

```bash
scripts/home-host.sh deploy          # build the web app, restart api, web, feed, worker (also: npm run deploy)
scripts/home-host.sh status          # what runs
scripts/home-host.sh logs [service]  # follow logs (.data/logs/<service>.log)
scripts/home-host.sh restart engine  # the engine is not restarted by deploy: outside market hours only
scripts/home-host.sh start|stop|restart [service]
scripts/home-host.sh setup|tunnel|install|uninstall   # one-time setup
```

Services: postgres, redis, api, web, feed, worker, signals, engine, tunnel, awake.

## Conventions

- Colours come only from theme tokens (`bg-surface`, `text-muted`, `text-profit`, ...); see `packages/ui/src/styles.css`.
- After changing API models run `make types`; CI fails if the generated types are stale.
- Secrets live in `.env` / the deployment secret store, never in git (CI runs gitleaks).
