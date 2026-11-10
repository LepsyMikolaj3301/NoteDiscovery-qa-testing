"""I14: JSON-RPC session over /mcp."""
import pytest

from mcp_server.server import SUPPORTED_PROTOCOL_VERSIONS
from mcp_server.tools import get_tool_names

pytestmark = [pytest.mark.integration, pytest.mark.mcp]


def test_full_session(mcp_call, live_server):
    init = mcp_call("initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": "t"}})
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["serverInfo"]["name"] == "notediscovery-mcp"

    assert mcp_call("notifications/initialized", msg_id=None) == {}

    tools = mcp_call("tools/list")["result"]["tools"]
    assert len(tools) == 18 and [t["name"] for t in tools] == get_tool_names()

    r = mcp_call("tools/call", {"name": "create_note", "arguments": {"path": "s.md", "content": "hi"}})
    assert "isError" not in r["result"] and (live_server.vault / "s.md").read_text() == "hi"

    assert mcp_call("ping")["result"] == {}


def test_unknown_version_gets_newest(mcp_call):
    r = mcp_call("initialize", {"protocolVersion": "1999-01-01"})
    assert r["result"]["protocolVersion"] == SUPPORTED_PROTOCOL_VERSIONS[0]


def test_stateless_no_initialize_needed(mcp_call):
    assert "tools" in mcp_call("tools/list")["result"]


def test_errors(mcp_call, call_tool):
    assert mcp_call("nope/method", msg_id=7)["error"]["code"] == -32601
    assert mcp_call("nope/notification", msg_id=None) == {}
    text, is_error = call_tool("no_such_tool")
    assert is_error and "Unknown tool: no_such_tool" in text
