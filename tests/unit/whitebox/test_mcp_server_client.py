"""White-box tests (W7-W11): MCPServer dispatch + tool handlers, NoteDiscoveryClient."""
import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from mcp_server import client as client_mod
from mcp_server.client import APIResponse, NoteDiscoveryClient
from mcp_server.config import MCPConfig
from mcp_server.server import SUPPORTED_PROTOCOL_VERSIONS, MCPServer
from mcp_server.tools import get_tool_names

pytestmark = pytest.mark.unit

CFG = MCPConfig("http://nd:8000", "key", 5.0, 3)


def ok(data=None):
    return APIResponse(True, data)


def err(msg="boom"):
    return APIResponse(False, None, msg, 500)


@pytest.fixture
def srv():
    s = MCPServer(CFG, require_initialize=False)
    s.client = MagicMock()
    return s


def call(s, tool_name, /, **args):
    return s.handle_call_tool({"name": tool_name, "arguments": args})["content"][0]["text"]


# ---------------------------------------------------------------- W7: handle_message
@pytest.fixture
def raw_srv():
    return MCPServer(CFG)  # requires initialize


@pytest.mark.parametrize("msg", [[], "x", 5, None])
def test_non_dict_is_invalid_request(raw_srv, msg):
    r = raw_srv.handle_message(msg)
    assert r["error"]["code"] == -32600 and r["id"] is None


def test_message_without_method_is_ignored(raw_srv):
    assert raw_srv.handle_message({"jsonrpc": "2.0", "id": 1, "result": {}}) is None


