"""White-box tests (W13-W20): vault scan and caches, path security, note/folder
operations, case-only name clashes, backlinks, templates, uploads, tags."""
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend import note_index, utils

pytestmark = pytest.mark.unit


def symlink_or_skip(link: Path, target: Path, is_dir: bool = False):
    """Creating symlinks needs a privilege on Windows, so skip when it is refused."""
    try:
        os.symlink(target, link, target_is_directory=is_dir)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"symlinks not available: {e}")


def paths_of(scan):
    notes, _folders = scan
    return {n["path"] for n in notes}


# W13: scan_notes_fast_walk
def test_scan_empty_vault(vault):
    assert utils.scan_notes_fast_walk(str(vault), use_cache=False) == ([], [])


def test_scan_skips_hidden_dirs_and_files(vault, make_note):
    make_note("visible.md")
    make_note("sub/nested.md")
    make_note(".git/hidden.md")
    make_note("sub/.obsidian/hidden.md")
    make_note(".dotfile.md")
    notes, folders = utils.scan_notes_fast_walk(str(vault), use_cache=False)
    assert {n["path"] for n in notes} == {"visible.md", "sub/nested.md"}
    assert folders == ["sub"]


def test_scan_note_record_shape(vault, make_note):
    make_note("sub/n.md", "---\ntags: [Alpha]\n---\nbody")
    notes, _ = utils.scan_notes_fast_walk(str(vault), use_cache=False)
    note = notes[0]
    assert (note["name"], note["folder"], note["type"], note["tags"]) == ("n", "sub", "note", ["alpha"])
    assert "_mtime" not in note  # internal field is removed before returning


def test_scan_root_note_has_empty_folder(vault, make_note):
    make_note("root.md")
    notes, _ = utils.scan_notes_fast_walk(str(vault), use_cache=False)
    assert notes[0]["folder"] == ""


def test_scan_media_excluded_by_default(vault, make_note):
    make_note("n.md")
    (vault / "pic.png").write_bytes(b"x")
    (vault / "other.txt").write_text("x")
    assert paths_of(utils.scan_notes_fast_walk(str(vault), use_cache=False)) == {"n.md"}


def test_scan_include_media_lists_media_but_not_unknown_files(vault, make_note):
    make_note("n.md")
    (vault / "pic.png").write_bytes(b"x")
    (vault / "drawing-1.png").write_bytes(b"x")
    (vault / "other.txt").write_text("x")
    notes, _ = utils.scan_notes_fast_walk(str(vault), use_cache=False, include_media=True)
    assert {n["path"]: n["type"] for n in notes} == {
        "n.md": "note", "pic.png": "image", "drawing-1.png": "drawing",
    }


def test_scan_cache_hit_returns_stale_result_until_bypassed(vault, make_note):
    make_note("first.md")
    assert paths_of(utils.scan_notes_fast_walk(str(vault))) == {"first.md"}
    (vault / "second.md").write_text("x")  # straight to disk: nothing invalidates the cache
    assert paths_of(utils.scan_notes_fast_walk(str(vault))) == {"first.md"}
    assert paths_of(utils.scan_notes_fast_walk(str(vault), use_cache=False)) == {"first.md", "second.md"}


def test_scan_use_cache_false_does_not_store(vault, make_note):
    make_note("first.md")
    utils.scan_notes_fast_walk(str(vault), use_cache=False)
    assert utils._scan_cache_get((str(vault.resolve()), False)) is None


def test_scan_notes_only_derived_from_media_cache_entry(vault, make_note):
    make_note("n.md")
    (vault / "pic.png").write_bytes(b"x")
    utils.scan_notes_fast_walk(str(vault), include_media=True)
    (vault / "later.md").write_text("x")  # must stay unseen: the walk is skipped

    notes, _ = utils.scan_notes_fast_walk(str(vault))

    assert [(n["path"], n["type"]) for n in notes] == [("n.md", "note")]
    assert utils._scan_cache_get((str(vault.resolve()), False)) == (notes, [])


def test_scan_skips_file_that_cannot_be_stat_ed(vault, make_note, monkeypatch):
    make_note("gone.md")
    make_note("kept.md")
    real_stat = Path.stat

    def stat(self, **kwargs):
        if self.name == "gone.md":
            raise OSError("vanished during the walk")
        return real_stat(self, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    assert paths_of(utils.scan_notes_fast_walk(str(vault), use_cache=False)) == {"kept.md"}


def test_scan_many_notes_uses_thread_pool_branch(vault, make_note):
    for i in range(50):
        make_note(f"n{i}.md", "---\ntags: [bulk]\n---\n")
    notes, _ = utils.scan_notes_fast_walk(str(vault), use_cache=False)
    assert len(notes) == 50
    assert all(n["tags"] == ["bulk"] for n in notes)


# W13: _scan_cache_get / _set / _invalidate
def test_scan_cache_get_unknown_key():
    assert utils._scan_cache_get(("nothing", False)) is None


def test_scan_cache_set_get_invalidate():
    key, value = ("k", True), ([], ["f"])
    utils._scan_cache_set(key, value)
    assert utils._scan_cache_get(key) == value
    utils._scan_cache_invalidate()
    assert utils._scan_cache_get(key) is None


def test_scan_cache_entry_expires_after_ttl(monkeypatch):
    key = ("k", True)
    utils._scan_cache_set(key, ([], []))
    monkeypatch.setattr(utils, "_SCAN_WALK_CACHE_TTL_SECONDS", -1.0)
    assert utils._scan_cache_get(key) is None
    assert key not in utils._SCAN_WALK_CACHE  # expired entry is evicted


# W13: ensure_index_built
def test_ensure_index_built_scans_when_not_built(vault, monkeypatch):
    scan = Mock()
    monkeypatch.setattr(utils, "scan_notes_fast_walk", scan)
    utils.ensure_index_built(str(vault))
    scan.assert_called_once_with(str(vault), use_cache=False, include_media=True)


def test_ensure_index_built_noop_when_built(vault, make_note, monkeypatch):
    make_note("n.md")
    utils.ensure_index_built(str(vault))
    assert note_index.get_index().is_built()
    scan = Mock()
    monkeypatch.setattr(utils, "scan_notes_fast_walk", scan)
    utils.ensure_index_built(str(vault))
    scan.assert_not_called()


# W13: get_tags_and_links_cached
def test_tags_links_read_from_file_then_cached(make_note):
    path = make_note("n.md", "---\ntags: [a]\n---\n[[Other]] [x](y.md)")
    tags, links = utils.get_tags_and_links_cached(path)
    assert tags == ["a"]
    assert links == {"wikilinks": ["Other"], "mdlinks": ["y.md"]}
    assert utils._tag_cache[str(path)][1] == ["a"]
    assert utils._links_cache[str(path)][1] == links


def test_tags_links_hit_when_mtime_unchanged(make_note):
    path = make_note("n.md", "---\ntags: [old]\n---\n")
    stat = path.stat()
    utils.get_tags_and_links_cached(path)
    path.write_text("---\ntags: [new]\n---\n", encoding="utf-8")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert utils.get_tags_and_links_cached(path)[0] == ["old"]


def test_tags_links_miss_when_mtime_changed(make_note):
    path = make_note("n.md", "---\ntags: [old]\n---\n")
    utils.get_tags_and_links_cached(path)
    path.write_text("---\ntags: [new]\n---\n", encoding="utf-8")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 10))
    assert utils.get_tags_and_links_cached(path)[0] == ["new"]


