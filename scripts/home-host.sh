#!/usr/bin/env bash
# Run AlgoEarning in production mode on this Mac, reachable from the internet through a Cloudflare Tunnel
# (ADR 0019, step-by-step guide: docs/home-hosting.md). Every service is a launchd agent: it starts at login and is
# restarted when it exits, so the stack survives crashes and reboots.
#
#   scripts/home-host.sh setup              # write .env.production + apps/web/.env.production.local (once)
#   scripts/home-host.sh tunnel             # after `cloudflared tunnel login`: create the tunnel + DNS names (once)
#   scripts/home-host.sh build              # install dependencies, production build of the web app
#   scripts/home-host.sh install            # register and start every service (stop `make dev` first)
#   scripts/home-host.sh deploy             # after a git pull: build, then restart everything but the engine
#   scripts/home-host.sh status             # what runs, local and public health checks, the server IP
#   scripts/home-host.sh start|stop|restart [service]
#   scripts/home-host.sh logs [service]     # follow the logs (all services when none given)
#   scripts/home-host.sh uninstall          # stop and remove every service (back to `make dev`)
#
# Services: postgres redis api web feed worker engine tunnel awake. DOMAIN (default algoearning.com) picks the
# public names: app.<domain> (web), api.<domain> (API), monitor.<domain> (admin panel).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
DOMAIN="${DOMAIN:-algoearning.com}"
TUNNEL_NAME=algoearning
TUNNEL_CONFIG="$HOME/.cloudflared/algoearning.yml"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$ROOT/.data/logs"
PREFIX=com.algoearning
DOMAIN_ID="gui/$(id -u)"
# start order; stop runs it backwards so the database goes last
SERVICES=(postgres redis api web feed worker signals engine tunnel awake)
APP_SERVICES=(api web feed worker signals) # what `deploy` restarts (the engine only on request: it steps live strategies)

bold=$'\033[1m'; red=$'\033[31m'; green=$'\033[32m'; yellow=$'\033[33m'; blue=$'\033[34m'; reset=$'\033[0m'
step() { echo "${bold}${blue}==>${reset} ${bold}$*${reset}"; }
ok() { echo "    ${green}✓${reset} $*"; }
warn() { echo "    ${yellow}!${reset} $*"; }
die() { echo "    ${red}✗ $*${reset}" >&2; exit 1; }

