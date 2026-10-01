"""Configuration from environment variables (and a local .env in development). Secrets never live in code.

| Variable      | Default                | Meaning                                            |
|---------------|------------------------|----------------------------------------------------|
| APP_ENV       | development            | development / test / staging / production          |
| WEB_ORIGIN    | http://localhost:3000  | the web app's origin (CORS; comma-separate several) |
| LOG_LEVEL     | INFO                   |                                                    |
| LOG_JSON      | false (true in prod)   | JSON log lines for log shipping                     |
| DATABASE_URL  | (unset until phase 3)  | postgresql+psycopg://...                            |
| REDIS_URL     | (unset until phase 3)  | redis://...                                         |
| DEV_AUTH      | false                  | local stand-in for sign-in (X-Dev-User header);     |
|               |                        | refused outside development/test                    |
| CLERK_PUBLISHABLE_KEY |                | identifies the Clerk instance (token issuer + JWKS) |
| CLERK_SECRET_KEY      |                | server-only: reads a new user's profile from Clerk  |
| CLERK_WEBHOOK_SECRET  |                | whsec_...: verifies Clerk -> /v1/webhooks/clerk     |
| CLERK_AUTHORIZED_PARTIES | WEB_ORIGIN  | origins allowed in a session token's `azp` claim     |
| SUBSCRIPTION_GRACE_DAYS | 3            | a failed renewal keeps the paid plan this many days  |
| RAZORPAY_KEY_ID       |                | rzp_test_... / rzp_live_... (public: sent to checkout) |
| RAZORPAY_KEY_SECRET   |                | server-only: creates orders, fetches payments          |
| RAZORPAY_WEBHOOK_SECRET |              | verifies Razorpay -> /v1/webhooks/razorpay             |
| APP_ENCRYPTION_KEY    |                | base64 32-byte master key for broker credentials       |
| API_PUBLIC_URL        | http://localhost:8000 | how browsers/brokers reach this API (callbacks) |
| BREEZE_API_KEY        |                | ICICI Breeze app key: the platform price feed (phase 10) |
| REGISTRATION_OPEN     | false in prod  | whether a new sign-in may create an account (closed alpha) |
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from fastapi import Depends, Request
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "staging", "production"] = "development"
    web_origin: str = "http://localhost:3000"
    log_level: str = "INFO"
    log_json: bool | None = None
    database_url: str | None = Field(default=None, repr=False)
    redis_url: str | None = Field(default=None, repr=False)
    dev_auth: bool = False
    clerk_publishable_key: str | None = None
    clerk_secret_key: str | None = Field(default=None, repr=False)
    clerk_webhook_secret: str | None = Field(default=None, repr=False)
    clerk_authorized_parties: str | None = None
    subscription_grace_days: int = Field(default=3, ge=0, le=30)
    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = Field(default=None, repr=False)
    razorpay_webhook_secret: str | None = Field(default=None, repr=False)
    app_encryption_key: str | None = Field(default=None, repr=False)
    api_public_url: str = "http://localhost:8000"
    smtp_host: str | None = None  # only to tell the UI whether email is set up; the worker sends
    telegram_bot_token: str | None = Field(default=None, repr=False)  # likewise
    telegram_bot_username: str | None = None  # for the "Connect Telegram" link
    breeze_api_key: str | None = None  # only for Monitor's login link; the feed process holds the secret
    registration_open: bool | None = None  # see accepts_new_users

    @property
    def web_url(self) -> str:
        return self.cors_origins[0] if self.cors_origins else "http://localhost:3000"

    @property
    def authorized_parties(self) -> list[str]:
        raw = self.clerk_authorized_parties or self.web_origin
        return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]

    @model_validator(mode="after")
    def _dev_auth_only_locally(self) -> Settings:
        if self.dev_auth and self.app_env not in ("development", "test"):
            raise ValueError("DEV_AUTH may only be enabled in development or test")
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.web_origin.split(",") if o.strip()]

    @property
    def json_logs(self) -> bool:
        return self.app_env in ("staging", "production") if self.log_json is None else self.log_json

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def accepts_new_users(self) -> bool:
        """Closed alpha: outside development and test, only accounts we already have may sign in."""
        return self.app_env in ("development", "test") if self.registration_open is None else self.registration_open


@lru_cache
def load_settings() -> Settings:
    """Settings from the environment, read once per process."""
    return Settings()


def app_settings(request: Request) -> Settings:
    """FastAPI dependency: the settings the running app was created with (tests pass their own)."""
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(app_settings)]