def test_tags_links_taken_from_index_when_rel_path_given(make_note, monkeypatch):
    path = make_note("n.md", "---\ntags: [from-file]\n---\n")
    indexed = (["from-index"], {"wikilinks": ["W"], "mdlinks": []})
    extraction = Mock(return_value=indexed)
    monkeypatch.setattr(note_index, "try_get_extraction", extraction)

    assert utils.get_tags_and_links_cached(path, "n.md") == indexed
    extraction.assert_called_once_with("n.md", path.stat().st_mtime)
    assert utils._tag_cache[str(path)][1] == ["from-index"]


def test_tags_links_index_miss_falls_back_to_file(make_note, monkeypatch):
    path = make_note("n.md", "---\ntags: [from-file]\n---\n")
    monkeypatch.setattr(note_index, "try_get_extraction", Mock(return_value=None))
    assert utils.get_tags_and_links_cached(path, "n.md")[0] == ["from-file"]


def test_tags_links_one_cache_valid_keeps_it_and_parses_the_other(make_note):
    path = make_note("n.md", "---\ntags: [real]\n---\n[[L]]")
    utils._tag_cache[str(path)] = (path.stat().st_mtime, ["cached"])
    tags, links = utils.get_tags_and_links_cached(path)
    assert tags == ["cached"]
    assert links["wikilinks"] == ["L"]


def test_tags_links_other_cache_valid_keeps_it_and_parses_tags(make_note):
    path = make_note("n.md", "---\ntags: [real]\n---\n[[L]]")
    cached = {"wikilinks": ["cached"], "mdlinks": []}
    utils._links_cache[str(path)] = (path.stat().st_mtime, cached)
    tags, links = utils.get_tags_and_links_cached(path)
    assert tags == ["real"]
    assert links == cached


def test_tags_links_missing_file_returns_empty(tmp_path):
    assert utils.get_tags_and_links_cached(tmp_path / "gone.md") == ([], {"wikilinks": [], "mdlinks": []})


# W14: validate_path_security
@pytest.mark.parametrize("rel,expected", [
    ("n.md", True),
    ("sub/deep/n.md", True),
    ("sub/../n.md", True),  # stays inside
    ("", True),  # the vault root itself
    ("../outside.md", False),
    ("sub/../../outside.md", False),
])
def test_validate_path_security(vault, rel, expected):
    assert utils.validate_path_security(str(vault), vault / rel) is expected


def test_validate_path_security_sibling_with_same_prefix(tmp_path, vault):
    # A plain startswith() check would accept notes-archive for a vault called notes.
    assert utils.validate_path_security(str(vault), tmp_path / "notes-archive" / "n.md") is False


def test_validate_path_security_symlink_out_of_vault(tmp_path, vault):
    outside = tmp_path / "outside"
    outside.mkdir()
    symlink_or_skip(vault / "link", outside, is_dir=True)
    assert utils.validate_path_security(str(vault), vault / "link" / "n.md") is False


# W14: resolve_vault_folder
def test_resolve_folder_root(vault):
    assert utils.resolve_vault_folder(str(vault), "") == vault


def test_resolve_folder_existing_subfolder(vault):
    (vault / "a" / "b").mkdir(parents=True)
    assert utils.resolve_vault_folder(str(vault), "a/b") == vault / "a" / "b"


@pytest.mark.parametrize("folder", ["../", "..", "a/../..", ".git", "a/.hidden", "missing"])
def test_resolve_folder_refused(vault, folder):
    (vault / "a" / ".hidden").mkdir(parents=True)
    (vault / ".git").mkdir()
    assert utils.resolve_vault_folder(str(vault), folder) is None


def test_resolve_folder_absolute_path_outside(tmp_path, vault):
    outside = tmp_path / "outside"
    outside.mkdir()
    assert utils.resolve_vault_folder(str(vault), str(outside)) is None


def test_resolve_folder_file_is_not_a_folder(vault, make_note):
    make_note("n.md")
    assert utils.resolve_vault_folder(str(vault), "n.md") is None


def test_resolve_folder_symlink_refused(vault):
    (vault / "real").mkdir()
    symlink_or_skip(vault / "link", vault / "real", is_dir=True)
    assert utils.resolve_vault_folder(str(vault), "link") is None


def test_resolve_folder_symlink_refused_mocked(vault, monkeypatch):
    # Same branch as above, for machines that are not allowed to create symlinks.
    (vault / "link").mkdir()
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "link")
    assert utils.resolve_vault_folder(str(vault), "link") is None


# W14: collect_folder_files
def test_collect_files_skips_symlinks_mocked(vault, monkeypatch):
    (vault / "linked-dir").mkdir()
    (vault / "linked-dir" / "inner.md").write_text("x")
    (vault / "linked-file.md").write_text("x")
    (vault / "kept.md").write_text("x")
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name.startswith("linked"))
    files, _ = utils.collect_folder_files(str(vault), vault)
    assert [rel for _, rel in files] == ["kept.md"]


def test_collect_files_stat_failure_skips_file(vault, monkeypatch):
    (vault / "n.md").write_text("x")
    # is_symlink() and is_file() call stat() as well, so they are fixed to reach the size lookup.
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(Path, "stat", Mock(side_effect=OSError("gone")))
    assert utils.collect_folder_files(str(vault), vault) == ([], 0)


