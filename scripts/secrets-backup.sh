#!/usr/bin/env bash
# Back up the files that are not in git (the env files and the Cloudflare Tunnel login) to Google Drive, as plain
# copies you can open there. Run it by hand after changing any of them; nothing runs on a schedule.
#
#   scripts/secrets-backup.sh push                     # copy the files into Google Drive
#   scripts/secrets-backup.sh restore                  # copy them back from Google Drive (e.g. on a new computer)
#   scripts/secrets-backup.sh list                     # what the Drive copy holds
#
# Google Drive: install "Google Drive for desktop" (brew install --cask google-drive) and sign in once. The files go
# to My Drive/AlgoEarning/config/, at the same paths as in the repo (~/.cloudflared as cloudflared/), and Drive
# uploads them by itself; Drive keeps older versions of each file. DRIVE_DIR=/some/folder overrides the folder.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
REPO_FILES=(.env .env.production apps/web/.env.local apps/web/.env.production.local)
HOME_DIR=.cloudflared # ~/.cloudflared: cert.pem, <tunnel id>.json, algoearning.yml

red=$'\033[31m'; green=$'\033[32m'; yellow=$'\033[33m'; reset=$'\033[0m'
ok() { echo "  ${green}✓${reset} $*"; }
warn() { echo "  ${yellow}!${reset} $*"; }
die() { echo "  ${red}✗ $*${reset}" >&2; exit 1; }

drive_dir() {
  if [ -n "${DRIVE_DIR:-}" ]; then echo "$DRIVE_DIR"; return; fi
  local d
  for d in "$HOME"/Library/CloudStorage/GoogleDrive-*/"My Drive"; do
    [ -d "$d" ] && { echo "$d/AlgoEarning/config"; return; }
  done
  die "Google Drive folder not found: install Google Drive for desktop (brew install --cask google-drive), sign in, or set DRIVE_DIR"
}

push() {
  local dest n=0 f
  dest=$(drive_dir)
  mkdir -p "$dest"
  for f in "${REPO_FILES[@]}"; do
    if [ -f "$f" ]; then
      mkdir -p "$dest/$(dirname "$f")" && cp -p "$f" "$dest/$f" && ok "$f" && n=$((n + 1))
    else
      warn "$f not found (skipped)"
    fi
  done
  if [ -d "$HOME/$HOME_DIR" ]; then
    mkdir -p "$dest/cloudflared" && cp -Rp "$HOME/$HOME_DIR/." "$dest/cloudflared/" && ok "~/$HOME_DIR"
  else
    warn "~/$HOME_DIR not found (skipped)"
  fi
  [ "$n" -gt 0 ] || die "no env files found in $ROOT"
  ok "copied to $dest; Google Drive uploads them in the background"
}

restore() {
  local src stamp f
  src=$(drive_dir)
  [ -d "$src" ] || die "$src not found: nothing backed up yet, or Google Drive is not signed in"
  stamp=$(date +%Y%m%d-%H%M%S)
  for f in "${REPO_FILES[@]}"; do
    [ -f "$src/$f" ] || continue
    if [ -f "$f" ] && ! cmp -s "$f" "$src/$f"; then
      cp -p "$f" "$f.bak-$stamp" && warn "$f differed: the old one is now $f.bak-$stamp"
    fi
    mkdir -p "$(dirname "$f")" && cp -p "$src/$f" "$f" && chmod 600 "$f" && ok "$f"
  done
  if [ -d "$src/cloudflared" ]; then
    if [ -d "$HOME/$HOME_DIR" ] && ! diff -rq "$src/cloudflared" "$HOME/$HOME_DIR" >/dev/null 2>&1; then
      cp -Rp "$HOME/$HOME_DIR" "$HOME/$HOME_DIR.bak-$stamp" && warn "old ~/$HOME_DIR kept as ~/$HOME_DIR.bak-$stamp"
    fi
    mkdir -p "$HOME/$HOME_DIR" && cp -Rp "$src/cloudflared/." "$HOME/$HOME_DIR/" && chmod 700 "$HOME/$HOME_DIR" &&
      ok "~/$HOME_DIR"
  fi
}

list() {
  local src
  src=$(drive_dir)
  [ -d "$src" ] || die "$src not found: nothing backed up yet"
  echo "  $src"
  (cd "$src" && find . -type f ! -name '.DS_Store' | sed -e 's#^\./cloudflared/#  ~/.cloudflared/#' -e 's#^\./#  #' | sort)
}

case "${1:-}" in
  push) push ;;
  restore) restore ;;
  list) list ;;
  *) sed -n '2,11p' "$0"; exit 2 ;;
esac
