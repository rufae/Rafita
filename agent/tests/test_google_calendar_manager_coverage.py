"""Cobertura de src/utils/google_calendar_manager.py con fakes tipo _Req."""

from types import SimpleNamespace

from googleapiclient.errors import HttpError

from src.config import settings
from src.utils.google_calendar_manager import GoogleCalendarManager


class _Req:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


async def _async_value(value):
    return value


def _http_error(status: int) -> HttpError:
    resp = SimpleNamespace(status=status, reason="error")
    return HttpError(resp=resp, content=b"boom", uri="http://fake")


def _manager() -> GoogleCalendarManager:
    manager = GoogleCalendarManager()
    manager._ready = True
    return manager


def _service(events=None, calendar_list=None):
    """Fake googleapiclient service for events() and calendarList()."""

    class _Events:
        def __init__(self):
            self.inserted = []
            self.listed = []
            self.deleted = []

        def insert(self, **kwargs):
            self.inserted.append(kwargs)
            return _Req(events["insert"])

        def list(self, **kwargs):
            self.listed.append(kwargs)
            return _Req(events["list"])

        def delete(self, **kwargs):
            self.deleted.append(kwargs)
            return _Req(events["delete"])

    class _CalendarList:
        def list(self, **kwargs):
            return _Req(calendar_list)

    ev = _Events()
    return SimpleNamespace(events=lambda: ev, calendarList=lambda: _CalendarList()), ev


# ---------- _resolve_calendar_id ----------


