"""Operator commands (run on the server, never exposed over HTTP):

uv run --env-file .env python -m ae_api.cli promote you@example.com   # make an existing user an admin
uv run --env-file .env python -m ae_api.cli demote you@example.com
uv run --env-file .env python -m ae_api.cli users                     # list users
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import timedelta

from ae_db.enums import BillingOrderStatus, UserRole
from ae_db.models import BillingOrder, User
from ae_db.session import Database
from sqlalchemy import select

from .settings import Settings


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    if not settings.database_url:
        print("DATABASE_URL is not set")
        return 2
    db = Database(settings.database_url, pool_size=1)
    try:
        async with db.system_session() as s:
            if args.cmd == "reconcile-payments":
                return await _reconcile(settings, db)
            if args.cmd == "users":
                for u in (await s.execute(select(User).order_by(User.created_at))).scalars():
                    print(f"{u.email:40} {u.role.value:6} {u.status.value:10} {u.created_at:%Y-%m-%d}")
                return 0
            user = (await s.execute(select(User).where(User.email == args.email.strip().lower()))).scalar_one_or_none()
            if user is None:
                print(f"no user {args.email!r} (they must sign in once first)")
                return 1
            user.role = UserRole.ADMIN if args.cmd == "promote" else UserRole.USER
            print(f"{user.email} is now {user.role.value}")
            return 0
    finally:
        await db.dispose()


async def _reconcile(settings: Settings, db: Database) -> int:
    """Checkouts whose confirmation never reached us (tab closed, webhook not deliverable, e.g. in local dev):
    ask Razorpay for each unpaid order's payments and settle captured ones through the normal, idempotent path."""
    from .billing.razorpay import RazorpayClient
    from .billing.service import settle

    if not (settings.razorpay_key_id and settings.razorpay_key_secret):
        print("Razorpay keys are not set")
        return 2
    client = RazorpayClient(settings.razorpay_key_id, settings.razorpay_key_secret)
    grace = timedelta(days=settings.subscription_grace_days)
    async with db.system_session() as s:
        orders = [
            o.provider_order_id
            for o in (
                await s.execute(select(BillingOrder).where(BillingOrder.status == BillingOrderStatus.CREATED))
            ).scalars()
        ]
    applied = 0
    for oid in orders:
        for pay in await client.order_payments(oid):
            if pay.get("status") in ("captured", "authorized"):
                async with db.system_session() as s:
                    result = await settle(s, client, oid, str(pay["id"]), grace)
                print(f"{oid} {pay['id']}: {result.outcome} {result.reason}".rstrip())
                applied += result.outcome == "paid"
                break
    print(f"checked {len(orders)} unpaid orders, applied {applied}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ae_api.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("promote", "demote"):
        sub.add_parser(name).add_argument("email")
    sub.add_parser("users")
    sub.add_parser("reconcile-payments")
    return asyncio.run(_run(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
