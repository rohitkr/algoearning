# Contributing to AlgoEarning

This guide explains how to set up the repo, run it, test a change and get it merged. How the system fits together
is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## 1. Set up (once)

On a Mac, run:

```bash
brew install node@22 pnpm uv postgresql@17 redis
git clone git@github.com:rohitkr/algoearning.git && cd algoearning
make setup              # pnpm install + uv sync --all-packages
```

Then run `make dev` once. It creates `.env` and `apps/web/.env.local` from the `.example` files and generates
`APP_ENCRYPTION_KEY`. Add your Clerk test keys to both files; sign-in won't work without them. The other keys
(Razorpay, Breeze, SMTP, Telegram) are optional. Each one is explained in `.env.example`.

**Never commit secrets.** `.env*` files are git-ignored, and CI runs gitleaks on every push.

## 2. Run it

| Command                                          | What it does                                                                                         |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------------------- |
| `make dev`                                       | Everything with live reload: Postgres, Redis, migrations, API :8000, web :3000, feed, worker, engine |
| `make dev-fresh`                                 | The same, after stopping whatever holds ports 3000/8000                                              |
| `make dev-api`                                   | Only the API, with reload                                                                            |
| `make dev-web`                                   | Only the web app                                                                                     |
| `make db-up` / `make db-down` / `make db-status` | Local Postgres + Redis (data in `.data/`)                                                            |
| `make up` / `make down`                          | The whole stack in Docker instead                                                                    |
| `make help`                                      | Every target                                                                                         |

Once it's running, the app is at http://localhost:3000, the API docs at http://localhost:8000/docs, and the admin
panel at http://localhost:3000/monitor (admin users only).

When the production services are running on this Mac (`scripts/home-host.sh`, see
[docs/home-hosting.md](docs/home-hosting.md)), `make dev` refuses to start because both need the same ports. Run
`scripts/home-host.sh stop` first, then `scripts/home-host.sh start` when you're done.

## 3. Check your change

`make check` runs everything CI runs. Keep it green before pushing.

| Command          | What it runs                                                               |
| ---------------- | -------------------------------------------------------------------------- |
| `make check`     | `lint` + `typecheck` + `test`                                              |
| `make lint`      | ruff (check + format) · ESLint · Prettier · theme contrast check           |
| `make typecheck` | mypy (strict) on all Python · `tsc` on all TypeScript                      |
| `make test`      | pytest (uses the `algoearning_test` database; `make db-up` first) · Vitest |
| `make format`    | Auto-fixes formatting: ruff + Prettier                                     |
| `make build`     | Production build of the web app                                            |

You can also run a narrower set:

```bash
uv run pytest apps/api/tests/test_brokers.py -k server_ip      # one Python test
uv run pytest apps/engine                                      # one app
pnpm --filter @algoearning/web test                            # web unit tests
pnpm --filter @algoearning/web exec tsc --noEmit               # web types only
```

Python tests run against a real Postgres, the `algoearning_test` database. Each run first proves the
migrations work by going down to base and back up, and each test starts from empty tables. Your development
data in the `algoearning` database is never touched.

## 4. Common changes

- **Database model:** edit `packages/py-db/src/ae_db/models.py`, then run `make migration m="add foo"`. Review
  the generated file in `packages/py-db/src/ae_db/migrations/versions/`, and add RLS for any new user-owned table
  (see `rls.py`). Then run `make migrate`.
- **API model or route:** after the change, run `make types` to regenerate `packages/api-types`. CI fails if
  they're stale.
- **New strategy behaviour:** put the logic in `packages/py-core` (pure, no I/O) with unit tests. The engine and
  the backtester pick it up from the runner.
- **New screen:** add a page under `apps/web/app/(app)/<section>/`. Use the `@algoearning/ui` components and
  theme tokens (`bg-surface`, `text-muted`, …), never hard-coded colours. Check it in both light and dark.
- **A decision that shapes the system:** add an ADR in `docs/adr/` (next number, short, with context, decision
  and consequences) and list it in `docs/adr/README.md`.

## 5. Branches and commits

- Branch from the current integration branch with a meaningful name: `feature/<what-it-does>`, such as
  `feature/home-hosting`.
- Make small, logical commits with messages in the form `type(scope): what changed`. Examples:
  `feat(brokers): …`, `fix(infra): …`, `docs: …`.
- Push your feature branch. Rohit builds, tests and merges, and pull requests are opened only when he asks
  for one.
- Before pushing, run `make check`, and run `make types` if the API changed.

## 6. Ship to production (the home-hosted site)

On the Mac that serves https://app.algoearning.com:

```bash
git pull                                 # the branch being served
scripts/home-host.sh deploy              # build, migrate, restart api/web/feed/worker
scripts/home-host.sh restart engine      # only if engine code changed, and outside market hours
scripts/home-host.sh status              # all green?
```

The landing page at https://algoearning.com deploys separately: see `apps/landing/README.md`.
