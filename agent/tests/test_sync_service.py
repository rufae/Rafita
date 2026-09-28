"""Sync inteligente Google <-> boveda (automatizacion A, 2026-09-28)."""

import pytest

from src.services import sync_service as sync


@pytest.fixture
def vault(tmp_path, monkeypatch):
    root = tmp_path / "obsidian_vault"
    (root / "00-Inbox").mkdir(parents=True)
    monkeypatch.setattr("src.utils.obsidian_manager.OBSIDIAN_VAULT", root)
    return root


class _FakeGS:
    def __init__(self, tasks=None, events=None):
        self._tasks = tasks or []
        self._events = events or []
        self.created_tasks = []
        self.completed_tasks = []
        self.created_events = []

    is_ready = True

    async def initialize(self):
        return True

    async def list_tasks(self, show_completed=False, max_results=20):
        return {"success": True, "tasks": self._tasks}

    async def create_task(self, title):
        self.created_tasks.append(title)
        return {"success": True, "message": "ok"}

    async def complete_task(self, task_id):
        self.completed_tasks.append(task_id)
        return {"success": True, "message": "ok"}

    async def list_calendar_events(self, days=90, max_results=100):
        return {"success": True, "events": self._events}

    async def create_event(self, title, start_datetime, **kwargs):
        self.created_events.append((title, start_datetime))
        return {"success": True, "event_id": "ev-new"}


class _FakeGcal:
    def __init__(self):
        self.moved = []

    async def move_event(self, event_id, new_start, duration_minutes=60):
        self.moved.append((event_id, new_start))
        return {"success": True, "message": "movido"}


def _write_note(vault, name, content):
    (vault / "00-Inbox" / (name + ".md")).write_text(content, encoding="utf-8")


# ---------------- tareas ----------------


async def test_sync_tasks_creates_completes_and_marks(vault, monkeypatch):
    _write_note(
        vault,
        "Tareas",
        "# Tareas\n\n- [ ] Comprar pan\n- [x] Pagar luz\n- [ ] Llamar gestor\n",
    )
    fake_gs = _FakeGS(
        tasks=[
            {"id": "t1", "title": "Pagar luz", "status": "needsAction"},
            {"id": "t2", "title": "Llamar gestor", "status": "completed"},
        ]
    )
    monkeypatch.setattr("src.services.google_services_manager.google_services", fake_gs)

    stats = await sync._sync_tasks()

    # "Comprar pan" no existe -> se crea; "Pagar luz" [x] y pendiente en Google
    # -> se completa; "Llamar gestor" completada en Google -> se marca [x].
    assert fake_gs.created_tasks == ["Comprar pan"]
    assert fake_gs.completed_tasks == ["t1"]
    assert stats == {"created": 1, "completed_google": 1, "completed_vault": 1}
    content = (vault / "00-Inbox" / "Tareas.md").read_text(encoding="utf-8")
    assert "- [x] Llamar gestor" in content


async def test_sync_tasks_without_note_does_nothing(vault, monkeypatch):
    monkeypatch.setattr("src.services.google_services_manager.google_services", _FakeGS())
    assert await sync._sync_tasks() == {
        "created": 0,
        "completed_google": 0,
        "completed_vault": 0,
    }


# ---------------- eventos ----------------


async def test_sync_events_creates_and_moves(vault, monkeypatch):
    _write_note(
        vault,
        "Calendario",
        "2026-10-01 10:00 | Dentista\n2026-10-02 09:00 | Gimnasio\n",
    )
    fake_gs = _FakeGS(
        events=[
            {"id": "ev1", "title": "Dentista", "start": "2026-10-01T18:00:00+02:00"},
        ]
    )
    fake_gcal = _FakeGcal()
    monkeypatch.setattr("src.services.google_services_manager.google_services", fake_gs)
    monkeypatch.setattr("src.utils.google_calendar_manager.gcal", fake_gcal)

    stats = await sync._sync_events()

    # Dentista existe a otra hora -> se mueve; Gimnasio no existe -> se crea.
    assert fake_gcal.moved == [("ev1", "2026-10-01T10:00:00")]
    assert fake_gs.created_events == [("Gimnasio", "2026-10-02T09:00:00")]
    assert stats == {"created": 1, "moved": 1}


async def test_sync_events_same_time_does_nothing(vault, monkeypatch):
    _write_note(vault, "Calendario", "2026-10-01 10:00 | Dentista\n")
    fake_gs = _FakeGS(
        events=[{"id": "ev1", "title": "Dentista", "start": "2026-10-01T10:00:00+02:00"}]
    )
    fake_gcal = _FakeGcal()
    monkeypatch.setattr("src.services.google_services_manager.google_services", fake_gs)
    monkeypatch.setattr("src.utils.google_calendar_manager.gcal", fake_gcal)

    stats = await sync._sync_events()
    assert stats == {"created": 0, "moved": 0}
    assert not fake_gcal.moved and not fake_gs.created_events


# ---------------- flujo completo ----------------


async def test_sync_google_vault_without_google(vault, monkeypatch):
    async def not_ready():
        return False

    monkeypatch.setattr(sync, "_google_ready", not_ready)
    result = await sync.sync_google_vault()
    assert result["success"]
    assert result["google"] is False
    assert "sin Google" in result["message"]
    assert result["changes"] == 0


async def test_sync_google_vault_full(vault, monkeypatch):
    _write_note(vault, "Tareas", "- [ ] Tarea nueva\n")

    async def ready():
        return True

    fake_gs = _FakeGS()
    monkeypatch.setattr(sync, "_google_ready", ready)
    monkeypatch.setattr("src.services.google_services_manager.google_services", fake_gs)
    monkeypatch.setattr("src.utils.google_calendar_manager.gcal", _FakeGcal())

    async def fake_export():
        return {"success": True}

    monkeypatch.setattr("src.utils.google_brain_sync.sync_google_to_vault", fake_export)

    result = await sync.sync_google_vault()
    assert result["google"] is True
    assert result["export"] is True
    assert result["tasks"]["created"] == 1
    assert result["changes"] == 1
    assert "Sync completa" in result["message"]


def test_sync_endpoint_requires_signature():
    from fastapi.testclient import TestClient

    from src.utils import webhook_server

    webhook_server.configure_gateway("secreto-sync")
    client = TestClient(webhook_server.app)
    resp = client.post("/automation/sync", json={})
    assert resp.status_code in (401, 503)
