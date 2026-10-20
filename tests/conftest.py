"""Shared fixtures for the whole suite (unit + REST integration + MCP integration).

Env vars are set before backend.main is imported, because it reads them (and
config.yaml) at import time. Per-test changes go through monkeypatch on
``main.config`` instead, since the app reads that dict on every request.
"""
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.request
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

API_KEY = "test-api-key"
PASSWORD = "test-password"

_boot = tempfile.mkdtemp(prefix="nd-tests-")
os.environ["NOTES_DIR"] = str(Path(_boot) / "notes")
os.environ["PLUGINS_DIR"] = str(Path(_boot) / "plugins")  # empty: no plugin alters results
os.environ["AUTHENTICATION_ENABLED"] = "false"  # tests switch it on via the `auth` fixture
os.environ["AUTHENTICATION_API_KEY"] = API_KEY
os.environ["AUTHENTICATION_PASSWORD"] = PASSWORD
os.environ["AUTHENTICATION_SECRET_KEY"] = "test-secret"
os.environ["MCP_ENABLED"] = "true"  # mounts /mcp (only possible at import time)
os.environ.pop("DEMO_MODE", None)  # keeps the limiter a no-op

import bcrypt  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import main, note_index, utils  # noqa: E402


def pytest_configure(config):
    for name in ("unit", "integration", "mcp"):
        config.addinivalue_line("markers", f"{name}: {name} tests")


# 
# Vault and caches
# 

@pytest.fixture(autouse=True)
def _reset_caches():
    """The note index and the scan cache are module globals; start each test clean."""
    note_index.get_index().reset()
    utils._scan_cache_invalidate()
    yield
    note_index.get_index().reset()
    utils._scan_cache_invalidate()


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """An empty notes directory, wired into the app."""
    notes = tmp_path / "notes"
    notes.mkdir()
    monkeypatch.setitem(main.config["storage"], "notes_dir", str(notes))
    return notes


@pytest.fixture
def make_note(vault):
    """make_note("folder/Name.md", "content") -> Path. Creates parent dirs.

    Writes straight to disk, so the index/scan cache are invalidated afterwards.
    """
    def _make(rel_path: str, content: str = "") -> Path:
        path = vault / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        note_index.get_index().invalidate()
        utils._scan_cache_invalidate()
        return path
    return _make


# 
# REST API (TestClient)
# 

@pytest.fixture
def client(vault):
    """Client for the app with authentication off."""
    return TestClient(main.app)


@pytest.fixture
def auth(monkeypatch):
    """Turn authentication on: API key API_KEY, password PASSWORD."""
    monkeypatch.setitem(main.config, "authentication", {
        "enabled": True,
        "api_key": API_KEY,
        "password_hash": bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode(),
        "secret_key": "test-secret",
    })


@pytest.fixture
def auth_client(vault, auth):
    """Client with authentication on and NOT logged in (expect 401; pass allow_redirects=False per request to see page redirects)."""
    return TestClient(main.app)


@pytest.fixture
def api_key_headers(auth):
    return {"X-API-Key": API_KEY}


@pytest.fixture
def logged_in_client(auth_client):
    """Auth on, session cookie obtained through /login."""
    resp = auth_client.post("/login", data={"password": PASSWORD})
    assert resp.status_code in (200, 302, 303), resp.text
    return auth_client


# 
# Plugins
# 

@pytest.fixture
def plugins_dir(tmp_path):
    """Empty dir for PluginManager tests; drop fake plugin .py files into it."""
    path = tmp_path / "plugins"
    path.mkdir()
    return path


@pytest.fixture
def make_plugin(plugins_dir):
    """make_plugin("name", "<python source>") -> Path of the written plugin file."""
    def _make(name: str, source: str) -> Path:
        path = plugins_dir / f"{name}.py"
        path.write_text(source, encoding="utf-8")
        return path
    return _make


# 
# Live server (MCP integration; the MCP client uses urllib, not TestClient)
# 

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    def __init__(self, url: str, vault: Path):
        self.url = url
        self.vault = vault
        self.api_key = API_KEY

    def mcp_call(self, method: str, params: dict | None = None, *, msg_id: int | None = 1,
                 headers: dict | None = None) -> dict:
        """POST a JSON-RPC message to /mcp and return the decoded response body."""
        body = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if msg_id is not None:
            body["id"] = msg_id
        req = urllib.request.Request(
            f"{self.url}/mcp",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
        return json.loads(raw) if raw else {}


@pytest.fixture
def live_server(vault):
    """Real uvicorn serving the app on a free port, backed by the temp vault.

    Auth stays off unless the test uses the `auth` fixture (the loopback call
    from /mcp carries API_KEY then).
    """
    import uvicorn

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("live_server did not start")
        time.sleep(0.05)

    yield LiveServer(f"http://127.0.0.1:{port}", vault)

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def mcp_call(live_server):
    return live_server.mcp_call
