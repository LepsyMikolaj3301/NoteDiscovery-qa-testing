"""I2: note lifecycle through the REST API."""
import pytest

pytestmark = pytest.mark.integration


def test_note_lifecycle(client, vault):
    r = client.post("/api/notes/a/Note", json={"content": "# Hi\n[[Other]]"})
    assert r.status_code == 200 and "created" in r.json()["message"]
    assert (vault / "a" / "Note.md").read_text() == "# Hi\n[[Other]]"  # .md appended, dirs created

    client.post("/api/notes/Other.md", json={"content": "x"})
    got = client.get("/api/notes/Other.md").json()
    assert got["content"] == "x"
    assert [b["path"] for b in got["backlinks"]] == ["a/Note.md"]
    assert "backlinks" not in client.get("/api/notes/Other.md?include_backlinks=false").json()

    r = client.post("/api/notes/Other.md", json={"content": "y"})
    assert "saved" in r.json()["message"]  # overwrite, not create
    assert (vault / "Other.md").read_text() == "y"

    r = client.patch("/api/notes/Other.md", json={"content": "more"})
    assert r.status_code == 200
    assert (vault / "Other.md").read_text() == "y\n\nmore"
    client.patch("/api/notes/Other.md", json={"content": "t", "add_timestamp": True})
    assert "\n\n---\n\n**" in (vault / "Other.md").read_text()

    assert client.delete("/api/notes/Other.md").status_code == 200
    assert not (vault / "Other.md").exists()
    assert client.get("/api/notes/Other.md").status_code == 404
    assert client.delete("/api/notes/Other.md").status_code == 404


def test_patch_errors(client, make_note):
    make_note("n.md", "x")
    assert client.patch("/api/notes/n.md", json={"content": ""}).status_code == 400
    assert client.patch("/api/notes/missing.md", json={"content": "x"}).status_code == 404


def test_name_clash_and_hash(client, make_note):
    make_note("Note.md", "x")
    assert client.post("/api/notes/note.md", json={"content": "y"}).status_code == 409
    assert client.post("/api/notes/Note.md", json={"content": "y"}).status_code == 200  # same case = update
    assert client.post("/api/notes/a%23b.md", json={"content": "y"}).status_code == 400


def test_traversal_is_contained(client, vault):
    outside = vault.parent / "evil.md"
    outside.write_text("keep")
    # Records current behaviour: the write is refused, and surfaces as 500
    assert client.post("/api/notes/..%2Fevil.md", json={"content": "pwn"}).status_code == 500
    assert client.get("/api/notes/..%2Fevil.md").status_code == 404
    assert client.delete("/api/notes/..%2Fevil.md").status_code == 404
    assert outside.read_text() == "keep"


def test_list_notes_pagination(client, make_note):
    for n in "cab":
        make_note(f"{n}.md", n)
    body = client.get("/api/notes").json()
    assert len(body["notes"]) == 3 and "pagination" not in body
    body = client.get("/api/notes?limit=2&offset=1").json()
    assert [n["path"] for n in body["notes"]] == ["b.md", "c.md"]  # sorted by path
    assert body["pagination"]["total"] == 3
