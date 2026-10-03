"""Cobertura extra de utils/webhook_server.py: endpoints, auth HMAC y arranque."""

import hashlib
import hmac
import json
import os
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.config import settings
from src.utils import webhook_server

SECRET = "s3cret"


async def _afn(result):
    return result


class _FakeBot:
    def __init__(self, exc=None):
        self.messages = []
        self._exc = exc

    async def send_proactive_message(self, chat_id, text):
        if self._exc is not None:
            raise self._exc
        self.messages.append((chat_id, text))


def _sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _client(monkeypatch, bot=None):
    monkeypatch.setattr(webhook_server, "_webhook_secret", SECRET)
    monkeypatch.setattr(webhook_server, "_bot_ref", bot)
    webhook_server._call_sessions.clear()
    return TestClient(webhook_server.app)


async def _noop(*args, **kwargs):
    return None


# ---------- comprobaciones de salud ----------


async def test_check_vector_db_delegates(monkeypatch):
    monkeypatch.setattr("src.utils.vector_manager.vector_db.health", lambda: _afn({"status": "ok"}))
    assert (await webhook_server._check_vector_db())["status"] == "ok"


def test_check_vault_not_readable(monkeypatch, tmp_path):
    vault = tmp_path / "vault-ro"
    vault.mkdir()
    monkeypatch.setattr(settings, "obsidian_vault_dir", str(vault))
    real_access = os.access
    monkeypatch.setattr(
        os,
        "access",
        lambda path, mode: False if mode == os.R_OK else real_access(path, mode),
    )
    assert webhook_server._check_vault()["status"] == "unhealthy"


def test_check_telegram_variants(monkeypatch):
    monkeypatch.setattr(webhook_server, "_bot_ref", None)
    assert webhook_server._check_telegram()["status"] == "unhealthy"

    monkeypatch.setattr(webhook_server, "_bot_ref", SimpleNamespace())
    assert webhook_server._check_telegram()["status"] == "unhealthy"

    monkeypatch.setattr(
        webhook_server, "_bot_ref", SimpleNamespace(polling_status=lambda: {"status": "ok"})
    )
    assert webhook_server._check_telegram()["status"] == "ok"


# ---------- /metrics y /connectors ----------


def test_metrics_endpoint(monkeypatch):
    client = _client(monkeypatch)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_connectors_endpoint(monkeypatch):
    monkeypatch.setattr(
        "src.utils.app_connector.connector.list_connectors", lambda: [{"name": "gmail"}]
    )
    client = _client(monkeypatch)
    sin_token = client.get("/connectors")
    assert sin_token.status_code == 401
    response = client.get("/connectors", headers={"X-Webhook-Token": SECRET})
    assert response.json()["connectors"] == [{"name": "gmail"}]


# ---------- /webhook/{source} ----------


def test_webhook_invalid_json_requires_chat_id(monkeypatch):
    client = _client(monkeypatch)
    body = b"no es json"
    response = client.post(
        "/webhook/telegram", content=body, headers={"X-Webhook-Signature": _sign(body)}
    )
    assert response.status_code == 400


def test_webhook_missing_chat_id(monkeypatch):
    client = _client(monkeypatch)
    body = json.dumps({"message": "hola"}).encode()
    response = client.post(
        "/webhook/telegram", content=body, headers={"X-Webhook-Signature": _sign(body)}
    )
    assert response.status_code == 400


def test_webhook_delivers_to_bot(monkeypatch):
    bot = _FakeBot()
    monkeypatch.setattr(webhook_server.db, "save_chat_message", _noop)
    client = _client(monkeypatch, bot=bot)
    body = json.dumps({"chat_id": 5, "message": "hola"}).encode()
    response = client.post(
        "/webhook/telegram", content=body, headers={"X-Webhook-Signature": _sign(body)}
    )
    assert response.json()["status"] == "delivered"
    assert bot.messages and "Webhook: telegram" in bot.messages[0][1]


