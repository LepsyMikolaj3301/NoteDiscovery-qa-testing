"""White-box tests (W1-W4): NoteIndex state, search index, caches, module hooks."""
import pytest

from backend import note_index
from backend.note_index import NoteIndex, NoteRecord, _fingerprint

pytestmark = pytest.mark.unit


def rec(path, tags=(), mtime=1.0, type="note", size=10):
    folder = path.rpartition("/")[0]
    name = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return NoteRecord(path, name, folder, f"t{mtime}", size, type, mtime, tuple(tags))


def raw(wiki=(), md=()):
    return {"wikilinks": list(wiki), "mdlinks": list(md)}


def build(notes, links=None, folders=()):
    """Fresh NoteIndex filled through bulk_set. notes: list of NoteRecord."""
    idx = NoteIndex()
    sources = {n.path: raw() for n in notes if n.type == "note"}
    sources.update(links or {})
    idx.bulk_set(notes, folders, sources)
    return idx


@pytest.fixture
def idx():
    return build(
        [rec("a.md", ["x", "y"]), rec("b.md", ["x"]), rec("d/c.md"), rec("pic.png", type="image")],
        links={"a.md": raw(wiki=["b"]), "b.md": raw(md=["d/c.md"]), "d/c.md": raw(wiki=["A"])},
        folders=["d"],
    )


# ---------------------------------------------------------------- W1: bulk_set
def test_bulk_set_builds_tags_links_backlinks(idx):
    assert idx.is_built()
    assert idx.get_all_tags() == {"x": 2, "y": 1}
    nodes, edges = idx.get_graph_data()
    assert nodes == ["a.md", "b.md", "d/c.md"]  # media is not a graph node
    assert set(edges) == {("a.md", "b.md", "wikilink"), ("b.md", "d/c.md", "markdown"), ("d/c.md", "a.md", "wikilink")}
    assert idx.get_backlink_candidate_sources("b.md") == {"a.md"}


def test_bulk_set_media_tags_ignored():
    idx = build([rec("pic.png", ["t"], type="image")])
    assert idx.get_all_tags() == {}


def test_bulk_set_same_input_short_circuits(idx):
    notes = [n for _, n in idx.all_note_records()]
    before = idx.stats()["counters"]["build_count"]
    idx.bulk_set(notes, ["d"], {n.path: raw() for n in notes})  # different links than idx -> rebuild
    assert idx.stats()["counters"]["build_count"] == before + 1
    idx.bulk_set(notes, ["d"], {n.path: raw() for n in notes})  # now identical
    c = idx.stats()["counters"]
    assert c["build_count"] == before + 1 and c["fingerprint_short_circuits"] == 1


def test_bulk_set_after_invalidate_rebuilds_even_if_same(idx):
    notes = [n for _, n in idx.all_note_records()]
    sources = {n.path: raw() for n in notes}
    idx.bulk_set(notes, [], sources)
    n = idx.stats()["counters"]["build_count"]
    idx.invalidate()
    assert not idx.is_built()
    idx.bulk_set(notes, [], sources)
    assert idx.stats()["counters"]["build_count"] == n + 1 and idx.is_built()


def test_bulk_set_drops_notes_missing_from_new_scan(idx):
    idx.bulk_set([rec("only.md", ["z"])], [], {"only.md": raw()})
    assert idx.get_all_tags() == {"z": 1}
    assert idx.get_note_record("a.md") is None
    assert idx.get_graph_data()[1] == []


def test_bulk_set_prunes_search_terms_of_vanished_notes(tmp_path):
    (tmp_path / "a.md").write_text("alpha")
    (tmp_path / "b.md").write_text("beta")
    idx = build([rec("a.md"), rec("b.md")])
    idx.ensure_search_index_built(str(tmp_path))
    idx.bulk_set([rec("a.md")], [], {"a.md": raw()})
    assert idx.get_search_candidates("beta") == set()
    assert idx.get_search_candidates("alpha") == {"a.md"}


def test_wikilink_to_self_and_unknown_makes_no_edge():
    idx = build([rec("a.md")], links={"a.md": raw(wiki=["a", "ghost"], md=["a.md", "ghost.md"])})
    assert idx.get_graph_data()[1] == []


