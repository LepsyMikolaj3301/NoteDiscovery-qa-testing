"""Black-box tests (B1-B10): utils / export / main helpers, spec only."""
import pytest

from backend import main
from backend.export import (
    attachment_content_disposition,
    generate_media_placeholder,
    parse_image_size_spec,
    strip_frontmatter,
)
from backend.utils import get_media_type, paginate, path_has_hash, sanitize_filename

pytestmark = pytest.mark.unit

ITEMS = list(range(10))


# B1
def test_paginate_no_limit_returns_all():
    r = paginate(ITEMS)
    assert r.items == ITEMS and r.total == 10 and not r.has_more


@pytest.mark.parametrize("limit,offset,expected,more", [
    (3, 0, [0, 1, 2], True),
    (3, 9, [9], False),
    (3, 10, [], False),    # offset == len
    (3, 99, [], False),    # offset > len
    (3, -5, [0, 1, 2], True),  # negative offset clamped
    (0, 0, [], True),
    (10, 0, ITEMS, False),
])
def test_paginate_bounds(limit, offset, expected, more):
    r = paginate(ITEMS, limit=limit, offset=offset)
    assert r.items == expected and r.has_more is more and r.total == 10


def test_paginate_sort_and_to_dict():
    r = paginate(["b", "c", "a"], limit=2, sort_key=lambda x: x, sort_reverse=True)
    assert r.items == ["c", "b"]
    d = r.to_dict()
    assert d["total"] == 3 and d["limit"] == 2 and d["has_more"] is True


# B2
@pytest.mark.parametrize("ch", list('\\/:*?"<>|') + ["\x00", "\x1f"])
def test_sanitize_filename_removes_forbidden(ch):
    out = sanitize_filename(f"a{ch}b.md")
    assert ch not in out and out.endswith(".md")


def test_sanitize_filename_keeps_unicode_and_edge_cases():
    assert sanitize_filename("zażółć.md") == "zażółć.md"
    assert sanitize_filename("") == ""
    assert sanitize_filename("noext") == "noext"


# B3
@pytest.mark.parametrize("name,kind", [
    ("a.png", "image"), ("a.JPG", "image"), ("a.mp3", "audio"),
    ("a.mp4", "video"), ("a.pdf", "document"),
    ("a.xyz", None), ("noext", None),
])
def test_get_media_type(name, kind):
    assert get_media_type(name) == kind


# B4
@pytest.mark.parametrize("path,expected", [
    ("a#b.md", True), ("dir#x/a.md", True), ("dir/a.md", False), ("", False),
])
def test_path_has_hash(path, expected):
    assert path_has_hash(path) is expected


# B5
@pytest.mark.parametrize("text,solo,expected", [
    ("caption", False, ("caption", None, None)),
    ("caption|100", False, ("caption", 100, None)),
    ("caption|100x200", False, ("caption", 100, 200)),
    ("100", True, ("", 100, None)),
    ("100x200", True, ("", 100, 200)),
    ("100", False, ("100", None, None)),
    ("c|0x200", False, ("c", None, 200)),
    ("", False, ("", None, None)),
])
def test_parse_image_size_spec(text, solo, expected):
    assert parse_image_size_spec(text, allow_solo=solo) == expected


def test_parse_image_size_spec_garbage_has_no_size():
    alt, w, h = parse_image_size_spec("cap|abc")
    assert (w, h) == (None, None)


# B6
def test_content_disposition_ascii():
    assert attachment_content_disposition("note.html") == 'attachment; filename="note.html"'


def test_content_disposition_non_ascii_and_special():
    assert "filename*=utf-8''" in attachment_content_disposition("zażółć.html")
    out = attachment_content_disposition('a";b.html')
    assert '"a";' not in out  # quote can't break out of the header


# B7
def test_strip_frontmatter():
    assert strip_frontmatter("---\ntags: a\n---\nbody") == "body"
    assert strip_frontmatter("body") == "body"
    unclosed = "---\ntags: a\nbody"
    assert strip_frontmatter(unclosed) == unclosed
    mid = "text\n---\nmore"
    assert strip_frontmatter(mid) == mid


# B8
@pytest.mark.parametrize("kind", ["audio", "video", "document"])
def test_media_placeholder_per_type(kind):
    assert "x.ext" in generate_media_placeholder(kind, "x.ext")


def test_media_placeholder_escapes_alt():
    out = generate_media_placeholder("audio", '<b>"x"</b>')
    assert "<b>" not in out and "&lt;b&gt;" in out and "&quot;" in out


# B9
@pytest.mark.parametrize("raw,expected", [
    (None, ""), ("", ""), ("  ", ""),
    ("http://example.com", "http://example.com"),
    ("https://example.com:8443/path?q=1", "https://example.com:8443"),
    ("ftp://example.com", ""),
    ("example.com", ""),
    ("http://", ""),
])
def test_normalize_share_public_origin(raw, expected):
    assert main._normalize_share_public_origin(raw) == expected


# B10
def test_safe_error_message(monkeypatch):
    err = ValueError("secret")
    monkeypatch.setitem(main.config, "server", {"debug": False})
    assert main.safe_error_message(err, "oops") == "oops"
    monkeypatch.setitem(main.config, "server", {"debug": True})
    assert main.safe_error_message(err, "oops") == "ValueError: secret"
