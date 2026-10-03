#!/usr/bin/env bash
# Back up the files that are not in git (the env files and the Cloudflare Tunnel login) to Google Drive, encrypted
# with a passphrase you type. Run it by hand after changing any of them; nothing runs on a schedule.
#
#   scripts/secrets-backup.sh push                     # encrypt + save into Google Drive (asks for the passphrase twice)
#   scripts/secrets-backup.sh restore [file]           # decrypt + put the files back (default: the copy in Google Drive)
#   scripts/secrets-backup.sh list                     # what a backup contains (asks for the passphrase)
#
# Google Drive: install "Google Drive for desktop" (brew install --cask google-drive) and sign in once. The file goes to
# My Drive/AlgoEarning/algoearning-secrets.tar.gz.enc and Drive uploads it by itself; Drive keeps older versions.
# DRIVE_DIR=/some/folder overrides the folder. Encryption: AES-256 (openssl, PBKDF2). Keep the passphrase in your
# password manager: without it the file cannot be opened, by anyone, including you.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
NAME=algoearning-secrets.tar.gz.enc
REPO_FILES=(.env .env.production apps/web/.env.local apps/web/.env.production.local)
HOME_DIR=.cloudflared # ~/.cloudflared: cert.pem, <tunnel id>.json, algoearning.yml
CIPHER=(-aes-256-cbc -pbkdf2 -iter 600000 -md sha256)
[ -z "${SECRETS_PASSPHRASE:-}" ] || CIPHER+=(-pass env:SECRETS_PASSPHRASE) # for scripts/tests; normally it asks
OPENSSL=openssl # prefer Homebrew's OpenSSL 3 over macOS's LibreSSL when it is installed
if command -v brew >/dev/null && [ -x "$(brew --prefix openssl@3 2>/dev/null)/bin/openssl" ]; then
  OPENSSL="$(brew --prefix openssl@3)/bin/openssl"
fi

bold=$'\033[1m'; red=$'\033[31m'; green=$'\033[32m'; yellow=$'\033[33m'; reset=$'\033[0m'
WORK=$(mktemp -d) # decrypted copies only ever live here, and it is removed on exit
trap 'rm -rf "$WORK"' EXIT

ok() { echo "  ${green}✓${reset} $*"; }
warn() { echo "  ${yellow}!${reset} $*"; }
die() { echo "  ${red}✗ $*${reset}" >&2; exit 1; }

drive_dir() {
  if [ -n "${DRIVE_DIR:-}" ]; then echo "$DRIVE_DIR"; return; fi
  local d
  for d in "$HOME"/Library/CloudStorage/GoogleDrive-*/"My Drive"; do
    [ -d "$d" ] && { echo "$d/AlgoEarning"; return; }
  done
  die "Google Drive folder not found: install Google Drive for desktop (brew install --cask google-drive), sign in, or set DRIVE_DIR"
}

push() {
  local dest stage="$WORK" n=0
  dest=$(drive_dir)
  mkdir -p "$stage/repo" "$stage/home"
  for f in "${REPO_FILES[@]}"; do
    if [ -f "$f" ]; then
      mkdir -p "$stage/repo/$(dirname "$f")" && cp -p "$f" "$stage/repo/$f" && ok "$f" && n=$((n + 1))
    else
      warn "$f not found (skipped)"
    fi
  done
  if [ -d "$HOME/$HOME_DIR" ]; then
    cp -Rp "$HOME/$HOME_DIR" "$stage/home/" && ok "~/$HOME_DIR"
  else
    warn "~/$HOME_DIR not found (skipped)"
  fi
  [ "$n" -gt 0 ] || die "no env files found in $ROOT"
  mkdir -p "$dest"
  echo "${bold}Choose a passphrase (typed twice). Keep it in your password manager.${reset}"
  tar -czf - -C "$stage" repo home | "$OPENSSL" enc -e "${CIPHER[@]}" -salt -out "$stage/$NAME" ||
    die "encryption failed: nothing was saved"
  [ -f "$dest/$NAME" ] && cp -p "$dest/$NAME" "$dest/$NAME.previous"
  mv "$stage/$NAME" "$dest/$NAME"
  ok "saved $dest/$NAME ($(du -h "$dest/$NAME" | cut -f1)); Google Drive uploads it in the background"
}

decrypt() { # $1: the encrypted file -> extracted into $2
  "$OPENSSL" enc -d "${CIPHER[@]}" -in "$1" | tar -xzf - -C "$2" || die "wrong passphrase, or not a backup file"
}

restore() {
  local src="${1:-$(drive_dir)/$NAME}" tmp="$WORK" stamp f
  [ -f "$src" ] || die "$src not found"
  decrypt "$src" "$tmp"
  stamp=$(date +%Y%m%d-%H%M%S)
  for f in "${REPO_FILES[@]}"; do
    [ -f "$tmp/repo/$f" ] || continue
    if [ -f "$f" ] && ! cmp -s "$f" "$tmp/repo/$f"; then
      cp -p "$f" "$f.bak-$stamp" && warn "$f differed: the old one is now $f.bak-$stamp"
    fi
    mkdir -p "$(dirname "$f")" && cp -p "$tmp/repo/$f" "$f" && chmod 600 "$f" && ok "$f"
  done
  if [ -d "$tmp/home/$HOME_DIR" ]; then
    if [ -d "$HOME/$HOME_DIR" ]; then
      cp -Rp "$HOME/$HOME_DIR" "$HOME/$HOME_DIR.bak-$stamp" && warn "old ~/$HOME_DIR kept as ~/$HOME_DIR.bak-$stamp"
    fi
    mkdir -p "$HOME/$HOME_DIR" && cp -Rp "$tmp/home/$HOME_DIR/." "$HOME/$HOME_DIR/" && chmod 700 "$HOME/$HOME_DIR" &&
      ok "~/$HOME_DIR"
  fi
}

list() {
  local src="${1:-$(drive_dir)/$NAME}" tmp="$WORK"
  [ -f "$src" ] || die "$src not found"
  decrypt "$src" "$tmp"
  (cd "$tmp" && find repo home -type f | sed -e 's#^repo/#  #' -e 's#^home/#  ~/#')
}

case "${1:-}" in
  push) push ;;
  restore) restore "${2:-}" ;;
  list) list "${2:-}" ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
