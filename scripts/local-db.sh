#!/usr/bin/env bash
# Local Postgres + Redis WITHOUT Docker (Homebrew binaries, data in ./.data, nothing installed as a service).
#   scripts/local-db.sh up|down|status
set -euo pipefail
export LC_ALL="${LC_ALL:-en_US.UTF-8}" LANG="${LANG:-en_US.UTF-8}"   # macOS: postgres refuses to start without a valid locale
cd "$(dirname "$0")/.."
PG_BIN="${PG_BIN:-$(brew --prefix postgresql@17 2>/dev/null)/bin}"
DATA=.data
PGDATA="$DATA/pg"
PGPORT="${PGPORT:-5432}"
REDIS_PORT="${REDIS_PORT:-6379}"
USER_NAME=algoearning
PASSWORD=algoearning

up() {
  mkdir -p "$DATA/redis"
  if [ ! -f "$PGDATA/PG_VERSION" ]; then
    pwfile=$(mktemp)
    echo "$PASSWORD" > "$pwfile"
    "$PG_BIN/initdb" -D "$PGDATA" -U "$USER_NAME" --pwfile="$pwfile" --auth=scram-sha-256 -E UTF8 >/dev/null
    rm -f "$pwfile"
  fi
  if ! "$PG_BIN/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1; then
    "$PG_BIN/pg_ctl" -D "$PGDATA" -l "$DATA/pg.log" -o "-p $PGPORT -k /tmp -c listen_addresses=localhost" -w start >/dev/null
  fi
  for db in algoearning algoearning_test; do
    PGPASSWORD=$PASSWORD "$PG_BIN/psql" -h localhost -p "$PGPORT" -U "$USER_NAME" -d postgres -tAc \
      "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 ||
      PGPASSWORD=$PASSWORD "$PG_BIN/createdb" -h localhost -p "$PGPORT" -U "$USER_NAME" "$db"
  done
  if ! redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1; then
    redis-server --port "$REDIS_PORT" --bind 127.0.0.1 --daemonize yes --dir "$PWD/$DATA/redis" \
      --save "" --appendonly no --logfile "$PWD/$DATA/redis.log" >/dev/null
  fi
  status
}

down() {
  "$PG_BIN/pg_ctl" -D "$PGDATA" -m fast stop >/dev/null 2>&1 || true
  redis-cli -p "$REDIS_PORT" shutdown nosave >/dev/null 2>&1 || true
  echo "stopped"
}

status() {
  if "$PG_BIN/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1; then echo "postgres: up on $PGPORT"; else echo "postgres: down"; fi
  if redis-cli -p "$REDIS_PORT" ping >/dev/null 2>&1; then echo "redis: up on $REDIS_PORT"; else echo "redis: down"; fi
}

"${1:-status}"
