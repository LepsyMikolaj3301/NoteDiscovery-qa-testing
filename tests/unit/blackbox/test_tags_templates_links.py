"""Black-box tests (B16-B20): tags, templates, wikilinks, link and term extraction."""
from datetime import datetime

import pytest

from backend import utils
from backend.export import convert_wikilinks_to_html
from backend.note_index import extract_links_from_content, extract_search_terms
from backend.utils import apply_template_placeholders, parse_tags

pytestmark = pytest.mark.unit


def fm(*lines: str) -> str:
    """Wrap lines in a frontmatter block followed by a body."""
    return "\n".join(["---", *lines, "---", "body"])


# B16
@pytest.mark.parametrize("content,expected", [
    (fm("tags: [python, tutorial]"), ["python", "tutorial"]),  # inline list
    (fm("tags:", "  - python", "  - tutorial"), ["python", "tutorial"]),  # YAML list
    (fm("tags: python"), ["python"]),  # single value
    (fm("tags: [a, a, b]"), ["a", "b"]),  # duplicates, inline
    (fm("tags:", "  - a", "  - a"), ["a"]),  # duplicates, YAML list
    (fm("tags: [Python, PYTHON]"), ["python"]),  # duplicates differing only in case
    (fm("tags: [Python]"), ["python"]),  # case normalised
    (fm("tags: [project/backend]"), ["project/backend"]),  # nested tag kept whole
    (fm("title: x", "tags: [a]"), ["a"]),  # tags is not the first key
    (fm("tags:", "  - a", "title: x", "  - b"), ["a"]),  # next key ends the list
])
def test_parse_tags_extracts(content, expected):
    assert parse_tags(content) == expected


@pytest.mark.parametrize("content", [
    "",
    "no frontmatter",
    "tags: [a]",  # looks like a tag line, but is not in frontmatter
    "---\ntags: [a]\nbody without a closing marker",
    fm("title: x"),  # frontmatter without tags
    fm("tags: []"),
    fm("tags:"),
])
def test_parse_tags_returns_empty(content):
    assert parse_tags(content) == []


def test_parse_tags_crlf():
    assert parse_tags("---\r\ntags: [a, b]\r\n---\r\nbody") == ["a", "b"]


@pytest.mark.xfail(strict=True, reason="Bug: YAML quotes are kept as part of the tag name")
@pytest.mark.parametrize("line", ['tags: ["a", "b"]', "tags: ['a', 'b']"])
def test_parse_tags_quoted_values(line):
    assert parse_tags(fm(line)) == ["a", "b"]


# B17
class _Frozen(datetime):
    """datetime with a fixed now(): Wednesday 2026-05-06 15:42:07."""

    @classmethod
    def now(cls, tz=None):
        return cls(2026, 5, 6, 15, 42, 7)


@pytest.fixture
def frozen_time(monkeypatch):
    monkeypatch.setattr(utils, "datetime", _Frozen)
    return _Frozen.now()


@pytest.mark.parametrize("template,expected", [
    ("{{date}}", "2026-05-06"),
    ("{{time}}", "15:42:07"),
    ("{{datetime}}", "2026-05-06 15:42:07"),
    ("{{year}}", "2026"),
    ("{{month}}", "05"),
    ("{{day}}", "06"),
    ("{{title}}", "Note"),
    ("{{folder}}", "sub"),
    ("# {{title}} on {{date}}", "# Note on 2026-05-06"),  # several in one template
    ("{{day}} {{day}}", "06 06"),  # repeated
    ("no placeholders", "no placeholders"),
    ("", ""),
])
def test_template_named_placeholders(frozen_time, template, expected):
    assert apply_template_placeholders(template, "projects/sub/Note.md") == expected


def test_template_timestamp(frozen_time):
    expected = str(int(frozen_time.timestamp()))
    assert apply_template_placeholders("{{timestamp}}", "Note.md") == expected


@pytest.mark.parametrize("note_path,expected", [
    ("Note.md", "Root"),
    ("a/Note.md", "a"),
    ("a/b/Note.md", "b"),  # immediate parent, not the whole path
])
def test_template_folder(frozen_time, note_path, expected):
    assert apply_template_placeholders("{{folder}}", note_path) == expected


@pytest.mark.parametrize("template,expected", [
    ("{{datetime:%Y%m%d%H%M%S}}", "20260506154207"),
    ("{{date:%d/%m/%Y}}", "06/05/2026"),
    ("{{date:%A}}", "Wednesday"),
    ("{{time:%H%M%S}}", "154207"),
    ("{{date:%V}}", "19"),
])
def test_template_custom_format(frozen_time, template, expected):
    assert apply_template_placeholders(template, "Note.md") == expected


