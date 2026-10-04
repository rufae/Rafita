"""Tareas.md Fase 1: observabilidad de automatizaciones n8n.

- DB: automation_runs idempotente por execution_id + listados.
- Endpoint /api/n8n/run: HMAC, validacion de payload y alerta a los admins
  solo tras 2 fallos seguidos del mismo workflow (sin spam).
- Tool get_automation_runs («¿qué automatizaciones han fallado?»).
- Regla TASK_RULE en el prompt (tareas y compromisos con fecha).
"""

import hashlib
import hmac
import json

import pytest_asyncio
from fastapi.testclient import TestClient

from src.config import settings
from src.database import DatabaseManager
from src.handlers import chat as chat_mod
from src.utils import webhook_server

SECRET = "s3cret-n8n"


@pytest_asyncio.fixture
async def db(tmp_path):
    manager = DatabaseManager(db_path=tmp_path / "runs.db")
    await manager.initialize()
    yield manager
    await manager.close()


def _sign(body: bytes) -> str:
    return hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _post(client, payload):
    body = json.dumps(payload).encode("utf-8")
    return client.post(
        "/api/n8n/run",
        content=body,
        headers={"Content-Type": "application/json", "X-Webhook-Signature": _sign(body)},
    )


class _FakeDB:
    def __init__(self):
        self.runs = []

    async def record_automation_run(
        self, execution_id, workflow, status, error=None, finished_at=None
    ):
        if any(r["execution_id"] == execution_id for r in self.runs):
            return False
        self.runs.append(
            {"execution_id": execution_id, "workflow": workflow, "status": status, "error": error}
        )
        return True

    async def last_automation_run(self, workflow):
        for r in reversed(self.runs):
            if r["workflow"] == workflow:
                return {"status": r["status"], "created_at": "ahora"}
        return None


class _FakeBot:
    def __init__(self):
        self.messages = []

    async def send_proactive_message(self, chat_id, text):
        self.messages.append((chat_id, text))


def _client(monkeypatch, fake_db=None, bot=None):
    monkeypatch.setattr(webhook_server, "_webhook_secret", SECRET)
    monkeypatch.setattr(webhook_server, "_bot_ref", bot)
    if fake_db is not None:
        monkeypatch.setattr(webhook_server, "db", fake_db)
    return TestClient(webhook_server.app)


# ---------- DB ----------


async def test_record_es_idempotente_por_execution_id(db):
    assert await db.record_automation_run("exec-1", "Briefing", "ok") is True
    assert await db.record_automation_run("exec-1", "Briefing", "ok") is False
    rows = await db.list_automation_runs(days=7)
    assert len(rows) == 1
    assert rows[0]["workflow"] == "Briefing"


async def test_list_filtrado_errores_y_ultima_ejecucion(db):
    await db.record_automation_run("e1", "A", "ok")
    await db.record_automation_run("e2", "B", "error", "boom")
    errores = await db.list_automation_runs(days=7, only_errors=True)
    assert [r["execution_id"] for r in errores] == ["e2"]
    assert len(await db.list_automation_runs(days=7)) == 2
    last = await db.last_automation_run("A")
    assert last and last["status"] == "ok"
    assert await db.last_automation_run("nadie") is None


async def test_list_vacio_fuera_de_ventana(db):
    assert await db.list_automation_runs(days=1) == []
    assert await db.list_automation_runs(days=1, only_errors=True) == []


async def test_record_es_visible_para_otros_procesos(db, tmp_path):
    # Regresión 2026-10-04 (visto en vivo en el HP): execute() no commitea,
    # la fila quedaba en la transaccion abierta y otros procesos veian
    # automation_runs vacia pese a que /api/n8n/run respondia success:true.
    import sqlite3

    assert await db.record_automation_run("exec-commit", "Briefing", "ok") is True
    conn = sqlite3.connect(str(tmp_path / "runs.db"))
    rows = conn.execute("SELECT execution_id, status FROM automation_runs").fetchall()
    conn.close()
    assert rows == [("exec-commit", "ok")]


# ---------- Endpoint ----------


def test_endpoint_rechaza_sin_firma_o_firma_mala(monkeypatch):
    fake = _FakeDB()
    client = _client(monkeypatch, fake)
    body = b'{"workflow":"A","execution_id":"x1","status":"ok"}'
    assert (
        client.post(
            "/api/n8n/run", content=body, headers={"Content-Type": "application/json"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/n8n/run",
            content=body,
            headers={"Content-Type": "application/json", "X-Webhook-Signature": "malasigna"},
        ).status_code
        == 401
    )
    assert fake.runs == []


def test_endpoint_valida_payload(monkeypatch):
    client = _client(monkeypatch, _FakeDB())
    assert _post(client, {"workflow": "", "execution_id": "x", "status": "ok"}).status_code == 400
    assert (
        _post(client, {"workflow": "A", "execution_id": "x", "status": "quiz"}).status_code == 400
    )
    assert _post(client, {"workflow": "A", "status": "ok"}).status_code == 400


def test_endpoint_almacena_y_detecta_duplicado(monkeypatch):
    fake = _FakeDB()
    client = _client(monkeypatch, fake)
    payload = {"workflow": "Rafita · 1 Briefing", "execution_id": "x1", "status": "ok"}
    resp = _post(client, payload)
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "duplicate": False, "alerted": False}
    resp2 = _post(client, payload)
    assert resp2.json()["duplicate"] is True
    assert len(fake.runs) == 1


