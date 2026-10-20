"""I15: note tools end to end (results checked on disk)."""
import os
import time

import pytest

from backend import utils

pytestmark = [pytest.mark.integration, pytest.mark.mcp]


def test_create_get_append_move_delete(call_tool, live_server):
    v = live_server.vault
    text, err = call_tool("create_note", path="zażółć/Łódź.md", content="# Zażółć\nbody")
    assert not err and "Note created" in text
    assert (v / "zażółć" / "Łódź.md").read_text(encoding="utf-8") == "# Zażółć\nbody"

    text, _ = call_tool("get_note", path="zażółć/Łódź.md")
    assert "body" in text and "Size:" in text

    call_tool("append_to_note", path="zażółć/Łódź.md", content="more")
    assert (v / "zażółć" / "Łódź.md").read_text(encoding="utf-8").endswith("body\n\nmore")
    call_tool("append_to_note", path="zażółć/Łódź.md", content="t", add_timestamp=True)
    assert "\n\n---\n\n**" in (v / "zażółć" / "Łódź.md").read_text(encoding="utf-8")

    text, _ = call_tool("move_note", old_path="zażółć/Łódź.md", new_path="moved/n.md")
    assert "moved" in text and (v / "moved" / "n.md").exists() and not (v / "zażółć" / "Łódź.md").exists()

    text, _ = call_tool("delete_note", path="moved/n.md")
    assert "deleted" in text and not (v / "moved" / "n.md").exists()


def test_create_folder(call_tool, live_server):
    text, _ = call_tool("create_folder", path="a/b")
    assert "Folder created" in text and (live_server.vault / "a" / "b").is_dir()


def test_list_notes_and_pagination(call_tool, make_note):
    for n in "abc":
        make_note(f"d/{n}.md", n)
    text, _ = call_tool("list_notes")
    assert "Found 3 note(s)" in text and "📁 d/" in text
    text, _ = call_tool("list_notes", limit=2)
    assert "(showing 2)" in text and "more notes available" in text


def test_get_recent_notes(call_tool, make_note, vault):
    now = time.time()
    for name, age_days in [("new.md", 1), ("newer.md", 0.5), ("old.md", 30)]:
        os.utime(make_note(name, "x"), (now - age_days * 86400,) * 2)
    utils._scan_cache_invalidate()
    text, _ = call_tool("get_recent_notes", days=7)
    assert "old.md" not in text and text.index("newer.md") < text.index("new.md")
    assert "No notes modified" in call_tool("get_recent_notes", days=0)[0]


def test_failures_are_reported_in_text(call_tool, make_note, live_server):
    make_note("Note.md", "x")
    assert "HTTP 404" in call_tool("get_note", path="missing.md")[0]
    assert "HTTP 409" in call_tool("create_note", path="note.md", content="y")[0]
    assert "HTTP 404" in call_tool("delete_note", path="missing.md")[0]
    assert (live_server.vault / "Note.md").read_text() == "x"


@pytest.mark.xfail(strict=True, reason="DEFECT: REST errors (404/409) reach the MCP client as plain "
                   "text, the tools/call result is not marked isError")
def test_failures_set_is_error(call_tool, make_note):
    make_note("Note.md", "x")
    assert call_tool("get_note", path="missing.md")[1] is True
    assert call_tool("create_note", path="note.md", content="y")[1] is True
    assert call_tool("delete_note", path="missing.md")[1] is True


@pytest.mark.parametrize("tool,args", [
    ("create_note", {"path": "../evil.md", "content": "x"}),
    ("get_note", {"path": "/etc/passwd"}),
    ("delete_note", {"path": "C:\\x.md"}),
    ("create_folder", {"path": "a/../../x"}),
])
def test_bad_paths_rejected_before_any_request(call_tool, live_server, tool, args):
    text, _ = call_tool(tool, **args)
    assert text.startswith("Error: path")
    assert not (live_server.vault.parent / "evil.md").exists()
