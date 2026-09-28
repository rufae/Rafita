"""Cobertura de database.py con una BD SQLite real en tmp_path (sin mocks de red)."""

import sqlite3
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from src.config import settings
from src.database import DatabaseManager
from src.utils import security_manager


@pytest.fixture
def crypto(monkeypatch):
    """Fernet fija para que encrypt/decrypt no toquen el entorno ni el .env."""
    key = Fernet.generate_key()
    monkeypatch.setattr(security_manager, "_cipher", Fernet(key))
    monkeypatch.setattr(settings, "encryption_key", key.decode("utf-8"))
    return key


@pytest_asyncio.fixture
async def db(tmp_path, crypto):
    manager = DatabaseManager(db_path=tmp_path / "test.db")
    await manager.initialize()
    yield manager
    await manager.close()


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


async def test_initialize_creates_tables_and_wal(db):
    cols = await db.fetchall("SELECT name FROM sqlite_master WHERE type='table'")
    names = {c["name"] for c in cols}
    for table in (
        "chat_history",
        "events",
        "alerts",
        "finance_records",
        "exports",
        "user_preferences",
        "personal_knowledge",
        "app_connectors",
        "voice_sessions",
        "kv_store",
        "second_brain_log",
        "credentials",
    ):
        assert table in names
    mode = await db.fetchone("PRAGMA journal_mode")
    assert mode["journal_mode"].lower() == "wal"


async def test_initialize_migrates_legacy_schema(tmp_path, crypto):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,"
        "title TEXT NOT NULL, description TEXT, event_datetime TEXT NOT NULL,"
        "created_at TEXT NOT NULL DEFAULT (datetime('now')),"
        "is_active INTEGER NOT NULL DEFAULT 1)"
    )
    conn.execute(
        "CREATE TABLE alerts ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,"
        "message TEXT NOT NULL, alert_type TEXT NOT NULL DEFAULT 'info',"
        "created_at TEXT NOT NULL DEFAULT (datetime('now')), expires_at TEXT,"
        "is_read INTEGER NOT NULL DEFAULT 0)"
    )
    conn.commit()
    conn.close()

    manager = DatabaseManager(db_path=path)
    await manager.initialize()
    try:
        event_cols = {r["name"] for r in await manager.fetchall("PRAGMA table_info(events)")}
        alert_cols = {r["name"] for r in await manager.fetchall("PRAGMA table_info(alerts)")}
        assert "google_event_id" in event_cols
        assert "pattern" in alert_cols
        assert "next_run" in alert_cols
    finally:
        await manager.close()


async def test_transaction_commits_and_rolls_back(db):
    async with db.transaction() as conn:
        await conn.execute(
            "INSERT INTO chat_history (chat_id, role, content) VALUES (?, ?, ?)",
            (1, "user", "commit-me"),
        )
    rows = await db.fetchall("SELECT * FROM chat_history WHERE content = 'commit-me'")
    assert len(rows) == 1

    with pytest.raises(RuntimeError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO chat_history (chat_id, role, content) VALUES (?, ?, ?)",
                (1, "user", "rollback-me"),
            )
            raise RuntimeError("boom")
    rows = await db.fetchall("SELECT * FROM chat_history WHERE content = 'rollback-me'")
    assert rows == []


async def test_not_initialized_raises(tmp_path):
    manager = DatabaseManager(db_path=tmp_path / "never.db")
    with pytest.raises(RuntimeError):
        async with manager.transaction():
            pass
    with pytest.raises(RuntimeError):
        await manager.execute("SELECT 1")
    with pytest.raises(RuntimeError):
        await manager.executemany("SELECT 1", [(1,)])


async def test_execute_helpers_and_executemany(db):
    row_id = await db.execute_insert(
        "INSERT INTO chat_history (chat_id, role, content) VALUES (?, ?, ?)",
        (7, "user", "via-insert"),
    )
    assert isinstance(row_id, int)
    one = await db.execute_fetchone("SELECT * FROM chat_history WHERE id = ?", (row_id,))
    assert one["content"] == "via-insert"
    assert await db.execute_fetchone("SELECT * FROM chat_history WHERE id = ?", (999999,)) is None

    await db.executemany(
        "INSERT INTO chat_history (chat_id, role, content) VALUES (?, ?, ?)",
        [(7, "assistant", "a"), (7, "assistant", "b")],
    )
    rows = await db.execute_fetchall("SELECT * FROM chat_history WHERE chat_id = ?", (7,))
    assert len(rows) == 3
    cur = await db.execute("SELECT COUNT(*) AS c FROM chat_history WHERE chat_id = ?", (7,))
    assert (await cur.fetchone())["c"] == 3


