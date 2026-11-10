"""White-box tests (W21, W22): link resolution in _Resolver, theme listing and CSS loading."""
from pathlib import Path

import pytest

from backend.note_index import _Resolver
from backend.themes import get_available_themes, get_theme_css

pytestmark = pytest.mark.unit


@pytest.fixture
def resolver():
    return _Resolver({
        "x.md",
        "a/x.md",
        "b/x.md",
        "a/only-a.md",
        "deep/down/unique.md",
        "Mixed Case.md",
    })


# W21: resolve_wikilink
@pytest.mark.parametrize("target,folder,expected", [
    ("x", "a", "a/x.md"),  # same folder wins over the root note and b/x.md
    ("x", "b", "b/x.md"),
    ("x", "", "x.md"),  # no source folder: root
    ("x.md", "a", "a/x.md"),  # with extension
    ("X", "a", "a/x.md"),  # case-insensitive inside the source folder
    ("X.MD", "a", "a/x.md"),
    ("  x  ", "a", "a/x.md"),  # whitespace trimmed
    ("x", "c", "x.md"),  # nothing in the source folder: falls back to the root
    ("b/x", "a", "b/x.md"),  # a slash means vault-relative, so the same-folder step is skipped
    ("b/x.md", "a", "b/x.md"),
    ("B/X", "a", "b/x.md"),
    ("deep/down/unique", "a", "deep/down/unique.md"),
    ("unique", "a", "deep/down/unique.md"),  # bare name found anywhere
    ("UNIQUE", "", "deep/down/unique.md"),
    ("unique.md", "", "deep/down/unique.md"),
    ("mixed case", "", "Mixed Case.md"),
])
def test_wikilink_resolution(resolver, target, folder, expected):
    assert resolver.resolve_wikilink(target, folder) == expected


@pytest.mark.parametrize("target", ["", "   ", "missing", "a/missing", "nowhere/x"])
def test_wikilink_unresolved(resolver, target):
    assert resolver.resolve_wikilink(target, "a") is None


def test_wikilink_bare_name_ambiguous_returns_one_of_the_candidates():
    # TEST_PLAN expected "shortest path", but there is no such logic: note_names is
    # filled from a set, so which note wins is arbitrary.
    resolver = _Resolver({"p/same.md", "q/r/same.md"})
    assert resolver.resolve_wikilink("same", "") in {"p/same.md", "q/r/same.md"}


def test_wikilink_exact_non_md_entry_gets_md_suffix():
    # Pins current behaviour: an exact match on a non-.md entry returns a path that
    # is not in the set. The index only passes notes in, so it stays harmless.
    resolver = _Resolver({"a/pic.png", "pic.png"})
    assert resolver.resolve_wikilink("pic.png", "a") == "a/pic.png.md"
    assert resolver.resolve_wikilink("pic.png", "") == "pic.png.md"


# W21: resolve_mdlink
@pytest.mark.parametrize("link,folder,expected", [
    ("x.md", "a", "a/x.md"),  # relative to the source folder first
    ("x", "a", "a/x.md"),  # no extension
    ("X.MD", "a", "a/x.md"),  # case-insensitive
    ("X", "a", "a/x.md"),
    ("./x.md", "a", "a/x.md"),
    ("x.md#heading", "a", "a/x.md"),  # anchor dropped
    ("x#heading", "b", "b/x.md"),
    ("x.md", "", "x.md"),  # no source folder
    ("x", "", "x.md"),
    ("x.md", "c", "x.md"),  # nothing in the source folder: vault-relative
    ("b/x.md", "a", "b/x.md"),
    ("b/x", "a", "b/x.md"),
    ("B/X.md", "a", "b/x.md"),
    ("B/X", "a", "b/x.md"),
    ("Mixed%20Case.md", "", "Mixed Case.md"),  # URL-encoded
    ("Mixed%20Case", "c", "Mixed Case.md"),
])
def test_mdlink_resolution(resolver, link, folder, expected):
    assert resolver.resolve_mdlink(link, folder) == expected


@pytest.mark.parametrize("link", ["", "#only-anchor", "missing.md", "unique.md", "nowhere/x.md"])
def test_mdlink_unresolved(resolver, link):
    # "unique.md" lives elsewhere: unlike wikilinks, markdown links never match by bare name.
    assert resolver.resolve_mdlink(link, "a") is None