def test_template_invalid_format_left_unchanged(monkeypatch):
    # Whether strftime rejects a format depends on the OS, so the rejection is forced.
    class Rejecting(_Frozen):
        def strftime(self, fmt):
            if fmt == "%Q":
                raise ValueError("Invalid format string")
            return super().strftime(fmt)

    monkeypatch.setattr(utils, "datetime", Rejecting)
    assert apply_template_placeholders("a {{date:%Q}} b", "Note.md") == "a {{date:%Q}} b"


@pytest.mark.parametrize("template", ["{{foo}}", "{{ date }}", "{date}", "{{foo:%Y}}"])
def test_template_unknown_placeholder_untouched(frozen_time, template):
    assert apply_template_placeholders(template, "Note.md") == template


# B18
def test_wikilink_plain():
    html = convert_wikilinks_to_html("see [[Note]] here")
    assert html.startswith("see <a ") and html.endswith("</a> here")
    assert 'class="wikilink"' in html
    assert 'title="Note"' in html
    assert ">Note</a>" in html
    assert "[[" not in html


def test_wikilink_alias():
    html = convert_wikilinks_to_html("[[Note|shown text]]")
    assert 'title="Note"' in html
    assert ">shown text</a>" in html


def test_wikilink_heading():
    html = convert_wikilinks_to_html("[[Note#Section]]")
    assert 'title="Note#Section"' in html
    assert ">Note#Section</a>" in html


def test_wikilink_surrounding_spaces_trimmed():
    html = convert_wikilinks_to_html("[[ Note | shown ]]")
    assert 'title="Note"' in html
    assert ">shown</a>" in html


def test_wikilink_several_in_one_text():
    assert convert_wikilinks_to_html("[[A]] and [[B]]").count("<a ") == 2


@pytest.mark.parametrize("text", [
    "no links at all",
    "",
    "![[image.png]]",  # image embed, not a link
    "`[[Note]]`",  # inline code
    "```\n[[Note]]\n```",  # fenced code
    "```python\nx = '[[Note]]'\n```",  # fenced code with a language
])
def test_wikilink_left_literal(text):
    assert convert_wikilinks_to_html(text) == text


def test_wikilink_converted_outside_code_only():
    html = convert_wikilinks_to_html("`[[Code]]` [[Real]]\n```\n[[Fenced]]\n```")
    assert "`[[Code]]`" in html
    assert "[[Fenced]]" in html
    assert 'title="Real"' in html
    assert html.count("<a ") == 1


def test_wikilink_html_is_escaped():
    html = convert_wikilinks_to_html('[[a"><script>x</script>|<b>bold</b>]]')
    assert "<script>" not in html
    assert "<b>" not in html
    assert "&lt;b&gt;bold&lt;/b&gt;" in html
    assert 'a&quot;&gt;' in html


# B19
@pytest.mark.parametrize("content,wikilinks,mdlinks", [
    ("", [], []),
    ("plain text", [], []),
    ("[[Note]]", ["Note"], []),
    ("[[A]] and [[B]]", ["A", "B"], []),
    ("[[Note|alias]]", ["Note"], []),  # alias is not the target
    ("[[  Note  ]]", ["Note"], []),  # whitespace trimmed
    ("[[Note#Section]]", ["Note#Section"], []),
    ("[text](other.md)", [], ["other.md"]),
    ("[text](sub/other.md)", [], ["sub/other.md"]),
    ("[[A]] [text](b.md)", ["A"], ["b.md"]),  # both kinds
])
def test_extract_links(content, wikilinks, mdlinks):
    assert extract_links_from_content(content) == {"wikilinks": wikilinks, "mdlinks": mdlinks}


@pytest.mark.parametrize("target", [
    "http://example.com",
    "https://example.com",
    "mailto:a@example.com",
    "#section",
    "data:text/plain,x",
])
def test_extract_links_ignores_non_note_targets(target):
    assert extract_links_from_content(f"[text]({target})")["mdlinks"] == []


# B20
@pytest.mark.parametrize("content,expected", [
    ("", set()),
    ("!!! ... ???", set()),  # punctuation only
    ("Hello WORLD", {"hello", "world"}),  # lowercased
    ("hello,world.again!", {"hello", "world", "again"}),  # punctuation splits
    ("line one\nline two", {"line", "one", "two"}),  # duplicates collapsed
    ("Word word WORD", {"word"}),  # duplicates differing only in case
    ("a b go", {"go"}),  # single characters are too short to index
    ("foo-bar foo_bar", {"foo-bar", "foo_bar"}),  # - and _ belong to the token
    ("v2 2026", {"v2", "2026"}),  # digits
])
def test_search_terms(content, expected):
    assert extract_search_terms(content) == expected


@pytest.mark.parametrize("content,expected", [
    ("café", {"caf"}),
    ("zażółć", {"za"}),
    ("żółć", set()),
])
def test_search_terms_only_ascii_runs_are_indexed(content, expected):
    # Pins current behaviour: non-ASCII letters end a token instead of being part
    # of it. Search for such words relies on the substring match and the full-scan
    # fallback, not on these terms.
    assert extract_search_terms(content) == expected
