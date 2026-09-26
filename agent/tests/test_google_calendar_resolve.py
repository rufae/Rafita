"""Detección automática del calendario compartido (tarea 3.8)."""

from src.config import settings
from src.services.google_service import GoogleService
from src.utils.google_calendar_manager import GoogleCalendarManager


class _FakeRequest:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _FakeCalendarList:
    def __init__(self, items):
        self._items = items

    def list(self):
        return _FakeRequest({"items": self._items})


class _FakeService:
    def __init__(self, items):
        self._items = items

    def calendarList(self):
        return _FakeCalendarList(self._items)


ITEMS = [
    {"id": "rafita@proyecto.iam.gserviceaccount.com", "accessRole": "owner", "summary": "SA"},
    {"id": "usuario@gmail.com", "accessRole": "writer", "summary": "Mi calendario"},
]


def test_google_service_detects_shared_calendar(monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_id", "primary")
    svc = GoogleService()
    svc._service = _FakeService(ITEMS)
    assert svc._resolve_calendar_id(sa_email="rafita@proyecto.iam.gserviceaccount.com") == (
        "usuario@gmail.com"
    )


def test_google_service_configured_id_wins(monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_id", "otro@grupo.google.com")
    svc = GoogleService()
    svc._service = _FakeService(ITEMS)
    assert svc._resolve_calendar_id() == "otro@grupo.google.com"


def test_google_service_without_share_falls_back_to_primary(monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_id", "primary")
    svc = GoogleService()
    svc._service = _FakeService([ITEMS[0]])
    assert svc._resolve_calendar_id(sa_email=ITEMS[0]["id"]) == "primary"


def test_manager_detects_shared_calendar(monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_id", "primary")
    manager = GoogleCalendarManager()
    manager._service = _FakeService(ITEMS)
    assert manager._resolve_calendar_id(sa_email=ITEMS[0]["id"]) == "usuario@gmail.com"
