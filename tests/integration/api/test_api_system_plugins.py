"""I10: system endpoints and plugin hooks."""
import pytest

from backend import main

pytestmark = pytest.mark.integration


def test_system_endpoints(client):
    assert client.get("/health").json()["status"] == "healthy"
    cfg = client.get("/api/config").json()
    assert cfg["name"] and cfg["version"] and cfg["authentication"] == {"enabled": False}

    themes = client.get("/api/themes").json()["themes"]
    assert "light" in {t["id"] for t in themes}
    assert client.get("/api/themes/light").json()["theme_id"] == "light"
    assert client.get("/api/themes/nope").status_code == 404

    codes = {l["code"] for l in client.get("/api/locales").json()["locales"]}
    assert "en-US" in codes
    assert client.get("/api/locales/en-US").status_code == 200
    assert client.get("/api/locales/nope").status_code == 404
    assert client.get("/api/locales/bad.code").status_code == 400

    assert client.get("/manifest.json").status_code == 200
    assert "javascript" in client.get("/sw.js").headers["content-type"]


class Upper:
    name, version, enabled = "Upper", "1.0", True

    def on_note_save(self, note_path, content):
        return content.upper()


@pytest.fixture
def plugin(monkeypatch, tmp_path):
    p = Upper()
    monkeypatch.setitem(main.plugin_manager.plugins, "upper", p)
    monkeypatch.setattr(main.plugin_manager, "config_file", tmp_path / "plugin_config.json")
    return p


def test_plugin_list_and_toggle(client, plugin):
    assert {"id": "upper", "name": "Upper", "version": "1.0", "enabled": True} in client.get("/api/plugins").json()["plugins"]
    r = client.post("/api/plugins/upper/toggle", json={"enabled": False})
    assert r.json() == {"success": True, "plugin": "upper", "enabled": False}
    assert plugin.enabled is False
    client.post("/api/plugins/upper/toggle", json={"enabled": True})
    assert plugin.enabled is True


def test_plugin_hook_transforms_content_only_while_enabled(client, plugin, vault):
    client.post("/api/notes/a.md", json={"content": "abc"})
    assert (vault / "a.md").read_text() == "ABC"
    client.post("/api/plugins/upper/toggle", json={"enabled": False})
    client.post("/api/notes/b.md", json={"content": "abc"})
    assert (vault / "b.md").read_text() == "abc"
