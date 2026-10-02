"""FastAPI application factory. Run: uvicorn ae_api.main:app --reload (or `make dev-api`)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from ae_brokers.registry import default_adapters
from ae_core.secrets import SecretBox, load_master_key
from ae_db.session import Database
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from . import __version__, errors
from .auth import Authenticator
from .auth.clerk import ClerkAuthenticator, clerk_profile_fetcher
from .auth.dev import DevHeaderAuthenticator
from .billing.razorpay import RazorpayClient
from .charts import ChartService
from .logconfig import configure_logging
from .login_state import LoginStateSigner
from .middleware import RequestContextMiddleware
from .routers import (
    admin,
    backtests,
    billing,
    brokers,
    charts,
    entitlements,
    health,
    market,
    me,
    monitor,
    notifications,
    plans,
    reports,
    runs,
    strategies,
    webhooks,
)
from .settings import Settings, load_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    app.state.db = Database(settings.database_url) if settings.database_url else None
    app.state.redis = Redis.from_url(settings.redis_url, socket_timeout=2) if settings.redis_url else None
    app.state.login_state = (
        LoginStateSigner(load_master_key(settings.app_encryption_key), app.state.redis)
        if settings.app_encryption_key and app.state.redis is not None
        else None
    )
    app.state.charts = (
        ChartService(app.state.redis, app.state.db)
        if app.state.redis is not None and app.state.db is not None
        else None
    )
    try:
        yield
    finally:
        if app.state.charts is not None:
            await app.state.charts.close()
        if app.state.db is not None:
            await app.state.db.dispose()
        if app.state.redis is not None:
            await app.state.redis.aclose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level, settings.json_logs)
    app = FastAPI(
        title="AlgoEarning API",
        version=__version__,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    authenticators: list[Authenticator] = []
    if settings.clerk_publishable_key:
        authenticators.append(ClerkAuthenticator(settings.clerk_publishable_key, settings.authorized_parties))
    if settings.dev_auth:
        authenticators.append(DevHeaderAuthenticator())
    app.state.authenticators = authenticators
    app.state.fetch_profile = clerk_profile_fetcher(settings.clerk_secret_key) if settings.clerk_secret_key else None
    app.state.secretbox = (
        SecretBox({1: load_master_key(settings.app_encryption_key)}, 1) if settings.app_encryption_key else None
    )
    app.state.brokers = default_adapters()
    app.state.payments = (
        RazorpayClient(settings.razorpay_key_id, settings.razorpay_key_secret)
        if settings.razorpay_key_id and settings.razorpay_key_secret
        else None
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )
    errors.install(app)
    for r in (
        health.router,
        me.router,
        entitlements.router,
        plans.router,
        strategies.router,
        monitor.router,
        market.router,
        charts.router,
        runs.router,
        backtests.router,
        notifications.router,
        reports.router,
        billing.router,
        brokers.router,
        webhooks.router,
        admin.router,
    ):
        app.include_router(r)
    return app


app = create_app()
