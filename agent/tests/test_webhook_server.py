"""Fail-closed webhook authentication tests (task 0.4)."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

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