def test_mdlink_exact_non_md_entry_gets_md_suffix():
    # Same pinned behaviour as the wikilink case above.
    resolver = _Resolver({"a/pic.png", "pic.png"})
    assert resolver.resolve_mdlink("pic.png", "a") == "a/pic.png.md"
    assert resolver.resolve_mdlink("pic.png", "") == "pic.png.md"


def test_mdlink_leading_slash_is_not_vault_root():
    # Pins current behaviour: "/x.md" skips the same-folder step, then matches nothing.
    assert _Resolver({"x.md"}).resolve_mdlink("/x.md", "a") is None


@pytest.mark.xfail(strict=True, reason="DEFECT: resolve_mdlink never normalises '..', "
                                       "so ordinary relative links to a parent folder are lost")
@pytest.mark.parametrize("link,folder,expected", [
    ("../x.md", "a", "x.md"),
    ("../b/x.md", "a", "b/x.md"),
])
def test_mdlink_parent_relative(resolver, link, folder, expected):
    assert resolver.resolve_mdlink(link, folder) == expected


# W22: get_available_themes
@pytest.fixture
def themes_dir(tmp_path):
    path = tmp_path / "themes"
    path.mkdir()
    return path


def test_themes_missing_dir(tmp_path):
    assert get_available_themes(str(tmp_path / "gone")) == []


def test_themes_empty_dir(themes_dir):
    assert get_available_themes(str(themes_dir)) == []


def test_themes_non_css_ignored(themes_dir):
    (themes_dir / "keep.css").write_text("")
    (themes_dir / "readme.txt").write_text("")
    (themes_dir / "skip.css.bak").write_text("")
    assert [t["id"] for t in get_available_themes(str(themes_dir))] == ["keep"]


def test_themes_contains_every_css_file(themes_dir):
    # TEST_PLAN expected a sorted list, but the function keeps glob order, which depends
    # on the filesystem. Compared as a set so the test does not depend on that order.
    for name in ("zeta", "alpha", "mid"):
        (themes_dir / f"{name}.css").write_text("")
    assert {t["id"] for t in get_available_themes(str(themes_dir))} == {"zeta", "alpha", "mid"}


def test_themes_known_id_has_its_icon_and_metadata_type(themes_dir):
    (themes_dir / "dracula.css").write_text("/* @theme-type: light */")
    assert get_available_themes(str(themes_dir)) == [
        {"id": "dracula", "name": "🧛 Dracula", "type": "light", "builtin": False},
    ]


def test_themes_unknown_id_gets_default_icon_and_title_case_name(themes_dir):
    (themes_dir / "my-cool_theme.css").write_text("")
    assert get_available_themes(str(themes_dir)) == [
        {"id": "my-cool_theme", "name": "🎨 My Cool Theme", "type": "dark", "builtin": False},
    ]


# W22: get_theme_css
def test_theme_css_returned(themes_dir):
    (themes_dir / "my-theme_1.css").write_text("body { color: red; }", encoding="utf-8")
    assert get_theme_css(str(themes_dir), "my-theme_1") == "body { color: red; }"


def test_theme_css_unknown_id(themes_dir):
    assert get_theme_css(str(themes_dir), "gone") == ""


@pytest.mark.parametrize("theme_id", ["../secret", "..\\secret", "a/b", "a.b", "", "with space", "x\n"])
def test_theme_css_invalid_id_refused(tmp_path, themes_dir, theme_id):
    (tmp_path / "secret.css").write_text("secret")
    (themes_dir / "a.css").write_text("x")
    assert get_theme_css(str(themes_dir), theme_id) == ""


def test_theme_css_resolved_path_outside_dir_refused_mocked(themes_dir, monkeypatch):
    # Same branch as the symlink test below, for machines that cannot create symlinks.
    (themes_dir / "linked.css").write_text("secret")
    monkeypatch.setattr(Path, "is_relative_to", lambda self, other: False)
    assert get_theme_css(str(themes_dir), "linked") == ""


def test_theme_css_resolved_path_outside_dir_refused(tmp_path, themes_dir):
    # The id passes the character check, but the file is a link that points elsewhere.
    secret = tmp_path / "secret.css"
    secret.write_text("secret")
    link = themes_dir / "linked.css"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"symlinks not available: {e}")
    assert get_theme_css(str(themes_dir), "linked") == ""