def test_collect_files_recursive_relative_paths_and_bytes(vault):
    (vault / "a" / "b").mkdir(parents=True)
    (vault / "a" / "one.md").write_bytes(b"12345")
    (vault / "a" / "b" / "pic.png").write_bytes(b"123")

    files, total = utils.collect_folder_files(str(vault), vault / "a")

    assert [rel for _, rel in files] == ["one.md", "b/pic.png"]
    assert [p for p, _ in files] == [vault / "a" / "one.md", vault / "a" / "b" / "pic.png"]
    assert total == 8


def test_collect_files_skips_dot_entries(vault):
    (vault / ".git").mkdir()
    (vault / ".git" / "config").write_text("x")
    (vault / ".hidden.md").write_text("x")
    (vault / "kept.md").write_text("x")
    files, total = utils.collect_folder_files(str(vault), vault)
    assert [rel for _, rel in files] == ["kept.md"]
    assert total == 1


def test_collect_files_skips_symlinks(tmp_path, vault):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("x")
    symlink_or_skip(vault / "linked-file.md", outside / "secret.md")
    symlink_or_skip(vault / "linked-dir", outside, is_dir=True)
    assert utils.collect_folder_files(str(vault), vault) == ([], 0)


def test_collect_files_empty_folder(vault):
    assert utils.collect_folder_files(str(vault), vault) == ([], 0)


def test_collect_files_drops_file_failing_security_check(vault, monkeypatch):
    (vault / "n.md").write_text("x")
    monkeypatch.setattr(utils, "validate_path_security", lambda *_: False)
    assert utils.collect_folder_files(str(vault), vault) == ([], 0)


# W15: ensure_directories, create_folder
def test_ensure_directories_creates_nested_and_tolerates_existing(tmp_path):
    config = {"storage": {"notes_dir": str(tmp_path / "x" / "notes"), "plugins_dir": str(tmp_path / "x" / "plugins")}}
    utils.ensure_directories(config)
    utils.ensure_directories(config)
    assert (tmp_path / "x" / "notes").is_dir() and (tmp_path / "x" / "plugins").is_dir()


def test_create_folder_nested_and_idempotent(vault):
    assert utils.create_folder(str(vault), "a/b/c") is True
    assert utils.create_folder(str(vault), "a/b/c") is True
    assert (vault / "a" / "b" / "c").is_dir()


def test_create_folder_traversal_refused(tmp_path, vault):
    assert utils.create_folder(str(vault), "../escaped") is False
    assert not (tmp_path / "escaped").exists()


def test_create_folder_invalidates_scan_cache(vault):
    utils.scan_notes_fast_walk(str(vault))
    utils.create_folder(str(vault), "new")
    assert utils.scan_notes_fast_walk(str(vault))[1] == ["new"]


# W15: save_note
def test_save_note_creates_parents(vault):
    assert utils.save_note(str(vault), "a/b/n.md", "hello") is True
    assert (vault / "a" / "b" / "n.md").read_text(encoding="utf-8") == "hello"


def test_save_note_appends_md(vault):
    assert utils.save_note(str(vault), "plain", "x") is True
    assert (vault / "plain.md").exists()


@pytest.mark.xfail(strict=True, reason="DEFECT: with_suffix() replaces the part after the last dot, "
                                       "so saving 'v1.2' writes 'v1.md' instead of 'v1.2.md'")
def test_save_note_name_with_dot_keeps_whole_name(vault):
    utils.save_note(str(vault), "v1.2", "x")
    assert (vault / "v1.2.md").exists()


def test_save_note_overwrites(vault):
    utils.save_note(str(vault), "n.md", "one")
    utils.save_note(str(vault), "n.md", "two")
    assert (vault / "n.md").read_text(encoding="utf-8") == "two"


def test_save_note_traversal_refused(tmp_path, vault):
    assert utils.save_note(str(vault), "../escaped.md", "x") is False
    assert not (tmp_path / "escaped.md").exists()


def test_save_note_refreshes_per_file_caches(vault):
    utils.save_note(str(vault), "n.md", "---\ntags: [t]\n---\n[[L]]")
    key = str(vault / "n.md")
    assert utils._tag_cache[key][1] == ["t"]
    assert utils._links_cache[key][1]["wikilinks"] == ["L"]


def test_save_note_survives_cache_refresh_failure(vault, monkeypatch):
    monkeypatch.setattr(utils, "parse_tags", Mock(side_effect=RuntimeError("boom")))
    assert utils.save_note(str(vault), "n.md", "x") is True


def test_save_note_invalidates_scan_cache_and_updates_index(vault):
    utils.scan_notes_fast_walk(str(vault), include_media=True)
    utils.save_note(str(vault), "n.md", "x")
    assert paths_of(utils.scan_notes_fast_walk(str(vault))) == {"n.md"}
    assert note_index.get_index().get_note_record("n.md") is not None


# W15: get_note_content
def test_get_note_content_ok(make_note, vault):
    make_note("sub/n.md", "zażółć")
    assert utils.get_note_content(str(vault), "sub/n.md") == "zażółć"


def test_get_note_content_missing_or_directory(vault):
    (vault / "dir").mkdir()
    assert utils.get_note_content(str(vault), "gone.md") is None
    assert utils.get_note_content(str(vault), "dir") is None


def test_get_note_content_traversal_refused(tmp_path, vault):
    (tmp_path / "secret.md").write_text("secret")
    assert utils.get_note_content(str(vault), "../secret.md") is None


# W15: delete_note
def test_delete_note_removes_file_and_drops_caches(vault):
    utils.save_note(str(vault), "n.md", "---\ntags: [t]\n---\n")
    utils.scan_notes_fast_walk(str(vault), use_cache=False)
    key = str(vault / "n.md")
    assert key in utils._tag_cache

    assert utils.delete_note(str(vault), "n.md") is True

    assert not (vault / "n.md").exists()
    assert key not in utils._tag_cache and key not in utils._links_cache
    assert note_index.get_index().get_note_record("n.md") is None


def test_delete_note_missing(vault):
    assert utils.delete_note(str(vault), "gone.md") is False


def test_delete_note_traversal_refused(tmp_path, vault):
    secret = tmp_path / "secret.md"
    secret.write_text("x")
    assert utils.delete_note(str(vault), "../secret.md") is False
    assert secret.exists()


