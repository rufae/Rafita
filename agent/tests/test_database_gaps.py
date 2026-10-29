"""Huecos de cobertura de database.py (gate90% de CI, auditoría 2026-10).

Funciones CRUD que ningún test ejercitaba (borrados, secuencias, usuarios
web, reuniones) — misma BD SQLite real en tmp_path que el resto de tests.
"""

import pytest_asyncio
from cryptography.fernet import Fernet

from src.config import settings
from src.database import DatabaseManager
from src.utils import security_manager


@pytest_asyncio.fixture
async def db(tmp_path):
    key = Fernet.generate_key()
    backup = getattr(security_manager, "_cipher", None)
    security_manager._cipher = Fernet(key)
    settings.encryption_key = key.decode("utf-8")
    manager = DatabaseManager(db_path=tmp_path / "gaps.db")
    await manager.initialize()
    yield manager
    await manager.close()
    if backup is not None:
        security_manager._cipher = backup


async def test_delete_chat_history_y_tasks(db):
    await db.save_chat_message(1, "user", "hola")
    await db.delete_chat_history(1)

    task_id = await db.add_task(1, "comprar pan", "2026-11-01")
    pendientes = await db.list_tasks(1)
    assert len(pendientes) == 1
    assert pendientes[0]["title"] == "comprar pan"

    await db.complete_task(1, task_id)
    # show_completed=False (rama por defecto) y True (rama %s del SQL)
    assert await db.list_tasks(1) == []
    assert len(await db.list_tasks(1, show_completed=True)) == 1


async def test_events_borrado_y_reprogramacion(db):
    ev_id = await db.add_event(1, "Dentista", "2026-12-01 10:00:00")
    await db.update_event_datetime(1, ev_id, "2026-12-02 11:00:00")
    await db.delete_event(1, ev_id)
    futuro = await db.get_upcoming_events(1)
    assert all(e["id"] != ev_id for e in futuro)


async def test_secuencias_de_email_completas(db):
    seq_id = await db.create_sequence("Ana", "Ana Lopez", "ana@x.es", "presupuesto")
    await db.add_sequence_step(seq_id, 1, 0, "enviar saludo")
    await db.add_sequence_step(seq_id, 2, 3, "seguir")

    assert len(await db.list_sequences()) == 2 or len(await db.list_sequences()) >= 1
    assert len(await db.list_sequences(status="active")) >= 1
    seq = await db.get_sequence(seq_id)
    assert seq["contact_email"] == "ana@x.es"
    assert len(await db.list_sequence_steps(seq_id)) == 2

    pasos = await db.due_sequence_steps()
    assert any(p["step_no"] == 1 for p in pasos)  # delay0 = vencido

    step_id = await db.list_sequence_steps(seq_id)
    primer = step_id[0]["id"]
    await db.update_sequence_step(primer, "sent", subject="Hola", message_id="m1")
    enviado = [s for s in await db.list_sequence_steps(seq_id) if s["id"] == primer][0]
    assert enviado["status"] == "sent"
    assert enviado["subject"] == "Hola"

    await db.delete_sequence(seq_id)
    assert await db.get_sequence(seq_id) is None


async def test_web_users_y_reuniones(db):
    uid = await db.create_web_user(" Ana@X.es ", "hash123", is_admin=True)
    assert await db.count_web_users() == 1
    usuarios = await db.list_web_users()
    assert usuarios[0]["email"] == "ana@x.es"
    assert usuarios[0]["is_admin"] in (1, True)

    await db.update_web_user_password(uid, "hash456")

    m_id = await db.create_meeting(uid, "Revisión semanal")
    await db.update_meeting(m_id, status="done", duration_s=3600)
    # Sin campos permitidos -> return sin tocar nada
    await db.update_meeting(m_id, campo_raro="x")

    reunido = await db.get_meeting(m_id)
    assert reunido["status"] == "done"
    assert len(await db.list_meetings(user_id=uid)) == 1
    assert len(await db.list_meetings()) == 1  # rama sin user_id

    await db.delete_meeting(m_id)
    assert await db.get_meeting(m_id) is None


async def test_delete_finance_record(db):
    rid = await db.add_finance_record(
        chat_id=1, amount=12.5, category="expense", subcategory="banco-auto", description="test"
    )
    assert await db.delete_finance_record(1, rid) == 1
    assert await db.delete_finance_record(1, rid) == 0  # ya no existia


async def test_alerts_basico(db):
    await db.add_alert(1, "disco lleno", "warning")
    activas = await db.get_active_alerts(1)
    assert len(activas) == 1
    await db.mark_alert_read(activas[0]["id"])
    assert await db.get_active_alerts(1) == []


async def test_ai_metrics_retencion(db):
    """La tabla crecia una fila por llamada LLM sin poda (revisión 2026-10-28)."""
    await db.record_ai_metric("chat", 1.0, True)
    await db.execute(
        "INSERT INTO ai_metrics (ts, operation, latency_ms, success) "
        "VALUES (datetime('now', '-60 days'), 'vieja', 1, 1)"
    )
    await db._conn.commit()

    db._ai_metric_writes = 499  # la siguiente inserción dispara la poda
    await db.record_ai_metric("chat", 1.0, True)

    viejas = await db.fetchall("SELECT id FROM ai_metrics WHERE operation = 'vieja'")
    assert viejas == []
    vivas = await db.fetchall("SELECT id FROM ai_metrics WHERE operation = 'chat'")
    assert len(vivas) == 2
