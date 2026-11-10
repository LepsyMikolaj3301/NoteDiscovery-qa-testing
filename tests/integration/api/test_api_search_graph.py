"""I5: search, tags, graph and stats stay consistent after writes."""
import pytest

pytestmark = pytest.mark.integration


def search_paths(client, q):
    return [r["path"] for r in client.get("/api/search", params={"q": q}).json()["results"]]


def test_search_follows_writes(client):
    client.post("/api/notes/a.md", json={"content": "the zebra runs"})
    assert search_paths(client, "zebra") == ["a.md"]
    assert search_paths(client, "ZEBRA") == ["a.md"]

    client.post("/api/notes/a.md", json={"content": "the lion runs"})
    assert search_paths(client, "zebra") == [] and search_paths(client, "lion") == ["a.md"]

    client.post("/api/notes/move", json={"oldPath": "a.md", "newPath": "d/b.md"})
    assert search_paths(client, "lion") == ["d/b.md"]

    client.delete("/api/notes/d/b.md")
    assert search_paths(client, "lion") == []


def test_search_edge_cases(client, make_note, monkeypatch):
    from backend import main
    for n in "abc":
        make_note(f"{n}.md", "needle")
    assert client.get("/api/search", params={"q": "  "}).json()["results"] == []
    body = client.get("/api/search", params={"q": "needle", "limit": 2, "offset": 2}).json()
    assert [r["path"] for r in body["results"]] == ["c.md"] and body["pagination"]["total"] == 3
    monkeypatch.setitem(main.config["search"], "enabled", False)
    assert client.get("/api/search", params={"q": "needle"}).status_code == 403


def test_tags_follow_writes(client, make_note):
    make_note("a.md", "---\ntags: [Alpha, beta]\n---\nx")
    make_note("b.md", "---\ntags: [alpha]\n---\nx")
    assert client.get("/api/tags").json()["tags"] == {"alpha": 2, "beta": 1}
    body = client.get("/api/tags/ALPHA").json()
    assert body["count"] == 2 and [n["path"] for n in body["notes"]] == ["a.md", "b.md"]
    assert client.get("/api/tags/alpha?limit=1").json()["pagination"]["has_more"] is True

    client.post("/api/notes/b.md", json={"content": "no tags"})
    assert client.get("/api/tags").json()["tags"]["alpha"] == 1
    client.delete("/api/notes/a.md")
    assert client.get("/api/tags").json()["tags"] == {}
    assert client.get("/api/tags/missing").json()["notes"] == []


def test_graph_follows_renames_and_deletes(client):
    client.post("/api/notes/a.md", json={"content": "[[b]]"})
    client.post("/api/notes/b.md", json={"content": "x"})
    g = client.get("/api/graph").json()
    assert {n["id"] for n in g["nodes"]} == {"a.md", "b.md"}
    assert [(e["source"], e["target"]) for e in g["edges"]] == [("a.md", "b.md")]

    client.post("/api/notes/move", json={"oldPath": "b.md", "newPath": "d/b.md"})
    g = client.get("/api/graph").json()
    assert [(e["source"], e["target"]) for e in g["edges"]] == [("a.md", "d/b.md")]

    client.delete("/api/notes/d/b.md")
    assert client.get("/api/graph").json()["edges"] == []


def test_stats(client, make_note):
    make_note("f/a.md", "---\ntags: [t]\n---\nx")
    make_note("_templates/T.md", "t")
    s = client.get("/api/stats").json()
    assert (s["notes_count"], s["tags_count"], s["templates_count"]) == (2, 1, 1)
    assert s["folders_count"] == 2
    idx = client.get("/api/index/stats").json()
    assert isinstance(idx, dict) and idx