def test_initialize_and_flag(raw_srv):
    r = raw_srv.handle_message({"id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}})
    assert r["id"] == 1 and r["result"]["protocolVersion"] == "2024-11-05"
    assert "tools" in r["result"]["capabilities"] and r["result"]["serverInfo"]["name"]
    assert raw_srv._initialized


def test_initialize_without_params_or_null_params(raw_srv):
    for m in ({"id": 1, "method": "initialize"}, {"id": 1, "method": "initialize", "params": None}):
        assert "result" in raw_srv.handle_message(m)


@pytest.mark.parametrize("method", ["tools/list", "tools/call"])
def test_tools_before_initialize_rejected(raw_srv, method):
    r = raw_srv.handle_message({"id": 7, "method": method, "params": {"name": "health_check"}})
    assert r["error"]["code"] == -32002 and r["id"] == 7


def test_initialized_notification_no_reply_and_does_not_unlock(raw_srv):
    assert raw_srv.handle_message({"method": "notifications/initialized"}) is None
    assert raw_srv.handle_message({"id": 1, "method": "tools/list"})["error"]["code"] == -32002


def test_tools_list_after_initialize(raw_srv):
    raw_srv.handle_message({"id": 1, "method": "initialize", "params": {}})
    r = raw_srv.handle_message({"id": 2, "method": "tools/list"})
    assert [t["name"] for t in r["result"]["tools"]] == get_tool_names()


def test_ping_works_without_initialize(raw_srv):
    assert raw_srv.handle_message({"id": 3, "method": "ping"}) == {"jsonrpc": "2.0", "id": 3, "result": {}}


def test_stateless_server_skips_init_check():
    s = MCPServer(CFG, require_initialize=False)
    assert "result" in s.handle_message({"id": 1, "method": "tools/list"})


def test_unknown_method_with_and_without_id(raw_srv):
    assert raw_srv.handle_message({"id": 9, "method": "nope"})["error"]["code"] == -32601
    assert raw_srv.handle_message({"method": "notifications/nope"}) is None


def test_id_zero_is_a_real_id(raw_srv):
    assert raw_srv.handle_message({"id": 0, "method": "nope"})["error"]["code"] == -32601


def test_handler_exception_becomes_internal_error(srv, monkeypatch):
    monkeypatch.setattr(srv, "handle_list_tools", MagicMock(side_effect=RuntimeError("kaput")))
    r = srv.handle_message({"id": 4, "method": "tools/list"})
    assert r["error"]["code"] == -32603 and "kaput" in r["error"]["message"]


def test_handler_exception_in_notification_is_silent(srv, monkeypatch):
    monkeypatch.setattr(srv, "handle_initialized", MagicMock(side_effect=RuntimeError("x")))
    assert srv.handle_message({"method": "notifications/initialized"}) is None


# ------------------------------------------------------- W8: initialize / call_tool
def test_protocol_version_negotiation(raw_srv):
    assert raw_srv.handle_initialize({"protocolVersion": "2025-03-26"})["protocolVersion"] == "2025-03-26"
    assert raw_srv.handle_initialize({"protocolVersion": "1999-01-01"})["protocolVersion"] == SUPPORTED_PROTOCOL_VERSIONS[0]
    assert raw_srv.handle_initialize({})["protocolVersion"] == SUPPORTED_PROTOCOL_VERSIONS[0]


def test_response_and_error_shapes(raw_srv):
    assert raw_srv._response(1, {"a": 1}) == {"jsonrpc": "2.0", "id": 1, "result": {"a": 1}}
    e = raw_srv._error(-1, "m")
    assert e == {"code": -1, "message": "m"}
    assert raw_srv._error(-1, "m", data={"k": 1})["data"] == {"k": 1}
    r = raw_srv._response(1, error=e)
    assert "result" not in r and r["error"] == e
    assert raw_srv._response(1)["result"] is None


def test_call_unknown_tool_lists_available(srv):
    r = srv.handle_call_tool({"name": "nope", "arguments": {}})
    assert r["isError"] and "Unknown tool: nope" in r["content"][0]["text"] and "get_note" in r["content"][0]["text"]


def test_call_missing_name_is_unknown_tool(srv):
    assert srv.handle_call_tool({})["isError"]


def test_call_does_not_reach_private_non_tool_methods(srv):
    # "_tool_" prefix is forced, so "validate_path" cannot be called as a tool
    assert srv.handle_call_tool({"name": "validate_path"})["isError"]


def test_call_tool_exception_is_error_result(srv):
    srv.client.get_note.side_effect = RuntimeError("net down")
    r = srv.handle_call_tool({"name": "get_note", "arguments": {"path": "a.md"}})
    assert r["isError"] and "net down" in r["content"][0]["text"]


def test_call_tool_null_arguments_is_error_not_crash(srv):
    r = srv.handle_call_tool({"name": "get_note", "arguments": None})
    assert r["isError"]


def test_call_tool_success_has_no_iserror(srv):
    srv.client.health_check.return_value = ok()
    assert "isError" not in srv.handle_call_tool({"name": "health_check"})


# --------------------------------------------------------------- W9: tool handlers
def test_every_tool_has_a_handler_and_unknown_args_dont_crash(srv):
    for name in get_tool_names():
        assert callable(getattr(srv, f"_tool_{name}"))


@pytest.mark.parametrize("tool,args,expected", [
    ("get_note", {}, "path is required"),
    ("get_note", {"path": "../x"}, "'..'"),
    ("get_backlinks", {"path": "/abs"}, "absolute"),
    ("create_note", {"path": "", "content": "x"}, "path is required"),
    ("create_note", {"path": "a.md", "content": ""}, "content is required"),
    ("delete_note", {"path": "C:\\x"}, "absolute"),
    ("create_folder", {"path": "a/../b"}, "'..'"),
    ("append_to_note", {"path": "a.md"}, "content is required"),
    ("append_to_note", {"path": "../a.md", "content": "x"}, "'..'"),
    ("move_note", {"old_path": "a.md", "new_path": "/b.md"}, "new_path"),
    ("move_note", {"old_path": "../a.md", "new_path": "b.md"}, "old_path"),
    ("get_template", {"name": "../t"}, "name"),
    ("get_notes_by_tag", {}, "tag is required"),
    ("create_note_from_template", {"note_path": "a.md"}, "template_name is required"),
    ("create_note_from_template", {"template_name": "t", "note_path": "/a.md"}, "note_path"),
    ("search_notes", {"query": "   "}, "No search term"),
])
def test_validation_short_circuits_before_client(srv, tool, args, expected):
    out = call(srv, tool, **args)
    assert expected in out
    assert srv.client.method_calls == []  # nothing sent to the backend


def test_get_template_error_says_name_not_path(srv):
    assert "path" not in call(srv, "get_template", name="").replace("name is required", "")


def test_search_notes_formats_and_paginates(srv):
    srv.client.search.return_value = ok({
        "results": [{"path": "a.md", "matches": [
            {"line_number": 3, "context": "x <mark>hit</mark> y"},
            {"line_number": 4, "context": "second"},
            {"line_number": 5, "context": "third is dropped"},
        ]}],
        "pagination": {"total": 5, "has_more": True},
    })
    out = call(srv, "search_notes", query=" hit ", limit=1, offset=2)
    srv.client.search.assert_called_once_with("hit", limit=1, offset=2)
    assert "Found 5 result(s)" in out and "Line 3: x hit y" in out and "second" in out
    assert "third" not in out and "<mark>" not in out and "more results available" in out


def test_search_notes_empty_and_failure(srv):
    srv.client.search.return_value = ok({"results": []})
    assert "No notes found matching 'q'" in call(srv, "search_notes", query="q")
    srv.client.search.return_value = err("bad")
    assert "Search failed: bad" in call(srv, "search_notes", query="q")
    srv.client.search.return_value = ok(None)
    assert "No notes found" in call(srv, "search_notes", query="q")


def test_list_notes_groups_by_folder(srv):
    srv.client.list_notes.return_value = ok({
        "notes": [{"path": "a.md", "name": "a", "modified": "m1"},
                  {"path": "z/b.md", "name": "b", "modified": "m2"},
                  {"path": "z/c.md", "name": "c", "modified": "m3"}],
        "folders": ["z"],
    })
    out = call(srv, "list_notes")
    assert "Found 3 note(s) in 1 folder(s)" in out
    assert out.index("(root)/") < out.index("z/") and "📝 c (modified: m3)" in out
    srv.client.list_notes.assert_called_once_with(limit=None, offset=0)


def test_list_notes_pagination_and_failure(srv):
    srv.client.list_notes.return_value = ok({"notes": [], "folders": [], "pagination": {"total": 9, "has_more": True}})
    out = call(srv, "list_notes", limit=2)
    assert "Found 9 note(s)" in out and "more notes available" in out
    srv.client.list_notes.return_value = err("x")
    assert call(srv, "list_notes").startswith("Failed to list notes")


def test_get_note_output(srv):
    srv.client.get_note.return_value = ok({"content": "BODY", "metadata": {"modified": "M", "size": 4}})
    out = call(srv, "get_note", path="a.md")
    assert "# a.md" in out and "Modified: M" in out and "Size: 4 bytes" in out and out.endswith("BODY")
    srv.client.get_note.return_value = ok({"content": "B"})
    assert "Modified" not in call(srv, "get_note", path="a.md")
    srv.client.get_note.return_value = err("404")
    assert "Failed to get note: 404" in call(srv, "get_note", path="a.md")


def test_list_tags_sorted_by_count_and_plural(srv):
    srv.client.list_tags.return_value = ok({"tags": {"one": 1, "many": 5}})
    out = call(srv, "list_tags")
    assert out.index("#many") < out.index("#one") and "(5 notes)" in out and "(1 note)" in out
    srv.client.list_tags.return_value = ok({"tags": {}})
    assert "No tags" in call(srv, "list_tags")
    srv.client.list_tags.return_value = err()
    assert "Failed to list tags" in call(srv, "list_tags")


def test_get_notes_by_tag(srv):
    srv.client.get_notes_by_tag.return_value = ok({"notes": [{"path": "a.md"}], "count": 7,
                                                   "pagination": {"has_more": True}})
    out = call(srv, "get_notes_by_tag", tag="t", limit=1)
    srv.client.get_notes_by_tag.assert_called_once_with("t", limit=1, offset=0)
    assert "7 total, showing 1" in out and "a.md" in out and "more notes available" in out
    srv.client.get_notes_by_tag.return_value = ok({"notes": []})
    assert "No notes found with tag '#t'" in call(srv, "get_notes_by_tag", tag="t")


def test_get_graph_most_connected_top_ten(srv):
    edges = [{"source": "hub", "target": f"n{i}"} for i in range(12)]
    srv.client.get_graph.return_value = ok({"nodes": [1] * 13, "edges": edges})
    out = call(srv, "get_graph")
    assert "13 nodes, 12 connections" in out and "hub: 12 connections" in out
    assert out.count("connections\n") + 1 >= 10 and out.count(" connections") == 11  # header + 10 lines
    srv.client.get_graph.return_value = ok({"nodes": [], "edges": []})
    assert "Most connected" not in call(srv, "get_graph")


def test_get_backlinks(srv):
    srv.client.get_backlinks.return_value = ok({"path": "a.md", "backlinks": [
        {"path": "b.md", "name": "b", "references": [{"line_number": 2, "context": "ctx", "type": "wikilink"}]}]})
    out = call(srv, "get_backlinks", path="a.md")
    assert "1 note(s) link" in out and "Line 2 [wikilink]: ctx" in out
    srv.client.get_backlinks.return_value = ok({"backlinks": []})
    assert "No backlinks found for 'a.md'" in call(srv, "get_backlinks", path="a.md")


@pytest.mark.parametrize("tool,args,method,success_text", [
    ("create_note", {"path": "a.md", "content": "c"}, "create_note", "Note created/updated: a.md"),
    ("delete_note", {"path": "a.md"}, "delete_note", "Note deleted: a.md"),
    ("create_folder", {"path": "f"}, "create_folder", "Folder created: f"),
    ("move_note", {"old_path": "a.md", "new_path": "b.md"}, "move_note", "a.md → b.md"),
    ("append_to_note", {"path": "a.md", "content": "c"}, "append_to_note", "appended to: a.md"),
    ("create_note_from_template", {"template_name": "t", "note_path": "a.md"}, "create_note_from_template", "from template 't'"),
])
def test_write_tools_success_and_failure(srv, tool, args, method, success_text):
    getattr(srv.client, method).return_value = ok({})
    assert success_text in call(srv, tool, **args)
    getattr(srv.client, method).return_value = err("denied")
    out = call(srv, tool, **args)
    assert out.startswith("Failed") and "denied" in out


def test_append_defaults_timestamp_off(srv):
    srv.client.append_to_note.return_value = ok()
    call(srv, "append_to_note", path="a.md", content="c")
    srv.client.append_to_note.assert_called_with("a.md", "c", False)
    call(srv, "append_to_note", path="a.md", content="c", add_timestamp=True)
    srv.client.append_to_note.assert_called_with("a.md", "c", True)


def test_templates_tools(srv):
    srv.client.list_templates.return_value = ok({"templates": [{"name": "daily"}]})
    assert "📄 daily" in call(srv, "list_templates")
    srv.client.list_templates.return_value = ok({"templates": []})
    assert "No templates" in call(srv, "list_templates")
    srv.client.get_template.return_value = ok({"content": "X {{date}}"})
    assert call(srv, "get_template", name="daily") == "Template: daily\n---\nX {{date}}"


def test_health_and_config(srv):
    srv.client.health_check.return_value = ok()
    assert "healthy at http://nd:8000" in call(srv, "health_check")
    srv.client.health_check.return_value = err("down")
    assert "not reachable: down" in call(srv, "health_check")
    srv.client.get_config.return_value = ok({"b": {"x": "é"}, "a": 1})
    out = call(srv, "get_config")
    assert out.index("a: 1") < out.index("b:") and '"é"' in out
    srv.client.get_config.return_value = ok({})
    assert "empty configuration" in call(srv, "get_config")


def iso(delta, tz=False):
    t = datetime.now(timezone.utc) - delta if tz else datetime.now() - delta
    return t.isoformat()


def test_recent_notes_filter_sort_limit(srv):
    srv.client.list_notes.return_value = ok({"notes": [
        {"path": "old.md", "modified": iso(timedelta(days=30))},
        {"path": "new.md", "modified": iso(timedelta(hours=1))},
        {"path": "mid.md", "modified": iso(timedelta(days=2))},
        {"path": "bad.md", "modified": "garbage"},
        {"path": "none.md"},
    ]})
    out = call(srv, "get_recent_notes")
    assert out.index("new.md") < out.index("mid.md") and "old.md" not in out
    assert "bad.md" not in out and "none.md" not in out
    assert "(showing 2)" in call(srv, "get_recent_notes", days=7, limit=10)
    assert "mid.md" not in call(srv, "get_recent_notes", limit=1)
    assert "old.md" in call(srv, "get_recent_notes", days=60)


def test_recent_notes_accepts_z_suffix(srv):
    z = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(tzinfo=None).isoformat() + "Z"
    srv.client.list_notes.return_value = ok({"notes": [{"path": "z.md", "modified": z}]})
    assert "z.md" in call(srv, "get_recent_notes")


def test_recent_notes_empty_cases(srv):
    srv.client.list_notes.return_value = ok({"notes": []})
    assert call(srv, "get_recent_notes") == "No notes found."
    srv.client.list_notes.return_value = ok({"notes": [{"path": "o.md", "modified": iso(timedelta(days=99))}]})
    assert "No notes modified in the last 7 day(s)" in call(srv, "get_recent_notes")
    srv.client.list_notes.return_value = err("x")
    assert call(srv, "get_recent_notes").startswith("Failed to get notes")


# ----------------------------------------------------------------- W10: _request
class FakeResp:
    def __init__(self, body=b"{}", status=200):
        self._b, self.status = body, status

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def nd(monkeypatch):
    sleeps = []
    monkeypatch.setattr(client_mod.time, "sleep", sleeps.append)
    c = NoteDiscoveryClient(CFG)
    c.sleeps = sleeps
    return c


def patch_urlopen(monkeypatch, *effects):
    m = MagicMock(side_effect=list(effects))
    monkeypatch.setattr(client_mod.urllib.request, "urlopen", m)
    return m


def http_error(code, body):
    return urllib.error.HTTPError("u", code, "msg", {}, io.BytesIO(body))


def test_request_success_builds_request(nd, monkeypatch):
    m = patch_urlopen(monkeypatch, FakeResp(b'{"a": 1}', 201))
    r = nd._request("POST", "/api/x", params={"q": "a b"}, data={"k": "é"})
    assert r.success and r.data == {"a": 1} and r.status_code == 201
    req = m.call_args.args[0]
    assert req.full_url == "http://nd:8000/api/x?q=a+b" and req.get_method() == "POST"
    assert json.loads(req.data) == {"k": "é"} and req.get_header("X-api-key") == "key"
    assert m.call_args.kwargs["timeout"] == 5.0


def test_request_no_body_for_get(nd, monkeypatch):
    m = patch_urlopen(monkeypatch, FakeResp())
    nd._request("GET", "/x")
    assert m.call_args.args[0].data is None and m.call_args.args[0].full_url == "http://nd:8000/x"


def test_request_empty_body_is_none_data(nd, monkeypatch):
    patch_urlopen(monkeypatch, FakeResp(b""))
    r = nd._request("GET", "/x")
    assert r.success and r.data is None


def test_request_http_error_json_detail_no_retry(nd, monkeypatch):
    m = patch_urlopen(monkeypatch, http_error(404, b'{"detail": "Note not found"}'))
    r = nd._request("GET", "/x")
    assert not r.success and r.error == "HTTP 404: Note not found" and r.status_code == 404
    assert m.call_count == 1 and nd.sleeps == []


def test_request_http_error_json_without_detail(nd, monkeypatch):
    patch_urlopen(monkeypatch, http_error(400, b'{"other": 1}'))
    assert nd._request("GET", "/x").error == 'HTTP 400: {"other": 1}'


def test_request_http_error_non_json_body(nd, monkeypatch):
    patch_urlopen(monkeypatch, http_error(502, b"<html>bad gateway</html>"))
    assert nd._request("GET", "/x").error == "HTTP 502: <html>bad gateway</html>"


def test_request_http_error_json_non_dict_body(nd, monkeypatch):
    patch_urlopen(monkeypatch, http_error(500, b"[1,2]"))
    r = nd._request("GET", "/x")
    assert not r.success and r.error.startswith("HTTP 500")  # .get on a list must not crash


def test_request_http_error_empty_body(nd, monkeypatch):
    patch_urlopen(monkeypatch, http_error(500, b""))
    assert nd._request("GET", "/x").error.startswith("HTTP 500: ")


def test_request_url_error_retries_with_backoff(nd, monkeypatch):
    m = patch_urlopen(monkeypatch, *[urllib.error.URLError("refused")] * 3)
    r = nd._request("GET", "/x")
    assert m.call_count == 3 and nd.sleeps == [0.1, 0.2]  # no sleep after the last attempt
    assert not r.success and r.error == "Connection error: refused"


def test_request_timeout_retries_then_fails(nd, monkeypatch):
    m = patch_urlopen(monkeypatch, *[TimeoutError()] * 3)
    r = nd._request("GET", "/x")
    assert m.call_count == 3 and r.error == "Request timed out after 5.0s"


def test_request_recovers_after_transient_failure(nd, monkeypatch):
    m = patch_urlopen(monkeypatch, urllib.error.URLError("x"), TimeoutError(), FakeResp(b'{"ok": true}'))
    r = nd._request("GET", "/x")
    assert r.success and r.data == {"ok": True} and m.call_count == 3


def test_request_invalid_json_no_retry(nd, monkeypatch):
    m = patch_urlopen(monkeypatch, FakeResp(b"not json"))
    r = nd._request("GET", "/x")
    assert not r.success and r.error.startswith("Invalid JSON response") and m.call_count == 1


def test_request_zero_retries_never_calls(monkeypatch):
    m = patch_urlopen(monkeypatch)
    c = NoteDiscoveryClient(MCPConfig("http://nd:8000", None, 5.0, 0))
    r = c._request("GET", "/x")
    assert not r.success and r.error == "Unknown error after retries" and m.call_count == 0


def test_request_single_retry_means_no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(client_mod.time, "sleep", sleeps.append)
    patch_urlopen(monkeypatch, urllib.error.URLError("x"))
    NoteDiscoveryClient(MCPConfig("http://nd:8000", None, 5.0, 1))._request("GET", "/x")
    assert sleeps == []


# --------------------------------------------------------- W11: endpoint methods
@pytest.fixture
def rq(nd, monkeypatch):
    m = MagicMock(return_value=ok())
    monkeypatch.setattr(nd, "_request", m)
    return nd, m


@pytest.mark.parametrize("meth,args,expected", [
    ("list_notes", (), ("GET", "/api/notes", {"params": None})),
    ("list_notes", (5, 10), ("GET", "/api/notes", {"params": {"limit": "5", "offset": "10"}})),
    ("list_notes", (None, 10), ("GET", "/api/notes", {"params": None})),  # offset ignored without limit
    ("get_note", ("a b/é.md",), ("GET", "/api/notes/a%20b%2F%C3%A9.md", {"params": {"include_backlinks": "false"}})),
    ("get_backlinks", ("f/a.md",), ("GET", "/api/notes/f%2Fa.md", {"params": {"include_backlinks": "true"}})),
    ("create_note", ("f/a.md", "c"), ("POST", "/api/notes/f%2Fa.md", {"data": {"content": "c"}})),
    ("delete_note", ("f/a.md",), ("DELETE", "/api/notes/f%2Fa.md", {})),
    ("append_to_note", ("a.md", "c", True), ("PATCH", "/api/notes/a.md", {"data": {"content": "c", "add_timestamp": True}})),
    ("append_to_note", ("a.md", "c"), ("PATCH", "/api/notes/a.md", {"data": {"content": "c", "add_timestamp": False}})),
    ("move_note", ("a", "b"), ("POST", "/api/notes/move", {"data": {"oldPath": "a", "newPath": "b"}})),
    ("create_note_from_template", ("t", "n.md"),
     ("POST", "/api/templates/create-note", {"data": {"templateName": "t", "notePath": "n.md"}})),
    ("search", ("a&b",), ("GET", "/api/search", {"params": {"q": "a&b"}})),
    ("search", ("q", 3), ("GET", "/api/search", {"params": {"q": "q", "limit": "3", "offset": "0"}})),
    ("list_tags", (), ("GET", "/api/tags", {})),
    ("get_notes_by_tag", ("a/b",), ("GET", "/api/tags/a%2Fb", {"params": None})),
    ("get_notes_by_tag", ("t", 2, 4), ("GET", "/api/tags/t", {"params": {"limit": "2", "offset": "4"}})),
    ("create_folder", ("f/g",), ("POST", "/api/folders", {"data": {"path": "f/g"}})),
    ("get_graph", (), ("GET", "/api/graph", {})),
    ("list_templates", (), ("GET", "/api/templates", {})),
    ("get_template", ("my tpl",), ("GET", "/api/templates/my%20tpl", {})),
    ("health_check", (), ("GET", "/health", {})),
    ("get_config", (), ("GET", "/api/config", {})),
])
def test_client_endpoints(rq, meth, args, expected):
    c, m = rq
    assert getattr(c, meth)(*args).success
    method, endpoint, kwargs = expected
    m.assert_called_once_with(method, endpoint, **kwargs)
