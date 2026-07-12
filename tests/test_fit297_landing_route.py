from tests.test_auth_login import _make_auth_app


def test_landing_route_renders_public_navigation(tmp_path, monkeypatch):
    app, _auth = _make_auth_app(tmp_path, monkeypatch)
    client = app.test_client()

    response = client.get("/landing")

    assert response.status_code == 200
    assert b'href="/login"' in response.data
    assert b'href="/register"' in response.data
    assert b'href="#pricing"' in response.data
    assert b'id="pricing"' in response.data


def test_landing_auth_navigation_targets_are_reachable(tmp_path, monkeypatch):
    app, _auth = _make_auth_app(tmp_path, monkeypatch)
    client = app.test_client()

    assert client.get("/landing").status_code == 200
    assert client.get("/login").status_code == 200
    assert client.get("/register").status_code == 200
