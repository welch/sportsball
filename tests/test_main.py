from sportsball.main import app


def test_index_responds() -> None:
    client = app.test_client()
    response = client.get("/")
    assert response.status_code == 200
    assert b"sportsball" in response.data


def test_healthz() -> None:
    client = app.test_client()
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.data == b"ok"