# W15: move_note
def test_move_note_ok_creates_target_folder_and_updates_state(vault):
    utils.save_note(str(vault), "old.md", "---\ntags: [t]\n---\n")
    utils.scan_notes_fast_walk(str(vault), use_cache=False)
    old_key = str(vault / "old.md")

    assert utils.move_note(str(vault), "old.md", "new/dir/moved.md") == (True, "")

    assert (vault / "new" / "dir" / "moved.md").exists() and not (vault / "old.md").exists()
    assert old_key not in utils._tag_cache and old_key not in utils._links_cache
    index = note_index.get_index()
    assert index.get_note_record("old.md") is None
    assert index.get_note_record("new/dir/moved.md") is not None


@pytest.mark.parametrize("old,new,message", [
    ("../x.md", "y.md", "Invalid source path"),
    ("a.md", "../y.md", "Invalid destination path"),
    ("gone.md", "y.md", "Source note does not exist"),
    ("a.md", "b.md", "A note already exists at: b.md"),
])
def test_move_note_refusals(vault, make_note, old, new, message):
    make_note("a.md")
    make_note("b.md")
    ok, error = utils.move_note(str(vault), old, new)
    assert ok is False and message in error
    assert (vault / "a.md").exists() and (vault / "b.md").exists()


def test_move_note_case_clash_with_third_note_refused(vault, make_note, monkeypatch):
    # Patched so the branch also runs on case-insensitive disks, where exists() answers first.
    make_note("a.md")
    monkeypatch.setattr(utils, "note_name_conflict", lambda *_: "Other.md")
    ok, error = utils.move_note(str(vault), "a.md", "other.md")
    assert ok is False and error == "A note already exists at: Other.md"


def test_move_note_clash_with_itself_allowed(vault, make_note, monkeypatch):
    make_note("a.md")
    monkeypatch.setattr(utils, "note_name_conflict", lambda *_: "A.md")
    assert utils.move_note(str(vault), "a.md", "sub/A.md") == (True, "")


def test_move_note_rename_failure_reported(vault, make_note, monkeypatch):
    make_note("a.md")
    monkeypatch.setattr(Path, "rename", Mock(side_effect=OSError("locked")))
    ok, error = utils.move_note(str(vault), "a.md", "b.md")
    assert ok is False and error == "Failed to move file: locked"


# W15: move_folder, rename_folder
def test_move_folder_ok_notes_follow_and_caches_dropped(vault):
    utils.save_note(str(vault), "src/n.md", "---\ntags: [t]\n---\n")
    utils.scan_notes_fast_walk(str(vault), use_cache=False)
    old_key = str(vault / "src" / "n.md")

    assert utils.move_folder(str(vault), "src", "dst/inner") == (True, "")

    assert (vault / "dst" / "inner" / "n.md").exists() and not (vault / "src").exists()
    assert old_key not in utils._tag_cache and old_key not in utils._links_cache
    assert note_index.get_index().get_note_record("dst/inner/n.md") is not None


def test_rename_folder_delegates_to_move(vault):
    (vault / "a").mkdir()
    assert utils.rename_folder(str(vault), "a", "b") == (True, "")
    assert (vault / "b").is_dir()


@pytest.mark.parametrize("old,new,message", [
    ("../x", "y", "Invalid source path"),
    ("a", "../y", "Invalid destination path"),
    ("gone", "y", "Source folder does not exist"),
    ("file.md", "y", "Source folder does not exist"),  # a file is not a folder
    ("a", "b", "A folder already exists at: b"),
])
def test_move_folder_refusals(vault, make_note, old, new, message):
    (vault / "a").mkdir()
    (vault / "b").mkdir()
    make_note("file.md")
    ok, error = utils.move_folder(str(vault), old, new)
    assert ok is False and message in error


def test_move_folder_case_clash_with_third_folder_refused(vault, monkeypatch):
    (vault / "a").mkdir()
    monkeypatch.setattr(utils, "folder_name_conflict", lambda *_: "Other")
    ok, error = utils.move_folder(str(vault), "a", "other")
    assert ok is False and error == "A folder already exists at: Other"


def test_move_folder_failure_reported(vault, monkeypatch):
    (vault / "a").mkdir()
    monkeypatch.setattr(utils.shutil, "move", Mock(side_effect=OSError("locked")))
    ok, error = utils.move_folder(str(vault), "a", "b")
    assert ok is False and error == "Failed to move folder: locked"


# W15: delete_folder, _drop_path_caches, _drop_prefix_caches
def test_delete_folder_non_empty_updates_index_and_caches(vault):
    utils.save_note(str(vault), "a/n.md", "---\ntags: [t]\n---\n")
    utils.scan_notes_fast_walk(str(vault), use_cache=False)
    key = str(vault / "a" / "n.md")

    assert utils.delete_folder(str(vault), "a") is True

    assert not (vault / "a").exists()
    assert key not in utils._tag_cache and key not in utils._links_cache
    assert note_index.get_index().get_note_record("a/n.md") is None


def test_delete_folder_refusals(tmp_path, vault, make_note):
    make_note("file.md")
    (tmp_path / "outside").mkdir()
    assert utils.delete_folder(str(vault), "gone") is False
    assert utils.delete_folder(str(vault), "file.md") is False
    assert utils.delete_folder(str(vault), "../outside") is False
    assert (tmp_path / "outside").exists() and (vault / "file.md").exists()


def test_delete_folder_rmtree_failure_returns_false(vault, monkeypatch):
    (vault / "a").mkdir()
    monkeypatch.setattr(utils.shutil, "rmtree", Mock(side_effect=OSError("locked")))
    assert utils.delete_folder(str(vault), "a") is False


class _PosixStr:
    """Stands in for a Path whose str() has a fixed spelling on every platform."""

    def __init__(self, value):
        self.value = value

    def __str__(self):
        return self.value


def test_drop_path_caches_only_that_note(monkeypatch):
    monkeypatch.setattr(utils, "_tag_cache", {"p/a.md": (1, []), "p/b.md": (1, [])})
    monkeypatch.setattr(utils, "_links_cache", {"p/a.md": (1, {}), "p/b.md": (1, {})})
    utils._drop_path_caches(_PosixStr("p/a.md"))
    assert set(utils._tag_cache) == {"p/b.md"} and set(utils._links_cache) == {"p/b.md"}


def test_drop_prefix_caches_only_under_folder(monkeypatch):
    monkeypatch.setattr(utils, "_tag_cache", {"p/a/n.md": (1, []), "p/z.md": (1, [])})
    monkeypatch.setattr(utils, "_links_cache", {"p/a/n.md": (1, {}), "p/z.md": (1, {})})
    utils._drop_prefix_caches(_PosixStr("p/a"))
    assert set(utils._tag_cache) == {"p/z.md"} and set(utils._links_cache) == {"p/z.md"}


