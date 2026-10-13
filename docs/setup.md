# Setup: a new computer from scratch, backups and restore

The code is on GitHub. Three things are not, and a new computer needs all three:

| What                                      | Where on this Mac                                                                  | How it moves                                           |
| ----------------------------------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------ |
| The database (users, strategies, history) | Postgres in `.data/pg/`                                                            | `scripts/backup.py` to Cloudflare R2, then restore     |
| Secrets (keys, `APP_ENCRYPTION_KEY`)      | `.env`, `.env.production`, `apps/web/.env.local`, `apps/web/.env.production.local` | `scripts/config-backup.sh` copies them to Google Drive |
| The Cloudflare Tunnel login               | `~/.cloudflared/` (`cert.pem`, `<tunnel id>.json`, `algoearning.yml`)              | included in `scripts/config-backup.sh`                 |

`APP_ENCRYPTION_KEY` must be the same on the new computer. Without it, the stored broker credentials and the daily
market-data sessions in the database cannot be opened, and every user has to enter their broker keys again.

## Keep both backups current

Nothing runs on a schedule. After changing a key or env file run `scripts/config-backup.sh push`; after a big
history download (or any day you care about) run `backup.py push full`. Both are described below. If this computer
dies, you get back only what was last backed up.

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
`backups/full-YYYYMMDD-HHMM.dump`. It only reads the database, so it is safe while the app runs. A full dump covers every table, so a new table is in it without any change here. It has
user emails and encrypted broker credentials in it, so keep the bucket private. Delete old backups from the R2
dashboard when you no longer need them.

### Restore a backup

```bash
uv run --env-file .env --with boto3 python scripts/backup.py pull latest-full   # or latest-history, or a name from list
scripts/home-host.sh stop api web feed worker engine                            # or stop make dev (Ctrl-C)
uv run --env-file .env python scripts/backup.py restore .data/backups/full-20261003-1130.dump
```

`restore` asks you to type the database name first. A **full** dump replaces every table. A **history** dump
replaces only `history_candles`. The database must already have the tables and roles from `make migrate` (step 4
below). Start the services again afterwards (`scripts/home-host.sh start`, or `make dev`).

## Secrets backup to Google Drive (manual)

The env files and `~/.cloudflared/` cannot go to GitHub, so this command copies them as they are into your Google
Drive folder, `My Drive/AlgoEarning/config/`: the same paths as in the repo, and `~/.cloudflared` as `cloudflared/`.
Run it again whenever you change any of them; Google Drive keeps the older versions of each file.

One-time setup: install Google Drive for desktop and sign in with your Google account. The Drive folder then
appears in Finder, and the script finds it by itself.

```bash
brew install --cask google-drive
```

```bash
scripts/config-backup.sh push       # copy the files into My Drive/AlgoEarning/config/
scripts/config-backup.sh list       # what the Drive copy holds
scripts/config-backup.sh restore    # copy them back; a file that differs is kept as <name>.bak-<date>
```

These are plain copies: anyone who can open your Google Drive can read every key in them, so keep two-step
verification on for your Google account.

## Set up a new computer from scratch

Written for a Mac where the old computer is **gone or unreachable**: nothing below needs it. Everything comes from
three places: GitHub (code), Google Drive (the env files and tunnel login), Cloudflare R2 (the database). Do steps
1 to 6 for development; add step 7 to run production there.

Before you start, make sure you can sign in to GitHub, your Google account (the Drive backup), and the Cloudflare
dashboard (the R2 backup).

### 1. Install the tools

```bash
xcode-select --install                     # git and compilers (skip if already installed)
# Homebrew, from https://brew.sh:
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install node@22 pnpm uv postgresql@17 redis cloudflared
brew install --cask google-drive           # then open it and sign in, for the secrets backup
```

### 2. Clone the repo

```bash
mkdir -p ~/git && cd ~/git
git clone https://github.com/rohitkr/algoearning.git algoearning-github
cd algoearning-github
```

### 3. Restore the env files from Google Drive, and install the dependencies

Open Google Drive for desktop, sign in, and wait until `My Drive/AlgoEarning/config/` has synced (open it in Finder
to check; it must hold `.env`). Then, from the repo:

```bash
scripts/config-backup.sh list              # shows what the Drive copy holds
scripts/config-backup.sh restore           # .env, .env.production, apps/web/.env.local, apps/web/.env.production.local
                                           # into the repo, and cloudflared/ into ~/.cloudflared
make setup                                 # pnpm install + uv sync (all JS and Python packages)
```

No Google Drive for desktop on this computer? Open https://drive.google.com in a browser, download the folder
`AlgoEarning/config`, unzip it, and copy the files to the same paths in the repo by hand (`.env` to the repo root,
`apps/web/.env.local` to `apps/web/`, and so on); copy `cloudflared/*` into `~/.cloudflared/`. Or point the script
at the unzipped folder: `DRIVE_DIR=/path/to/config scripts/config-backup.sh restore`.

Check that `.env` has `APP_ENCRYPTION_KEY` (the same one as before, see the top of this page) and the four `R2_*`
values, which step 5 needs.

If the Drive backup is missing (it was never taken), `make dev` creates `.env` and `apps/web/.env.local` from the
examples and you fill in the keys by hand. Broker credentials and market-data sessions stored in the database
cannot be opened without the old `APP_ENCRYPTION_KEY`: users enter their broker keys again, and you log in to
Breeze or Kite again in Monitor > Market data. Clerk, Razorpay and Breeze keys can be re-created from their dashboards.

### 4. Start the database and create the tables

```bash
make db-up                                 # Postgres + Redis, data in .data/ (creates algoearning + algoearning_test)
make migrate                               # every table, plus the ae_app/ae_system roles
```

### 5. Restore the database from Cloudflare R2

This needs the `R2_*` values in `.env` (restored in step 3). If they are missing, create a new token: Cloudflare
dashboard > R2 Object Storage > **Manage API tokens** > **Create API token** (Object Read & Write, bucket
`algoearning-backups`), and put the Account ID, Access Key ID and Secret Access Key into `.env`. The bucket and its
backups are still there; only the token is new.

```bash
uv run --env-file .env --with boto3 python scripts/backup.py list                # the backups in the bucket
uv run --env-file .env --with boto3 python scripts/backup.py pull latest-full    # into .data/backups/
uv run --env-file .env python scripts/backup.py restore .data/backups/full-<date>.dump
uv run --env-file .env python scripts/show-history.py                            # check: the history is there
```

The full dump has every table: users, strategies, plans, broker accounts (encrypted), the historical candles, the
market holidays and the data-sync job history. Only `latest-history` exists if you never took a full backup: restore
that after creating users some other way, it replaces just `history_candles`.

Anything newer than the last backup is not in it. Open Monitor > Data sync to see which days are missing and
download them (needs the day's Breeze or Kite login in Monitor > Market data). Market holidays come from the repo
(`packages/py-marketdata/src/ae_marketdata/holidays.toml`), so they need no restore.

### 6. Run it (development: API + frontend)

```bash
make dev                                   # API on http://localhost:8000 (docs at /docs), web on http://localhost:3000
make check                                 # in another terminal: lint, typecheck, tests
```

`make dev` runs the API and the Next.js frontend together. The frontend reads `apps/web/.env.local` (Clerk keys,
`NEXT_PUBLIC_API_URL`). Open http://localhost:3000 and sign in.

### 7. Run production on it (home hosting)

If the old computer might still be running, it must be off: two computers would both answer on the tunnel, and two
engines would trade the same accounts.

1. Stop `make dev` (Ctrl-C), then:

   ```bash
   scripts/home-host.sh tunnel             # finds the existing tunnel, rewrites ~/.cloudflared/algoearning.yml
   scripts/home-host.sh build              # production build of the frontend (about a minute)
   scripts/home-host.sh install            # every service: now and at each login
   scripts/home-host.sh status             # health, and this computer's public IP
   ```

   Without the restored `~/.cloudflared/` folder, run `cloudflared tunnel login` first and see
   [home-hosting.md](home-hosting.md), step 2.

2. If the public IP changed, update it in each Zerodha Kite app (developers.kite.trade > your app > IP list).
   The redirect URLs stay the same, because the domain does not change.
3. Open https://app.algoearning.com, sign in, and do the day's market-data login in Monitor > Market data.
4. The Mac settings that keep it awake: [home-hosting.md](home-hosting.md), step 5.