def test_webhook_delivery_failure_returns_500(monkeypatch):
    bot = _FakeBot(exc=RuntimeError("telegram caido"))
    monkeypatch.setattr(webhook_server.db, "save_chat_message", _noop)
    client = _client(monkeypatch, bot=bot)
    body = json.dumps({"chat_id": 5, "text": "hola"}).encode()
    response = client.post(
        "/webhook/telegram", content=body, headers={"X-Webhook-Signature": _sign(body)}
    )
    assert response.status_code == 500


# ---------- /connector/{name} ----------


def test_register_connector_endpoint(monkeypatch):
    calls = []

    async def fake_register(name, connector_type, credentials, config):
        calls.append((name, connector_type))
        return 42

    async def fake_register_boom(*args, **kwargs):
        raise RuntimeError("db caida")

    monkeypatch.setattr("src.utils.app_connector.connector.register_connector", fake_register)
    client = _client(monkeypatch)
    body = json.dumps({"type": "custom", "credentials": {"k": "v"}, "config": {}}).encode()
    response = client.post(
        "/connector/foo", content=body, headers={"X-Webhook-Signature": _sign(body)}
    )
    assert response.json() == {"status": "registered", "name": "foo", "id": 42}
    assert calls == [("foo", "custom")]

    bad = client.post(
        "/connector/foo", content=b"{mal", headers={"X-Webhook-Signature": _sign(b"{mal")}
    )
    assert bad.status_code == 400

    monkeypatch.setattr("src.utils.app_connector.connector.register_connector", fake_register_boom)
    failed = client.post(
        "/connector/foo", content=body, headers={"X-Webhook-Signature": _sign(body)}
    )
    assert failed.status_code == 500


def test_remove_connector_endpoint(monkeypatch):
    monkeypatch.setattr(
        "src.utils.app_connector.connector.remove_connector", lambda name: _afn(True)
    )
    client = _client(monkeypatch)
    headers = {"X-Webhook-Signature": _sign(b"")}
    removed = client.request("DELETE", "/connector/foo", content=b"", headers=headers)
    assert removed.json() == {"status": "removed", "name": "foo"}

    monkeypatch.setattr(
        "src.utils.app_connector.connector.remove_connector", lambda name: _afn(False)
    )
    missing = client.request("DELETE", "/connector/foo", content=b"", headers=headers)
    assert missing.status_code == 404


# ---------- gmail y home assistant ----------


def test_gmail_check_endpoint(monkeypatch):
    monkeypatch.setattr(
        "src.utils.app_connector.connector.fetch_urgent_emails", lambda **kw: _afn([{"s": 1}])
    )
    client = _client(monkeypatch)
    response = client.post(
        "/gmail/check", content=b"{}", headers={"X-Webhook-Signature": _sign(b"{}")}
    )
    assert response.json()["count"] == 1


def test_homeassistant_control_endpoint(monkeypatch):
    seen = {}

    async def fake_call(entity_id, action):
        seen["action"] = action
        return {"ok": True}

    monkeypatch.setattr("src.utils.app_connector.connector.call_home_assistant", fake_call)
    client = _client(monkeypatch)
    body = json.dumps({"action": "turn_on"}).encode()
    response = client.post(
        "/homeassistant/luz", content=body, headers={"X-Webhook-Signature": _sign(body)}
    )
    assert response.json() == {"ok": True}
    assert seen["action"] == "turn_on"

    empty = client.post(
        "/homeassistant/luz", content=b"{mal", headers={"X-Webhook-Signature": _sign(b"{mal")}
    )
    assert empty.json() == {"ok": True}
    assert seen["action"] == "toggle"


def test_homeassistant_state_endpoint(monkeypatch):
    monkeypatch.setattr(
        "src.utils.app_connector.connector.get_home_assistant_state",
        lambda entity_id: _afn({"state": "on"}),
    )
    client = _client(monkeypatch)
    response = client.get(
        "/homeassistant/state?entity_id=light.x", headers={"X-Webhook-Token": SECRET}
    )
    assert response.json() == {"state": "on"}