def test_resolve_calendar_id_configured_wins(monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_id", "team@cal.com")
    manager = _manager()
    assert manager._resolve_calendar_id() == "team@cal.com"


def test_resolve_calendar_id_skips_service_account(monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_id", "")
    service, _ = _service(
        calendar_list={
            "items": [
                {"id": "sa@sa.iam.gserviceaccount.com", "accessRole": "owner", "summary": "SA"},
                {"id": "otro@cal.com", "accessRole": "writer", "summary": "Otro"},
            ]
        }
    )
    manager = _manager()
    manager._service = service
    assert manager._resolve_calendar_id(sa_email="sa@sa.iam.gserviceaccount.com") == "otro@cal.com"


def test_resolve_calendar_id_prefers_owner_then_writer(monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_id", "")
    service, _ = _service(
        calendar_list={
            "items": [
                {"id": "reader@cal.com", "accessRole": "reader"},
                {"id": "writer@cal.com", "accessRole": "writer"},
                {"id": "owner@cal.com", "accessRole": "owner"},
            ]
        }
    )
    manager = _manager()
    manager._service = service
    assert manager._resolve_calendar_id() == "owner@cal.com"


def test_resolve_calendar_id_list_failure_falls_back(monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_id", "")

    class _Boom:
        def list(self, **kwargs):
            raise RuntimeError("API caida")

    manager = _manager()
    manager._service = SimpleNamespace(calendarList=lambda: _Boom())
    assert manager._resolve_calendar_id() == "primary"


def test_resolve_calendar_id_no_candidates_falls_back_to_primary(monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_id", "")
    service, _ = _service(
        calendar_list={"items": [{"id": "sa@sa.iam.gserviceaccount.com", "accessRole": "owner"}]}
    )
    manager = _manager()
    manager._service = service
    assert manager._resolve_calendar_id(sa_email="sa@sa.iam.gserviceaccount.com") == "primary"


# ---------- initialize ----------


async def test_initialize_delegates_to_google_services(monkeypatch):
    fake = SimpleNamespace(
        initialize=lambda: _async_value(True),
        calendar="servicio",
        calendar_id="cal-123",
        auth_method="oauth",
    )
    monkeypatch.setattr("src.services.google_services_manager.google_services", fake)
    manager = GoogleCalendarManager()
    assert await manager.initialize() is True
    assert manager._service == "servicio"
    assert manager._calendar_id == "cal-123"
    assert manager._auth_method == "oauth"


async def test_initialize_not_ready(monkeypatch):
    fake = SimpleNamespace(initialize=lambda: _async_value(False))
    monkeypatch.setattr("src.services.google_services_manager.google_services", fake)
    manager = GoogleCalendarManager()
    assert await manager.initialize() is False
    assert manager._ready is False


async def test_initialize_exception_returns_false(monkeypatch):
    async def boom():
        raise RuntimeError("sin credenciales")

    fake = SimpleNamespace(initialize=boom)
    monkeypatch.setattr("src.services.google_services_manager.google_services", fake)
    manager = GoogleCalendarManager()
    assert await manager.initialize() is False
    assert manager._ready is False


# ---------- add_event ----------


async def test_add_event_rejects_past_date():
    manager = _manager()
    result = await manager.add_event("Reunion", "2020-01-01T10:00:00")
    assert result["success"] is False
    assert "pasado" in result["message"]


async def test_add_event_without_service_returns_error():
    manager = GoogleCalendarManager()
    manager._ready = False
    result = await manager.add_event("Reunion", "2030-01-01T10:00:00")
    assert result["success"] is False
    assert "no configurado" in result["message"]


async def test_add_event_computes_default_end_time():
    manager = _manager()
    service, events = _service(
        events={"insert": {"id": "ev1", "htmlLink": "http://cal/ev1"}, "list": {}, "delete": {}}
    )
    manager._service = service
    manager._calendar_id = "cal-x"
    result = await manager.add_event("Reunion", "2030-01-01T10:00:00")
    assert result["success"] is True
    assert result["event_id"] == "ev1"
    assert result["html_link"] == "http://cal/ev1"
    body = events.inserted[0]["body"]
    assert body["end"]["dateTime"] == "2030-01-01T11:00:00"
    assert events.inserted[0]["calendarId"] == "cal-x"
    assert body["start"]["timeZone"] == settings.timezone


async def test_add_event_invalid_start_without_end_keeps_start():
    manager = _manager()
    service, events = _service(
        events={"insert": {"id": "ev2", "htmlLink": "l"}, "list": {}, "delete": {}}
    )
    manager._service = service
    result = await manager.add_event("Reunion", "manana por la manana")
    assert result["success"] is True
    body = events.inserted[0]["body"]
    assert body["end"]["dateTime"] == "manana por la manana"


async def test_add_event_http_error_returns_api_message():
    manager = _manager()

    class _Events:
        def insert(self, **kwargs):
            raise _http_error(500)

    manager._service = SimpleNamespace(events=lambda: _Events())
    result = await manager.add_event("Reunion", "2030-01-01T10:00:00")
    assert result["success"] is False
    assert "Error de API" in result["message"]


async def test_add_event_generic_error_returns_message():
    manager = _manager()

    class _Events:
        def insert(self, **kwargs):
            raise ValueError("body invalido")

    manager._service = SimpleNamespace(events=lambda: _Events())
    result = await manager.add_event("Reunion", "2030-01-01T10:00:00")
    assert result["success"] is False
    assert "Error creando evento" in result["message"]


# ---------- list_upcoming_events ----------


async def test_list_upcoming_events_without_service():
    manager = GoogleCalendarManager()
    manager._ready = False
    assert await manager.list_upcoming_events() == []


async def test_list_upcoming_events_maps_fields():
    manager = _manager()
    service, events = _service(
        events={
            "insert": {},
            "list": {
                "items": [
                    {
                        "id": "e1",
                        "summary": "Reunion",
                        "start": {"dateTime": "2030-01-01T10:00:00"},
                        "end": {"dateTime": "2030-01-01T11:00:00"},
                        "description": "desc",
                    },
                    {
                        "id": "e2",
                        "start": {"date": "2030-01-02"},
                        "end": {"date": "2030-01-03"},
                    },
                ]
            },
            "delete": {},
        }
    )
    manager._service = service
    result = await manager.list_upcoming_events(max_results=5)
    assert result[0] == {
        "id": "e1",
        "title": "Reunion",
        "start": "2030-01-01T10:00:00",
        "end": "2030-01-01T11:00:00",
        "description": "desc",
    }
    assert result[1]["title"] == "Sin título"
    assert result[1]["start"] == "2030-01-02"
    assert result[1]["end"] == "2030-01-03"
    assert events.listed[0]["maxResults"] == 5
    assert events.listed[0]["orderBy"] == "startTime"


async def test_list_upcoming_events_error_returns_empty():
    manager = _manager()

    class _Events:
        def list(self, **kwargs):
            raise RuntimeError("API caida")

    manager._service = SimpleNamespace(events=lambda: _Events())
    assert await manager.list_upcoming_events() == []


# ---------- delete_event ----------


async def test_delete_event_without_service():
    manager = GoogleCalendarManager()
    manager._ready = False
    result = await manager.delete_event("ev1")
    assert result["success"] is False


async def test_delete_event_success():
    manager = _manager()
    service, events = _service(events={"insert": {}, "list": {}, "delete": {}})
    manager._service = service
    result = await manager.delete_event("ev1")
    assert result["success"] is True
    assert events.deleted[0]["eventId"] == "ev1"


async def test_delete_event_already_gone_is_success():
    manager = _manager()

    class _Events:
        def delete(self, **kwargs):
            raise _http_error(410)

    manager._service = SimpleNamespace(events=lambda: _Events())
    result = await manager.delete_event("ev1")
    assert result["success"] is True
    assert "ya no existe" in result["message"]


async def test_delete_event_other_http_error():
    manager = _manager()

    class _Events:
        def delete(self, **kwargs):
            raise _http_error(500)

    manager._service = SimpleNamespace(events=lambda: _Events())
    result = await manager.delete_event("ev1")
    assert result["success"] is False
    assert "Error de API" in result["message"]


async def test_delete_event_generic_error():
    manager = _manager()

    class _Events:
        def delete(self, **kwargs):
            raise ValueError("boom")

    manager._service = SimpleNamespace(events=lambda: _Events())
    result = await manager.delete_event("ev1")
    assert result["success"] is False
    assert "Error eliminando evento" in result["message"]


# ---------- sync_from_local_db / close ----------


class _FakeConn:
    def __init__(self):
        self.executed = []
        self.commits = 0

    async def execute(self, sql, params):
        self.executed.append((sql, params))

    async def commit(self):
        self.commits += 1


class _FakeDb:
    def __init__(self, events_by_chat):
        self._conn = _FakeConn()
        self._events_by_chat = events_by_chat

    async def get_all_chat_ids(self):
        return list(self._events_by_chat.keys())

    async def get_upcoming_events(self, chat_id, limit=50):
        return self._events_by_chat[chat_id]


async def test_sync_from_local_db_not_ready():
    manager = GoogleCalendarManager()
    manager._ready = False
    result = await manager.sync_from_local_db(_FakeDb({}))
    assert result["success"] is False
    assert result["synced"] == 0


async def test_sync_from_local_db_skips_already_synced(monkeypatch):
    manager = _manager()
    fake_db = _FakeDb(
        {
            1: [
                {
                    "id": 1,
                    "title": "A",
                    "event_datetime": "2030-01-01T10:00:00",
                    "google_event_id": "g0",
                },
                {
                    "id": 2,
                    "title": "B",
                    "event_datetime": "2030-01-02T10:00:00",
                    "description": "d",
                },
            ]
        }
    )
    created = []

    async def fake_add_event(title, start_datetime, end_datetime=None, description=None):
        created.append(title)
        return {"success": True, "message": "ok", "event_id": "g-%s" % title}

    monkeypatch.setattr(manager, "add_event", fake_add_event)
    result = await manager.sync_from_local_db(fake_db)
    assert result["success"] is True
    assert result["synced"] == 1
    assert created == ["B"]
    assert fake_db._conn.executed == [
        ("UPDATE events SET google_event_id = ? WHERE id = ?", ("g-B", 2))
    ]
    assert fake_db._conn.commits == 1


async def test_sync_from_local_db_counts_only_successes(monkeypatch):
    manager = _manager()
    fake_db = _FakeDb({1: [{"id": 9, "title": "C", "event_datetime": "2030-01-01T10:00:00"}]})

    async def fake_add_event(**kwargs):
        return {"success": False, "message": "fallo"}

    monkeypatch.setattr(manager, "add_event", fake_add_event)
    result = await manager.sync_from_local_db(fake_db)
    assert result["synced"] == 0
    assert fake_db._conn.executed == []


async def test_close_resets_service():
    manager = _manager()
    manager._service = object()
    await manager.close()
    assert manager._service is None
    assert manager._ready is False


async def test_close_without_service_is_noop():
    manager = GoogleCalendarManager()
    await manager.close()
    assert manager._service is None
