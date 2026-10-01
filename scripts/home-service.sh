#!/usr/bin/env bash
# One production service, run by launchd (installed by scripts/home-host.sh; ADR 0019). launchd restarts it when it
# exits, so every branch ends in `exec` and never daemonizes. For development use `make dev` instead.
#
#   scripts/home-service.sh postgres|redis|api|web|feed|worker|engine|tunnel|awake
set -euo pipefail
cd "$(dirname "$0")/.."
export LC_ALL="${LC_ALL:-en_US.UTF-8}" LANG="${LANG:-en_US.UTF-8}" # postgres refuses to start without a locale

API_PORT=8000
WEB_PORT=3000
PG_BIN="${PG_BIN:-$(brew --prefix postgresql@17)/bin}"
# .env holds the secrets shared with development; .env.production overrides the public URLs and APP_ENV
ENV_FILES=(--env-file .env --env-file .env.production)

wait_for_db() { # the database and cache start as their own services; wait for both (launchd has no ordering)
  until "$PG_BIN/pg_isready" -q -h localhost -p 5432 && redis-cli -p 6379 ping >/dev/null 2>&1; do
    echo "waiting for postgres and redis..."
    sleep 3
  done
}

case "${1:-}" in
  postgres)
    exec "$PG_BIN/postgres" -D .data/pg -p 5432 -k /tmp -c listen_addresses=localhost ;;
  redis)
    mkdir -p .data/redis
    exec redis-server --port 6379 --bind 127.0.0.1 --dir "$PWD/.data/redis" --save "" --appendonly no ;;
  api)
    wait_for_db
    uv run "${ENV_FILES[@]}" alembic -c packages/py-db/alembic.ini upgrade head
    exec uv run "${ENV_FILES[@]}" uvicorn ae_api.main:app --host 127.0.0.1 --port "$API_PORT" \
      --proxy-headers --forwarded-allow-ips 127.0.0.1 ;;
  web)
    # the standalone server built by `scripts/home-host.sh build`; env: the dev file, then the production overrides
    exec env NODE_ENV=production PORT="$WEB_PORT" HOSTNAME=localhost \
      node --env-file=apps/web/.env.local --env-file=apps/web/.env.production.local \
      apps/web/.next/standalone/apps/web/server.js ;;
  feed)
    wait_for_db
    exec uv run "${ENV_FILES[@]}" python -m ae_marketdata ;;
  worker)
    wait_for_db
    exec uv run "${ENV_FILES[@]}" python -m ae_worker ;;
  engine)
    wait_for_db
    exec uv run "${ENV_FILES[@]}" python -m ae_engine ;;
  tunnel)
    exec cloudflared tunnel --no-autoupdate --config "$HOME/.cloudflared/algoearning.yml" run ;;
  awake)
    exec caffeinate -i -s ;; # no idle or system sleep while this runs (the lid may still be closed on power)
  *)
    echo "usage: $0 postgres|redis|api|web|feed|worker|engine|tunnel|awake" >&2
    exit 2 ;;
esac
