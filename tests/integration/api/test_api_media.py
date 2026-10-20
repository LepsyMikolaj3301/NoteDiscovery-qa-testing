"""I4: media upload, download, in-place update and move."""
import pytest

from backend import main

pytestmark = pytest.mark.integration

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


def upload(client, name="pic.png", data=PNG, ctype="image/png", **form):
    return client.post("/api/upload-media", files={"file": (name, data, ctype)}, data=form)


def test_upload_and_get(client, vault):
    r = upload(client, note_path="folder/n.md")
    assert r.status_code == 200 and r.json()["type"] == "image"
    path = r.json()["path"]
    assert path.startswith("folder/_attachments/pic-") and (vault / path).read_bytes() == PNG
    got = client.get(f"/api/media/{path}")
    assert got.status_code == 200 and got.content == PNG

    root = upload(client).json()["path"]  # root-level note
    assert root.startswith("_attachments/")


def test_upload_rejections(client, monkeypatch):
    assert upload(client, name="a.txt", data=b"x", ctype="text/plain").status_code == 400
    monkeypatch.setattr(main, "UPLOAD_MAX_IMAGE_MB", 0)
    assert upload(client).status_code == 400  # too large


def test_upload_next_to_notes(client, vault):
    r = upload(client, next_to_notes="1", content_folder="d")
    assert r.status_code == 200 and r.json()["path"].startswith("d/drawing-")
    r = upload(client, name="a.jpg", data=b"x", ctype="image/jpeg", next_to_notes="1")
    assert r.status_code == 400  # PNG only


def test_get_media_errors(client, make_note, vault):
    make_note("n.md", "x")
    assert client.get("/api/media/n.md").status_code == 400  # not a media file
    assert client.get("/api/media/nope.png").status_code == 404
    assert client.get("/api/media/..%2Fx.png").status_code in (403, 404)


def test_put_media(client, vault):
    (vault / "drawing-1.png").write_bytes(PNG)
    new = PNG + b"new"
    assert client.put("/api/media/drawing-1.png", content=new).status_code == 200
    assert (vault / "drawing-1.png").read_bytes() == new
    assert client.put("/api/media/drawing-1.png", content=b"notpng!!!").status_code == 400
    assert client.put("/api/media/drawing-2.png", content=PNG).status_code == 404
    (vault / "pic.png").write_bytes(PNG)
    assert client.put("/api/media/pic.png", content=PNG).status_code == 400  # name rule


def test_move_media(client, vault):
    (vault / "a.png").write_bytes(PNG)
    (vault / "b.png").write_bytes(PNG)
    mv = lambda o, n: client.post("/api/media/move", json={"oldPath": o, "newPath": n})
    assert mv("a.png", "d/a.png").status_code == 200 and (vault / "d" / "a.png").exists()
    assert mv("d/a.png", "b.png").status_code == 409
    assert mv("zz.png", "y.png").status_code == 404
    assert mv("b.png", "b.txt").status_code == 200  # only the source extension is checked
    (vault / "n.md").write_text("x")
    assert mv("n.md", "n2.md").status_code == 400
    assert mv("b.png", "../out.png").status_code == 403
