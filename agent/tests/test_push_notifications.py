"""PWA completa (mejora 6): notificaciones web push con VAPID.

- Servicio: claves derivadas del PEM, alta/baja de suscripciones, envío con
  pywebpush y limpieza de endpoints caducos (404/410).
- API: /api/push/config (pública), /api/push/{subscribe,unsubscribe} (auth).
- Avisos: error de automatización y briefing también llegan por push.
- RGPD: las suscripciones entran en export/borrado de datos.
- Script scripts/generate_vapid_keys.py genera la clave VAPID.
"""

import asyncio
import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

import src.database as database
from src.config import settings
from src.database import DatabaseManager
from src.services import push_service as push
from src.utils.web_auth import hash_password

REPO = Path(__file__).resolve().parents[2]


def _generar_clave(tmp_path: Path) -> Path:
    clave = ec.generate_private_key(ec.SECP256R1())
    pem = clave.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    ruta = tmp_path / "vapid.pem"
    ruta.write_bytes(pem)
    return ruta


@pytest.fixture
def vapid(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vapid_key_file", str(_generar_clave(tmp_path)))
    return tmp_path / "vapid.pem"


@pytest_asyncio.fixture
async def db(tmp_path):
    manager = DatabaseManager(db_path=tmp_path / "push.db")
    await manager.initialize()
    yield manager
    await manager.close()


# ---------------- claves y config ----------------


def test_config_sin_clave_queda_desactivado(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "vapid_key_file", str(tmp_path / "no-existe.pem"))
    cfg = asyncio.run(push.push_config())
    assert cfg == {"enabled": False, "publicKey": ""}


def test_config_con_clave_deriva_publica(vapid):
    cfg = asyncio.run(push.push_config())
    assert cfg["enabled"] is True
    publica = base64.urlsafe_b64decode(cfg["publicKey"] + "=" * (-len(cfg["publicKey"]) % 4))
    assert len(publica) == 65
    assert publica[0] == 0x04  # punto sin comprimir de P-256


def test_pem_malformada_no_revienta(monkeypatch, tmp_path):
    mala = tmp_path / "mala.pem"
    mala.write_text("esto no es una clave", encoding="utf-8")
    monkeypatch.setattr(settings, "vapid_key_file", str(mala))
    cfg = asyncio.run(push.push_config())
    assert cfg["enabled"] is False
    assert cfg["publicKey"] == ""


# ---------------- suscripciones ----------------


async def test_suscripcion_alta_upsert_y_baja(db, monkeypatch):
    monkeypatch.setattr(push, "db", db)
    url = "https://push.example/abc"
    ok = await push.subscribe({"endpoint": url, "keys": {"p256dh": "k1", "auth": "a1"}}, user_id=7)
    assert ok["success"] is True
    filas = await db.list_push_subscriptions()
    assert len(filas) == 1
    assert filas[0]["user_id"] == 7
    assert filas[0]["p256dh"] == "k1"

    # La misma endpoint se actualiza (no duplica).
    await push.subscribe({"endpoint": url, "keys": {"p256dh": "k2", "auth": "a2"}}, user_id=9)
    filas = await db.list_push_subscriptions()
    assert len(filas) == 1
    assert filas[0]["user_id"] == 9
    assert filas[0]["p256dh"] == "k2"

    assert (await push.unsubscribe({"endpoint": url}))["success"] is True
    assert await db.list_push_subscriptions() == []
    assert (await push.unsubscribe({"endpoint": url}))["success"] is True  # idempotente


async def test_suscripcion_invalida_rechazada(db, monkeypatch):
    monkeypatch.setattr(push, "db", db)
    inseguro = await push.subscribe(
        {"endpoint": "http://inseguro/abc", "keys": {"p256dh": "k", "auth": "a"}},
        user_id=1,
    )
    assert inseguro["success"] is False
    sin_keys = await push.subscribe({"endpoint": "https://push.example/abc", "keys": {}}, user_id=1)
    assert sin_keys["success"] is False
    assert await db.list_push_subscriptions() == []


# ---------------- envío ----------------


class _Respuesta:
    def __init__(self, status_code):
        self.status_code = status_code


class _FalloPush(Exception):
    def __init__(self, status_code):
        self.response = _Respuesta(status_code)


async def test_send_sin_clave_se_omite(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "vapid_key_file", str(tmp_path / "no.pem"))
    res = await push.send_web_push("hola", "mundo")
    assert res["sent"] == 0
    assert "VAPID" in res["skipped"]


async def test_send_sin_suscripciones_se_omite(vapid, db, monkeypatch):
    monkeypatch.setattr(push, "db", db)
    res = await push.send_web_push("hola", "mundo")
    assert res["skipped"] == "sin suscripciones"


async def test_send_envia_y_limpia_endpoint_caduco(vapid, db, monkeypatch):
    monkeypatch.setattr(push, "db", db)
    for endpoint in ("https://push.example/viva", "https://push.example/muerta"):
        await push.subscribe(
            {"endpoint": endpoint, "keys": {"p256dh": "k", "auth": "a"}}, user_id=1
        )

    enviados = []

    def fake_webpush(subscription_info, data, vapid_private_key, vapid_claims):
        payload = json.loads(data)
        enviados.append((subscription_info["endpoint"], payload, vapid_claims["sub"]))
        if subscription_info["endpoint"].endswith("muerta"):
            raise _FalloPush(410)

    monkeypatch.setattr(push, "webpush", fake_webpush)
    res = await push.send_web_push("Briefing de hoy", "resumen del dia")

    assert res == {"sent": 1, "failed": 0, "stale_removed": 1}
    assert enviados[0][1] == {
        "title": "Briefing de hoy",
        "body": "resumen del dia",
        "url": "/app",
    }
    assert enviados[0][2].startswith("mailto:")
    restantes = await db.list_push_subscriptions()
    assert [f["endpoint"] for f in restantes] == ["https://push.example/viva"]


async def test_send_fallo_no_caduco_no_borra(vapid, db, monkeypatch):
    monkeypatch.setattr(push, "db", db)
    await push.subscribe(
        {"endpoint": "https://push.example/ok", "keys": {"p256dh": "k", "auth": "a"}},
        user_id=1,
    )

    def fake_webpush(**kwargs):
        raise RuntimeError("sin red")

    monkeypatch.setattr(push, "webpush", fake_webpush)
    res = await push.send_web_push("t", "b")
    assert res == {"sent": 0, "failed": 1, "stale_removed": 0}
    assert len(await db.list_push_subscriptions()) == 1


# ---------------- API ----------------


@pytest.fixture
def cliente_push(monkeypatch, tmp_path):
    manager = DatabaseManager(db_path=tmp_path / "web.db")
    asyncio.run(manager.initialize())
    monkeypatch.setattr(settings, "web_auth_secret", "secreto-web-test")
    for nombre in (
        "create_web_user",
        "get_web_user",
        "get_web_user_by_email",
        "count_web_users",
        "list_web_users",
        "add_push_subscription",
        "list_push_subscriptions",
        "delete_push_subscription",
    ):
        monkeypatch.setattr(database.db, nombre, getattr(manager, nombre))
    from src.utils import webhook_server

    client = TestClient(webhook_server.app)
    asyncio.run(manager.create_web_user("admin@x.com", hash_password("clave12345"), is_admin=True))
    yield client, manager
    asyncio.run(manager.close())


def _token(client):
    resp = client.post("/api/auth/login", json={"email": "admin@x.com", "password": "clave12345"})
    assert resp.status_code == 200
    return resp.json()["token"]


def test_api_config_publica(vapid, cliente_push):
    client, _ = cliente_push
    resp = client.get("/api/push/config")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
    assert len(resp.json()["publicKey"]) > 80


def test_api_subscribe_requiere_auth(vapid, cliente_push):
    client, manager = cliente_push
    sin = client.post("/api/push/subscribe", json={"endpoint": "https://x/y"})
    assert sin.status_code == 401

    headers = {"Authorization": "Bearer " + _token(client)}
    malo = client.post(
        "/api/push/subscribe",
        json={"endpoint": "http://inseguro", "keys": {"p256dh": "k", "auth": "a"}},
        headers=headers,
    )
    assert malo.status_code == 200
    assert malo.json()["success"] is False

    bueno = client.post(
        "/api/push/subscribe",
        json={
            "endpoint": "https://push.example/navegador",
            "keys": {"p256dh": "k", "auth": "a"},
        },
        headers=headers,
    )
    assert bueno.json()["success"] is True
    assert len(asyncio.run(manager.list_push_subscriptions())) == 1

    baja = client.post(
        "/api/push/unsubscribe",
        json={"endpoint": "https://push.example/navegador"},
        headers=headers,
    )
    assert baja.json()["success"] is True
    assert asyncio.run(manager.list_push_subscriptions()) == []


# ---------------- avisos (Telegram + push) ----------------


def test_aviso_automatizacion_tambien_envia_push(monkeypatch):
    from src.utils import webhook_server

    llamadas = []

    async def fake_send(title, body, url="/app"):
        llamadas.append((title, body, url))
        return {"sent": 1}

    monkeypatch.setattr("src.services.push_service.send_web_push", fake_send)
    monkeypatch.setattr(settings, "admin_ids", [])  # sin Telegram: aun asi push

    ok = asyncio.run(webhook_server._notify_automation_error("Facturas", "timeout 504"))
    assert ok is False  # sin admins de Telegram
    assert llamadas == [("Automatización con fallos", "Workflow: Facturas — timeout 504", "/app")]


def test_briefing_tambien_envia_push(monkeypatch):
    from src.services import automation_service as auto

    class _GS:
        is_ready = True

        async def initialize(self):
            return True

        async def list_calendar_events(self, **kwargs):
            return {"events": []}

        async def list_tasks(self, **kwargs):
            return {"tasks": []}

        async def search_gmail(self, **kwargs):
            return {"messages": []}

    monkeypatch.setattr("src.services.google_services_manager.google_services", _GS())

    async def fake_weather():
        return ""

    async def fake_server():
        return {}

    async def fake_alerts():
        return []

    monkeypatch.setattr(auto, "_weather", fake_weather)
    monkeypatch.setattr(auto, "_server_status", fake_server)
    monkeypatch.setattr(auto, "_aemet_alerts", fake_alerts)

    class _LLM:
        async def chat(self, **kwargs):
            raise RuntimeError("sin modelo")

    monkeypatch.setattr("src.ollama_client.llm", _LLM())

    llamadas = []

    async def fake_send(title, body, url="/app"):
        llamadas.append((title, body))
        return {"sent": 1}

    monkeypatch.setattr("src.services.push_service.send_web_push", fake_send)

    result = asyncio.run(auto.build_briefing())
    assert result["success"]
    assert llamadas and llamadas[0][0] == "Briefing de hoy"
    assert llamadas[0][1]  # preview no vacio


# ---------------- RGPD ----------------


async def test_gdpr_incluye_suscripciones(db):
    await db.add_push_subscription(1, "https://push.example/x", "k", "a")
    exportado = await db.export_user_data(5, 1)
    assert exportado.get("push_subscriptions")
    assert exportado["push_subscriptions"][0]["endpoint"] == "https://push.example/x"
    borrado = await db.delete_user_data(5, 1)
    assert borrado.get("push_subscriptions") == 1
    assert await db.list_push_subscriptions() == []


# ---------------- script de claves ----------------


def test_script_generate_vapid_keys(tmp_path):
    ruta = tmp_path / "clave.pem"
    comando = [sys.executable, str(REPO / "scripts" / "generate_vapid_keys.py"), str(ruta)]
    primero = subprocess.run(comando, capture_output=True, text=True, timeout=30)
    assert primero.returncode == 0, primero.stderr
    assert ruta.exists()
    assert "applicationServerKey" in primero.stdout
    segundo = subprocess.run(comando, capture_output=True, text=True, timeout=30)
    assert segundo.returncode == 1
    assert "Ya existe" in segundo.stdout
