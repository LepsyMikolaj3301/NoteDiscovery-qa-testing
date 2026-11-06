"""I13: /mcp with authentication."""
import os
import subprocess
import sys

import httpx
import pytest

from tests.conftest import API_KEY, ROOT

pytestmark = [pytest.mark.integration, pytest.mark.mcp]

PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}


@pytest.mark.parametrize("headers,status", [
    ({}, 401),
    ({"X-API-Key": "wrong"}, 401),
    ({"X-API-Key": API_KEY}, 200),
    ({"Authorization": f"Bearer {API_KEY}"}, 200),
])
def test_mcp_requires_key(live_server, auth, headers, status):
    r = httpx.post(f"{live_server.url}/mcp", json=PING, headers=headers)
    assert r.status_code == status
    if status == 401:
        assert r.json() == {"detail": "Not authenticated"}  # JSON, not a login redirect


def test_loopback_call_carries_the_key(live_server, auth):
    live_server.vault.joinpath("n.md").write_text("x")
    # list_notes goes back through /api/notes, which needs the key
    reply = live_server.mcp_call(
        "tools/call", {"name": "list_notes", "arguments": {}}, headers={"X-API-Key": API_KEY}
    )
    assert "Found 1 note(s)" in reply["result"]["content"][0]["text"]


@pytest.mark.parametrize("env,mounted", [
    ({"AUTHENTICATION_ENABLED": "true", "AUTHENTICATION_API_KEY": ""}, False),
    ({"AUTHENTICATION_ENABLED": "true", "AUTHENTICATION_API_KEY": "k"}, True),
    ({"AUTHENTICATION_ENABLED": "false", "MCP_ENABLED": "false"}, False),
    ({"AUTHENTICATION_ENABLED": "false"}, True),
])
def test_mount_decision_at_startup(tmp_path, env, mounted):
    # The decision is taken at import time, so it needs a fresh interpreter
    child_env = {**os.environ, "NOTES_DIR": str(tmp_path / "n"), "PLUGINS_DIR": str(tmp_path / "p"), **env}
    code = "from backend.main import app; print(any(getattr(r, 'path', '') == '/mcp' for r in app.routes))"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=child_env, capture_output=True, text=True, timeout=60)
    assert out.stdout.strip().splitlines()[-1] == str(mounted), out.stderr
