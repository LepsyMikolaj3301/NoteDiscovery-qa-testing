"""I12: /mcp Streamable HTTP transport rules."""
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.mcp_http import _loopback_url, build_mcp_router

pytestmark = [pytest.mark.integration, pytest.mark.mcp]

PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}


@pytest.fixture
def post(live_server):
    def _post(**kw):
        kw.setdefault("json", PING)
        return httpx.post(f"{live_server.url}/mcp", **kw)
    return _post


def test_ping_ok(post):
    r = post()
    assert r.status_code == 200 and r.json() == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_wildcard_origin_allowed(post):
    assert post(headers={"Origin": "http://evil.example"}).status_code == 200


def test_protocol_version(post):
    r = post(headers={"MCP-Protocol-Version": "1999-01-01"})
    assert r.status_code == 400 and r.json()["error"]["code"] == -32600
    assert post(headers={"MCP-Protocol-Version": "2025-06-18"}).status_code == 200


def test_invalid_json_and_batch(post):
    r = post(content=b"{nope", json=None)
    assert r.status_code == 400 and r.json()["error"]["code"] == -32700
    assert post(content=b"\xff\xfe", json=None).json()["error"]["code"] == -32700
    for body in ([PING], "x", 5):
        r = post(json=body)
        assert r.status_code == 400 and r.json()["error"]["code"] == -32600


def test_notification_gets_202(post):
    r = post(json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202 and r.content == b""


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_other_methods_405(live_server, method):
    r = httpx.request(method, f"{live_server.url}/mcp")
    assert r.status_code == 405 and r.headers["allow"] == "POST"


def test_origin_allowlist():
    app = FastAPI()
    app.include_router(build_mcp_router(lambda: "", lambda: None, ["http://good.example"]))
    c = TestClient(app)
    assert c.post("/mcp", json=PING, headers={"Origin": "http://evil.example"}).status_code == 403
    assert c.post("/mcp", json=PING, headers={"Origin": "http://good.example"}).status_code == 200
    assert c.post("/mcp", json=PING).status_code == 200  # no Origin header (non-browser client)


def test_loopback_url(monkeypatch):
    request = SimpleNamespace(scope={"server": ("0.0.0.0", 1234)})
    monkeypatch.delenv("MCP_LOOPBACK_URL", raising=False)
    assert _loopback_url(request) == "http://127.0.0.1:1234"
    monkeypatch.setenv("MCP_LOOPBACK_URL", "http://internal:9/")
    assert _loopback_url(request) == "http://internal:9"
