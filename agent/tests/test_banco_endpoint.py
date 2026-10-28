"""Endpoint /automation/banco-gasto (captura desde la app del banco)."""

import json

import pytest
from fastapi.testclient import TestClient

from src.utils import webhook_server

PAYLOAD = {"amount": 45.2, "merchant": "Mercadona", "text": "Google Pay", "dedupe_key": "k1"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(webhook_server, "_bot_ref", None)
    monkeypatch.setattr(webhook_server, "_webhook_secret", "")
    monkeypatch.setattr(webhook_server.settings, "banco_webhook_token", "")
    monkeypatch.setattr(webhook_server.settings, "admin_ids", [1])
    return TestClient(webhook_server.app)


def _post(client, payload, headers=None):
    body = json.dumps(payload).encode()
    return client.post("/automation/banco-gasto", content=body, headers=headers or {})


def test_fail_closed_sin_token(client):
    assert _post(client, PAYLOAD).status_code == 503


def test_token_invalido(client, monkeypatch):
    monkeypatch.setattr(webhook_server.settings, "banco_webhook_token", "tok-bueno")
    assert _post(client, PAYLOAD).status_code == 401
    assert _post(client, PAYLOAD, headers={"X-Banco-Token": "otro"}).status_code == 401


def test_json_invalido(client, monkeypatch):
    monkeypatch.setattr(webhook_server.settings, "banco_webhook_token", "tok-bueno")
    resp = client.post(
        "/automation/banco-gasto",
        content=b"no-json",
        headers={"X-Banco-Token": "tok-bueno"},
    )
    assert resp.status_code == 400


def test_gasto_ok_registra_y_avisa(client, monkeypatch):
    from src.services import finance_service

    monkeypatch.setattr(webhook_server.settings, "banco_webhook_token", "tok-bueno")

    capturas = {}

    async def _falso_registrar(**kw):
        capturas.update(kw)
        return {
            "success": True,
            "notify": True,
            "record_id": 9,
            "amount": kw["amount"],
            "concepto": kw.get("merchant") or "Pago",
            "message": "ok",
        }

    monkeypatch.setattr(finance_service, "registrar_gasto_banco", _falso_registrar)

    class _Bot:
        enviados = []

        async def send_proactive_message(self, chat_id, text):
            self.enviados.append((chat_id, text))

    bot = _Bot()
    monkeypatch.setattr(webhook_server, "_bot_ref", bot)

    resp = _post(client, PAYLOAD, headers={"X-Banco-Token": "tok-bueno"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert capturas["amount"] == 45.2
    assert capturas["merchant"] == "Mercadona"
    assert len(bot.enviados) == 1
    assert "45.20" in bot.enviados[0][1]
    assert "9" in bot.enviados[0][1]


def test_domiciliado_no_avisa(client, monkeypatch):
    from src.services import finance_service

    monkeypatch.setattr(webhook_server.settings, "banco_webhook_token", "tok-bueno")

    async def _falso_registrar(**kw):
        return {
            "success": True,
            "notify": False,
            "skipped": "domiciliado",
            "message": "domiciliacion",
        }

    monkeypatch.setattr(finance_service, "registrar_gasto_banco", _falso_registrar)

    class _Bot:
        llamados = 0

        async def send_proactive_message(self, chat_id, text):
            self.llamados += 1

    bot = _Bot()
    monkeypatch.setattr(webhook_server, "_bot_ref", bot)

    resp = _post(
        client,
        {"amount": 39.99, "text": "Domiciliación gimnasio"},
        headers={"X-Banco-Token": "tok-bueno"},
    )
    assert resp.status_code == 200
    assert bot.llamados == 0


# ---------- /automation/banco-ingreso (auditoría 2026-10) ----------

PAYLOAD_ING = {"amount": 1500.0, "merchant": "Nómina", "text": "Transferencia recibida"}


def _post_ingreso(client, payload, headers=None):
    body = json.dumps(payload).encode()
    return client.post("/automation/banco-ingreso", content=body, headers=headers or {})


def test_ingreso_fail_closed_y_401(client):
    assert _post_ingreso(client, PAYLOAD_ING).status_code == 503


def test_ingreso_token_invalido(client, monkeypatch):
    monkeypatch.setattr(webhook_server.settings, "banco_webhook_token", "tok-bueno")
    assert _post_ingreso(client, PAYLOAD_ING).status_code == 401
    assert _post_ingreso(client, PAYLOAD_ING, headers={"X-Banco-Token": "otro"}).status_code == 401


def test_ingreso_ok_registra_y_avisa(client, monkeypatch):
    from src.services import finance_service

    monkeypatch.setattr(webhook_server.settings, "banco_webhook_token", "tok-bueno")

    capturas = {}

    async def _falso_registrar(**kw):
        capturas.update(kw)
        return {
            "success": True,
            "notify": True,
            "record_id": 11,
            "amount": kw["amount"],
            "concepto": kw.get("merchant") or "Ingreso",
            "message": "ok",
        }

    monkeypatch.setattr(finance_service, "registrar_ingreso_banco", _falso_registrar)

    class _Bot:
        enviados = []

        async def send_proactive_message(self, chat_id, text):
            self.enviados.append((chat_id, text))

    bot = _Bot()
    monkeypatch.setattr(webhook_server, "_bot_ref", bot)

    resp = _post_ingreso(client, PAYLOAD_ING, headers={"X-Banco-Token": "tok-bueno"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert capturas["amount"] == 1500.0
    assert len(bot.enviados) == 1
    assert "1500.00" in bot.enviados[0][1]
    assert "Ingreso" in bot.enviados[0][1]
    assert "11" in bot.enviados[0][1]