def test_wikilink_wins_over_mdlink_for_same_target():
    idx = build([rec("a.md"), rec("b.md")], links={"a.md": raw(wiki=["b"], md=["b.md"])})
    assert idx.get_graph_data()[1] == [("a.md", "b.md", "wikilink")]


# -------------------------------------------------------------- W1: update_note
def test_update_note_moves_tag_buckets(idx):
    idx.update_note(rec("a.md", ["y", "new"], mtime=2.0), raw())
    assert idx.get_all_tags() == {"new": 1, "x": 1, "y": 1}
    assert idx.get_paths_for_tag("x") == {"b.md"}


def test_update_note_last_tag_removed_deletes_bucket():
    idx = build([rec("a.md", ["only"])])
    idx.update_note(rec("a.md", [], mtime=2.0), raw())
    assert idx.get_all_tags() == {}


def test_update_note_new_note_and_folder():
    idx = build([rec("a.md")])
    idx.update_note(rec("new/n.md", ["t"]), raw(wiki=["a"]))
    assert idx.get_note_record("new/n.md") is not None
    assert idx.stats()["folders"] == 1
    assert idx.get_backlink_candidate_sources("a.md") == {"new/n.md"}


def test_update_note_replaces_old_links(idx):
    idx.update_note(rec("a.md", ["x", "y"]), raw(md=["d/c.md"]))
    edges = set(idx.get_graph_data()[1])
    assert ("a.md", "b.md", "wikilink") not in edges
    assert ("a.md", "d/c.md", "markdown") in edges
    assert idx.get_backlink_candidate_sources("b.md") == set()


def test_update_note_resets_fingerprint_so_next_bulk_set_rebuilds(idx):
    notes = [n for _, n in idx.all_note_records()]
    sources = {n.path: raw() for n in notes}
    idx.bulk_set(notes, [], sources)
    idx.update_note(rec("a.md", mtime=9.0), raw(wiki=["b"]))
    n = idx.stats()["counters"]["build_count"]
    idx.bulk_set(notes, [], sources)
    assert idx.stats()["counters"]["build_count"] == n + 1


def test_update_note_patches_search_only_when_built(tmp_path):
    (tmp_path / "a.md").write_text("old")
    idx = build([rec("a.md")])
    idx.update_note(rec("a.md", mtime=2.0), raw(), content="ignored text")
    assert not idx.is_search_built() and idx.stats()["search_terms"] == 0
    idx.ensure_search_index_built(str(tmp_path))
    idx.update_note(rec("a.md", mtime=3.0), raw(), content="fresh words")
    assert idx.get_search_candidates("fresh") == {"a.md"}
    assert idx.get_search_candidates("old") == set()  # stale term removed


# -------------------------------------------------------------- W1: remove_note
def test_remove_note_cleans_everything(idx):
    idx.remove_note("b.md")
    assert idx.get_note_record("b.md") is None
    assert idx.get_all_tags() == {"x": 1, "y": 1}
    edges = set(idx.get_graph_data()[1])
    assert all("b.md" not in e[:2] for e in edges)
    assert idx.get_backlink_candidate_sources("d/c.md") == set()


def test_remove_note_unknown_is_noop(idx):
    before = idx.stats()["counters"]["incremental_updates"]
    idx.remove_note("nope.md")
    assert idx.stats()["counters"]["incremental_updates"] == before


def test_remove_note_drops_it_from_loose_wikilink_tokens():
    idx = build([rec("a.md"), rec("b.md")], links={"a.md": raw(wiki=["Target"])})
    assert idx.get_backlink_candidate_sources("target.md") == {"a.md"}
    idx.remove_note("a.md")
    assert idx.get_backlink_candidate_sources("target.md") == set()


def test_remove_note_drops_search_terms(tmp_path):
    (tmp_path / "a.md").write_text("unique")
    idx = build([rec("a.md")])
    idx.ensure_search_index_built(str(tmp_path))
    idx.remove_note("a.md")
    assert idx.stats()["search_terms"] == 0


