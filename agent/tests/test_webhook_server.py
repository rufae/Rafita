"""Fail-closed webhook authentication tests (task 0.4)."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from src.config import settings
from src.utils import webhook_server

PROTECTED_POSTS = [
    ("/webhook/test", {"chat_id": 1, "message": "hola"}),
    ("/connector/foo", {"type": "custom"}),
    ("/gmail/check", {}),
    ("/homeassistant/luz", {"action": "toggle"}),
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(webhook_server, "_bot_ref", None)
    monkeypatch.setattr(webhook_server, "_webhook_secret", "")
    return TestClient(webhook_server.app)


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


class TestFailClosed:
    @pytest.mark.parametrize(("path", "payload"), PROTECTED_POSTS)
    def test_unconfigured_secret_rejects(self, client, monkeypatch, path, payload):
        body = json.dumps(payload).encode()
        response = client.post(path, content=body)
        assert response.status_code == 503

    def test_connector_delete_rejected_when_unconfigured(self, client):
        assert client.delete("/connector/foo").status_code == 503

    @pytest.mark.parametrize(("path", "payload"), PROTECTED_POSTS)
    def test_missing_signature_rejected(self, client, monkeypatch, path, payload):
        monkeypatch.setattr(webhook_server, "_webhook_secret", "s3cret")
        body = json.dumps(payload).encode()
        assert client.post(path, content=body).status_code == 401

    @pytest.mark.parametrize(("path", "payload"), PROTECTED_POSTS)
    def test_bad_signature_rejected(self, client, monkeypatch, path, payload):
        monkeypatch.setattr(webhook_server, "_webhook_secret", "s3cret")
        body = json.dumps(payload).encode()
        response = client.post(path, content=body, headers={"X-Webhook-Signature": "deadbeef"})
        assert response.status_code == 401

    def test_valid_signature_accepted(self, client, monkeypatch):
        secret = "s3cret"
        monkeypatch.setattr(webhook_server, "_webhook_secret", secret)
        body = json.dumps({"chat_id": 1, "message": "hola"}).encode()
        response = client.post(
            "/webhook/test",
            content=body,
            headers={"X-Webhook-Signature": _sign(body, secret)},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "queued"


# ---------- Rate limit por IP (auditoría 2026-10, S1) ----------


def test_rate_limiter_bloquea_exceso_y_deja_pasar_luego(monkeypatch):
    limiter = webhook_server._IpRateLimiter(max_requests=3, window_s=60.0)
    assert [limiter.allow("1.2.3.4") for _ in range(3)] == [True, True, True]
    assert limiter.allow("1.2.3.4") is False  # 4a peticion dentro de la ventana
    assert limiter.allow("5.6.7.8") is True  # por IP, no global


def test_rate_limiter_desactivado_con_cero(monkeypatch):
    limiter = webhook_server._IpRateLimiter(max_requests=0)
    assert all(limiter.allow("1.2.3.4") for _ in range(100))


def test_rate_limiter_ventana_deslizante(monkeypatch):
    import unittest.mock as mock

    # Reloj controlado: el test original usaba un valor fijo (9999) que solo
    # pasaba si el uptime iba por debajo — flaky. Ahora el tiempo es 100% del test.
    reloj = {"t": 100.0}
    with mock.patch.object(webhook_server.time, "monotonic", side_effect=lambda: reloj["t"]):
        limiter = webhook_server._IpRateLimiter(max_requests=1, window_s=60.0)
        assert limiter.allow("ip") is True
        reloj["t"] = 150.0  # +50s: aún dentro de la ventana
        assert limiter.allow("ip") is False
        reloj["t"] = 161.0  # +61s: ventana expirada, vuelve a permitir
        assert limiter.allow("ip") is True


def test_middleware_devuelve_429(monkeypatch):
    import unittest.mock as mock

    monkeypatch.setattr(settings, "webhook_rate_limit", 1)
    with mock.patch.object(webhook_server, "_rate_limiter", webhook_server._IpRateLimiter(1)):
        client = TestClient(webhook_server.app)
        r1 = client.post("/webhook/test", content=b"{}")
        r2 = client.post("/webhook/test", content=b"{}")
        assert r1.status_code != 429
        assert r2.status_code == 429
        assert "Too Many Requests" in r2.json()["detail"]
