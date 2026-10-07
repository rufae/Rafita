"""buscar_historial (FTS-lite) y confirmaciones de acciones destructivas."""

import tempfile
from pathlib import Path

import pytest

from src.database import DatabaseManager
from src.handlers import chat as chat_mod


@pytest.fixture
async def db_limpia():
    tmp = Path(tempfile.mkdtemp(prefix="rafita_hist_"))
    db = DatabaseManager(db_path=tmp / "t.db")
    await db.initialize()
    yield db
    if db._conn:
        await db._conn.close()


async def test_search_chat_history_encuentra_y_escapa(db_limpia):
    await db_limpia.save_chat_message(1, "user", "tengo que renovar el dominio")
    await db_limpia.save_chat_message(1, "assistant", "lo apunto")
    await db_limpia.save_chat_message(2, "user", "otro chat sobre el dominio")

    rows = await db_limpia.search_chat_history(1, "renovar")
    assert len(rows) == 1
    assert "dominio" in rows[0]["content"]

    # Filtra por chat y por ventana temporal.
    assert await db_limpia.search_chat_history(2, "renovar") == []
    assert len(await db_limpia.search_chat_history(1, "renovar", days=3650)) == 1

    # Los comodines quedan escapados (no hacen de comodin): `_` es literal.
    await db_limpia.save_chat_message(1, "user", "100% real")
    assert await db_limpia.search_chat_history(1, "domini_") == []
    assert len(await db_limpia.search_chat_history(1, "100%")) == 1

    assert await db_limpia.search_chat_history(1, "   ") == []


async def test_tool_buscar_historial(monkeypatch):
    async def fake_search(chat_id, query, days=30, limit=20):
        return [{"role": "user", "content": "renovar el dominio", "created_at": "2026-10-01"}]

    monkeypatch.setattr(chat_mod.db, "search_chat_history", fake_search)
    res = await chat_mod._execute_tool(7, "buscar_historial", {"consulta": "dominio"})
    assert res["success"] is True
    assert "renovar el dominio" in res["message"]

    res = await chat_mod._execute_tool(7, "buscar_historial", {"consulta": ""})
    assert res["success"] is False

    async def vacia(*args, **kwargs):
        return []

    monkeypatch.setattr(chat_mod.db, "search_chat_history", vacia)
    res = await chat_mod._execute_tool(7, "buscar_historial", {"consulta": "nada", "dias": "xx"})
    assert res["success"] is True
    assert "Nada en el historial" in res["message"]


async def test_delete_exige_confirm_true(monkeypatch):
    async def no_borrar(*args, **kwargs):
        raise AssertionError("no debe borrarse sin confirm=true")

    monkeypatch.setattr(chat_mod.ob, "delete_note", no_borrar)

    res = await chat_mod._execute_tool(
        7, "manage_obsidian_note", {"action": "delete", "title": "Importante"}
    )
    assert res["success"] is False
    assert "confirm=true" in res["message"]

    monkeypatch.setattr(chat_mod.db, "delete_relation", no_borrar)
    res = await chat_mod._execute_tool(7, "delete_relation", {"relation_id": 3})
    assert res["success"] is False
    assert "confirm=true" in res["message"]

    res = await chat_mod._execute_tool(
        7, "manage_google_tasks", {"action": "delete", "task_id": "1"}
    )
    assert res["success"] is False
    assert "confirm=true" in res["message"]

    res = await chat_mod._execute_tool(
        7, "manage_google_calendar", {"action": "delete", "event_id": "1"}
    )
    assert res["success"] is False
    assert "confirm=true" in res["message"]

    res = await chat_mod._execute_tool(
        7, "create_condition_rule", {"action": "delete", "rule_id": 1}
    )
    assert res["success"] is False
    assert "confirm=true" in res["message"]


async def test_delete_con_confirm_sigue_funcionando(monkeypatch):
    async def borra_ok(title, folder=""):
        return {"success": True, "message": "borrada"}

    monkeypatch.setattr(chat_mod.ob, "delete_note", borra_ok)
    res = await chat_mod._execute_tool(
        7, "manage_obsidian_note", {"action": "delete", "title": "Adios", "confirm": True}
    )
    assert res["success"] is True
