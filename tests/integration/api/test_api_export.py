"""I8: HTML export and folder ZIP archive."""
import io
import zipfile

import pytest

pytestmark = pytest.mark.integration

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


def test_export_download_and_inline(client, make_note):
    make_note("d/My Note.md", "---\ntags: [x]\n---\n# Heading\n[[Other]]")
    r = client.get("/api/export/d/My Note.md")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "attachment" in r.headers["content-disposition"] and "My%20Note.html" in r.headers["content-disposition"]
    assert "Heading" in r.text and "tags:" not in r.text  # frontmatter stripped

    inline = client.get("/api/export/d/My Note.md?download=false")
    assert "content-disposition" not in inline.headers and "Heading" in inline.text


def test_export_theme_and_fallback(client, make_note):
    make_note("n.md", "x")
    dark = client.get("/api/export/n.md?theme=dark").text
    assert dark != client.get("/api/export/n.md?theme=light").text
    assert client.get("/api/export/n.md?theme=does-not-exist").status_code == 200  # falls back to light
    assert client.get("/api/export/missing.md").status_code == 404


def test_export_embeds_images(client, make_note, vault):
    make_note("n.md", "![pic](pic.png)")
    (vault / "pic.png").write_bytes(PNG)
    assert "data:image/png;base64," in client.get("/api/export/n.md").text


def zip_names(resp):
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/zip"
    return sorted(zipfile.ZipFile(io.BytesIO(resp.content)).namelist())


def test_archive_folder_and_vault(client, make_note, vault):
    make_note("f/a.md", "a")
    make_note("f/sub/b.md", "b")
    make_note("top.md", "t")
    (vault / "f" / "_attachments").mkdir()
    (vault / "f" / "_attachments" / "p.png").write_bytes(PNG)

    r = client.get("/api/archive", params={"folder": "f"})
    assert zip_names(r) == ["_attachments/p.png", "a.md", "sub/b.md"]
    assert "f.zip" in r.headers["content-disposition"]
    assert zip_names(client.get("/api/archive")) == ["f/_attachments/p.png", "f/a.md", "f/sub/b.md", "top.md"]


def test_archive_excludes_hidden(client, make_note, vault):
    make_note("f/a.md", "a")
    make_note("f/.hidden.md", "h")
    make_note("f/.git/config", "g")
    (vault / ".share-tokens.json").write_text("{}")
    assert zip_names(client.get("/api/archive", params={"folder": "f"})) == ["a.md"]
    assert ".share-tokens.json" not in zip_names(client.get("/api/archive"))


def test_archive_errors(client, make_note, vault):
    make_note("a.md", "a")
    (vault / ".git").mkdir()
    (vault / "empty").mkdir()
    for folder in ["nope", ".git", "..", "a.md", "empty"]:
        assert client.get("/api/archive", params={"folder": folder}).status_code == 404, folder