async def test_chat_history_lifecycle(db):
    first = await db.save_chat_message(42, "user", "hola")
    await db.save_chat_message(42, "assistant", "buenas")
    await db.save_chat_message(42, "user", "segundo")
    assert isinstance(first, int)

    history = await db.get_chat_history(42)
    assert [m["content"] for m in history] == ["hola", "buenas", "segundo"]
    assert len(await db.get_chat_history(42, limit=1)) == 1

    await db.update_last_chat_message(42, "user", "editado")
    history = await db.get_chat_history(42)
    assert history[-1]["content"] == "editado"

    await db.clear_chat_history(42)
    assert await db.get_chat_history(42) == []


async def test_events_crud_and_sync(db):
    event_id = await db.add_event(1, "Reunion", "2099-01-01 10:00:00", "con equipo")
    assert isinstance(event_id, int)

    upcoming = await db.get_upcoming_events(1)
    assert [e["title"] for e in upcoming] == ["Reunion"]
    assert await db.get_expiring_events(1) == []

    now = datetime.utcnow()
    near_id = await db.add_event(1, "Cerca", _fmt(now + timedelta(hours=36)))
    window = await db.get_expiring_events(1)
    assert [e["id"] for e in window] == [near_id]

    missing = await db.get_events_missing_google_sync()
    assert {e["id"] for e in missing} == {event_id, near_id}

    await db.update_event_google_id(event_id, "gcal-123")
    missing = await db.get_events_missing_google_sync()
    assert [e["id"] for e in missing] == [near_id]


async def test_alerts_crud(db):
    alert_id = await db.add_alert(2, "Recordatorio", "warning", expires_at="2099-01-01 00:00:00")
    expired_id = await db.add_alert(2, "Vieja", expires_at="2000-01-01 00:00:00")

    active = await db.get_active_alerts(2)
    assert [a["id"] for a in active] == [alert_id]

    assert await db.get_unread_alert_count(2) == 2
    await db.mark_alert_read(alert_id)
    assert await db.get_unread_alert_count(2) == 1

    now = datetime.utcnow()
    await db.add_alert(3, "Proxima", expires_at=_fmt(now + timedelta(hours=30)))
    expiring = await db.get_expiring_alerts(1)
    assert len(expiring) == 1
    assert expiring[0]["message"] == "Proxima"
    assert expired_id not in [a["id"] for a in expiring]


async def test_finance_summary_and_records(db):
    now = datetime.utcnow()
    await db.add_finance_record(5, 1000.0, "income", "salario", "pago quincenal")
    await db.add_finance_record(5, 250.5, "expense", "comida", "super", recorded_at=_fmt(now))
    await db.add_finance_record(
        6, 99.0, "expense", "otro", "otro chat", currency="USD", recorded_at=_fmt(now)
    )

    summary = await db.get_finance_summary(5)
    assert summary["total_income"] == 1000.0
    assert summary["total_expenses"] == 250.5
    assert summary["balance"] == 749.5
    assert summary["expense_by_category"] == {"comida": 250.5}
    assert summary["income_by_category"] == {"salario": 1000.0}
    assert summary["transaction_count"] == 2
    assert summary["period_start"] and summary["period_end"]

    explicit = await db.get_finance_summary(
        5, start_date="2000-01-01 00:00:00", end_date="2099-01-01 00:00:00"
    )
    assert explicit["transaction_count"] == 2

    all_records = await db.get_finance_records(5)
    assert len(all_records) == 2
    filtered = await db.get_finance_records(
        5,
        start_date="2000-01-01 00:00:00",
        end_date="2099-01-01 00:00:00",
        category="income",
    )
    assert [r["category"] for r in filtered] == ["income"]