# ---------- contestador ----------


async def test_call_summary_falls_back_without_llm(monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("llm caido")

    monkeypatch.setattr("src.ollama_client.llm.chat", boom)
    summary = await webhook_server._call_summary(
        "Ana", [{"role": "caller", "text": "hola"}, {"role": "rafita", "text": "buenas"}]
    )
    assert "Ana" in summary
    assert "hola" in summary


async def test_notify_call_summary_without_targets(monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", [])
    monkeypatch.setattr(webhook_server, "_bot_ref", None)
    await webhook_server._notify_call_summary("Ana", "resumen")


async def test_notify_call_summary_survives_delivery_errors(monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", [1])
    monkeypatch.setattr(webhook_server.db, "save_chat_message", _noop)
    monkeypatch.setattr(webhook_server, "_bot_ref", _FakeBot(exc=RuntimeError("sin telegram")))
    await webhook_server._notify_call_summary("Ana", "resumen")


def test_call_endpoint_rejects_invalid_json(monkeypatch):
    client = _client(monkeypatch, bot=_FakeBot())
    response = client.post(
        "/call", content=b"{mal", headers={"X-Webhook-Signature": _sign(b"{mal")}
    )
    assert response.status_code == 400


def test_call_endpoint_llm_failures_use_defaults(monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("llm caido")

    monkeypatch.setattr(settings, "admin_ids", [1])
    monkeypatch.setattr(webhook_server.db, "save_chat_message", _noop)
    monkeypatch.setattr("src.ollama_client.llm.chat", boom)
    client = _client(monkeypatch, bot=_FakeBot())
    body = json.dumps({"call_id": "c9", "caller": "Ana", "text": "hola"}).encode()
    response = client.post("/call", content=body, headers={"X-Webhook-Signature": _sign(body)})
    assert "Disculpa" in response.json()["reply"]

    async def empty_reply(**kwargs):
        return ""

    monkeypatch.setattr("src.ollama_client.llm.chat", empty_reply)
    body = json.dumps({"call_id": "c10", "caller": "Ana", "text": "hola"}).encode()
    response = client.post("/call", content=body, headers={"X-Webhook-Signature": _sign(body)})
    assert "Sigues ahi" in response.json()["reply"]


def test_call_endpoint_prunes_stale_sessions(monkeypatch):
    async def fake_chat(messages, max_tokens=160, **kwargs):
        return "Bien, se lo dire."

    monkeypatch.setattr(settings, "admin_ids", [1])
    monkeypatch.setattr(webhook_server.db, "save_chat_message", _noop)
    monkeypatch.setattr("src.ollama_client.llm.chat", fake_chat)
    client = _client(monkeypatch, bot=_FakeBot())
    webhook_server._call_sessions["vieja"] = {"turns": [], "updated": 0.0}
    body = json.dumps({"call_id": "c11", "caller": "Ana", "text": "hola"}).encode()
    response = client.post("/call", content=body, headers={"X-Webhook-Signature": _sign(body)})
    assert response.status_code == 200
    assert "vieja" not in webhook_server._call_sessions
    assert "c11" in webhook_server._call_sessions


# ---------- arranque del gateway ----------


async def test_start_gateway_server_builds_uvicorn(monkeypatch):
    created = {}

    class _FakeServer:
        def __init__(self, config):
            created["config"] = config

        async def serve(self):
            created["served"] = True

    monkeypatch.setattr(
        webhook_server.uvicorn, "Config", lambda *args, **kwargs: {"kwargs": kwargs}
    )
    monkeypatch.setattr(webhook_server.uvicorn, "Server", _FakeServer)
    await webhook_server.start_gateway_server(host="127.0.0.1", port=8765)
    assert created["served"] is True
    assert created["config"]["kwargs"]["port"] == 8765