# -------------------------------------------------------------- W1: rename_note
def test_rename_note_same_or_unknown_is_noop(idx):
    idx.rename_note("a.md", "a.md")
    idx.rename_note("ghost.md", "x.md")
    assert idx.get_note_record("a.md") and idx.get_note_record("x.md") is None


@pytest.mark.parametrize("new,folder", [("z.md", ""), ("f/g/z.md", "f/g")])
def test_rename_note_record_and_folder(idx, new, folder):
    idx.rename_note("a.md", new)
    r = idx.get_note_record(new)
    assert r.folder == folder and r.name == "z" and r.tags == ("x", "y")
    assert idx.get_note_record("a.md") is None
    assert idx.get_paths_for_tag("x") == {"b.md", new}


def test_rename_note_forces_rebuild_and_wipes_links(idx):
    idx.rename_note("b.md", "bb.md")
    assert not idx.is_built()
    edges = idx.get_graph_data()[1]
    assert all("b.md" not in e[:2] for e in edges)  # stale edge to old path gone
    assert idx.get_backlink_candidate_sources("bb.md") == set()


def test_rename_note_moves_search_terms(tmp_path):
    (tmp_path / "a.md").write_text("needle")
    idx = build([rec("a.md")])
    idx.ensure_search_index_built(str(tmp_path))
    idx.rename_note("a.md", "b.md")
    assert idx.get_search_candidates("needle") == {"b.md"}


# --------------------------------------------------------- W1: folder prefix ops
def folder_idx():
    return build(
        [rec("a/x.md", ["t"]), rec("a/sub/y.md"), rec("ab/z.md"), rec("a.md")],
        links={"a/x.md": raw(wiki=["z"])},
        folders=["a", "a/sub", "ab"],
    )


def test_rename_folder_prefix_only_matching_paths():
    idx = folder_idx()
    idx.rename_folder_prefix("a", "n")
    paths = {p for p, _ in idx.all_note_records()}
    assert paths == {"n/x.md", "n/sub/y.md", "ab/z.md", "a.md"}  # "ab/" and "a.md" untouched
    assert idx.get_note_record("n/sub/y.md").folder == "n/sub"
    assert idx.get_paths_for_tag("t") == {"n/x.md"}
    assert idx.stats()["folders"] == 3
    assert not idx.is_built()


def test_rename_folder_prefix_trailing_slash_and_same():
    idx = folder_idx()
    idx.rename_folder_prefix("a/", "a")
    assert idx.is_built()  # no-op: nothing invalidated
    idx.rename_folder_prefix("a/", "q/")
    assert idx.get_note_record("q/x.md") is not None


def test_remove_folder_prefix_only_matching_paths():
    idx = folder_idx()
    idx.remove_folder_prefix("a")
    assert {p for p, _ in idx.all_note_records()} == {"ab/z.md", "a.md"}
    assert idx.get_all_tags() == {}
    assert idx.stats()["folders"] == 1  # only "ab" left


def test_remove_folder_prefix_unknown_is_harmless():
    idx = folder_idx()
    idx.remove_folder_prefix("nope")
    assert len(idx.all_note_records()) == 4


def test_reset_clears_everything(idx):
    idx.reset()
    s = idx.stats()
    assert not s["built"] and s["notes"] == s["tags"] == s["folders"] == s["links_forward_entries"] == 0
    assert idx.get_graph_data() == ([], [])


# ------------------------------------------------------------------- W2: search
def test_search_not_built_without_main_index(tmp_path):
    idx = NoteIndex()
    assert idx.ensure_search_index_built(str(tmp_path)) is False
    assert idx.get_search_candidates("anything") is None


def test_search_index_lazy_and_built_once(tmp_path):
    (tmp_path / "a.md").write_text("hello world")
    idx = build([rec("a.md")])
    assert not idx.is_search_built()
    assert idx.ensure_search_index_built(str(tmp_path)) is True
    assert idx.ensure_search_index_built(str(tmp_path)) is True
    assert idx.stats()["counters"]["search_build_count"] == 1


def test_search_index_unreadable_file_is_skipped(tmp_path):
    (tmp_path / "ok.md").write_text("fine")
    idx = build([rec("ok.md"), rec("missing.md")])
    assert idx.ensure_search_index_built(str(tmp_path)) is True
    assert idx.get_search_candidates("fine") == {"ok.md"}


