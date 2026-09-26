"""Drive (búsqueda/lectura) y comando /calendario (tarea 3.9)."""

from types import SimpleNamespace

import src.services.google_service as gs_module
from src.handlers.chat import TOOLS_DEFINITIONS
from src.services.google_service import GoogleService


class _Req:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _Files:
    def __init__(self, listing=None, meta=None, media=b""):
        self._listing = listing or {}
        self._meta = meta or {}
        self._media = media

    def list(self, **_kwargs):
        return _Req(self._listing)

    def get(self, **_kwargs):
        return _Req(self._meta)

    def export(self, **_kwargs):
        return _Req(self._media)

    def get_media(self, **_kwargs):
        return _Req(self._media)


class _Drive:
    def __init__(self, files):
        self._files = files

    def files(self):
        return self._files


class _Calendars:
    def __init__(self, info):
        self._info = info

    def get(self, **_kwargs):
        return _Req(self._info)


class _Events:
    def list(self, **_kwargs):
        return _Req({})


class _CalService:
    def __init__(self, info):
        self._info = info

    def calendars(self):
        return _Calendars(self._info)

    def events(self):
        return _Events()


def test_new_tools_registered():
    names = {t["function"]["name"] for t in TOOLS_DEFINITIONS}
    assert {"search_google_drive", "read_google_drive_file"} <= names


async def test_search_drive_returns_files():
    svc = GoogleService()
    svc._ready = True
    svc._drive = _Drive(_Files(listing={"files": [{"id": "abc", "name": "Factura enero.pdf"}]}))
    result = await svc.search_drive("factura")
    assert result["success"] is True
    assert "Factura enero.pdf" in result["message"]
    assert result["files"][0]["id"] == "abc"


async def test_read_drive_file_exports_google_doc():
    svc = GoogleService()
    svc._ready = True
    svc._drive = _Drive(
        _Files(
            meta={
                "id": "doc1",
                "name": "Notas",
                "mimeType": "application/vnd.google-apps.document",
                "webViewLink": "https://docs.google.com/x",
            },
            media=b"contenido del documento",
        )
    )
    result = await svc.read_drive_file("doc1")
    assert result["success"] is True
    assert "contenido del documento" in result["text"]
    assert result["name"] == "Notas"


async def test_read_drive_file_requires_id():
    svc = GoogleService()
    svc._ready = True
    svc._drive = _Drive(_Files())
    result = await svc.read_drive_file("")
    assert result["success"] is False


async def test_set_calendar_id_validates_and_saves(monkeypatch):
    saved = {}

    async def fake_kv_set(key, value):
        saved[key] = value

    monkeypatch.setattr(gs_module.db, "kv_set", fake_kv_set)
    svc = GoogleService()
    svc._ready = True
    svc._service = _CalService({"summary": "Mi calendario"})
    result = await svc.set_calendar_id("usuario@gmail.com")
    assert result["success"] is True
    assert svc.calendar_id == "usuario@gmail.com"
    assert saved["google_calendar_id"] == "usuario@gmail.com"
    assert "Mi calendario" in result["message"]


async def test_set_calendar_id_reports_share_problem(monkeypatch):
    class _Broken(_CalService):
        def calendars(self):
            raise RuntimeError("not found")

    svc = GoogleService()
    svc._ready = True
    svc._service = _Broken({})
    result = await svc.set_calendar_id("usuario@gmail.com")
    assert result["success"] is False
    assert "Compartiste" in result["message"] or "compartiste" in result["message"].lower()


async def test_calendario_command_admin_only(monkeypatch):
    from src.handlers import admin as admin_module

    monkeypatch.setattr(admin_module.settings, "admin_ids", [999])
    replies = []

    class _Msg:
        async def reply_text(self, text, **_kwargs):
            replies.append(text)

    update = SimpleNamespace(effective_message=_Msg(), effective_user=SimpleNamespace(id=1))
    await admin_module.calendario_command(update, SimpleNamespace(args=[]))
    assert "administradores" in replies[-1]