# W15: create_note_metadata
def test_note_metadata_ok(make_note, vault):
    make_note("n.md", "one\ntwo\nthree")
    meta = utils.create_note_metadata(str(vault), "n.md")
    assert meta["lines"] == 3
    assert meta["size"] == (vault / "n.md").stat().st_size
    assert set(meta) == {"created", "modified", "size", "lines"}


def test_note_metadata_missing_directory_traversal(tmp_path, vault):
    (vault / "dir").mkdir()
    (tmp_path / "secret.md").write_text("x")
    assert utils.create_note_metadata(str(vault), "gone.md") == {}
    assert utils.create_note_metadata(str(vault), "dir") == {}
    assert utils.create_note_metadata(str(vault), "../secret.md") == {}


# W16: _normalized_rel_path
@pytest.mark.parametrize("raw,expected", [
    ("a/b.md", "a/b.md"),
    ("a\\b.md", "a/b.md"),
    ("/a/b/", "a/b"),
    ("\\a\\", "a"),
    ("", ""),
])
def test_normalized_rel_path(raw, expected):
    assert utils._normalized_rel_path(raw) == expected


# W16: note_name_conflict
def test_note_conflict_case_only_clash(vault, make_note):
    make_note("Note.md")
    assert utils.note_name_conflict(str(vault), "note.md") == "Note.md"


def test_note_conflict_exact_name_is_an_update(vault, make_note):
    make_note("Note.md")
    assert utils.note_name_conflict(str(vault), "Note.md") is None


def test_note_conflict_extension_added(vault, make_note):
    make_note("Note.md")
    assert utils.note_name_conflict(str(vault), "NOTE") == "Note.md"


def test_note_conflict_follows_case_of_parent_folder(vault, make_note):
    make_note("Sub/Note.md")
    assert utils.note_name_conflict(str(vault), "sub/note.md") == "Sub/Note.md"


def test_note_conflict_backslash_path(vault, make_note):
    make_note("Sub/Note.md")
    assert utils.note_name_conflict(str(vault), "Sub\\note.md") == "Sub/Note.md"


def test_note_conflict_missing_parent_or_note(vault, make_note):
    make_note("Note.md")
    assert utils.note_name_conflict(str(vault), "nowhere/note.md") is None
    assert utils.note_name_conflict(str(vault), "other.md") is None


def test_note_conflict_traversal_returns_none(tmp_path, vault):
    (tmp_path / "Secret.md").write_text("x")
    assert utils.note_name_conflict(str(vault), "../secret.md") is None


def test_note_conflict_ignores_directory_of_that_name(vault):
    (vault / "Note.md").mkdir()
    assert utils.note_name_conflict(str(vault), "note.md") is None


def test_note_conflict_parent_outside_notes_dir_uses_given_parent(vault, make_note, monkeypatch):
    make_note("sub/Note.md")
    # relative_to() fails, so the path falls back to the parent as it was typed.
    monkeypatch.setattr(utils, "_resolve_existing_dir", lambda *_: Path("/elsewhere"))
    monkeypatch.setattr(utils, "_matching_child", lambda *_, **__: "Note.md")
    assert utils.note_name_conflict(str(vault), "sub/note.md") == "sub/Note.md"


# W16: folder_name_conflict
def test_folder_conflict_case_only_clash(vault):
    (vault / "Docs").mkdir()
    assert utils.folder_name_conflict(str(vault), "docs") == "Docs"


def test_folder_conflict_exact_is_none(vault):
    (vault / "Docs" / "Sub").mkdir(parents=True)
    assert utils.folder_name_conflict(str(vault), "Docs/Sub") is None


def test_folder_conflict_reports_first_differing_segment(vault):
    (vault / "Docs" / "Sub").mkdir(parents=True)
    assert utils.folder_name_conflict(str(vault), "docs/sub") == "Docs"
    assert utils.folder_name_conflict(str(vault), "Docs/SUB") == "Docs/Sub"


def test_folder_conflict_none_cases(tmp_path, vault, make_note):
    (tmp_path / "Outside").mkdir()
    make_note("file.md")
    assert utils.folder_name_conflict(str(vault), "") is None
    assert utils.folder_name_conflict(str(vault), "missing") is None
    assert utils.folder_name_conflict(str(vault), "../outside") is None
    assert utils.folder_name_conflict(str(vault), "FILE.md") is None  # a file, not a folder


# W16: note_exists_exact, folder_exists_exact
def test_note_exists_exact(tmp_path, vault, make_note):
    make_note("Sub/Note.md")
    (tmp_path / "secret.md").write_text("x")
    assert utils.note_exists_exact(str(vault), "Sub/Note.md") is True
    assert utils.note_exists_exact(str(vault), "Sub/Note") is True
    assert utils.note_exists_exact(str(vault), "Sub/note.md") is False
    assert utils.note_exists_exact(str(vault), "nowhere/Note.md") is False
    assert utils.note_exists_exact(str(vault), "../secret.md") is False


def test_note_exists_exact_tolerates_case_in_parent(vault, make_note):
    # Only the note's own name has to match exactly; parent folders follow case-only matches.
    make_note("Sub/Note.md")
    assert utils.note_exists_exact(str(vault), "sub/Note.md") is True


def test_folder_exists_exact(tmp_path, vault):
    (vault / "Docs" / "Sub").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    assert utils.folder_exists_exact(str(vault), "Docs/Sub") is True
    assert utils.folder_exists_exact(str(vault), "docs/Sub") is False
    assert utils.folder_exists_exact(str(vault), "Docs/sub") is False
    assert utils.folder_exists_exact(str(vault), "Docs/Missing") is False
    assert utils.folder_exists_exact(str(vault), "") is False
    assert utils.folder_exists_exact(str(vault), "../outside") is False


# W16: _matching_child, _directory_entries, _resolve_existing_dir
def entry(name, is_dir=False, symlink=False):
    return SimpleNamespace(name=name, is_dir=lambda: is_dir, is_symlink=lambda: symlink)


def test_matching_child_parent_not_a_directory(tmp_path):
    assert utils._matching_child(tmp_path / "gone", "x", want_dir=False) is None


def test_matching_child_exact_beats_earlier_case_match(tmp_path, monkeypatch):
    # Two spellings cannot coexist on NTFS, so the directory listing is faked.
    monkeypatch.setattr(utils, "_directory_entries", lambda _: [entry("NOTE.md"), entry("note.md")])
    assert utils._matching_child(tmp_path, "note.md", want_dir=False) == "note.md"


