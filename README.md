# AlgoEarning

Algo trading SaaS for Indian markets: build and deploy options strategies on your own broker account.

## Repository layout

```
apps/
  web/          Next.js web app (landing page + dashboard), light and dark themes
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

`make help` lists every target.

## Conventions

- Colours come only from theme tokens (`bg-surface`, `text-muted`, `text-profit`, ...); see `packages/ui/src/styles.css`.
- After changing API models run `make types`; CI fails if the generated types are stale.
- Secrets live in `.env` / the deployment secret store, never in git (CI runs gitleaks).
