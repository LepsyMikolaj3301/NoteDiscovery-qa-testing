"""I9: authentication."""
import pytest

from tests.conftest import API_KEY, PASSWORD

pytestmark = pytest.mark.integration


def test_auth_off_is_open(client):
    assert client.get("/api/notes").status_code == 200
    r = client.get("/login", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert client.get("/api/config").json()["authentication"]["enabled"] is False


def test_auth_on_rejects_anonymous(auth_client):
    r = auth_client.get("/api/notes")
    assert r.status_code == 401 and r.json() == {"detail": "Not authenticated"}
    r = auth_client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    for public in ["/health", "/login", "/api/themes/light", "/api/locales", "/manifest.json"]:
        assert auth_client.get(public).status_code == 200, public
    assert auth_client.get("/api/themes").status_code == 401  # list is protected, single theme is not


def test_login(auth_client):
    r = auth_client.post("/login", data={"password": "wrong"}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=incorrect_password"
    assert auth_client.get("/api/notes").status_code == 401

    r = auth_client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert auth_client.get("/api/notes").status_code == 200  # session cookie
    assert auth_client.get("/login", follow_redirects=False).headers["location"] == "/"


def test_logout(logged_in_client):
    assert logged_in_client.get("/logout", follow_redirects=False).status_code == 405
    assert logged_in_client.get("/api/notes").status_code == 200  # GET did not log out
    r = logged_in_client.post("/logout", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert logged_in_client.get("/api/notes").status_code == 401


@pytest.mark.parametrize("headers,status", [
    ({"X-API-Key": API_KEY}, 200),
    ({"Authorization": f"Bearer {API_KEY}"}, 200),
    ({"X-API-Key": "wrong"}, 401),
    ({"Authorization": "Bearer wrong"}, 401),
    ({"X-API-Key": ""}, 401),
])
def test_api_key(auth_client, headers, status):
    assert auth_client.get("/api/notes", headers=headers).status_code == status
