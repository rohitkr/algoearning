# AlgoEarning

Algo trading SaaS for Indian markets: build and deploy options strategies on your own broker account.

New here? Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how it fits together, [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md) for how trading, prices and stop-losses work (with interview questions), and
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

Two things are not in git, and both are backed up by hand (nothing runs on a schedule). Take both after changing
config, and the database backup after any big history download. Setting up another computer from nothing, without
the old one: [docs/setup.md](docs/setup.md).

| What                                            | Where it goes | Take a backup                                                                  |
| ----------------------------------------------- | ------------- | ------------------------------------------------------------------------------ |
| Env files (`.env`…) and the tunnel login        | Google Drive  | `scripts/config-backup.sh push` (`pnpm config:push`)                           |
| The whole database, or only the historical data | Cloudflare R2 | `backup.py push full` / `push history` (`pnpm db:backup`, `db:backup:history`) |

**Database to Cloudflare R2.** One-time: create a private R2 bucket `algoearning-backups` and an Object Read & Write
API token, then put `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` in `.env`. A full dump
covers every table (new tables included automatically).

```bash
uv run --env-file .env --with boto3 python scripts/backup.py push full          # or: push history
uv run --env-file .env --with boto3 python scripts/backup.py list
uv run --env-file .env --with boto3 python scripts/backup.py pull latest-full   # into .data/backups/
uv run --env-file .env python scripts/backup.py restore .data/backups/full-<date>.dump   # stop the services first
```

**Env files to Google Drive.** These files are never in git: `.env`, `.env.production`, `apps/web/.env.local`,
`apps/web/.env.production.local` and the `~/.cloudflared/` folder. The command copies them as they are into Google
Drive, `My Drive/AlgoEarning/config/` (same paths as in the repo, `~/.cloudflared` as `cloudflared/`). Drive keeps
older versions of each file (right-click > Manage versions). One-time: `brew install --cask google-drive`, open it
and sign in.

```bash
scripts/config-backup.sh push      # after any change: copy the files into Google Drive
scripts/config-backup.sh list      # what the Drive copy holds
scripts/config-backup.sh restore   # copy them back, e.g. on a new computer; a differing file is kept as .bak-<date>
```

`APP_ENCRYPTION_KEY` in `.env` matters most: without it the broker logins in the database backups cannot be read,
and unlike the other keys it cannot be re-created. The R2 keys are in the same `.env`, so a restored `.env` is what
lets you pull the database.

### Filling in missing history (Monitor > Data sync)

Instead of the `run_backfill` commands above, open Monitor > Data sync (admin only): a calendar shows every day as
complete, partial, missing or closed, the plan says how many calls a download needs, and **Download** fetches
everything missing up to today from Breeze (or Kite, which cannot fetch expired contracts) with live progress, an
estimate of the time left, and Stop. One download runs at a time. Log in to the provider first in Monitor > Market
data. Run `backup.py push history` afterwards.

Market holidays are read from `packages/py-marketdata/src/ae_marketdata/holidays.toml` (the NSE calendar; weekends
need no entry). Those weekdays show as "market closed (holiday)" in the calendar and are never downloaded or counted
as missing. **Each year, append the next year's dates to that file** and commit it. A weekday with no index candles
at all is also detected as a holiday by the download.

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

#### Team password (app.algoearning.com)

The master password that guards the whole site is the `APP_GATE_PASSWORD=` line in
`apps/web/.env.production.local`. That file is git-ignored, so VS Code's search does not look inside it: open it by
path (Cmd+P, then type `.env.production.local`) or read it with:

```bash
grep APP_GATE_PASSWORD apps/web/.env.production.local
```

To change it, replace the value after `APP_GATE_PASSWORD=` (no spaces or quotes), then run
`scripts/home-host.sh deploy`. Everyone enters the new password once; the old 30-day sign-ins stop working. An empty
value opens the site to anyone (the build warns).

## Conventions

- Colours come only from theme tokens (`bg-surface`, `text-muted`, `text-profit`, ...); see `packages/ui/src/styles.css`.
- After changing API models run `make types`; CI fails if the generated types are stale.
- Secrets live in `.env` / the deployment secret store, never in git (CI runs gitleaks).
