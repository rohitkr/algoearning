"""Request dependencies: database, the signed-in user, and a transaction scoped to that user.

current_user asks each configured Authenticator (auth/) in turn; the first that recognises the request's
credentials wins. The identity is mapped onto our users table (created on first sight, with the profile read
from the provider when the token carries no email)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from ae_db.repositories import UserRepo
from ae_db.session import Database
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import Authenticator, AuthError, Identity
from .auth.clerk import ProfileFetcher
from .errors import Forbidden, Unauthorized, Unavailable


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    email: str
    role: str


def get_db(request: Request) -> Database:
    db: Database | None = getattr(request.app.state, "db", None)
    if db is None:
        raise Unavailable("database is not configured")
    return db


DbDep = Annotated[Database, Depends(get_db)]


async def identify(request: Request) -> Identity:
    authenticators: list[Authenticator] = request.app.state.authenticators
    for a in authenticators:
        try:
            identity = await a.authenticate(request)
        except AuthError as exc:
            raise Unauthorized(str(exc)) from exc
        if identity is not None:
            return identity
    raise Unauthorized("sign in required")


async def current_user(request: Request, db: DbDep) -> Principal:
    identity = await identify(request)
    async with db.system_session() as s:
        users = UserRepo(s)
        user = await users.by_auth_subject(identity.subject)
        if user is None:
            if identity.email is None:
                fetch: ProfileFetcher | None = getattr(request.app.state, "fetch_profile", None)
                if fetch is None:
                    raise Unauthorized("cannot create an account without a profile source")
                try:
                    identity = await fetch(identity.subject)
                except AuthError as exc:
                    raise Unauthorized(str(exc)) from exc
            if not identity.email:
                raise Forbidden("your sign-in has no email address; add one to your account")
            user = await users.upsert_from_auth(identity.subject, identity.email, identity.name, identity.avatar_url)
        if not UserRepo.is_active(user):
            raise Forbidden("account is not active")
        return Principal(user.id, user.email, user.role.value)


CurrentUser = Annotated[Principal, Depends(current_user)]


async def user_session(user: CurrentUser, db: DbDep) -> AsyncIterator[AsyncSession]:
    """One transaction per request, as the signed-in user (row-level security applies)."""
    async with db.user_session(user.user_id) as s:
        yield s


UserSession = Annotated[AsyncSession, Depends(user_session)]
