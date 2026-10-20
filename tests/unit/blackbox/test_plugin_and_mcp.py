"""Black-box tests (B11-B15): note_stats plugin and mcp_server pieces, spec only."""
import json

import pytest

from mcp_server.client import APIResponse
from mcp_server.config import MCPConfig, load_config
from mcp_server.server import MCPServer
from mcp_server.tools import TOOLS, get_tool_by_name, get_tool_names
from plugins.note_stats import Plugin

pytestmark = pytest.mark.unit

ENV = ("NOTEDISCOVERY_URL", "NOTEDISCOVERY_API_KEY", "NOTEDISCOVERY_TIMEOUT", "NOTEDISCOVERY_MAX_RETRIES")


@pytest.fixture
def cfg(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


@pytest.fixture
def server():
    return MCPServer(MCPConfig("http://localhost:8000", None, 30.0, 3))


# B11
def test_stats_empty_note():
    s = Plugin().calculate_stats("")
    assert s["words"] == 0 and s["characters"] == 0 and s["tasks"]["total"] == 0
    assert s["reading_time_minutes"] >= 1


def test_stats_counts():
    text = "# Title\n\nhello world.\n\n- [x] done\n- [ ] todo\n\n```py\nx\n```\n[a](b.md) [[Wiki]] [e](http://x.com)\n"
    s = Plugin().calculate_stats(text)
    assert s["words"] == len(text.split())
    assert s["headings"]["h1"] == 1
    assert s["code_blocks"] == 1
    assert s["tasks"] == {"total": 2, "completed": 1, "pending": 1, "completion_rate": 50}
    assert s["links"] == 3 and s["internal_links"] == 2 and s["external_links"] == 1
    assert s["wikilinks"] == 1


def test_stats_reading_time_grows_with_words():
    p = Plugin()
    assert p.calculate_stats("w " * 2000)["reading_time_minutes"] > p.calculate_stats("w")["reading_time_minutes"]


# B12
def test_config_defaults(cfg):
    c = load_config()
    assert c.base_url == "http://localhost:8000" and c.api_key is None


def test_config_strips_trailing_slash(cfg):
    cfg.setenv("NOTEDISCOVERY_URL", "http://host:1234///")
    assert load_config().base_url == "http://host:1234"


@pytest.mark.parametrize("url", ["not a url", "ftp://host", "localhost:8000", ""])
def test_config_invalid_url(cfg, url):
    cfg.setenv("NOTEDISCOVERY_URL", url)
    with pytest.raises(ValueError):
        load_config()


def test_config_bad_numbers_fall_back(cfg):
    cfg.setenv("NOTEDISCOVERY_TIMEOUT", "abc")
    cfg.setenv("NOTEDISCOVERY_MAX_RETRIES", "x")
    c = load_config()
    assert c.timeout == 30.0 and c.max_retries == 3


def test_config_headers(cfg):
    assert "X-API-Key" not in load_config().headers
    cfg.setenv("NOTEDISCOVERY_API_KEY", "k")
    assert load_config().headers["X-API-Key"] == "k"


# B13
@pytest.mark.parametrize("path", ["", "../x.md", "a/../b.md", "/abs.md", "\\abs.md", "C:\\x.md", "a\x00.md"])
def test_validate_path_rejects(server, path):
    ok, msg = server._validate_path(path)
    assert ok is False and msg


def test_validate_path_accepts(server):
    assert server._validate_path("folder/note.md")[0] is True


# B14
def test_format_response(server):
    assert server._format_response(APIResponse(False, None, "boom")) == "Error: boom"
    assert server._format_response(APIResponse(True, None)) == "Success (no data)"
    out = server._format_response(APIResponse(True, {"t": "zażółć"}))
    assert "zażółć" in out and json.loads(out) == {"t": "zażółć"}


# B15
def test_tool_lookup():
    assert get_tool_by_name("nope") is None
    assert get_tool_by_name(get_tool_names()[0]) is not None


def test_tool_schema_sanity(server):
    names = get_tool_names()
    assert len(names) == len(set(names))
    for t in TOOLS:
        assert t["name"] and t["description"] and isinstance(t["inputSchema"], dict)
        assert hasattr(server, f"_tool_{t['name']}"), t["name"]
