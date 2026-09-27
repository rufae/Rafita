"""Contestador automatico de llamadas (2026-09-27)."""

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from src.config import settings
from src.utils import webhook_server

SECRET = "s3cret"


def _post(client, payload, secret=SECRET, signed=True):
    body = json.dumps(payload).encode()
    headers = {}
    if signed:
        headers["X-Webhook-Signature"] = hmac.new(
            (secret or "").encode(), body, hashlib.sha256
        ).hexdigest()
    return client.post("/call", content=body, headers=headers)


class _FakeBot:
    def __init__(self):
        self.messages = []

    async def send_proactive_message(self, chat_id, text):
        self.messages.append((chat_id, text))


async def _fake_chat(messages, max_tokens=160, **kwargs):
    if "Resume esta llamada" in messages[0]["content"]:
        return "Quien llama: Ana\nMotivo: cita\nUrgencia: baja\nContacto: 600111222"
    return "Hola, soy Rafita. ¿Quién llama y qué necesitas?"


def _prepare(monkeypatch):
    bot = _FakeBot()
    webhook_server.configure_gateway(SECRET, bot_ref=bot)
    webhook_server._call_sessions.clear()
    monkeypatch.setattr("src.ollama_client.llm.chat", _fake_chat)
    monkeypatch.setattr(settings, "admin_ids", [123])

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(webhook_server.db, "save_chat_message", _noop)
    return bot


def test_call_conversation_and_summary(monkeypatch):
    bot = _prepare(monkeypatch)
    client = TestClient(webhook_server.app)

    first = _post(client, {"call_id": "c1", "caller": "+34600111222", "text": "Hola"})
    assert first.status_code == 200
    assert first.json()["reply"]
    assert first.json()["end"] is False

    last = _post(
        client,
        {
            "call_id": "c1",
            "caller": "+34600111222",
            "text": "Soy Ana, queria una cita",
            "end": True,
        },
    )
    assert last.status_code == 200
    assert last.json()["end"] is True
    assert "Ana" in last.json()["summary"]
    assert bot.messages and bot.messages[0][0] == 123
    assert "Llamada atendida" in bot.messages[0][1]


def test_call_requires_signature(monkeypatch):
    _prepare(monkeypatch)
    client = TestClient(webhook_server.app)
    bad = _post(client, {"call_id": "c2", "text": "Hola"}, signed=False)
    assert bad.status_code in (401, 503)


def test_call_requires_fields(monkeypatch):
    _prepare(monkeypatch)
    client = TestClient(webhook_server.app)
    bad = _post(client, {"call_id": "c3"})
    assert bad.status_code == 400
