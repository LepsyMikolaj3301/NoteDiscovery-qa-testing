"""I3: folder operations and their effect on notes, index and share tokens."""
import pytest

pytestmark = pytest.mark.integration


def test_create_folder(client, vault):
    assert client.post("/api/folders", json={"path": "a/b"}).status_code == 200
    assert (vault / "a" / "b").is_dir()
    assert client.post("/api/folders", json={"path": "A"}).status_code == 409  # case clash
    assert client.post("/api/folders", json={"path": ""}).status_code == 400
    assert client.post("/api/folders", json={"path": "x#y"}).status_code == 400


def test_rename_and_move_folder_carry_notes(client, make_note, vault):
    make_note("old/n.md", "x")
    r = client.post("/api/folders/rename", json={"oldPath": "old", "newPath": "new"})
    assert r.status_code == 200
    assert (vault / "new" / "n.md").exists() and not (vault / "old").exists()
    assert [n["path"] for n in client.get("/api/notes").json()["notes"]] == ["new/n.md"]

    make_note("dest/keep.md", "k")
    assert client.post("/api/folders/move", json={"oldPath": "new", "newPath": "dest/new"}).status_code == 200
    assert (vault / "dest" / "new" / "n.md").exists()


def test_folder_move_errors(client, make_note):
    make_note("a/n.md", "x")
    make_note("b/n.md", "x")
    assert client.post("/api/folders/move", json={"oldPath": "a"}).status_code == 400
    assert client.post("/api/folders/move", json={"oldPath": "a", "newPath": "b"}).status_code == 400
    assert client.post("/api/folders/rename", json={"oldPath": "nope", "newPath": "z"}).status_code == 400


def test_delete_non_empty_folder(client, make_note, vault):
    make_note("f/sub/n.md", "x")
    assert client.delete("/api/folders/f").status_code == 200
    assert not (vault / "f").exists()
    assert client.get("/api/notes").json()["notes"] == []


@pytest.mark.xfail(strict=True, reason="DEFECT: deleting a folder that does not exist returns 500 "
                   "(delete_folder() -> False is mapped to 500) instead of 404")
def test_delete_missing_folder_is_404(client, vault):
    assert client.delete("/api/folders/nope").status_code == 404


def test_move_note(client, make_note, vault):
    make_note("n.md", "x")
    r = client.post("/api/notes/move", json={"oldPath": "n.md", "newPath": "d/n.md"})
    assert r.status_code == 200 and (vault / "d" / "n.md").exists()
    assert client.post("/api/notes/move", json={"oldPath": "n.md", "newPath": "z.md"}).status_code == 400
    assert client.post("/api/notes/move", json={"oldPath": "d/n.md"}).status_code == 400


def test_share_tokens_follow_moves(client, make_note):
    make_note("a/n.md", "x")
    make_note("ab/m.md", "y")
    t_n = client.post("/api/share/a/n.md", json={}).json()["token"]
    t_m = client.post("/api/share/ab/m.md", json={}).json()["token"]

    client.post("/api/folders/rename", json={"oldPath": "a", "newPath": "c"})
    paths = client.get("/api/shared-notes").json()["paths"]
    assert sorted(paths) == ["ab/m.md", "c/n.md"]  # "a/" prefix did not touch "ab/"
    assert client.get(f"/share/{t_n}").status_code == 200

    client.delete("/api/folders/ab")
    assert client.get(f"/share/{t_m}").status_code == 404

    client.post("/api/notes/move", json={"oldPath": "c/n.md", "newPath": "moved.md"})
    assert client.get("/api/shared-notes").json()["paths"] == ["moved.md"]
