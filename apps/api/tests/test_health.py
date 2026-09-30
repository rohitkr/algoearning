from ae_api import __version__
from ae_api.main import create_app
from ae_api.settings import Settings
from fastapi.testclient import TestClient


def client(**kw) -> TestClient:
    kw.setdefault("app_env", "test")
    return TestClient(create_app(Settings(**kw)))


def test_health_reports_version_and_env():
    r = client().get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": __version__, "env": "test"}


def test_ready_lists_dependencies():
    r = client().get("/health/ready")
    assert r.status_code == 200
    assert {c["name"]: c["status"] for c in r.json()["checks"]} == {
        "database": "not_configured",
        "redis": "not_configured",
    }


def test_request_id_is_echoed_or_generated():
    c = client()
    assert c.get("/health", headers={"X-Request-ID": "abc12345-req"}).headers["X-Request-ID"] == "abc12345-req"
    generated = c.get("/health", headers={"X-Request-ID": "bad id\n"}).headers["X-Request-ID"]
    assert len(generated) == 32 and generated != "bad id\n"


def test_cors_allows_only_the_web_origin():
    c = client(web_origin="https://algoearning.com")
    ok = c.options("/health", headers={"Origin": "https://algoearning.com", "Access-Control-Request-Method": "GET"})
    bad = c.options("/health", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "https://algoearning.com"
    assert "access-control-allow-origin" not in bad.headers


def test_docs_hidden_in_production():
    c = client(app_env="production")
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
