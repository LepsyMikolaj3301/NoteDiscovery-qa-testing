"""Black-box tests (B21-B26): themes, auth helpers, open-task plugin, share slugs and tokens."""
import re

import bcrypt
import pytest

from backend import main
from backend.share import ShareSlugError, generate_token, validate_slug
from backend.themes import parse_theme_metadata
from plugins.contrib.search_open_tasks import Plugin as OpenTasksPlugin

pytestmark = pytest.mark.unit


# B21
@pytest.mark.parametrize("css,expected", [
    ("/* @theme-type: light */\nbody {}", "light"),
    ("/* @theme-type: dark */\nbody {}", "dark"),
    ("/* My theme */\n/* @theme-type: light */\nbody {}", "light"),  # not on line 1
    ("body {}", "dark"),  # no header: default
    ("", "dark"),  # empty file: default
    ("/* @theme-type: blue */", "dark"),  # invalid value: default
    ("\n" * 20 + "/* @theme-type: light */", "dark"),  # only the file header is read
])
def test_theme_type(tmp_path, css, expected):
    theme = tmp_path / "theme.css"
    theme.write_text(css, encoding="utf-8")
    assert parse_theme_metadata(theme) == {"type": expected}


def test_theme_missing_file_returns_default(tmp_path):
    assert parse_theme_metadata(tmp_path / "missing.css") == {"type": "dark"}


# B22
@pytest.fixture
def set_auth(monkeypatch):
    def _set(**settings):
        monkeypatch.setitem(main.config, "authentication", settings)
    return _set


@pytest.mark.parametrize("configured,provided,expected", [
    ("", "anything", False),  # no key configured
    ("", "", False),
    ("secret", "", False),  # empty provided key
    ("secret", "wrong", False),
    ("secret", "secre", False),  # prefix of the real key
    ("secret", "SECRET", False),  # case matters
    ("secret", "secret", True),
    ("sekret-ąę", "sekret-ąę", True),  # non-ASCII correct
    ("secret", "sekret-ąę", False),  # non-ASCII wrong must not raise
])
def test_verify_api_key(set_auth, configured, provided, expected):
    set_auth(api_key=configured)
    assert main.verify_api_key(provided) is expected


def test_verify_api_key_without_auth_settings(set_auth):
    set_auth()
    assert main.verify_api_key("anything") is False


# B23
def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=4)).decode()


@pytest.mark.parametrize("stored,provided,expected", [
    ("pass", "pass", True),
    ("pass", "wrong", False),
    ("pass", "", False),
    ("hasło-ąę", "hasło-ąę", True),  # non-ASCII correct
    ("pass", "hasło-ąę", False),  # non-ASCII wrong
])
def test_verify_password(set_auth, stored, provided, expected):
    set_auth(password_hash=_hash(stored))
    assert main.verify_password(provided) is expected


def test_verify_password_no_hash_configured(set_auth):
    set_auth(password_hash="")
    assert main.verify_password("pass") is False


@pytest.mark.parametrize("bad_hash", ["not-a-bcrypt-hash", "$2b$12$short"])
def test_verify_password_malformed_hash(set_auth, bad_hash):
    set_auth(password_hash=bad_hash)
    assert main.verify_password("pass") is False


# B24
def open_tasks(content: str) -> list:
    return OpenTasksPlugin()._open_tasks(content)


@pytest.mark.parametrize("line", [
    "- [ ] todo",
    "* [ ] todo",
    "+ [ ] todo",
    "1. [ ] todo",
    "1) [ ] todo",
    "  - [ ] todo",  # indented with spaces
    "\t- [ ] todo",  # indented with a tab
])
def test_open_task_found(line):
    tasks = open_tasks(line)
    assert len(tasks) == 1
    assert "todo" in tasks[0]["context"]


@pytest.mark.parametrize("content", [
    "",
    "plain text",
    "- plain list item",
    "- [x] done",
    "- [X] done",
    "- [] no space in the box",
    "- [ ]",  # box with no task text
    "[ ] no list marker",
])
def test_open_task_not_found(content):
    assert open_tasks(content) == []


def test_open_tasks_only_unchecked_with_line_numbers():
    tasks = open_tasks("# Title\n- [ ] first\n- [x] done\n\n* [ ] second\n")
    assert [t["line_number"] for t in tasks] == [2, 5]
    assert "first" in tasks[0]["context"]
    assert "second" in tasks[1]["context"]


def test_open_task_text_is_escaped():
    context = open_tasks("- [ ] <b>bold</b>")[0]["context"]
    assert "<b>" not in context
    assert "&lt;b&gt;bold&lt;/b&gt;" in context


@pytest.mark.parametrize("length,truncated", [(200, False), (201, True)])
def test_open_task_long_text_is_truncated(length, truncated):
    context = open_tasks("- [ ] " + "x" * length)[0]["context"]
    assert ("x" * 201 in context) is False
    assert ("x" * 200 in context) is True
    assert ("…" in context) is truncated


# B25
@pytest.mark.parametrize("slug", ["abc", "a" * 64, "My_note-1", "ABC", "123"])
def test_slug_valid_returned_unchanged(slug):
    assert validate_slug(slug) == slug


@pytest.mark.parametrize("slug,expected", [
    ("  abc  ", "abc"),
    ("\tabc\n", "abc"),
    (" " + "a" * 64 + " ", "a" * 64),  # length is measured after trimming
])
def test_slug_whitespace_trimmed(slug, expected):
    assert validate_slug(slug) == expected


@pytest.mark.parametrize("slug,reason", [
    (None, "invalid_chars"),
    (123, "invalid_chars"),
    (["abc"], "invalid_chars"),
    ("", "too_short"),
    ("   ", "too_short"),
    ("ab", "too_short"),
    ("  ab  ", "too_short"),  # length is measured after trimming
    ("a" * 65, "too_long"),
    ("a b", "invalid_chars"),
    ("a/b", "invalid_chars"),
    ("a\\b", "invalid_chars"),
    ("a.b", "invalid_chars"),
    ("a?b", "invalid_chars"),
    ("ąbc", "invalid_chars"),
    ("ab\ncd", "invalid_chars"),
])
def test_slug_rejected(slug, reason):
    with pytest.raises(ShareSlugError) as exc:
        validate_slug(slug)
    assert exc.value.reason == reason


# B26
def test_token_default_length():
    assert len(generate_token()) == 16


@pytest.mark.parametrize("length", [0, 1, 32])
def test_token_requested_length(length):
    assert len(generate_token(length)) == length


def test_token_is_url_safe():
    assert re.fullmatch(r"[A-Za-z0-9_-]+", generate_token(500))


def test_tokens_differ():
    assert generate_token(32) != generate_token(32)
