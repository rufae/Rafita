"""Tests del módulo centralizado de servicios de Google (auditoría 2026-09-26)."""

from types import SimpleNamespace

import httplib2
from googleapiclient.errors import HttpError

import src.services.google_services_manager as gsm
from src.services.google_services_manager import (
    GoogleServiceError,
    GoogleServicesManager,
    _humanize_http_error,
)


def _http_error(status: int, detail: str = "error") -> HttpError:
    return HttpError(httplib2.Response({"status": status}), detail.encode())


class _Request:
    """Petición falsa: falla las primeras N veces y luego devuelve el valor."""

    def __init__(self, result, failures=0, status=429):
        self._result = result
        self._failures = failures
        self._status = status
        self.calls = 0

    def execute(self):
        self.calls += 1
        if self.calls <= self._failures:
            raise _http_error(self._status, "rate limited")
        return self._result


def test_humanize_api_disabled():
    message = _humanize_http_error(
        _http_error(403, "SERVICE_DISABLED has not been used in project"), "listar", "sa@x"
    )
    assert "no está habilitada" in message


def test_humanize_forbidden_mentions_sharing():
    message = _humanize_http_error(_http_error(403, "insufficientPermissions"), "leer", "sa@x")
    assert "comparte" in message.lower()
    assert "sa@x" in message


def test_humanize_not_found():
    assert "(404)" in _humanize_http_error(_http_error(404), "leer")


def test_execute_retries_with_backoff(monkeypatch):
    monkeypatch.setattr(gsm.time, "sleep", lambda _s: None)
    manager = GoogleServicesManager()
    request = _Request({"ok": True}, failures=2, status=429)
    assert manager._execute(lambda: request, "probar") == {"ok": True}
    assert request.calls == 3


def test_execute_gives_actionable_error_after_retries(monkeypatch):
    monkeypatch.setattr(gsm.time, "sleep", lambda _s: None)
    manager = GoogleServicesManager()
    request = _Request({}, failures=99, status=403)
    try:
        manager._execute(lambda: request, "probar", retries=1)
        raise AssertionError("debería haber lanzado GoogleServiceError")
    except GoogleServiceError as exc:
        assert "403" in str(exc) or "comparte" in str(exc).lower()


def test_normalize_time_adds_local_timezone(monkeypatch):
    monkeypatch.setattr(gsm.settings, "timezone", "Europe/Madrid")
    normalized = GoogleServicesManager._normalize_time("2026-09-26T09:00:00")
    assert normalized.endswith("+02:00") or normalized.endswith("+01:00")


def test_normalize_time_keeps_offset():
    value = "2026-09-26T09:00:00+00:00"
    assert GoogleServicesManager._normalize_time(value) == value


def test_resolve_calendar_id_prefers_shared(monkeypatch):
    manager = GoogleServicesManager()
    manager._sa_email = "sa@x"
    manager._calendar = SimpleNamespace(
        calendarList=lambda: SimpleNamespace(
            list=lambda: _Request(
                {
                    "items": [
                        {"id": "sa@x", "accessRole": "owner", "summary": "SA"},
                        {"id": "yo@gmail.com", "accessRole": "writer", "summary": "Mío"},
                    ]
                }
            )
        )
    )
    monkeypatch.setattr(gsm.settings, "google_calendar_id", "primary")
    assert manager._resolve_calendar_id_sync() == "yo@gmail.com"


async def test_set_calendar_id_saves_override(monkeypatch):
    saved = {}

    async def fake_kv_set(key, value):
        saved[key] = value

    monkeypatch.setattr(gsm.db, "kv_set", fake_kv_set)
    manager = GoogleServicesManager()
    manager._ready = True
    manager._calendar = SimpleNamespace(
        calendars=lambda: SimpleNamespace(get=lambda **_k: _Request({"summary": "Mi calendario"})),
        events=lambda: SimpleNamespace(list=lambda **_k: _Request({})),
    )
    result = await manager.set_calendar_id("yo@gmail.com")
    assert result["success"] is True
    assert manager.calendar_id == "yo@gmail.com"
    assert saved["google_calendar_id"] == "yo@gmail.com"


async def test_status_marks_disabled_apis(monkeypatch):
    manager = GoogleServicesManager()
    manager._ready = True
    manager._auth_method = "service_account"

    def _disabled():
        return _Request({}, failures=99, status=403)  # 403 genérico -> forbidden

    def _ok():
        return _Request({"files": []})

    manager._calendar = SimpleNamespace(
        calendarList=lambda: SimpleNamespace(list=lambda **_k: _Request({"items": []}))
    )
    manager._drive = SimpleNamespace(files=lambda: SimpleNamespace(list=lambda **_k: _ok()))
    manager._sheets = SimpleNamespace(
        spreadsheets=lambda: SimpleNamespace(get=lambda **_k: _disabled())
    )
    manager._docs = SimpleNamespace(documents=lambda: SimpleNamespace(get=lambda **_k: _disabled()))
    manager._tasks = SimpleNamespace(tasklists=lambda: SimpleNamespace(list=_disabled))
    manager._gmail = SimpleNamespace(
        users=lambda: SimpleNamespace(getProfile=lambda **_k: _disabled())
    )

    monkeypatch.setattr(gsm.time, "sleep", lambda _s: None)
    status = await manager.status()
    assert status["authenticated"] is True
    assert status["services"]["calendar"] == "ok"
    assert status["services"]["drive"] == "ok"
    assert "forbidden" in status["services"]["sheets"]
