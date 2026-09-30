"""The broker seam. An adapter knows one broker's API; it is stateless and never touches our database: callers pass
the decrypted credentials in and store what comes back (encrypted). Execution methods (place/modify/cancel,
order book, positions, margins) join this interface in phase 9, ported from the local app's trader/broker.py."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class BrokerError(Exception):
    """The broker refused or failed a call. `code` is stable for the UI; message is safe to show."""

    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass(frozen=True)
class Credentials:
    api_key: str
    api_secret: str


@dataclass(frozen=True)
class BrokerSession:
    access_token: str
    client_id: str  # the broker account that actually logged in
    expires_at: datetime
    profile: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BrokerInfo:
    """Catalogue entry shown on "Add broker"."""

    code: str
    name: str
    available: bool
    developer_console: str
    fields: tuple[str, ...] = ("client_id", "api_key", "api_secret")
    notes: str = ""


class BrokerAdapter(Protocol):
    info: BrokerInfo

    def login_url(self, creds: Credentials, state: str) -> str:
        """Where to send the user's browser to log in; the broker redirects back to our callback with `state`."""
        ...

    async def exchange(self, creds: Credentials, callback_params: dict[str, str]) -> BrokerSession:
        """Turn the callback's one-time token into a session."""
        ...

    async def profile(self, creds: Credentials, access_token: str) -> dict[str, Any]:
        """A cheap authenticated call: proves the session works ("Test connection")."""
        ...

    async def logout(self, creds: Credentials, access_token: str) -> None:
        """Invalidate the session at the broker (best effort)."""
        ...
