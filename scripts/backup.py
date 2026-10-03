"""Manual database backups to Cloudflare R2, and restoring them (docs/setup.md). Nothing runs this on a schedule.

    uv run --env-file .env --with boto3 python scripts/backup.py push history     # history_candles only
    uv run --env-file .env --with boto3 python scripts/backup.py push full        # the whole database
    uv run --env-file .env --with boto3 python scripts/backup.py list             # what is in the bucket
    uv run --env-file .env --with boto3 python scripts/backup.py pull latest-full # or latest-history, or a name
    uv run --env-file .env --with boto3 python scripts/backup.py restore .data/backups/full-20261003-1130.dump

push: pg_dump (custom format, compressed) into .data/backups/, then upload to the bucket under backups/.
restore: pg_restore into DATABASE_URL, replacing what the dump holds: a history dump replaces only the
history_candles table, a full dump replaces every table. Run `make migrate` on an empty database first (it creates
the ae_app and ae_system roles the dump's grants refer to), and stop the app's services while restoring.

Environment (.env): DATABASE_URL, and for push/list/pull R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
R2_BUCKET (an R2 API token with Object Read & Write on that bucket). Encrypted secrets inside a full dump (broker
credentials, the daily feed sessions) only open with the same APP_ENCRYPTION_KEY, which is never uploaded."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / ".data" / "backups"
PREFIX = "backups/"
KINDS = ("history", "full")


def die(msg: str) -> None:
    sys.exit(f"error: {msg}")


def pg_tool(name: str) -> str:
    """pg_dump/pg_restore from Homebrew's postgresql@17 (matching the server), else whatever is on PATH."""
    if pg_bin := os.environ.get("PG_BIN"):
        return str(Path(pg_bin) / name)
    brew = shutil.which("brew")
    if brew:
        found = subprocess.run([brew, "--prefix", "postgresql@17"], capture_output=True, text=True)  # noqa: S603
        prefix = found.stdout.strip()
        if prefix and (Path(prefix) / "bin" / name).exists():
            return str(Path(prefix) / "bin" / name)
    found = shutil.which(name)
    if not found:
        die(f"{name} not found: brew install postgresql@17")
    return found  # type: ignore[return-value]


def db_url() -> str:
    url = os.environ.get("DATABASE_URL") or die("DATABASE_URL is not set: run with --env-file .env")
    return str(url).replace("postgresql+psycopg://", "postgresql://")


def db_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/") or "?"


def bucket():  # type: ignore[no-untyped-def]
    try:
        import boto3
    except ImportError:
        die("boto3 is missing: run with uv run --with boto3 ...")
    need = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
    missing = [k for k in need if not os.environ.get(k)]
    if missing:
        die(f"set {', '.join(missing)} in .env (docs/setup.md, one-time setup)")
    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    return s3, os.environ["R2_BUCKET"]


def size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def remote(s3, name: str) -> list[dict]:  # type: ignore[no-untyped-def,type-arg]
    items = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=name, Prefix=PREFIX):
        items += page.get("Contents", [])
    return sorted(items, key=lambda o: o["Key"])


def push(kind: str) -> None:
    url = db_url()
    LOCAL.mkdir(parents=True, exist_ok=True)
    path = LOCAL / f"{kind}-{datetime.now():%Y%m%d-%H%M}.dump"
    cmd = [pg_tool("pg_dump"), "--format=custom", "--compress=9", "--no-owner", f"--file={path}", f"--dbname={url}"]
    if kind == "history":
        cmd.insert(1, "--table=history_candles")
    print(f"dumping {kind} of database {db_name(url)} ...")
    subprocess.run(cmd, check=True)  # noqa: S603 (our own argv, no shell)
    print(f"  {path.relative_to(ROOT)}  {size(path.stat().st_size)}")
    s3, name = bucket()
    print(f"uploading to r2://{name}/{PREFIX}{path.name} ...")
    s3.upload_file(str(path), name, PREFIX + path.name)
    print("done")


def list_() -> None:
    s3, name = bucket()
    items = remote(s3, name)
    if not items:
        print(f"r2://{name}/{PREFIX} is empty")
    for o in items:
        print(f"{o['Key'].removeprefix(PREFIX):<36}{size(o['Size']):>10}   {o['LastModified']:%Y-%m-%d %H:%M} UTC")


def pull(which: str) -> None:
    s3, name = bucket()
    keys = [o["Key"] for o in remote(s3, name)]
    if which.startswith("latest-"):
        kind = which.removeprefix("latest-")
        matching = [k for k in keys if k.removeprefix(PREFIX).startswith(f"{kind}-")]
        if kind not in KINDS or not matching:
            die(f"no {kind} backup in r2://{name}/{PREFIX}")
        key = matching[-1]  # names sort by time
    else:
        key = PREFIX + which.removeprefix(PREFIX)
        if key not in keys:
            die(f"{which} is not in the bucket: see `list`")
    LOCAL.mkdir(parents=True, exist_ok=True)
    path = LOCAL / key.removeprefix(PREFIX)
    print(f"downloading r2://{name}/{key} ...")
    s3.download_file(name, key, str(path))
    print(f"  {path.relative_to(ROOT)}  {size(path.stat().st_size)}")
    print(f"next: uv run --env-file .env python scripts/backup.py restore {path.relative_to(ROOT)}")


def restore(file: str, yes: bool) -> None:
    path = Path(file)
    if not path.exists():
        die(f"{file} not found")
    url = db_url()
    what = "the history_candles table" if path.name.startswith("history-") else "every table in the dump"
    print(f"This replaces {what} in database '{db_name(url)}' ({urlsplit(url).hostname}) with {path.name}.")
    print("Stop the app's services first (make dev, or scripts/home-host.sh stop api web feed worker engine).")
    if not yes and input(f"Type the database name ({db_name(url)}) to go on: ").strip() != db_name(url):
        die("cancelled")
    cmd = [pg_tool("pg_restore"), "--clean", "--if-exists", "--no-owner", "--single-transaction", f"--dbname={url}"]
    subprocess.run([*cmd, str(path)], check=True)  # noqa: S603 (our own argv, no shell)
    print("restored")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("push", help="dump and upload").add_argument("kind", choices=KINDS)
    sub.add_parser("list", help="the backups in the bucket")
    sub.add_parser("pull", help="download one").add_argument("which", help="latest-full, latest-history or a name")
    r = sub.add_parser("restore", help="restore a downloaded dump into DATABASE_URL")
    r.add_argument("file")
    r.add_argument("--yes", action="store_true", help="skip the confirmation")
    a = ap.parse_args()
    if a.cmd == "push":
        push(a.kind)
    elif a.cmd == "list":
        list_()
    elif a.cmd == "pull":
        pull(a.which)
    else:
        restore(a.file, a.yes)


if __name__ == "__main__":
    main()
