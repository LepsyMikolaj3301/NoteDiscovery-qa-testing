"""White-box tests (W27, W28): share token store and path retargeting.

create and revoke prune tokens whose note file is gone, so a token only survives
when its note really exists: tests create the file first through `note`.
"""
import json
from unittest.mock import Mock

import pytest

from backend import share
from backend.share import (
    ShareSlugError,
    _find_conflicting_token,
    _normalize_storage_path,
    _retarget_note_path,
    create_share_token,
    delete_token_for_note,
    get_all_shared_paths,
    get_note_by_token,
    get_share_info,
    get_share_token,
    get_tokens_file_path,
    is_slug_available,
    load_tokens,
    prune_inaccessible_share_tokens,
    revoke_share_token,
    save_tokens,
    update_token_path,
    update_token_prefix,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def data_dir(tmp_path):
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def note(data_dir):
    """note("a/n.md") creates the file and returns the relative path."""
    def _make(rel="n.md"):
        path = data_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
        return rel
    return _make


def store(data_dir, tokens):
    """Writes tokens directly, bypassing the code under test."""
    (data_dir / ".share-tokens.json").write_text(json.dumps(tokens), encoding="utf-8")


def stored(data_dir):
    return json.loads((data_dir / ".share-tokens.json").read_text(encoding="utf-8"))


# W27: get_tokens_file_path, load_tokens, save_tokens
def test_tokens_file_path(data_dir):
    assert get_tokens_file_path(str(data_dir)) == data_dir / ".share-tokens.json"


def test_load_tokens_missing_file(data_dir):
    assert load_tokens(str(data_dir)) == {}


def test_load_tokens_corrupt_json(data_dir):
    (data_dir / ".share-tokens.json").write_text("{not json", encoding="utf-8")
    assert load_tokens(str(data_dir)) == {}


def test_load_tokens_read_error(data_dir, monkeypatch):
    store(data_dir, {})
    monkeypatch.setattr(share, "open", Mock(side_effect=OSError("denied")), raising=False)
    assert load_tokens(str(data_dir)) == {}


@pytest.mark.xfail(strict=True, reason="DEFECT: valid JSON that is not an object is returned as is, "
                                       "so every caller's .items() raises AttributeError")
def test_load_tokens_json_not_an_object(data_dir):
    (data_dir / ".share-tokens.json").write_text("[1, 2]", encoding="utf-8")
    assert load_tokens(str(data_dir)) == {}


@pytest.mark.xfail(strict=True, reason="DEFECT: UnicodeDecodeError is neither JSONDecodeError nor IOError, "
                                       "so a tokens file that is not UTF-8 raises instead of giving {}")
def test_load_tokens_not_utf8(data_dir):
    (data_dir / ".share-tokens.json").write_bytes(b"\xff\xfe\xff")
    assert load_tokens(str(data_dir)) == {}


def test_save_tokens_round_trip_keeps_unicode(data_dir):
    tokens = {"tok": {"path": "zażółć.md"}}
    assert save_tokens(str(data_dir), tokens) is True
    assert load_tokens(str(data_dir)) == tokens
    assert "zażółć" in (data_dir / ".share-tokens.json").read_text(encoding="utf-8")


def test_save_tokens_creates_parent_dir(tmp_path):
    target = tmp_path / "not" / "yet"
    assert save_tokens(str(target), {}) is True
    assert (target / ".share-tokens.json").exists()


def test_save_tokens_write_failure(data_dir, monkeypatch):
    monkeypatch.setattr(share, "open", Mock(side_effect=OSError("disk full")), raising=False)
    assert save_tokens(str(data_dir), {}) is False


# W27: create_share_token
def test_create_random_token_stored(data_dir, note):
    rel = note()
    token = create_share_token(str(data_dir), rel, theme="dark")
    assert len(token) == 16
    entry = stored(data_dir)[token]
    assert (entry["path"], entry["theme"]) == (rel, "dark")
    assert entry["created"].endswith("+00:00")  # timezone-aware UTC


def test_create_default_theme_is_light(data_dir, note):
    token = create_share_token(str(data_dir), note())
    assert stored(data_dir)[token]["theme"] == "light"


def test_create_existing_token_reused_and_entry_untouched(data_dir, note):
    rel = note()
    token = create_share_token(str(data_dir), rel, theme="dark")
    before = stored(data_dir)
    assert create_share_token(str(data_dir), rel, theme="light") == token
    assert stored(data_dir) == before


def test_create_random_token_retries_on_collision(data_dir, note, monkeypatch):
    first = create_share_token(str(data_dir), note("a.md"), slug="dup-token")
    monkeypatch.setattr(share, "generate_token", Mock(side_effect=[first, "fresh-token"]))
    assert create_share_token(str(data_dir), note("b.md")) == "fresh-token"


def test_create_custom_slug(data_dir, note):
    rel = note()
    assert create_share_token(str(data_dir), rel, slug="my-slug") == "my-slug"
    assert stored(data_dir)["my-slug"]["path"] == rel


def test_create_slug_is_trimmed(data_dir, note):
    assert create_share_token(str(data_dir), note(), slug="  my-slug  ") == "my-slug"


@pytest.mark.parametrize("slug,reason", [
    ("ab", "too_short"),
    ("a" * 65, "too_long"),
    ("has space", "invalid_chars"),
    (123, "invalid_chars"),
])
def test_create_invalid_slug_rejected_before_writing(data_dir, note, slug, reason):
    with pytest.raises(ShareSlugError) as error:
        create_share_token(str(data_dir), note(), slug=slug)
    assert error.value.reason == reason
    assert not (data_dir / ".share-tokens.json").exists()


@pytest.mark.parametrize("wanted", ["taken-slug", "TAKEN-SLUG"])
def test_create_slug_taken_by_another_note(data_dir, note, wanted):
    create_share_token(str(data_dir), note("a.md"), slug="taken-slug")
    with pytest.raises(ShareSlugError) as error:
        create_share_token(str(data_dir), note("b.md"), slug=wanted)
    assert error.value.reason == "taken"
    assert get_share_token(str(data_dir), "b.md") is None


def test_create_same_slug_on_shared_note_is_noop(data_dir, note):
    rel = note()
    create_share_token(str(data_dir), rel, slug="my-slug")
    before = stored(data_dir)
    assert create_share_token(str(data_dir), rel, slug="my-slug") == "my-slug"
    assert stored(data_dir) == before


def test_create_no_slug_on_shared_note_keeps_link(data_dir, note):
    rel = note()
    create_share_token(str(data_dir), rel, slug="my-slug")
    assert create_share_token(str(data_dir), rel) == "my-slug"


def test_create_new_slug_renames_link_and_retires_old_url(data_dir, note):
    rel = note()
    create_share_token(str(data_dir), rel, theme="dark", slug="old-slug")
    created = stored(data_dir)["old-slug"]["created"]

    assert create_share_token(str(data_dir), rel, theme="light", slug="new-slug") == "new-slug"

    tokens = stored(data_dir)
    assert set(tokens) == {"new-slug"}
    assert tokens["new-slug"] == {"path": rel, "theme": "dark", "created": created}
    assert get_note_by_token(str(data_dir), "old-slug") is None


def test_create_case_only_rename_of_own_slug_allowed(data_dir, note):
    rel = note()
    create_share_token(str(data_dir), rel, slug="my-slug")
    assert create_share_token(str(data_dir), rel, slug="MY-SLUG") == "MY-SLUG"
    assert set(stored(data_dir)) == {"MY-SLUG"}


def test_create_rename_to_slug_of_another_note_refused(data_dir, note):
    create_share_token(str(data_dir), note("a.md"), slug="slug-a")
    create_share_token(str(data_dir), note("b.md"), slug="slug-b")
    with pytest.raises(ShareSlugError) as error:
        create_share_token(str(data_dir), "b.md", slug="slug-a")
    assert error.value.reason == "taken"
    assert stored(data_dir)["slug-b"]["path"] == "b.md"


def test_create_save_failure_returns_none(data_dir, note, monkeypatch):
    rel = note()
    monkeypatch.setattr(share, "save_tokens", Mock(return_value=False))
    assert create_share_token(str(data_dir), rel) is None


def test_create_rename_save_failure_returns_none_and_keeps_old_link(data_dir, note, monkeypatch):
    rel = note()
    create_share_token(str(data_dir), rel, slug="old-slug")
    monkeypatch.setattr(share, "save_tokens", Mock(return_value=False))
    assert create_share_token(str(data_dir), rel, slug="new-slug") is None
    assert set(stored(data_dir)) == {"old-slug"}


def test_create_for_missing_note_returns_token_that_is_then_pruned(data_dir):
    # Pins current behaviour: the caller gets a token that no longer exists.
    token = create_share_token(str(data_dir), "ghost.md")
    assert token is not None
    assert get_note_by_token(str(data_dir), token) is None


def test_create_prunes_dead_tokens_of_other_notes(data_dir, note):
    store(data_dir, {"dead": {"path": "gone.md"}})
    create_share_token(str(data_dir), note())
    assert "dead" not in stored(data_dir)


# W27: get_share_token, get_note_by_token, get_share_info, get_all_shared_paths
def test_get_share_token(data_dir):
    store(data_dir, {"tok": {"path": "n.md"}})
    assert get_share_token(str(data_dir), "n.md") == "tok"
    assert get_share_token(str(data_dir), "other.md") is None


def test_get_note_by_token(data_dir):
    store(data_dir, {"tok": {"path": "n.md", "theme": "dark"}})
    assert get_note_by_token(str(data_dir), "tok") == {"path": "n.md", "theme": "dark"}
    assert get_note_by_token(str(data_dir), "nope") is None


def test_get_note_by_token_default_theme(data_dir):
    store(data_dir, {"tok": {"path": "n.md"}})
    assert get_note_by_token(str(data_dir), "tok")["theme"] == "light"


def test_get_note_by_token_is_case_sensitive(data_dir):
    store(data_dir, {"tok": {"path": "n.md"}})
    assert get_note_by_token(str(data_dir), "TOK") is None


def test_get_share_info_shared(data_dir):
    store(data_dir, {"tok": {"path": "n.md", "theme": "dark", "created": "2026"}})
    assert get_share_info(str(data_dir), "n.md") == {
        "token": "tok", "theme": "dark", "created": "2026", "shared": True,
    }


def test_get_share_info_finds_note_after_other_entries(data_dir):
    store(data_dir, {"first": {"path": "other.md"}, "tok": {"path": "n.md"}})
    assert get_share_info(str(data_dir), "n.md")["token"] == "tok"


def test_get_share_info_defaults(data_dir):
    store(data_dir, {"tok": {"path": "n.md"}})
    assert get_share_info(str(data_dir), "n.md") == {
        "token": "tok", "theme": "light", "created": None, "shared": True,
    }


def test_get_share_info_not_shared(data_dir):
    # The docstring promises None, but callers get this dict.
    assert get_share_info(str(data_dir), "n.md") == {"shared": False}


def test_get_all_shared_paths_skips_entries_without_path(data_dir):
    store(data_dir, {"a": {"path": "a.md"}, "b": {"path": "b/c.md"}, "c": {"theme": "x"}, "d": {"path": ""}})
    assert sorted(get_all_shared_paths(str(data_dir))) == ["a.md", "b/c.md"]


def test_get_all_shared_paths_empty(data_dir):
    assert get_all_shared_paths(str(data_dir)) == []


# W27: revoke_share_token, delete_token_for_note
def test_revoke_shared_note(data_dir, note):
    rel, other = note("a.md"), note("b.md")
    token = create_share_token(str(data_dir), rel)
    kept = create_share_token(str(data_dir), other)
    assert revoke_share_token(str(data_dir), rel) is True
    assert get_note_by_token(str(data_dir), token) is None
    assert set(stored(data_dir)) == {kept}


def test_revoke_not_shared_note(data_dir, note):
    token = create_share_token(str(data_dir), note("a.md"))
    assert revoke_share_token(str(data_dir), "b.md") is False
    assert set(stored(data_dir)) == {token}


def test_revoke_without_tokens_file(data_dir):
    assert revoke_share_token(str(data_dir), "n.md") is False


def test_revoke_not_shared_still_prunes_dead_tokens(data_dir):
    store(data_dir, {"dead": {"path": "gone.md"}})
    assert revoke_share_token(str(data_dir), "n.md") is False
    assert stored(data_dir) == {}


def test_revoke_save_failure_returns_false(data_dir, note, monkeypatch):
    rel = note()
    create_share_token(str(data_dir), rel)
    monkeypatch.setattr(share, "save_tokens", Mock(return_value=False))
    assert revoke_share_token(str(data_dir), rel) is False
    assert get_share_token(str(data_dir), rel) is not None


def test_delete_token_for_note_is_revoke(data_dir, note):
    rel = note()
    create_share_token(str(data_dir), rel)
    assert delete_token_for_note(str(data_dir), rel) is True
    assert delete_token_for_note(str(data_dir), rel) is False


# W28: _normalize_storage_path
@pytest.mark.parametrize("raw,expected", [
    ("a/b", "a/b"),
    ("a\\b\\c.md", "a/b/c.md"),
    ("/a/b/", "a/b"),
    ("\\a\\", "a"),
    ("", ""),
    ("/", ""),
])
def test_normalize_storage_path(raw, expected):
    assert _normalize_storage_path(raw) == expected


# W28: _retarget_note_path
@pytest.mark.parametrize("path,old,new,expected", [
    ("a", "a", "b", "b"),  # exact match
    ("a/n.md", "a", "b", "b/n.md"),
    ("a/deep/n.md", "a", "b/c", "b/c/deep/n.md"),
    ("a/n.md", "a", "", "n.md"),  # moved to the vault root
    ("ab/n.md", "a", "b", None),  # partial folder name must not match
    ("a-archive/n.md", "a", "b", None),
    ("x/a/n.md", "a", "b", None),  # prefix only at the start
    ("n.md", "a", "b", None),
])
def test_retarget_note_path(path, old, new, expected):
    assert _retarget_note_path(path, old, new) == expected


# W28: update_token_path
def test_update_token_path_moved(data_dir):
    store(data_dir, {"tok": {"path": "a.md", "theme": "dark"}, "other": {"path": "b.md"}})
    assert update_token_path(str(data_dir), "a.md", "new/a.md") is True
    assert stored(data_dir) == {"tok": {"path": "new/a.md", "theme": "dark"}, "other": {"path": "b.md"}}


def test_update_token_path_not_shared(data_dir):
    store(data_dir, {"tok": {"path": "a.md"}})
    assert update_token_path(str(data_dir), "other.md", "new.md") is False
    assert stored(data_dir) == {"tok": {"path": "a.md"}}


def test_update_token_path_save_failure(data_dir, monkeypatch):
    store(data_dir, {"tok": {"path": "a.md"}})
    monkeypatch.setattr(share, "save_tokens", Mock(return_value=False))
    assert update_token_path(str(data_dir), "a.md", "b.md") is False


# W28: update_token_prefix
def test_update_prefix_matches_folder_not_partial_name(data_dir):
    store(data_dir, {
        "in": {"path": "a/n.md", "theme": "dark"},
        "deep": {"path": "a/b/n.md"},
        "partial": {"path": "ab/n.md"},
        "root": {"path": "n.md"},
    })
    assert update_token_prefix(str(data_dir), "a", "z") == 2
    tokens = stored(data_dir)
    assert tokens["in"] == {"path": "z/n.md", "theme": "dark"}  # token and theme kept
    assert tokens["deep"]["path"] == "z/b/n.md"
    assert tokens["partial"]["path"] == "ab/n.md"
    assert tokens["root"]["path"] == "n.md"


@pytest.mark.parametrize("old,new", [
    ("a\\sub", "z\\new"),
    ("/a/sub/", "/z/new/"),
])
def test_update_prefix_arguments_normalised(data_dir, old, new):
    store(data_dir, {"tok": {"path": "a/sub/n.md"}})
    assert update_token_prefix(str(data_dir), old, new) == 1
    assert stored(data_dir)["tok"]["path"] == "z/new/n.md"


def test_update_prefix_stored_windows_separators_rewritten(data_dir):
    store(data_dir, {"tok": {"path": "a\\sub\\n.md"}})
    assert update_token_prefix(str(data_dir), "a", "z") == 1
    assert stored(data_dir)["tok"]["path"] == "z/sub/n.md"


def test_update_prefix_move_to_root(data_dir):
    store(data_dir, {"tok": {"path": "a/n.md"}})
    assert update_token_prefix(str(data_dir), "a", "") == 1
    assert stored(data_dir)["tok"]["path"] == "n.md"


@pytest.mark.parametrize("old,new", [("", "z"), ("/", "z"), ("a", "a"), ("a/", "\\a")])
def test_update_prefix_empty_or_unchanged_rewrites_nothing(data_dir, old, new):
    store(data_dir, {"tok": {"path": "a/n.md"}})
    assert update_token_prefix(str(data_dir), old, new) == 0
    assert stored(data_dir)["tok"]["path"] == "a/n.md"


@pytest.mark.parametrize("bad_path", [None, 123, "", ["a/n.md"]])
def test_update_prefix_skips_unusable_stored_paths(data_dir, bad_path):
    store(data_dir, {"bad": {"path": bad_path}, "good": {"path": "a/n.md"}})
    assert update_token_prefix(str(data_dir), "a", "z") == 1
    tokens = stored(data_dir)
    assert tokens["bad"]["path"] == bad_path
    assert tokens["good"]["path"] == "z/n.md"


def test_update_prefix_nothing_matches_does_not_write(data_dir, monkeypatch):
    store(data_dir, {"tok": {"path": "other/n.md"}})
    save = Mock()
    monkeypatch.setattr(share, "save_tokens", save)
    assert update_token_prefix(str(data_dir), "a", "z") == 0
    save.assert_not_called()


def test_update_prefix_save_failure_reports_zero(data_dir, monkeypatch):
    store(data_dir, {"tok": {"path": "a/n.md"}})
    monkeypatch.setattr(share, "save_tokens", Mock(return_value=False))
    assert update_token_prefix(str(data_dir), "a", "z") == 0


# W28: _find_conflicting_token, is_slug_available
TOKENS = {"My-Slug": {"path": "a.md"}, "other": {"path": "b.md"}}


@pytest.mark.parametrize("slug,note_path,expected", [
    ("free", None, None),
    ("My-Slug", None, "My-Slug"),
    ("my-slug", None, "My-Slug"),  # case-insensitive
    ("my-slug", "b.md", "My-Slug"),  # belongs to another note
    ("my-slug", "a.md", None),  # the note's own token is not a conflict
])
def test_find_conflicting_token(slug, note_path, expected):
    assert _find_conflicting_token(TOKENS, slug, note_path) == expected


def test_is_slug_available(data_dir):
    store(data_dir, TOKENS)
    assert is_slug_available(str(data_dir), "free") is True
    assert is_slug_available(str(data_dir), "MY-SLUG") is False
    assert is_slug_available(str(data_dir), "my-slug", "a.md") is True
    assert is_slug_available(str(data_dir), "my-slug", "b.md") is False


def test_is_slug_available_without_tokens_file(data_dir):
    assert is_slug_available(str(data_dir), "anything") is True


# W28: prune_inaccessible_share_tokens
def test_prune_no_tokens(data_dir):
    assert prune_inaccessible_share_tokens(str(data_dir)) == 0
    assert not (data_dir / ".share-tokens.json").exists()


def test_prune_keeps_accessible_tokens_without_rewriting(data_dir, note, monkeypatch):
    store(data_dir, {"ok": {"path": note("sub/n.md")}})
    save = Mock()
    monkeypatch.setattr(share, "save_tokens", save)
    assert prune_inaccessible_share_tokens(str(data_dir)) == 0
    save.assert_not_called()


def test_prune_removes_deleted_note(data_dir, note):
    store(data_dir, {"ok": {"path": note()}, "dead": {"path": "gone.md"}})
    assert prune_inaccessible_share_tokens(str(data_dir)) == 1
    assert set(stored(data_dir)) == {"ok"}


def test_prune_removes_token_outside_storage_root(tmp_path, data_dir):
    # The file exists, so only the boundary check can reject it.
    (tmp_path / "outside.md").write_text("x")
    store(data_dir, {"escape": {"path": "../outside.md"}, "abs": {"path": str(tmp_path / "outside.md")}})
    assert prune_inaccessible_share_tokens(str(data_dir)) == 2
    assert stored(data_dir) == {}


def test_prune_removes_token_pointing_at_directory(data_dir):
    (data_dir / "folder").mkdir()
    store(data_dir, {"dir": {"path": "folder"}})
    assert prune_inaccessible_share_tokens(str(data_dir)) == 1


@pytest.mark.parametrize("bad_path", [None, "", 123, ["n.md"], "bad\x00name.md"])
def test_prune_removes_unusable_paths(data_dir, bad_path):
    store(data_dir, {"bad": {"path": bad_path}})
    assert prune_inaccessible_share_tokens(str(data_dir)) == 1


def test_prune_removes_entry_without_path_key(data_dir):
    store(data_dir, {"bad": {"theme": "dark"}})
    assert prune_inaccessible_share_tokens(str(data_dir)) == 1


def test_prune_path_that_cannot_be_resolved(data_dir, monkeypatch):
    store(data_dir, {"tok": {"path": "n.md"}})
    monkeypatch.setattr(share.Path, "resolve", Mock(side_effect=OSError("too many links")))
    assert prune_inaccessible_share_tokens(str(data_dir)) == 1


def test_prune_save_failure_reports_zero(data_dir, monkeypatch):
    store(data_dir, {"dead": {"path": "gone.md"}})
    monkeypatch.setattr(share, "save_tokens", Mock(return_value=False))
    assert prune_inaccessible_share_tokens(str(data_dir)) == 0