async def test_exports_lifecycle(db):
    export_id = await db.create_export(9, "excel")
    row = await db.fetchone("SELECT * FROM exports WHERE id = ?", (export_id,))
    assert row["status"] == "pending"

    await db.update_export(export_id, "running")
    row = await db.fetchone("SELECT * FROM exports WHERE id = ?", (export_id,))
    assert row["status"] == "running"
    assert row["completed_at"] is None

    await db.update_export(export_id, "completed", file_path="/tmp/out.xlsx")
    row = await db.fetchone("SELECT * FROM exports WHERE id = ?", (export_id,))
    assert row["file_path"] == "/tmp/out.xlsx"
    assert row["completed_at"] is not None

    fail_id = await db.create_export(9, "csv")
    await db.update_export(fail_id, "failed", error_message="disk full")
    row = await db.fetchone("SELECT * FROM exports WHERE id = ?", (fail_id,))
    assert row["error_message"] == "disk full"
    assert row["completed_at"] is not None


async def test_preferences_get_set_update(db):
    prefs = await db.get_or_create_preferences(8)
    assert prefs["currency"] == settings.default_currency
    assert prefs["voice_replies"] is False

    again = await db.get_or_create_preferences(8)
    assert again == prefs

    await db.update_preferences(8, {"currency": "USD", "voice_replies": True})
    loaded = await db.get_or_create_preferences(8)
    assert loaded == {"currency": "USD", "voice_replies": True}

    assert await db.get_preference(8, "currency") == "USD"
    assert await db.get_preference(8, "missing", "fallback") == "fallback"

    await db.set_preference(8, "voice_replies", False)
    assert await db.get_preference(8, "voice_replies") is False


async def test_cleanup_inactive_chats(db):
    old = _fmt(datetime.utcnow() - timedelta(days=90))
    await db.execute(
        "INSERT INTO chat_history (chat_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (11, "user", "viejo", old),
    )
    await db.save_chat_message(11, "user", "reciente")
    await db.save_chat_message(12, "user", "otro reciente")

    removed = await db.cleanup_inactive_chats(30)
    assert removed == 1
    remaining = await db.fetchall("SELECT * FROM chat_history")
    assert [r["content"] for r in remaining] == ["reciente", "otro reciente"]


async def test_get_all_chat_ids(db):
    await db.save_chat_message(21, "user", "hola")
    await db.add_event(22, "Ev", "2099-01-01 10:00:00")
    await db.add_alert(23, "Al")
    await db.add_finance_record(24, 10.0, "expense")
    ids = await db.get_all_chat_ids()
    assert sorted(ids) == [21, 22, 23, 24]


async def test_personal_knowledge_roundtrip(db):
    await db.store_personal_knowledge(31, "  Nombre_Completo ", "Usuario Test", "perfil")
    await db.store_personal_knowledge(31, "ciudad", "CDMX")
    await db.store_personal_knowledge(31, "ciudad", "Guadalajara", "ubicacion")

    assert await db.count_personal_knowledge(31) == 2

    found = await db.search_personal_knowledge(31, "nombre")
    assert [f["key"] for f in found] == ["nombre_completo"]
    assert found[0]["value"] == "Usuario Test"

    by_category = await db.search_personal_knowledge(31, "ubicacion")
    assert [f["key"] for f in by_category] == ["ciudad"]

    all_rows = await db.get_all_personal_knowledge(31)
    assert {r["key"]: r["value"] for r in all_rows} == {
        "nombre_completo": "Usuario Test",
        "ciudad": "Guadalajara",
    }

    assert await db.delete_personal_knowledge(31, "CIUDAD") is True
    assert await db.delete_personal_knowledge(31, "ciudad") is False
    assert await db.count_personal_knowledge(31) == 1


async def test_recurring_alerts_and_compute_next_run(db):
    now = datetime.utcnow()
    due_id = await db.add_recurring_alert(
        41, "Riego", "daily", first_run=_fmt(now - timedelta(hours=2))
    )
    await db.add_recurring_alert(41, "Futuro", "daily", first_run=_fmt(now + timedelta(days=2)))

    due = await db.get_due_recurring_alerts()
    assert [a["id"] for a in due] == [due_id]

    daily = await db.compute_next_run("daily", "2026-01-01 10:00:00")
    assert daily == "2026-01-02 10:00:00"
    weekly = await db.compute_next_run("weekly", "2026-01-01 10:00:00")
    assert weekly == "2026-01-08 10:00:00"
    every = await db.compute_next_run("every_3_hours", "2026-01-01 10:00:00")
    assert every == "2026-01-01 13:00:00"
    every_h = await db.compute_next_run("every_5h", "2026-01-01 10:00:00")
    assert every_h == "2026-01-01 15:00:00"
    assert await db.compute_next_run("every_xx", "2026-01-01 10:00:00") is None
    assert await db.compute_next_run("no-such-pattern", "2026-01-01 10:00:00") is None
    assert await db.compute_next_run("daily", "not-a-date") is None

    monday = datetime(2026, 1, 5, 9, 0, 0)
    assert await db.compute_next_run("weekdays", _fmt(monday)) == "2026-01-06 09:00:00"
    friday = datetime(2026, 1, 2, 9, 0, 0)
    assert await db.compute_next_run("weekdays", _fmt(friday)) == "2026-01-05 09:00:00"
    saturday = datetime(2026, 1, 3, 9, 0, 0)
    assert await db.compute_next_run("weekends", _fmt(saturday)) == "2026-01-04 09:00:00"
    wednesday = datetime(2026, 1, 7, 9, 0, 0)
    assert await db.compute_next_run("weekends", _fmt(wednesday)) == "2026-01-10 09:00:00"

    await db.update_alert_next_run(due_id, "2099-01-01 00:00:00")
    assert await db.get_due_recurring_alerts() == []


