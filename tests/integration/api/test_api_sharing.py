"""I7: share tokens and the public /share/{token} page."""
import json

import pytest

from backend import main

pytestmark = pytest.mark.integration


@pytest.fixture
def note(make_note):
    make_note("n.md", "---\ntitle: secret meta\n---\n# Public body")
    return "n"


def test_create_and_view(client, note):
    r = client.post("/api/share/n", json={})
    assert r.status_code == 200
    token = r.json()["token"]
    assert r.json()["path"] == "n.md" and r.json()["url"].endswith(f"/share/{token}")
    assert client.post("/api/share/n.md", json={}).json()["token"] == token  # reused

    page = client.get(f"/share/{token}")
    assert page.status_code == 200 and "Public body" in page.text and "secret meta" not in page.text

    status = client.get("/api/share/n").json()
    assert status["shared"] and status["token"] == token and status["url"].endswith(token)
    assert client.get("/api/shared-notes").json()["paths"] == ["n.md"]


def test_custom_slug_and_rename(client, note):
    assert client.post("/api/share/n", json={"slug": "my-slug"}).json()["token"] == "my-slug"
    assert client.post("/api/share/n", json={"slug": "new-slug"}).json()["token"] == "new-slug"
    assert client.get("/share/my-slug").status_code == 404  # old URL retired
    assert client.get("/share/new-slug").status_code == 200


def test_slug_errors(client, note, make_note):
    make_note("m.md", "x")
    client.post("/api/share/n", json={"slug": "taken"})
    r = client.post("/api/share/m", json={"slug": "taken"})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "taken"
    for slug, reason in [("ab", "too_short"), ("x" * 65, "too_long"), ("bad slug!", "invalid_chars")]:
        r = client.post("/api/share/m", json={"slug": slug})
        assert r.status_code == 400 and r.json()["detail"]["reason"] == reason
    assert client.post("/api/share/missing", json={}).status_code == 404


def test_slug_check(client, note):
    client.post("/api/share/n", json={"slug": "mine"})
    check = lambda **p: client.get("/api/share-slug", params=p).json()
    assert check(slug="free-one") == {"available": True, "reason": None}
    assert check(slug="mine") == {"available": False, "reason": "taken"}
    assert check(slug="mine", note_path="n")["available"] is True  # own token is not a conflict
    assert check(slug="a") == {"available": False, "reason": "too_short"}


def test_revoke(client, note):
    token = client.post("/api/share/n", json={}).json()["token"]
    assert client.delete("/api/share/n").json()["success"] is True
    assert client.get(f"/share/{token}").status_code == 404
    assert client.delete("/api/share/n").json()["success"] is False
    assert client.get("/api/share/n").json() == {"shared": False}


def test_deleted_note_link_is_404(client, note, vault):
    token = client.post("/api/share/n", json={}).json()["token"]
    (vault / "n.md").unlink()  # removed behind the app's back
    assert client.get(f"/share/{token}").status_code == 404
    assert client.get("/api/shared-notes").json()["paths"] == []


def test_public_with_auth_on(auth_client, note, api_key_headers, vault):
    (vault / "n.md").write_text("hello")
    token = auth_client.post("/api/share/n", json={}, headers=api_key_headers).json()["token"]
    assert auth_client.get(f"/share/{token}").status_code == 200
    assert auth_client.post("/api/share/n", json={}).status_code == 401


def token_file(vault):
    return json.loads((vault / ".share-tokens.json").read_text())


def test_deleting_note_through_api_removes_token(client, note, vault):
    token = client.post("/api/share/n", json={}).json()["token"]
    assert client.delete("/api/notes/n.md").status_code == 200
    assert token not in token_file(vault)  # token removed by the delete itself, not lazily
    assert client.get("/api/shared-notes").json()["paths"] == []
    assert client.get(f"/share/{token}").status_code == 404


def test_theme_is_stored_and_used(client, note, vault):
    r = client.post("/api/share/n", json={"theme": "dark"}).json()
    assert r["theme"] == "dark" and client.get("/api/share/n").json()["theme"] == "dark"
    assert token_file(vault)[r["token"]]["theme"] == "dark"

    dark_page = client.get(f"/share/{r['token']}").text
    renamed = client.post("/api/share/n", json={"slug": "renamed-one"}).json()["token"]
    assert client.get(f"/share/{renamed}").text == dark_page  # renaming the link keeps the theme
    (vault / "m.md").write_text("x")
    default = client.post("/api/share/m", json={}).json()["token"]  # theme defaults to light
    assert client.get(f"/share/{default}").text != dark_page


def test_public_origin_in_share_urls(client, note, monkeypatch):
    monkeypatch.setattr(main, "SHARE_PUBLIC_ORIGIN", "https://notes.example.com")
    r = client.post("/api/share/n", json={}).json()
    assert r["url"] == f"https://notes.example.com/share/{r['token']}"
    assert client.get("/api/share/n").json()["url"] == r["url"]


def test_tokens_are_persisted_on_disk(client, note, vault):
    token = client.post("/api/share/n", json={"theme": "dark"}).json()["token"]
    entry = token_file(vault)[token]
    assert (entry["path"], entry["theme"]) == ("n.md", "dark") and entry["created"]

    # A token file written outside the app (e.g. restored from backup) is picked up as-is
    (vault / "m.md").write_text("restored body")
    (vault / ".share-tokens.json").write_text(json.dumps({"abc123": {"path": "m.md", "theme": "light"}}))
    assert "restored body" in client.get("/share/abc123").text
    assert client.get("/share/" + token).status_code == 404
    assert client.get("/api/shared-notes").json()["paths"] == ["m.md"]