def test_matching_child_case_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "_directory_entries", lambda _: [entry("NOTE.md")])
    assert utils._matching_child(tmp_path, "note.md", want_dir=False) == "NOTE.md"


def test_matching_child_skips_symlinks_and_wrong_kind(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "_directory_entries", lambda _: [
        entry("note.md", symlink=True),
        entry("NOTE.md", is_dir=True),
    ])
    assert utils._matching_child(tmp_path, "note.md", want_dir=False) is None


def test_matching_child_skips_entry_that_raises(tmp_path, monkeypatch):
    def broken():
        raise OSError("gone")
    bad = SimpleNamespace(name="note.md", is_symlink=broken, is_dir=broken)
    monkeypatch.setattr(utils, "_directory_entries", lambda _: [bad])
    assert utils._matching_child(tmp_path, "note.md", want_dir=False) is None


def test_directory_entries_oserror_gives_empty_list():
    parent = Mock()
    parent.iterdir.side_effect = OSError("denied")
    assert utils._directory_entries(parent) == []


def test_resolve_existing_dir(vault):
    (vault / "Docs" / "Sub").mkdir(parents=True)
    assert utils._resolve_existing_dir(vault, "") == vault
    assert utils._resolve_existing_dir(vault, "docs/sub") == vault / "Docs" / "Sub"
    assert utils._resolve_existing_dir(vault, "./Docs//Sub") == vault / "Docs" / "Sub"
    assert utils._resolve_existing_dir(vault, "Docs/missing") is None


# W17: get_backlinks
def backlink_sources(vault, target):
    return {b["path"] for b in utils.get_backlinks(str(vault), target)}


def test_backlinks_wikilink_with_and_without_alias(vault, make_note):
    make_note("target.md")
    make_note("plain.md", "see [[target]]")
    make_note("alias.md", "see [[target|the other one]]")
    assert backlink_sources(vault, "target.md") == {"plain.md", "alias.md"}


def test_backlinks_record_contents(vault, make_note):
    make_note("target.md")
    make_note("sub/src.md", "first line\nsee [[target]] here")
    assert utils.get_backlinks(str(vault), "target.md") == [{
        "path": "sub/src.md",
        "name": "src",
        "references": [{"line_number": 2, "context": "see [[target]] here", "type": "wikilink"}],
    }]


def test_backlinks_markdown_link(vault, make_note):
    make_note("target.md")
    make_note("src.md", "[text](target.md)")
    refs = utils.get_backlinks(str(vault), "target.md")[0]["references"]
    assert refs[0]["type"] == "markdown"


def test_backlinks_self_link_ignored(vault, make_note):
    make_note("target.md", "[[target]] and [me](target.md)")
    assert utils.get_backlinks(str(vault), "target.md") == []


def test_backlinks_none_when_nothing_links(vault, make_note):
    make_note("target.md")
    make_note("other.md", "no links")
    assert utils.get_backlinks(str(vault), "target.md") == []


def test_backlinks_external_link_not_counted(vault, make_note):
    make_note("target.md")
    make_note("src.md", "[x](https://example.com/target.md) [y](mailto:target.md)")
    assert utils.get_backlinks(str(vault), "target.md") == []


def test_backlinks_same_name_resolved_by_markdown_path(vault, make_note):
    make_note("a/x.md")
    make_note("b/x.md")
    make_note("a/src.md", "[link](x.md)")
    assert backlink_sources(vault, "a/x.md") == {"a/src.md"}
    assert backlink_sources(vault, "b/x.md") == set()


def test_backlinks_same_name_wikilink_counts_for_same_folder_note(vault, make_note):
    make_note("a/x.md")
    make_note("b/x.md")
    make_note("a/src.md", "[[x]]")
    assert backlink_sources(vault, "a/x.md") == {"a/src.md"}


@pytest.mark.xfail(strict=True, reason="DEFECT: [[x]] resolves to a/x.md, but stem matching also reports it as a "
                                       "backlink of the unrelated b/x.md (the graph has no such edge)")
def test_backlinks_same_name_wikilink_not_counted_for_other_folder(vault, make_note):
    make_note("a/x.md")
    make_note("b/x.md")
    make_note("a/src.md", "[[x]]")
    assert backlink_sources(vault, "b/x.md") == set()


def test_backlinks_candidate_without_references_left_out(vault, make_note):
    make_note("target.md")
    src = make_note("src.md", "[[target]]")
    utils.ensure_index_built(str(vault))
    src.write_text("link removed, index not told", encoding="utf-8")
    assert utils.get_backlinks(str(vault), "target.md") == []


def test_backlinks_capped_at_three_references(vault, make_note):
    make_note("target.md")
    make_note("src.md", "\n".join(["[[target]]"] * 5))
    assert len(utils.get_backlinks(str(vault), "target.md")[0]["references"]) == 3


def test_backlinks_most_recent_source_first(vault, make_note):
    make_note("target.md")
    older = make_note("older.md", "[[target]]")
    newer = make_note("newer.md", "[[target]]")
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))
    assert [b["path"] for b in utils.get_backlinks(str(vault), "target.md")] == ["newer.md", "older.md"]


# W17: _extract_backlink_references
def extract(vault, source, target):
    lower = target.lower()
    no_ext = lower.replace(".md", "")
    refs = {lower, no_ext, Path(target).stem.lower()}
    return utils._extract_backlink_references(str(vault), source, target, lower, no_ext, refs)


def test_extract_unreadable_source_gives_empty_list(vault):
    assert extract(vault, "gone.md", "target.md") == []


@pytest.mark.parametrize("line", [
    "[[target]]",
    "[[TARGET]]",  # case-insensitive
    "[[target.md]]",
    "[[ target ]]",  # whitespace trimmed
    "[[target|alias]]",
])
def test_extract_wikilink_forms(vault, make_note, line):
    make_note("src.md", line)
    assert [r["type"] for r in extract(vault, "src.md", "target.md")] == ["wikilink"]