def test_search_index_skips_media(tmp_path):
    (tmp_path / "pic.png").write_bytes(b"binary")
    idx = build([rec("pic.png", type="image")])
    idx.ensure_search_index_built(str(tmp_path))
    assert idx.stats()["search_terms"] == 0


@pytest.fixture
def sidx(tmp_path):
    (tmp_path / "a.md").write_text("features and Planning")
    (tmp_path / "b.md").write_text("features only")
    idx = build([rec("a.md"), rec("b.md")])
    idx.ensure_search_index_built(str(tmp_path))
    return idx


@pytest.mark.parametrize("q,expected", [
    ("features", {"a.md", "b.md"}),
    ("feat", {"a.md", "b.md"}),         # prefix of an indexed word
    ("PLANNING", {"a.md"}),             # case-insensitive
    ("features planning", {"a.md"}),    # multi-term AND
    ("features nothing", set()),
    ("zzzz", set()),
    ("Plan Z", {"a.md"}),               # single-letter token is ignored, not required
])
def test_search_candidates(sidx, q, expected):
    assert sidx.get_search_candidates(q) == expected


@pytest.mark.parametrize("q", ["", "???", "a", "b c", "  "])
def test_search_candidates_none_when_unindexable(sidx, q):
    assert sidx.get_search_candidates(q) is None


def test_search_candidates_result_is_a_copy(sidx):
    sidx.get_search_candidates("features").clear()
    assert sidx.get_search_candidates("features") == {"a.md", "b.md"}


def test_search_terms_helpers():
    assert note_index.extract_search_terms("Go, go! a AI_x-y") == {"go", "ai_x-y"}
    assert note_index.extract_links_from_content("[[ A ]] [t](p.md) [e](https://x) [m](#h)") == {
        "wikilinks": ["A"], "mdlinks": ["p.md"]}


# --------------------------------------- W3: extraction cache, fingerprint, reads
def test_try_get_extraction_hit_miss_and_copy(idx):
    got = idx.try_get_extraction("a.md", 1.0)
    assert got == (["x", "y"], {"wikilinks": ["b"], "mdlinks": []})
    got[0].append("mutated")
    got[1]["wikilinks"].append("mutated")
    assert idx.try_get_extraction("a.md", 1.0)[0] == ["x", "y"]
    assert idx.try_get_extraction("a.md", 1.5) is None   # mtime changed
    assert idx.try_get_extraction("zzz.md", 1.0) is None  # unknown
    assert idx.try_get_extraction("pic.png", 1.0) is None  # media has no raw links


def test_fingerprint_sensitivity():
    n = [rec("a.md", mtime=1.0)]
    base = _fingerprint(n, {"a.md": raw(wiki=["b"])})
    assert base == _fingerprint([rec("a.md", mtime=1.0)], {"a.md": raw(wiki=["b"])})
    assert base != _fingerprint([rec("a.md", mtime=2.0)], {"a.md": raw(wiki=["b"])})
    assert base != _fingerprint(n, {"a.md": raw(wiki=["c"])})
    assert base != _fingerprint(n, {"a.md": raw(md=["b"])})
    assert base != _fingerprint(n + [rec("b.md")], {"a.md": raw(wiki=["b"])})


def test_backlink_candidates_loose_and_strict():
    idx = build(
        [rec("a.md"), rec("f/Target.md"), rec("c.md")],
        links={"a.md": raw(wiki=["target"]), "c.md": raw(wiki=["f/Target.md"])},
    )
    assert idx.get_backlink_candidate_sources("f/Target.md") == {"a.md", "c.md"}


def test_backlink_candidates_exclude_self_and_unlinked(idx):
    assert idx.get_backlink_candidate_sources("a.md") == {"d/c.md"}
    assert idx.get_backlink_candidate_sources("unknown.md") == set()


def test_get_paths_for_tag_case_insensitive_and_copy(idx):
    assert idx.get_paths_for_tag("X") == {"a.md", "b.md"}
    idx.get_paths_for_tag("x").clear()
    assert len(idx.get_paths_for_tag("x")) == 2
    assert idx.get_paths_for_tag("nope") == set()