plist() { echo "$AGENTS/$PREFIX.$1.plist"; }
loaded() { launchctl print "$DOMAIN_ID/$PREFIX.$1" >/dev/null 2>&1; }
pid_of() { launchctl print "$DOMAIN_ID/$PREFIX.$1" 2>/dev/null | awk '/^\tpid = / { print $3 }'; }
wanted() { # the tunnel only once it is configured
  [ "$1" != tunnel ] || [ -f "$TUNNEL_CONFIG" ]
}
pick() { # the services named on the command line, or all of them
  if [ $# -gt 0 ]; then
    for s in "$@"; do [[ " ${SERVICES[*]} " == *" $s "* ]] || die "unknown service: $s (${SERVICES[*]})"; done
    echo "$@"
  else
    echo "${SERVICES[@]}"
  fi
}

# -- setup -----------------------------------------------------------------------------------------------
cmd_setup() {
  step "Writing production settings for $DOMAIN"
  [ -f .env ] || die ".env is missing: run make dev once first (it creates it), then add your keys"
  [ -f apps/web/.env.local ] || die "apps/web/.env.local is missing: run make dev once first"
  if [ -f .env.production ]; then
    ok ".env.production already exists (left as is)"
  else
    cat >.env.production <<EOF
# Production overrides for the home-hosted stack (scripts/home-host.sh). Loaded AFTER .env, so the secrets stay in
# .env and only what differs from development lives here. Never commit this file.
APP_ENV=production
DEV_AUTH=false
# the web app's public addresses: CORS and the Clerk session token's allowed origins (azp)
WEB_ORIGIN=https://app.$DOMAIN,https://monitor.$DOMAIN
# how browsers and Zerodha reach the API: Kite apps redirect to <API_PUBLIC_URL>/v1/brokers/zerodha/callback
API_PUBLIC_URL=https://api.$DOMAIN
API_URL=http://127.0.0.1:8000
EOF
    chmod 600 .env.production
    ok "created .env.production"
  fi
  if [ -f apps/web/.env.production.local ]; then
    ok "apps/web/.env.production.local already exists (left as is)"
  else
    cat >apps/web/.env.production.local <<EOF
# Production overrides for the web app (scripts/home-host.sh). \`next build\` bakes NEXT_PUBLIC_* in, so rebuild
# (scripts/home-host.sh deploy) after changing them. The Clerk keys come from .env.local. Never commit this file.
NEXT_PUBLIC_API_URL=https://api.$DOMAIN
API_URL=http://127.0.0.1:8000
# Master password for the whole site (the browser asks for it, any username). Unset = the site is open to anyone.
APP_GATE_PASSWORD=
EOF
    chmod 600 apps/web/.env.production.local
    ok "created apps/web/.env.production.local"
  fi
  grep -Eq '^DEV_AUTH=true' .env.production && die "DEV_AUTH must be false in production"
  ok "next: cloudflared tunnel login, then scripts/home-host.sh tunnel (docs/home-hosting.md)"
}

# -- tunnel ----------------------------------------------------------------------------------------------
cmd_tunnel() {
  step "Creating the Cloudflare Tunnel '$TUNNEL_NAME' for $DOMAIN"
  command -v cloudflared >/dev/null || die "cloudflared is not installed: brew install cloudflared"
  [ -f "$HOME/.cloudflared/cert.pem" ] || die "not logged in: run cloudflared tunnel login (pick $DOMAIN) first"
  local id
  id=$(cloudflared tunnel list --output json 2>/dev/null |
    python3 -c "import json,sys; print(next((t['id'] for t in (json.load(sys.stdin) or []) if t['name']=='$TUNNEL_NAME'), ''))")
  if [ -z "$id" ]; then
    cloudflared tunnel create "$TUNNEL_NAME" >/dev/null
    id=$(cloudflared tunnel list --output json |
      python3 -c "import json,sys; print(next(t['id'] for t in (json.load(sys.stdin) or []) if t['name']=='$TUNNEL_NAME'))")
    ok "created tunnel $id"
  else
    ok "tunnel exists: $id"
  fi
  [ -f "$HOME/.cloudflared/$id.json" ] || die "credentials ~/.cloudflared/$id.json are missing (tunnel made elsewhere?)"
  cat >"$TUNNEL_CONFIG" <<EOF
# AlgoEarning home hosting (written by scripts/home-host.sh tunnel). Only these names reach this Mac; the apex
# $DOMAIN and www stay wherever their own DNS records point.
tunnel: $id
credentials-file: $HOME/.cloudflared/$id.json
ingress:
  - hostname: app.$DOMAIN
    service: http://localhost:3000
  - hostname: monitor.$DOMAIN
    service: http://localhost:3000
  - hostname: api.$DOMAIN
    service: http://127.0.0.1:8000
  - service: http_status:404
EOF
  ok "wrote $TUNNEL_CONFIG"
  for host in app api monitor; do
    # creates a proxied CNAME <host> -> <id>.cfargotunnel.com; never touches other records
    if cloudflared tunnel route dns "$TUNNEL_NAME" "$host.$DOMAIN" >/dev/null 2>&1; then
      ok "DNS: $host.$DOMAIN -> tunnel"
    else
      warn "could not add DNS for $host.$DOMAIN (a record with that name already exists?): see the Cloudflare DNS page"
    fi
  done
  cloudflared tunnel ingress validate --config "$TUNNEL_CONFIG" >/dev/null && ok "config is valid"
}

# -- build -----------------------------------------------------------------------------------------------
cmd_build() {
  step "Installing dependencies"
  pnpm install --frozen-lockfile --silent
  uv sync --all-packages --frozen --quiet
  ok "done"
  step "Building the web app for production (NEXT_PUBLIC_API_URL from apps/web/.env.production.local)"
  [ -f apps/web/.env.production.local ] || die "run scripts/home-host.sh setup first"
  grep -Eq '^APP_GATE_PASSWORD=.+' apps/web/.env.production.local ||
    warn "APP_GATE_PASSWORD is not set in apps/web/.env.production.local: the site will be open to anyone"
  pnpm --filter @algoearning/web build
  # the standalone server serves its own copy of the static files (as in infra/docker/web.Dockerfile)
  rm -rf apps/web/.next/standalone/apps/web/.next/static apps/web/.next/standalone/apps/web/public
  cp -R apps/web/.next/static apps/web/.next/standalone/apps/web/.next/static
  cp -R apps/web/public apps/web/.next/standalone/apps/web/public
  ok "built apps/web/.next/standalone"
}

# -- launchd ---------------------------------------------------------------------------------------------
write_plist() { # service
  local svc=$1 path_dirs
  # launchd starts with a bare PATH: give it the directories of the tools this setup was installed with
  path_dirs=$(for t in uv pnpm node brew redis-server cloudflared; do
    p=$(command -v "$t" 2>/dev/null) && dirname "$p"
  done | awk '!seen[$0]++' | paste -sd: -)
  cat >"$(plist "$svc")" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$PREFIX.$svc</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$ROOT/scripts/home-service.sh</string><string>$svc</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$path_dirs:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>HOME</key><string>$HOME</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ExitTimeOut</key><integer>30</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$LOGS/$svc.log</string>
  <key>StandardErrorPath</key><string>$LOGS/$svc.log</string>
</dict>
</plist>
EOF
}

start_one() {
  local svc=$1
  wanted "$svc" || { warn "$svc: skipped (no tunnel yet: scripts/home-host.sh tunnel)"; return 0; }
  [ -f "$(plist "$svc")" ] || write_plist "$svc"
  if loaded "$svc"; then
    launchctl kickstart "$DOMAIN_ID/$PREFIX.$svc" >/dev/null 2>&1 || true
  else
    launchctl bootstrap "$DOMAIN_ID" "$(plist "$svc")"
  fi
  ok "$svc started"
}

stop_one() {
  local svc=$1
  loaded "$svc" || return 0
  launchctl bootout "$DOMAIN_ID/$PREFIX.$svc" 2>/dev/null || true
  ok "$svc stopped"
}

reverse() { local i a=("$@"); for ((i = ${#a[@]} - 1; i >= 0; i--)); do echo "${a[i]}"; done; }

cmd_install() {
  step "Checking that the development stack is stopped"
  [ -f .env.production ] || die "run scripts/home-host.sh setup first"
  [ -f apps/web/.next/standalone/apps/web/server.js ] || die "run scripts/home-host.sh build first"
  for port in 3000 8000; do
    if lsof -nP -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 && ! loaded api; then
      die "port $port is in use (make dev still running?). Stop it with Ctrl-C first."
    fi
  done
  if pgrep -f "python -m ae_engine" >/dev/null && ! loaded engine; then
    die "an engine is already running outside launchd (make dev?): stop it first, one stack at a time."
  fi
  # Postgres/Redis left running by `make dev` (pg_ctl / redis --daemonize) would hold the ports: hand them over
  if ! loaded postgres; then scripts/local-db.sh down >/dev/null; fi
  ok "free"
  step "Installing services (logs in .data/logs)"
  mkdir -p "$AGENTS" "$LOGS"
  local s
  for s in "${SERVICES[@]}"; do
    wanted "$s" || continue
    write_plist "$s"
  done
  for s in "${SERVICES[@]}"; do start_one "$s"; done
  echo
  echo "    They start again at every login. Check with: scripts/home-host.sh status"
}

cmd_uninstall() {
  step "Removing services"
  local s
  for s in $(reverse "${SERVICES[@]}"); do
    stop_one "$s"
    rm -f "$(plist "$s")"
  done
  ok "removed: make dev works again"
}

cmd_start() { local s; for s in $(pick "$@"); do start_one "$s"; done; }
cmd_stop() { local s; for s in $(reverse $(pick "$@")); do stop_one "$s"; done; }
cmd_restart() {
  local s
  for s in $(pick "$@"); do
    if loaded "$s"; then
      launchctl kickstart -k "$DOMAIN_ID/$PREFIX.$s" >/dev/null && ok "$s restarted"
    else
      start_one "$s"
    fi
  done
}

cmd_deploy() {
  cmd_build
  step "Restarting ${APP_SERVICES[*]}"
  cmd_restart "${APP_SERVICES[@]}"
  warn "the engine was not restarted (it steps running strategies). For engine changes, outside market hours:"
  echo "      scripts/home-host.sh restart engine"
}

cmd_status() {
  step "Services"
  local s pid
  for s in "${SERVICES[@]}"; do
    if ! loaded "$s"; then
      printf "    %-9s %s\n" "$s" "${yellow}not installed${reset}"
    elif pid=$(pid_of "$s") && [ -n "$pid" ]; then
      printf "    %-9s %s\n" "$s" "${green}running${reset} (pid $pid)"
    else
      printf "    %-9s %s\n" "$s" "${red}not running${reset}: scripts/home-host.sh logs $s"
    fi
  done
  step "Health"
  check() { # label url
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$2" || true)
    if [[ "$code" =~ ^[23] ]]; then ok "$1 $2 ($code)"; else warn "$1 $2 (${code:-no answer})"; fi
  }
  check "api, local " "http://127.0.0.1:8000/health"
  check "web, local " "http://localhost:3000/"
  check "api, public" "https://api.$DOMAIN/health"
  check "web, public" "https://app.$DOMAIN/"
  step "Server IP (what each Kite app must list)"
  echo "    $(curl -s -4 --max-time 5 https://1.1.1.1/cdn-cgi/trace | sed -n 's/^ip=//p')"
}

cmd_logs() {
  mkdir -p "$LOGS"
  local files=() s
  for s in $(pick "$@"); do files+=("$LOGS/$s.log"); touch "$LOGS/$s.log"; done
  exec tail -n 50 -F "${files[@]}"
}

cmd="${1:-help}"
shift || true
case "$cmd" in
  setup | tunnel | build | install | uninstall | start | stop | restart | deploy | status | logs) "cmd_$cmd" "$@" ;;
  -h | --help | help) sed -n '2,17p' "$0" ;;
  *) die "unknown command: $cmd (see scripts/home-host.sh help)" ;;
esac
