"""I6: templates."""
import re

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def templates(make_note):
    make_note("_templates/Daily.md", "# {{title}}\nin {{folder}} on {{date}}")
    make_note("_templates/Other.md", "o")
    make_note("_templates/ignore.txt", "x")


def test_list_and_get(client, templates):
    names = [t["name"] for t in client.get("/api/templates").json()["templates"]]
    assert names == ["Daily", "Other"]
    assert client.get("/api/templates/Daily").json()["content"].startswith("# {{title}}")
    assert client.get("/api/templates/Nope").status_code == 404


def test_list_without_templates_folder(client):
    assert client.get("/api/templates").json() == {"templates": []}


def test_create_note_from_template(client, templates, vault):
    r = client.post("/api/templates/create-note", json={"templateName": "Daily", "notePath": "proj/Plan.md"})
    assert r.status_code == 200
    text = (vault / "proj" / "Plan.md").read_text()
    assert text.startswith("# Plan\nin proj on ") and re.search(r"\d{4}-\d{2}-\d{2}$", text)
    assert "{{" not in text and r.json()["content"] == text


def test_create_note_errors(client, templates, make_note):
    make_note("exists.md", "x")
    post = lambda **d: client.post("/api/templates/create-note", json=d)
    assert post(templateName="Daily", notePath="exists.md").status_code == 409
    assert post(templateName="Daily", notePath="EXISTS.md").status_code == 409
    assert post(templateName="Daily").status_code == 400
    assert post(templateName="Nope", notePath="n.md").status_code == 404