@pytest.mark.parametrize("source,line,target", [
    ("src.md", "[x](target.md)", "target.md"),
    ("src.md", "[x](target)", "target.md"),  # no extension
    ("src.md", "[x](TARGET.md)", "target.md"),  # case-insensitive
    ("src.md", "[x](target.md#section)", "target.md"),  # anchor dropped
    ("src.md", "[x](./target.md)", "target.md"),
    ("src.md", "[x](my%20note.md)", "my note.md"),  # URL-encoded
    ("sub/src.md", "[x](target.md)", "sub/target.md"),  # relative to the source folder
    ("sub/src.md", "[x](sub/target.md)", "sub/target.md"),  # from the vault root
    ("sub/src.md", "[x](target)", "sub/target"),  # target spelled without extension
    ("src.md", "[x](target)", "target"),
])
def test_extract_markdown_forms(vault, make_note, source, line, target):
    make_note(source, line)
    assert [r["type"] for r in extract(vault, source, target)] == ["markdown"]


@pytest.mark.xfail(strict=True, reason="DEFECT: endswith('.md') is case-sensitive here, so '[x](TARGET.MD)' becomes "
                                       "'TARGET.MD.md' and is missed, although _Resolver resolves the same link")
def test_extract_markdown_uppercase_extension(vault, make_note):
    make_note("src.md", "[x](TARGET.MD)")
    assert [r["type"] for r in extract(vault, "src.md", "target.md")] == ["markdown"]


@pytest.mark.parametrize("line", [
    "[x](other.md)",
    "[x](#anchor)",
    "[x](data:image/png;base64,AAAA)",
    "[[other]]",
])
def test_extract_non_matching_links(vault, make_note, line):
    make_note("src.md", line)
    assert extract(vault, "src.md", "target.md") == []


@pytest.mark.parametrize("link", ["[[target]]", "[x](target.md)"])
def test_extract_context_ellipsis_on_long_lines(vault, make_note, link):
    make_note("src.md", "a" * 40 + link + "b" * 40)
    context = extract(vault, "src.md", "target.md")[0]["context"]
    assert context == "..." + "a" * 30 + link + "b" * 30 + "..."


def test_extract_no_ellipsis_on_short_lines(vault, make_note):
    make_note("src.md", "[x](target.md)")
    assert extract(vault, "src.md", "target.md")[0]["context"] == "[x](target.md)"


# W18: get_templates
def test_templates_folder_missing(vault):
    assert utils.get_templates(str(vault)) == []


def test_templates_sorted_and_only_md(vault):
    folder = vault / "_templates"
    folder.mkdir()
    (folder / "zeta.md").write_text("z")
    (folder / "alpha.md").write_text("a")
    (folder / "notes.txt").write_text("x")
    (folder / "nested").mkdir()
    (folder / "nested" / "inner.md").write_text("x")

    templates = utils.get_templates(str(vault))

    assert [t["name"] for t in templates] == ["alpha", "zeta"]
    assert templates[0]["path"] == "_templates/alpha.md"
    assert "modified" in templates[0]


def test_templates_folder_failing_security_check(vault, monkeypatch):
    (vault / "_templates").mkdir()
    (vault / "_templates" / "a.md").write_text("x")
    monkeypatch.setattr(utils, "validate_path_security", lambda *_: False)
    assert utils.get_templates(str(vault)) == []


def test_templates_entry_failing_security_check_skipped(vault, monkeypatch):
    (vault / "_templates").mkdir()
    (vault / "_templates" / "a.md").write_text("x")
    (vault / "_templates" / "b.md").write_text("x")
    monkeypatch.setattr(utils, "validate_path_security", lambda _, p: p.name != "a.md")
    assert [t["name"] for t in utils.get_templates(str(vault))] == ["b"]


def test_templates_entry_stat_error_skipped(vault, monkeypatch):
    (vault / "_templates").mkdir()
    (vault / "_templates" / "bad.md").write_text("x")
    (vault / "_templates" / "good.md").write_text("x")
    real_stat = Path.stat

    def stat(self, **kwargs):
        if self.name == "bad.md":
            raise OSError("denied")
        return real_stat(self, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    assert [t["name"] for t in utils.get_templates(str(vault))] == ["good"]


def test_templates_listing_error_gives_empty_list(vault, monkeypatch):
    (vault / "_templates").mkdir()
    monkeypatch.setattr(Path, "glob", Mock(side_effect=OSError("denied")))
    assert utils.get_templates(str(vault)) == []


# W18: get_template_content
def test_template_content_ok_and_missing(vault):
    (vault / "_templates").mkdir()
    (vault / "_templates" / "daily.md").write_text("# {{date}}")
    assert utils.get_template_content(str(vault), "daily") == "# {{date}}"
    assert utils.get_template_content(str(vault), "gone") is None


def test_template_content_traversal_out_of_vault_refused(tmp_path, vault):
    (vault / "_templates").mkdir()
    (tmp_path / "secret.md").write_text("secret")
    assert utils.get_template_content(str(vault), "../../secret") is None


def test_template_content_read_error_returns_none(vault):
    (vault / "_templates").mkdir()
    (vault / "_templates" / "bad.md").write_bytes(b"\xff\xfe\xff")  # not UTF-8
    assert utils.get_template_content(str(vault), "bad") is None


# W19: get_attachment_dir
@pytest.mark.parametrize("note_path,expected", [
    ("", "_attachments"),
    ("root.md", "_attachments"),
    ("sub/n.md", "sub/_attachments"),
    ("a/b/n.md", "a/b/_attachments"),
])
def test_attachment_dir(vault, note_path, expected):
    assert utils.get_attachment_dir(str(vault), note_path) == vault / expected


# W19: save_uploaded_image
class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 1, 2, 3, 4, 5)


@pytest.fixture
def frozen_time(monkeypatch):
    monkeypatch.setattr(utils, "datetime", FrozenDatetime)


def test_upload_root_note(vault, frozen_time):
    rel = utils.save_uploaded_image(str(vault), "root.md", "pic.png", b"data")
    assert rel == "_attachments/pic-20260102030405.png"
    assert (vault / rel).read_bytes() == b"data"


def test_upload_nested_note(vault, frozen_time):
    rel = utils.save_uploaded_image(str(vault), "a/b/n.md", "pic.png", b"data")
    assert rel == "a/b/_attachments/pic-20260102030405.png"


def test_upload_filename_sanitised(vault, frozen_time):
    rel = utils.save_uploaded_image(str(vault), "n.md", 'a:b?"c.png', b"data")
    assert rel == "_attachments/a_b_c-20260102030405.png"


def test_upload_invalidates_scan_cache(vault, frozen_time):
    utils.scan_notes_fast_walk(str(vault), include_media=True)
    rel = utils.save_uploaded_image(str(vault), "n.md", "pic.png", b"data")
    assert rel in paths_of(utils.scan_notes_fast_walk(str(vault), include_media=True))


