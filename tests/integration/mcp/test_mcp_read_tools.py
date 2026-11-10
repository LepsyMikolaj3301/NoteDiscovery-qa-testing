"""I16: read tools end to end."""
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.mcp]


@pytest.fixture
def seeded(make_note):
    make_note("a.md", "---\ntags: [alpha]\n---\nSee [[b]] and findme here")
    make_note("b.md", "---\ntags: [alpha]\n---\nplain")


def test_search(call_tool, seeded):
    text, _ = call_tool("search_notes", query="findme")
    assert "Found 1 result(s)" in text and "a.md" in text and "findme" in text and "<mark" not in text
    assert "No notes found matching 'zzz'" in call_tool("search_notes", query="zzz")[0]


def test_tags(call_tool, seeded):
    assert "#alpha (2 notes)" in call_tool("list_tags")[0]
    text, _ = call_tool("get_notes_by_tag", tag="alpha")
    assert "a.md" in text and "b.md" in text
    assert "more notes available" in call_tool("get_notes_by_tag", tag="alpha", limit=1)[0]
    assert "No notes found with tag '#nope'" in call_tool("get_notes_by_tag", tag="nope")[0]


def test_graph_and_backlinks(call_tool, seeded):
    assert "2 nodes, 1 connections" in call_tool("get_graph")[0]
    text, _ = call_tool("get_backlinks", path="b.md")
    assert "1 note(s) link to this note" in text and "a.md" in text
    assert "No backlinks found" in call_tool("get_backlinks", path="a.md")[0]


def test_templates(call_tool, make_note, live_server):
    assert "No templates available" in call_tool("list_templates")[0]
    make_note("_templates/Plan.md", "# {{title}}")
    assert "Plan" in call_tool("list_templates")[0]
    assert "# {{title}}" in call_tool("get_template", name="Plan")[0]
    assert "HTTP 404" in call_tool("get_template", name="Nope")[0]

    text, _ = call_tool("create_note_from_template", template_name="Plan", note_path="x/Roadmap.md")
    assert "Note created from template" in text
    assert (live_server.vault / "x" / "Roadmap.md").read_text() == "# Roadmap"


def test_health_and_config(call_tool):
    assert "is healthy" in call_tool("health_check")[0]
    text, _ = call_tool("get_config")
    assert "NoteDiscovery Server Configuration" in text and "  name:" in text
