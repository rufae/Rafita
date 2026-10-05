"""Tareas.md línea 67: esquema de respuesta único en el gateway.

- `_fallo()` y `_resultado()`: {success, error, message} en todos los fallos
  (n8n detecta el fallo por la clave `error` en la raíz del cuerpo).
- Handler HTTPException: los detail-dict salen planos (sin envoltorio
  {"detail": ...}) para que el nodo 'Firmar informe' de n8n los lea.
- send-voice y auth: mismo esquema en 503/500/401.
- n8n: nodos Enviar Telegram unificados a JSON.stringify($json) con el
  payload canónico construido aguas arriba (ver test_n8n_robustez).
"""

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from src.utils import webhook_server

SECRET = "s3cret-esquema"


def _client(monkeypatch):
    monkeypatch.setattr(webhook_server, "_webhook_secret", SECRET)
    monkeypatch.setattr(webhook_server, "_bot_ref", None)
    return TestClient(webhook_server.app)


def _sign(body: bytes) -> str:
    return hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()


# ---------- helpers del backend ----------


def test_fallo_es_esquema_unico():
    assert webhook_server._fallo("boom") == {
        "success": False,
        "error": "boom",
        "message": "boom",
    }


def test_resultado_anade_clave_error_en_fallo_de_servicio():
    ok = webhook_server._resultado({"success": True, "text": "hola"})
    assert ok == {"success": True, "text": "hola"}

    fallo = webhook_server._resultado({"success": False, "message": "sin google"})
    assert fallo["success"] is False
    assert fallo["error"] == "sin google"
    assert fallo["message"] == "sin google"

    ya_tiene = webhook_server._resultado({"success": False, "error": "x", "message": "y"})
    assert ya_tiene["error"] == "x"
    assert ya_tiene["message"] == "y"

    assert webhook_server._resultado("texto") == "texto"


# ---------- HTTP: auth y send-voice ----------


def test_auth_invalida_responde_error_en_raiz(monkeypatch):
    client = _client(monkeypatch)
    body = b"{}"
    resp = client.post(
        "/automation/briefing", content=body, headers={"X-Webhook-Signature": "mala"}
    )
    assert resp.status_code == 401
    data = resp.json()
    assert data["success"] is False
    assert data["error"] == "Invalid signature"
    assert "detail" not in data


def test_send_voice_sin_bot_responde_esquema_unico(monkeypatch):
    client = _client(monkeypatch)
    body = json.dumps({"text": "Hola"}).encode("utf-8")
    resp = client.post(
        "/automation/send-voice",
        content=body,
        headers={"X-Webhook-Signature": _sign(body)},
    )
    assert resp.status_code == 503
    data = resp.json()
    assert data["success"] is False
    assert data["error"] == "bot o chat_id no disponible"
    assert data["message"] == "bot o chat_id no disponible"


def test_send_voice_body_invalido_es_esquema_unico(monkeypatch):
    client = _client(monkeypatch)
    body = b"{}"
    resp = client.post(
        "/automation/send-voice",
        content=body,
        headers={"X-Webhook-Signature": _sign(body)},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["success"] is False
    assert data["error"] == "text required"
    assert "detail" not in data
