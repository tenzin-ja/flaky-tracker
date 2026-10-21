from django.test import Client


def test_healthz_returns_ok(client: Client) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_rejects_post(client: Client) -> None:
    assert client.post("/healthz").status_code == 405
