"""Modo local (Google opcional): calendario, tareas y ficheros sin Google.

Si Google no esta conectado, Rafita usa su almacen local (BD + boveda) con las
mismas acciones. Estos tests cubren ese camino.
"""

import pytest

from src.handlers import chat as chat_mod
from src.services.google_services_manager import GoogleServicesManager


@pytest.fixture(autouse=True)
def _google_off(monkeypatch):
    monkeypatch.setattr(GoogleServicesManager, "is_ready", property(lambda self: False))


class _FakeDB:
    def __init__(self):
        self.events = []
        self.tasks = []
        self.deleted = []
        self.moved = []

    async def add_event(self, chat_id, title, event_datetime, description=None):
        self.events.append(
            {"id": len(self.events) + 1, "title": title, "event_datetime": event_datetime}
        )
        return len(self.events)

    async def get_upcoming_events(self, chat_id, limit=10):
        return self.events[:limit]

    async def delete_event(self, chat_id, event_id):
        self.deleted.append(event_id)

    async def update_event_datetime(self, chat_id, event_id, new_datetime):
        self.moved.append((event_id, new_datetime))

    async def add_task(self, chat_id, title, due=None):
        self.tasks.append({"id": len(self.tasks) + 1, "title": title, "status": "pending"})
        return len(self.tasks)

    async def list_tasks(self, chat_id, show_completed=False):
        return [t for t in self.tasks if t["status"] == "pending"]

    async def complete_task(self, chat_id, task_id):
        for t in self.tasks:
            if t["id"] == task_id:
                t["status"] = "completed"
                return 1
        return 0

    async def delete_task(self, chat_id, task_id):
        antes = len(self.tasks)
        self.tasks = [t for t in self.tasks if t["id"] != task_id]
        return antes - len(self.tasks)


def _install_db(monkeypatch):
    fake = _FakeDB()
    monkeypatch.setattr(chat_mod, "db", fake)
    return fake


# ---------------- calendario local ----------------


async def test_calendar_create_local(monkeypatch):
    fake = _install_db(monkeypatch)
    result = await chat_mod._execute_tool(
        7,
        "manage_google_calendar",
        {"action": "create", "title": "Cita", "when": "mañana a las 10"},
    )
    assert result["success"]
    assert "calendario local" in result["message"]
    assert fake.events and fake.events[0]["title"] == "Cita"


async def test_calendar_list_local(monkeypatch):
    fake = _install_db(monkeypatch)
    fake.events = [{"id": 1, "title": "Dentista", "event_datetime": "2026-10-01 10:00:00"}]
    result = await chat_mod._execute_tool(7, "manage_google_calendar", {"action": "list"})
    assert "calendario local" in result["message"]
    assert "Dentista" in result["message"]


async def test_calendar_delete_and_move_local(monkeypatch):
    fake = _install_db(monkeypatch)
    fake.events = [{"id": 3, "title": "Reunion", "event_datetime": "2026-10-01 10:00:00"}]

    deleted = await chat_mod._execute_tool(
        7, "manage_google_calendar", {"action": "delete", "title": "reunion"}
    )
    assert deleted["success"]
    assert fake.deleted == [3]

    moved = await chat_mod._execute_tool(
        7,
        "manage_google_calendar",
        {"action": "move", "title": "reunion", "when": "el viernes a las 9"},
    )
    assert moved["success"]
    assert fake.moved and fake.moved[0][0] == 3


# ---------------- tareas locales ----------------


async def test_tasks_local_lifecycle(monkeypatch):
    fake = _install_db(monkeypatch)
    created = await chat_mod._execute_tool(
        7, "manage_google_tasks", {"action": "create", "title": "Comprar pilas"}
    )
    assert created["success"]
    assert "localmente" in created["message"]

    listed = await chat_mod._execute_tool(7, "manage_google_tasks", {"action": "list"})
    assert "tareas locales" in listed["message"]
    assert "Comprar pilas" in listed["message"]

    done = await chat_mod._execute_tool(
        7, "manage_google_tasks", {"action": "complete", "task_id": "1"}
    )
    assert done["success"]
    assert fake.tasks[0]["status"] == "completed"


# ---------------- boveda como "Drive local" ----------------


async def test_drive_lists_vault_when_google_off(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    (vault / "01-Proyectos").mkdir(parents=True)
    (vault / "01-Proyectos" / "Huerto.md").write_text("x", encoding="utf-8")
    (vault / "Nota suelta.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr("src.utils.obsidian_manager.OBSIDIAN_VAULT", vault)

    result = await chat_mod._execute_tool(7, "list_google_drive", {})
    assert result["success"]
    assert "bóveda" in result["message"]
    assert any("Huerto.md" in f["name"] for f in result["files"])

    search = await chat_mod._execute_tool(7, "search_google_drive", {"query": "huerto"})
    assert search["success"]
    assert any("Huerto.md" in f["name"] for f in search["files"])