def test_summary(idx):
    s = idx.summary()
    assert s == {"notes_count": 3, "media_count": 1, "folders_count": 1, "tags_count": 2,
                 "total_size_bytes": 40, "last_modified": "t1.0"}


def test_summary_last_modified_is_newest_note_only():
    idx = build([rec("a.md", mtime=1.0), rec("b.md", mtime=5.0), rec("p.png", mtime=99.0, type="image")])
    assert idx.summary()["last_modified"] == "t5.0"


def test_summary_empty():
    s = NoteIndex().summary()
    assert s["notes_count"] == 0 and s["last_modified"] is None


def test_stats_counters(idx):
    s = idx.stats()
    assert s["built"] and s["notes"] == 4 and s["counters"]["build_count"] == 1
    assert s["counters"]["last_built_at"] is not None


# --------------------------------------------------------------- W4: module hooks
@pytest.fixture
def ni():
    return note_index.get_index()


def test_hook_note_saved_adds_record_with_tags_and_links(tmp_path, ni):
    f = tmp_path / "f" / "n.md"
    f.parent.mkdir()
    f.write_text("---\ntags: [Foo]\n---\n[[other]]")
    note_index.on_note_saved(str(tmp_path), f, f.read_text())
    r = ni.get_note_record("f/n.md")
    assert r.folder == "f" and r.size == f.stat().st_size and r.tags == ("foo",)
    assert ni.is_built() is False  # a hook never marks the index as fully built


def test_hook_note_saved_outside_vault_is_swallowed(tmp_path, ni):
    other = tmp_path / "x.md"
    other.write_text("hi")
    vault = tmp_path / "v"
    vault.mkdir()
    note_index.on_note_saved(str(vault), other, "hi")  # relative_to raises -> logged
    assert ni.get_note_record("x.md") is None


def test_hook_deleted_and_renamed(tmp_path, ni):
    ni.bulk_set([rec("a.md", ["t"])], [], {"a.md": raw()})
    note_index.on_note_renamed(str(tmp_path), tmp_path / "a.md", tmp_path / "b.md")
    assert ni.get_note_record("b.md") and ni.get_note_record("a.md") is None
    note_index.on_note_deleted(str(tmp_path), tmp_path / "b.md")
    assert ni.get_note_record("b.md") is None
    note_index.on_note_deleted(str(tmp_path), tmp_path / "never.md")  # no raise


def test_hook_folder_renamed_and_deleted(tmp_path, ni):
    ni.bulk_set([rec("a/x.md"), rec("k.md")], ["a"], {"a/x.md": raw(), "k.md": raw()})
    note_index.on_folder_renamed(str(tmp_path), tmp_path / "a", tmp_path / "b")
    assert ni.get_note_record("b/x.md")
    note_index.on_folder_deleted(str(tmp_path), tmp_path / "b")
    assert {p for p, _ in ni.all_note_records()} == {"k.md"}


def test_hook_folder_errors_invalidate_index(tmp_path, ni):
    ni.bulk_set([rec("a.md")], [], {"a.md": raw()})
    assert ni.is_built()
    note_index.on_folder_renamed("/vault", tmp_path / "a", tmp_path / "b")  # outside vault
    assert not ni.is_built()
    ni.bulk_set([rec("a.md")], [], {"a.md": raw()})
    note_index.on_folder_deleted("/vault", tmp_path / "a")
    assert not ni.is_built()


def test_populate_from_scan_and_failure(ni):
    note_index.populate_from_scan([rec("a.md")], [], {"a.md": raw()})
    assert ni.is_built()
    note_index.populate_from_scan(None, [], {})  # bad input is logged, not raised


def test_ensure_search_index_facade(tmp_path, ni):
    assert note_index.ensure_search_index(str(tmp_path)) is False
    (tmp_path / "a.md").write_text("word")
    ni.bulk_set([rec("a.md")], [], {"a.md": raw()})
    assert note_index.ensure_search_index(str(tmp_path)) is True
    assert note_index.get_search_candidates("word") == {"a.md"}
