# Backups, and moving AlgoEarning to another computer

The code is on GitHub. Three things are not, and a new computer needs all three:

| What                                      | Where on this Mac                                                                  | How it moves                                            |
| ----------------------------------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------- |
| The database (users, strategies, history) | Postgres in `.data/pg/`                                                            | `scripts/backup.py` to Cloudflare R2, then restore      |
| Secrets (keys, `APP_ENCRYPTION_KEY`)      | `.env`, `.env.production`, `apps/web/.env.local`, `apps/web/.env.production.local` | copy them by hand, keep a copy in your password manager |
| The Cloudflare Tunnel login               | `~/.cloudflared/` (`cert.pem`, `<tunnel id>.json`, `algoearning.yml`)              | copy the folder by hand                                 |

`APP_ENCRYPTION_KEY` must be the same on the new computer. Without it, the stored broker credentials and the daily
market-data sessions in the database cannot be opened, and every user has to enter their broker keys again.

## Backups to Cloudflare R2 (manual)

Nothing runs on a schedule: run `push` whenever you want a backup, for example after a big history download.

### One-time setup

1. Cloudflare dashboard > **R2 Object Storage**. Create a bucket named `algoearning-backups`. Leave it private
   (no public access, no custom domain). The free tier covers 10 GB.
2. R2 > **Manage API tokens** > **Create API token**. Pick **Object Read & Write**, limited to that bucket.
   Cloudflare shows an Access Key ID and a Secret Access Key once. Your Account ID is on the R2 overview page.
3. Add them to `.env` (the names are in `.env.example`):

   ```bash
   R2_ACCOUNT_ID=...
   R2_ACCESS_KEY_ID=...
   R2_SECRET_ACCESS_KEY=...
   R2_BUCKET=algoearning-backups
   ```

### Take a backup

```bash
uv run --env-file .env --with boto3 python scripts/backup.py push full      # the whole database (about 30 MB now)
uv run --env-file .env --with boto3 python scripts/backup.py push history   # only history_candles
uv run --env-file .env --with boto3 python scripts/backup.py list           # what is in the bucket
```

`push` writes the dump to `.data/backups/` (never committed: `.data/` is in `.gitignore`), then uploads it as
`backups/full-YYYYMMDD-HHMM.dump`. It only reads the database, so it is safe while the app runs. A full dump has
user emails and encrypted broker credentials in it, so keep the bucket private. Delete old backups from the R2
dashboard when you no longer need them.

### Restore a backup

```bash
uv run --env-file .env --with boto3 python scripts/backup.py pull latest-full   # or latest-history, or a name from list
scripts/home-host.sh stop api web feed worker engine                            # or stop make dev (Ctrl-C)
uv run --env-file .env python scripts/backup.py restore .data/backups/full-20261003-1130.dump
```

`restore` asks you to type the database name first. A **full** dump replaces every table. A **history** dump
replaces only `history_candles`. The database must already have the tables and roles from `make migrate` (step 5
below). Start the services again afterwards (`scripts/home-host.sh start`, or `make dev`).

## Set up a new computer from scratch

The steps for a Mac. Do steps 1 to 7 for development; add step 8 to move production there.

### 1. Take a fresh backup on the old computer

```bash
uv run --env-file .env --with boto3 python scripts/backup.py push full
```

Also copy these to the new computer (AirDrop, a USB stick, or from your password manager):
`.env`, `.env.production`, `apps/web/.env.local`, `apps/web/.env.production.local`, and the folder
`~/.cloudflared/`.

### 2. Install the tools

```bash
xcode-select --install                     # git and compilers (skip if already installed)
# Homebrew, from https://brew.sh:
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install node@22 pnpm uv postgresql@17 redis cloudflared
```

### 3. Clone the repo

```bash
mkdir -p ~/git && cd ~/git
git clone https://github.com/rohitkr/algoearning.git algoearning-github
cd algoearning-github
```

### 4. Put the secrets back, and install the dependencies

Copy the files from step 1 into the same places in the repo, then lock them down:

```bash
chmod 600 .env .env.production apps/web/.env.local apps/web/.env.production.local
make setup                                 # pnpm install + uv sync (all JS and Python packages)
```

Starting without the old files? `make dev` creates `.env` and `apps/web/.env.local` from the examples. Fill in the
keys by hand, and use the old `APP_ENCRYPTION_KEY` (see the top of this page).

### 5. Start the database and create the tables

```bash
make db-up                                 # Postgres + Redis, data in .data/ (creates algoearning + algoearning_test)
make migrate                               # every table, plus the ae_app/ae_system roles
```

### 6. Restore the data

```bash
uv run --env-file .env --with boto3 python scripts/backup.py pull latest-full
uv run --env-file .env python scripts/backup.py restore .data/backups/full-<date>.dump
uv run --env-file .env python scripts/show-history.py   # check: the history is there
```

### 7. Run it (development: API + frontend)

```bash
make dev                                   # API on http://localhost:8000 (docs at /docs), web on http://localhost:3000
make check                                 # in another terminal: lint, typecheck, tests
```

`make dev` runs the API and the Next.js frontend together. The frontend reads `apps/web/.env.local` (Clerk keys,
`NEXT_PUBLIC_API_URL`). Open http://localhost:3000 and sign in.

### 8. Move production to it (home hosting)

Only one computer may serve production at a time: two would both answer on the tunnel, and two engines would trade.

1. **On the old computer**, stop everything for good:

   ```bash
   scripts/home-host.sh uninstall
   ```

2. **On the new computer**, stop `make dev` (Ctrl-C), then:

   ```bash
   scripts/home-host.sh tunnel             # finds the existing tunnel, rewrites ~/.cloudflared/algoearning.yml
   scripts/home-host.sh build              # production build of the frontend (about a minute)
   scripts/home-host.sh install            # every service: now and at each login
   scripts/home-host.sh status             # health, and this computer's public IP
   ```

   Without the old `~/.cloudflared/` folder, run `cloudflared tunnel login` first and see
   [home-hosting.md](home-hosting.md), step 2.

3. If the public IP changed, update it in each Zerodha Kite app (developers.kite.trade > your app > IP list).
   The redirect URLs stay the same, because the domain does not change.
4. Open https://app.algoearning.com, sign in, and do the day's market-data login in Monitor > Market data.
5. The Mac settings that keep it awake: [home-hosting.md](home-hosting.md), step 5.
