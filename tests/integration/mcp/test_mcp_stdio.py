"""I17: `python -m mcp_server` over stdio against the live server."""
import json
import os
import socket
import subprocess
import sys

import pytest

from tests.conftest import API_KEY, ROOT

pytestmark = [pytest.mark.integration, pytest.mark.mcp]


def run_stdio(messages, url, key=API_KEY):
    """Send JSON-RPC messages (dict, or raw str for a bad line); return decoded replies."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("NOTEDISCOVERY_")}
    env.update(NOTEDISCOVERY_URL=url, NOTEDISCOVERY_API_KEY=key)
    lines = "".join((m if isinstance(m, str) else json.dumps(m)) + "\n" for m in messages)
    out = subprocess.run([sys.executable, "-m", "mcp_server"], input=lines, cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=30)
    return [json.loads(line) for line in out.stdout.splitlines()]


def rpc(id, method, **params):
    return {"jsonrpc": "2.0", "id": id, "method": method, "params": params}


def tool(id, name, **args):
    return rpc(id, "tools/call", name=name, arguments=args)


INIT = rpc(0, "initialize", protocolVersion="2025-06-18")


def text_of(reply):
    return reply["result"]["content"][0]["text"]


def test_session(live_server):
    replies = run_stdio([
        rpc(1, "initialize", protocolVersion="2025-06-18"),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        rpc(2, "tools/list"),
        tool(3, "create_note", path="s.md", content="via stdio"),
        rpc(4, "ping"),
    ], live_server.url)
    assert [r["id"] for r in replies] == [1, 2, 3, 4]  # the notification got no reply
    assert len(replies[1]["result"]["tools"]) == 18
    assert (live_server.vault / "s.md").read_text() == "via stdio"


def test_requires_initialize_and_survives_bad_line(live_server):
    replies = run_stdio([rpc(1, "tools/list"), "{not json", rpc(2, "ping")], live_server.url)
    assert replies[0]["error"]["code"] == -32002
    assert replies[1]["error"]["code"] == -32700
    assert replies[2] == {"jsonrpc": "2.0", "id": 2, "result": {}}


def test_backend_down():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{s.getsockname()[1]}"  # closed again on exit
    reply = run_stdio([INIT, tool(1, "health_check")], url)[1]
    assert "not reachable" in text_of(reply) and "Connection error" in text_of(reply)


def test_wrong_api_key(live_server, auth):
    reply = run_stdio([INIT, tool(1, "list_notes")], live_server.url, key="wrong")[1]
    # Records current behaviour: reported as text, without isError
    assert "HTTP 401" in text_of(reply) and "isError" not in reply["result"]