def test_upload_type_not_checked_here(vault, frozen_time):
    # TEST_PLAN expected a refusal, but the type check lives in the /api/upload-media endpoint.
    assert utils.save_uploaded_image(str(vault), "n.md", "tool.exe", b"x") == "_attachments/tool-20260102030405.exe"


def test_upload_traversal_note_path_writes_no_file(tmp_path, vault, frozen_time):
    assert utils.save_uploaded_image(str(vault), "../out/n.md", "pic.png", b"x") is None
    assert list(tmp_path.rglob("pic-*.png")) == []


@pytest.mark.xfail(strict=True, reason="DEFECT: mkdir runs before the security check, so a refused "
                                       "upload still creates a directory outside the vault")
def test_upload_traversal_note_path_creates_nothing_outside_vault(tmp_path, vault, frozen_time):
    utils.save_uploaded_image(str(vault), "../out/n.md", "pic.png", b"x")
    assert not (tmp_path / "out").exists()


@pytest.mark.xfail(strict=True, reason="DEFECT: names only get a one-second timestamp, so two uploads of "
                                       "the same name in one second overwrite each other silently")
def test_upload_name_collision_keeps_both_files(vault, frozen_time):
    first = utils.save_uploaded_image(str(vault), "n.md", "pic.png", b"first")
    second = utils.save_uploaded_image(str(vault), "n.md", "pic.png", b"second")
    assert first != second
    assert (vault / first).read_bytes() == b"first"


def test_upload_write_failure_returns_none(vault, frozen_time, monkeypatch):
    monkeypatch.setattr(utils, "open", Mock(side_effect=OSError("disk full")), raising=False)
    assert utils.save_uploaded_image(str(vault), "n.md", "pic.png", b"x") is None


# W19: save_uploaded_image with sibling_folder (drawings)
@pytest.mark.parametrize("folder,expected", [
    ("", "drawing-20260102030405.png"),
    (".", "drawing-20260102030405.png"),
    ("a/b", "a/b/drawing-20260102030405.png"),
    ("a\\b", "a/b/drawing-20260102030405.png"),
    (" a/./b/ ", "a/b/drawing-20260102030405.png"),
])
def test_drawing_stored_next_to_notes(vault, frozen_time, folder, expected):
    # The uploaded name is ignored here: a drawing always gets a generated one.
    assert utils.save_uploaded_image(str(vault), "n.md", "x.png", b"d", sibling_folder=folder) == expected
    assert (vault / expected).read_bytes() == b"d"


@pytest.mark.parametrize("folder", ["..", "../x", "a/../../x"])
def test_drawing_traversal_refused(tmp_path, vault, folder):
    assert utils.save_uploaded_image(str(vault), "n.md", "x.png", b"d", sibling_folder=folder) is None
    assert list(tmp_path.rglob("drawing-*.png")) == []


def test_drawing_absolute_folder_stays_inside_vault(tmp_path, vault, frozen_time):
    # The folder is split into segments, so an absolute path is rebuilt under the vault.
    outside = tmp_path / "outside"
    outside.mkdir()
    rel = utils.save_uploaded_image(str(vault), "n.md", "x.png", b"d", sibling_folder=str(outside))
    assert list(outside.iterdir()) == []
    assert (vault / rel).read_bytes() == b"d"


def test_drawing_folder_failing_security_check(vault, monkeypatch):
    monkeypatch.setattr(utils, "validate_path_security", lambda *_: False)
    assert utils.save_uploaded_image(str(vault), "n.md", "x.png", b"d", sibling_folder="new") is None
    assert not (vault / "new").exists()


def test_drawing_file_failing_security_check(vault, monkeypatch):
    monkeypatch.setattr(utils, "validate_path_security", lambda _, p: p.name == ".nd_probe")
    assert utils.save_uploaded_image(str(vault), "n.md", "x.png", b"d", sibling_folder="") is None
    assert list(vault.glob("drawing-*.png")) == []


def test_drawing_mkdir_failure_returns_none(vault, monkeypatch):
    monkeypatch.setattr(Path, "mkdir", Mock(side_effect=OSError("denied")))
    assert utils.save_uploaded_image(str(vault), "n.md", "x.png", b"d", sibling_folder="new") is None


def test_drawing_write_failure_returns_none(vault, monkeypatch):
    monkeypatch.setattr(utils, "open", Mock(side_effect=OSError("disk full")), raising=False)
    assert utils.save_uploaded_image(str(vault), "n.md", "x.png", b"d", sibling_folder="") is None


# W20: get_all_tags, get_notes_by_tag
def test_all_tags_counts_across_notes(vault, make_note):
    make_note("a.md", "---\ntags: [x, y]\n---\n")
    make_note("sub/b.md", "---\ntags: [x]\n---\n")
    make_note("c.md", "no tags")
    assert utils.get_all_tags(str(vault)) == {"x": 2, "y": 1}


def test_all_tags_empty_vault(vault):
    assert utils.get_all_tags(str(vault)) == {}


def test_all_tags_merges_case_variants(vault, make_note):
    make_note("a.md", "---\ntags: [Python]\n---\n")
    make_note("b.md", "---\ntags: [PYTHON]\n---\n")
    assert utils.get_all_tags(str(vault)) == {"python": 2}


def test_notes_by_tag_record_shape(vault, make_note):
    make_note("sub/a.md", "---\ntags: [x, y]\n---\n")
    result = utils.get_notes_by_tag(str(vault), "x")
    assert len(result) == 1
    note = result[0]
    assert (note["name"], note["path"], note["folder"], note["tags"]) == ("a", "sub/a.md", "sub", ["x", "y"])
    assert {"modified", "size"} <= set(note)


def test_notes_by_tag_missing_tag(vault, make_note):
    make_note("a.md", "---\ntags: [x]\n---\n")
    assert utils.get_notes_by_tag(str(vault), "nope") == []


def test_notes_by_tag_case_insensitive(vault, make_note):
    make_note("a.md", "---\ntags: [python]\n---\n")
    assert [n["path"] for n in utils.get_notes_by_tag(str(vault), "PyThOn")] == ["a.md"]


def test_notes_by_tag_newest_first(vault, make_note):
    older = make_note("older.md", "---\ntags: [x]\n---\n")
    newer = make_note("newer.md", "---\ntags: [x]\n---\n")
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))
    assert [n["path"] for n in utils.get_notes_by_tag(str(vault), "x")] == ["newer.md", "older.md"]
