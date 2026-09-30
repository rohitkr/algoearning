"""Notification settings, Telegram link codes, the test message and history: per user."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from ae_api.main import create_app
from ae_api.settings import Settings
from fastapi.testclient import TestClient

from conftest import TEST_REDIS_URL

A = {"X-Dev-User": "alice@example.com"}
B = {"X-Dev-User": "bob@example.com"}


@pytest.fixture
def api(clean_db: str) -> Iterator[TestClient]:
    settings = Settings(app_env="test", dev_auth=True, database_url=clean_db, redis_url=TEST_REDIS_URL,
                        smtp_host="smtp.test", telegram_bot_token="t", telegram_bot_username="algo_bot")  # fmt: skip
    with TestClient(create_app(settings)) as c:
        yield c


def test_defaults_and_saving(api: TestClient) -> None:
    d = api.get("/v1/me/notifications", headers=A).json()
    assert d["email_enabled"] is True and d["account_email"] == "alice@example.com"
    assert "stop_loss" in d["events"] and "trade_opened" not in d["events"]
    assert d["email_available"] and d["telegram_available"] and not d["telegram_connected"]
    body = {"email_enabled": True, "email_address": "alerts@example.com", "telegram_enabled": False,
            "events": ["trade_opened", "order_problem"]}  # fmt: skip
    r = api.put("/v1/me/notifications", json=body, headers=A).json()
    assert r["events"] == ["order_problem", "trade_opened"] and r["email_address"] == "alerts@example.com"
    assert api.get("/v1/me/notifications", headers=B).json()["events"] != r["events"]  # per user
    assert api.put("/v1/me/notifications", json={**body, "events": ["nope"]}, headers=A).status_code == 400
    assert api.put("/v1/me/notifications", json={**body, "email_address": "bad"}, headers=A).status_code == 422
    # Telegram cannot be switched on before a chat is linked
    assert api.put("/v1/me/notifications", json={**body, "telegram_enabled": True}, headers=A).status_code == 400


def test_telegram_link_test_message_and_history(api: TestClient) -> None:
    link = api.post("/v1/me/notifications/telegram/link", headers=A).json()
    assert link["url"] == f"https://t.me/algo_bot?start={link['code']}" and len(link["code"]) >= 10
    assert api.post("/v1/me/notifications/telegram/link", headers=A).json()["code"] != link["code"]
    t = api.post("/v1/me/notifications/test", headers=A)
    assert t.status_code == 201 and t.json()["status"] == "pending"
    assert [n["title"] for n in api.get("/v1/me/notifications/history", headers=A).json()] == ["Test notification"]
    assert api.get("/v1/me/notifications/history", headers=B).json() == []
    assert api.delete("/v1/me/notifications/telegram", headers=A).json()["telegram_connected"] is False