async def test_kv_store(db):
    await db.kv_set("simple", "valor")
    assert await db.kv_get("simple") == "valor"

    await db.kv_set("simple", "valor2")
    assert await db.kv_get("simple") == "valor2"

    await db.kv_set("futuro", "v", expires_at="2099-01-01 00:00:00")
    assert await db.kv_get("futuro") == "v"

    await db.kv_set("caducado", "v", expires_at="2000-01-01 00:00:00")
    assert await db.kv_get("caducado") is None
    leftover = await db.fetchall("SELECT * FROM kv_store WHERE key = 'caducado'")
    assert leftover == []

    await db.kv_set("roto", "v", expires_at="not-a-date")
    assert await db.kv_get("roto") == "v"

    assert await db.kv_get("no-existe") is None

    await db.kv_delete("simple")
    assert await db.kv_get("simple") is None


async def test_voice_sessions(db):
    await db.save_voice_session("sess-1", 51, "active", "hola mundo")
    row = await db.fetchone("SELECT * FROM voice_sessions WHERE session_id = 'sess-1'")
    assert row["chat_id"] == 51
    assert row["state"] == "active"
    assert row["ended_at"] is None

    await db.update_voice_session_state("sess-1", "ended")
    row = await db.fetchone("SELECT * FROM voice_sessions WHERE session_id = 'sess-1'")
    assert row["state"] == "ended"

    await db.save_voice_session("sess-1", 51, "active", "otra vez")
    rows = await db.fetchall("SELECT * FROM voice_sessions")
    assert len(rows) == 1


async def test_second_brain_log_and_stats(db):
    empty = await db.get_second_brain_stats()
    assert empty["total_queries"] == 0
    assert empty["recent_queries"] == []

    await db.log_second_brain_query("como pago mi casa", 61, ["nota-a", "nota-b"], 2, 0.91)
    await db.log_second_brain_query("sin notas", None, [], 0, 0.0)
    long_query = "x" * 400
    await db.log_second_brain_query(long_query, 61, ["n"] * 15, 1, 0.1)

    stats = await db.get_second_brain_stats()
    assert stats["total_queries"] == 3
    assert len(stats["recent_queries"]) == 3
    stored = await db.fetchone("SELECT * FROM second_brain_log WHERE query_text LIKE 'x%'")
    assert len(stored["query_text"]) == 300
    notes = await db.fetchone(
        "SELECT * FROM second_brain_log WHERE query_text = 'como pago mi casa'"
    )
    assert notes["notes_found"] == "nota-a,nota-b"


async def test_credentials_roundtrip(db):
    await db.store_credential(71, " Google ", "secreto-1")
    await db.store_credential(71, "google", "secreto-2")

    assert await db.get_credential(71, "GOOGLE") == "secreto-2"
    assert await db.get_credential(71, "inexistente") is None

    listed = await db.list_credentials(71)
    assert [c["service"] for c in listed] == ["google"]

    assert await db.delete_credential(71, "Google") is True
    assert await db.delete_credential(71, "google") is False
    assert await db.list_credentials(71) == []


async def test_close_twice_and_default_path(monkeypatch, tmp_path):
    manager = DatabaseManager(db_path=tmp_path / "closing.db")
    await manager.initialize()
    await manager.close()
    await manager.close()
    assert manager._conn is None

    monkeypatch.setattr(settings, "db_path", str(tmp_path / "default.db"))
    auto = DatabaseManager()
    assert auto.db_path == tmp_path / "default.db"
