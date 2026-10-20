import pytest


@pytest.fixture
def call_tool(mcp_call):
    """call_tool("get_note", path="a.md") -> (text, is_error)"""
    def _call(tool_name, /, **args):
        result = mcp_call("tools/call", {"name": tool_name, "arguments": args})["result"]
        return result["content"][0]["text"], result.get("isError", False)
    return _call
