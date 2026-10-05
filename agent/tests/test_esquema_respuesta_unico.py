"""Tareas.md línea 67: esquema de respuesta único en el gateway.

- `_fallo()` y `_resultado()`: {success, error, message} en todos los fallos
  (n8n detecta el fallo por la clave `error` en la raíz del cuerpo).
- Handler HTTPException: los detail-dict salen planos (sin envoltorio
  {"detail": ...}) para que el nodo 'Firmar informe' de n8n los lea.
- send-voice y auth: mismo esquema en 503/500/401.
- n8n: nodos Enviar Telegram unificados a JSON.stringify($json) con el
  payload canónico construido aguas arriba (ver test_n8n_robustez).
- Línea 68 (flag genérico de silencio): toda respuesta lleva `notify`;
  solo `_notificar()` lo enciende cuando el campo relevante tiene contenido
  y las guardas de n8n exigen `r.notify === true` (fail-closed).
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
        "notify": False,
    }


def test_resultado_anade_clave_error_en_fallo_de_servicio():
    ok = webhook_server._resultado({"success": True, "text": "hola"})
    assert ok == {"success": True, "text": "hola", "notify": False}

    fallo = webhook_server._resultado({"success": False, "message": "sin google"})
    assert fallo["success"] is False
    assert fallo["error"] == "sin google"
    assert fallo["message"] == "sin google"
    assert fallo["notify"] is False

    ya_tiene = webhook_server._resultado({"success": False, "error": "x", "message": "y"})
    assert ya_tiene["error"] == "x"
    assert ya_tiene["message"] == "y"
    assert ya_tiene["notify"] is False

    assert webhook_server._resultado("texto") == "texto"


def test_notificar_enciende_notify_solo_si_hay_contenido():
    silencio = webhook_server._notificar({"success": True, "text": ""}, "text")
    assert silencio["notify"] is False
    aviso = webhook_server._notificar({"success": True, "text": "hola"}, "text")
    assert aviso["notify"] is True
    fallo = webhook_server._notificar({"success": False, "message": "x"}, "changes")
    assert fallo["notify"] is False
    assert fallo["error"] == "x"


def test_endpoints_automation_encienden_notify_solo_con_info(monkeypatch):
    """Cada endpoint /automation/* enciende `notify` solo con info relevante:
    es el único interruptor de silencio que leen las guardas de n8n."""
    from src.services import (
        automation_service,
        crm_service,
        sequence_service,
        sync_service,
    )

    client = _client(monkeypatch)

    def firma_post(path, payload=None):
        body = json.dumps(payload if payload is not None else {}).encode("utf-8")
        return client.post(path, content=body, headers={"X-Webhook-Signature": _sign(body)})

    async def briefing_con():
        return {"success": True, "text": "Buenos dias"}

    async def briefing_sin():
        return {"success": False, "message": "sin datos"}

    async def inbox_con(hours=2, max_results=8):
        return {"success": True, "urgent_count": 2, "items": []}

    async def inbox_sin(hours=2, max_results=8):
        return {"success": True, "urgent_count": 0, "items": []}

    async def radar_con():
        return {"success": True, "text": "novedades"}

    async def radar_sin():
        return {"success": False, "message": "fallo"}

    async def infra_con():
        return {"success": True, "text": "todo ok"}

    async def infra_sin():
        return {"success": False, "message": "fallo"}

    async def sync_con():
        return {"success": True, "changes": 3, "message": "x"}

    async def sync_sin():
        return {"success": True, "changes": 0, "message": ""}

    async def crm_con(dias=7):
        return [{"nombre": "Ana", "estado": "caliente", "motivo": "llamar"}]

    async def crm_sin(dias=7):
        return []

    async def seq_con(dry_run=False, max_envios=3):
        return {"success": True, "changes": 2, "sent": [1], "message": "enviados"}

    async def seq_sin(dry_run=False, max_envios=3):
        return {"success": True, "changes": 0, "sent": [], "message": ""}

    casos = [
        ("/automation/briefing", automation_service, "build_briefing", briefing_con, briefing_sin),
        ("/automation/inbox-scan", automation_service, "scan_inbox", inbox_con, inbox_sin),
        ("/automation/radar", automation_service, "radar", radar_con, radar_sin),
        ("/automation/infra-report", automation_service, "infra_report", infra_con, infra_sin),
        ("/automation/sync", sync_service, "sync_google_vault", sync_con, sync_sin),
        ("/automation/crm-remind", crm_service, "seguimientos_pendientes", crm_con, crm_sin),
        (
            "/automation/sequences-run",
            sequence_service,
            "ejecutar_secuencias",
            seq_con,
            seq_sin,
        ),
    ]
    for path, modulo, attr, con, sin in casos:
        monkeypatch.setattr(modulo, attr, con)
        assert firma_post(path).json()["notify"] is True, "%s sin info" % path
        monkeypatch.setattr(modulo, attr, sin)
        assert firma_post(path).json()["notify"] is False, "%s en silencio" % path

    # capture: la decisión de avisar es del agente, n8n nunca spamea
    async def captura(**kwargs):
        return {"success": True, "path": "nota.md"}

    monkeypatch.setattr(automation_service, "capture_to_vault", captura)
    assert firma_post("/automation/capture", {"text": "nota"}).json()["notify"] is False

    # send-voice sin bot: el error también lleva el flag en silencio
    resp = firma_post("/automation/send-voice", {"text": "hola"})
    assert resp.status_code == 503
    assert resp.json()["notify"] is False


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