def test_endpoint_alerta_tras_dos_fallos_seguidos(monkeypatch):
    fake = _FakeDB()
    bot = _FakeBot()
    monkeypatch.setattr(settings, "admin_ids", [7])
    client = _client(monkeypatch, fake, bot=bot)

    primero = _post(
        client, {"workflow": "Inbox", "execution_id": "e1", "status": "error", "error": "timeout"}
    )
    assert primero.json()["alerted"] is False
    assert bot.messages == []

    segundo = _post(
        client, {"workflow": "Inbox", "execution_id": "e2", "status": "error", "error": "timeout"}
    )
    assert segundo.json()["alerted"] is True
    assert len(bot.messages) == 1
    assert "2 fallos seguidos" in bot.messages[0][1]
    assert "Inbox" in bot.messages[0][1]

    # Un OK despues de un fallo no alerta (hacen falta 2 seguidos).
    _post(client, {"workflow": "Inbox", "execution_id": "e3", "status": "ok"})
    _post(client, {"workflow": "Inbox", "execution_id": "e4", "status": "error"})
    assert len(bot.messages) == 1


def test_endpoint_no_alerta_si_no_hay_bot(monkeypatch):
    fake = _FakeDB()
    monkeypatch.setattr(settings, "admin_ids", [7])
    client = _client(monkeypatch, fake, bot=None)
    _post(client, {"workflow": "X", "execution_id": "a", "status": "error"})
    resp = _post(client, {"workflow": "X", "execution_id": "b", "status": "error"})
    assert resp.status_code == 200
    assert resp.json()["alerted"] is False


# ---------- Tool ----------


async def test_tool_get_automation_runs(monkeypatch):
    rows = [
        {
            "execution_id": "e1",
            "workflow": "Radar IA",
            "status": "error",
            "error": "HTTP 500",
            "finished_at": None,
            "created_at": "2026-10-04 09:00:00",
        },
        {
            "execution_id": "e2",
            "workflow": "CRM",
            "status": "ok",
            "error": None,
            "finished_at": None,
            "created_at": "2026-10-04 10:00:00",
        },
    ]
    captured = {}

    async def fake_list(days=7, only_errors=False, limit=50):
        captured.update(days=days, only_errors=only_errors)
        return rows if not only_errors else rows[:1]

    monkeypatch.setattr(chat_mod.db, "list_automation_runs", fake_list)
    result = await chat_mod._execute_tool(1, "get_automation_runs", {"only_errors": True})
    assert result["success"] is True
    assert result["errors"] == 1
    assert "Radar IA" in result["message"] and "error" in result["message"]
    assert captured["only_errors"] is True

    async def fake_empty(days=7, only_errors=False, limit=50):
        return []

    monkeypatch.setattr(chat_mod.db, "list_automation_runs", fake_empty)
    vacio = await chat_mod._execute_tool(1, "get_automation_runs", {})
    assert vacio["success"] is True and vacio["runs"] == []


# ---------- Prompt ----------


def test_prompt_incluye_task_rule():
    from src.core.orchestrator import build_system_prompt

    texto = build_system_prompt()
    assert "TASK_RULE" in texto
    assert "manage_google_tasks" in texto
    assert "recordatorio de seguimiento" in texto


def test_tool_definida_en_tools():
    from src.handlers.chat_tools import TOOLS_DEFINITIONS

    nombres = [t["function"]["name"] for t in TOOLS_DEFINITIONS]
    assert "get_automation_runs" in nombres


# ---------- Guards (regresión 2026-10-04) ----------


def test_mensaje_en_vinetas_detectable_si_lo_ignoran():
    # Formato «• workflow · fecha · estado»: _tool_lista_ignorada cazaba
    # saludos con listas delante pero el mensaje no tenía viñetas.
    from src.core.orchestrator import _tool_lista_ignorada

    mensaje = (
        "2 ejecucion(es) en 7 dia(s), 1 fallo(s):\n"
        "• Radar IA · 2026-10-04 09:00 · error · boom\n"
        "• CRM · 2026-10-04 10:00 · ok"
    )
    msgs = [{"role": "tool", "content": json.dumps({"success": True, "message": mensaje})}]
    assert _tool_lista_ignorada(msgs, "¡Claro! Puedo ayudarte con tus citas y tareas.") is True
    assert _tool_lista_ignorada(msgs, "Radar IA falló por 'boom' ayer a las 09:00.") is False


def test_respuesta_desviada_caza_saludos_reales():
    # gemma abrió con "¡Hola! Soy tu asistente personal..." y el guard
    # antiguo (que exigía "soy rafita") no lo cazaba: la pregunta del
    # usuario se respondía con un saludo.
    from src.core.orchestrator import _respuesta_desviada

    assert _respuesta_desviada("¡Hola! Soy tu asistente personal. Estoy aquí para ayudarte.")
    assert _respuesta_desviada("¡Hola! Estoy listo para ayudarte a organizar tu día.")
    assert _respuesta_desviada("Buenos días, ¿en qué puedo ayudarte?")
    assert not _respuesta_desviada("He creado el evento 'Dentista' el viernes a las 10:00.")
    assert not _respuesta_desviada("En los últimos 7 días no hay ejecuciones fallidas.")


def test_describir_logs_no_compite_con_automatizaciones():
    # Eligió analyze_system_logs para "¿qué automatizaciones han corrido?":
    # la descripción ahora apunta explícitamente a get_automation_runs.
    from src.handlers.chat_tools import TOOLS_DEFINITIONS

    desc = next(
        t["function"]["description"]
        for t in TOOLS_DEFINITIONS
        if t["function"]["name"] == "analyze_system_logs"
    )
    assert "get_automation_runs" in desc
    assert "automatizaciones" in desc
