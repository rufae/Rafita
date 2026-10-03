"""Cobertura del adapter legacy google_service.py con google_services falso."""

from types import SimpleNamespace

import httplib2
from googleapiclient.errors import HttpError

import src.services.google_service as gs
from src.services.google_service import GoogleService


class _Req:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    def execute(self):
        if self._error is not None:
            raise self._error
        return self._result


def _http_error(status: int, detail: str = "error") -> HttpError:
    return HttpError(httplib2.Response({"status": status}), detail.encode())


class FakeKV:
    def __init__(self, store=None):
        self.store = store or {}
        self.sets = []

    async def kv_get(self, key):
        return self.store.get(key)

    async def kv_set(self, key, value):
        self.sets.append((key, value))
        self.store[key] = value


def _ready_service(service=None, drive=None) -> GoogleService:
    svc = GoogleService()
    svc._ready = True
    svc._service = service
    svc._drive = drive
    return svc


# ---------------------------------------------------------------------------
# initialize / close
# ---------------------------------------------------------------------------


async def test_initialize_uses_manager_clients(monkeypatch):
    fake_kv = FakeKV({"google_calendar_id": "guardado"})
    monkeypatch.setattr(gs, "db", fake_kv)
    calls = {}

    class _Manager:
        calendar = "CAL"
        drive = "DRV"
        calendar_id = "detectado"

        @staticmethod
        async def initialize(force=False):
            calls["force"] = force
            return True

    monkeypatch.setattr(gs, "google_services", _Manager)
    svc = GoogleService()
    assert await svc.initialize() is True
    assert svc._service == "CAL"
    assert svc._drive == "DRV"
    assert svc._calendar_id == "guardado"
    assert svc._ready is True

    class _Off:
        @staticmethod
        async def initialize(force=False):
            return False

    monkeypatch.setattr(gs, "google_services", _Off)
    svc = GoogleService()
    assert await svc.initialize() is False

    await svc.close()
    assert svc._ready is False
    assert svc._service is None


# ---------------------------------------------------------------------------
# generate_auth_url
# ---------------------------------------------------------------------------


async def test_generate_auth_url_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "OAUTH_CREDENTIALS_FILE", tmp_path / "no-existe.json")
    result = await GoogleService().generate_auth_url()
    assert result["success"] is False
    assert "credentials.json" in result["message"]


async def test_generate_auth_url_builds_flow(tmp_path, monkeypatch):
    cred_file = tmp_path / "credentials.json"
    cred_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(gs, "OAUTH_CREDENTIALS_FILE", cred_file)
    fake_kv = FakeKV()
    monkeypatch.setattr(gs, "db", fake_kv)

    class _Flow:
        redirect_uri = ""

        @staticmethod
        def from_client_secrets_file(path, scopes):
            return _Flow()

        def authorization_url(self, **kwargs):
            return "https://accounts.google.com/o/oauth2/auth?x=1", "estado-1"

    monkeypatch.setattr(gs, "InstalledAppFlow", _Flow)
    result = await GoogleService().generate_auth_url()
    assert result["success"] is True
    assert result["auth_url"].startswith("https://accounts.google.com")
    assert ("google_flow_state", "estado-1") in fake_kv.sets


async def test_generate_auth_url_reports_errors(tmp_path, monkeypatch):
    cred_file = tmp_path / "credentials.json"
    cred_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(gs, "OAUTH_CREDENTIALS_FILE", cred_file)

    class _BrokenFlow:
        @staticmethod
        def from_client_secrets_file(path, scopes):
            raise RuntimeError("client secrets ilegibles")

    monkeypatch.setattr(gs, "InstalledAppFlow", _BrokenFlow)
    result = await GoogleService().generate_auth_url()
    assert result["success"] is False
    assert "client secrets ilegibles" in result["message"]


# ---------------------------------------------------------------------------
# exchange_code
# ---------------------------------------------------------------------------


async def test_exchange_code_requires_flow_or_state(monkeypatch):
    monkeypatch.setattr(gs, "db", FakeKV())
    result = await GoogleService().exchange_code("codigo")
    assert result["success"] is False
    assert "No hay flujo OAuth activo" in result["message"]


async def test_exchange_code_recreate_flow_error(monkeypatch):
    monkeypatch.setattr(gs, "db", FakeKV({"google_flow_state": "estado"}))

    class _BrokenFlow:
        @staticmethod
        def from_client_secrets_file(path, scopes):
            raise RuntimeError("sin credentials")

    monkeypatch.setattr(gs, "InstalledAppFlow", _BrokenFlow)
    result = await GoogleService().exchange_code("codigo")
    assert result["success"] is False
    assert "Error recreando flujo" in result["message"]


async def test_exchange_code_success_stores_token(monkeypatch):
    fake_kv = FakeKV({"google_calendar_id": "cal-x"})
    monkeypatch.setattr(gs, "db", fake_kv)
    monkeypatch.setattr(gs, "encrypt_value", lambda v: "cifrado:%s" % v)
    built = []
    monkeypatch.setattr(
        gs, "build", lambda name, version, credentials=None: built.append(name) or name
    )
    manager_calls = []

    class _Manager:
        @staticmethod
        async def initialize(force=False):
            manager_calls.append(force)
            return True

    monkeypatch.setattr(gs, "google_services", _Manager)

    class _Creds:
        @staticmethod
        def to_json():
            return '{"token": "t"}'

    class _Flow:
        credentials = None

        def fetch_token(self, code):
            self.credentials = _Creds()

    flow = _Flow()
    svc = GoogleService()
    svc._flow = flow
    result = await svc.exchange_code("codigo-1")
    assert result["success"] is True
    assert svc._ready is True
    assert svc._calendar_id == "cal-x"
    assert built == ["calendar", "drive"]
    assert manager_calls == [True]
    assert ("google_token", 'cifrado:{"token": "t"}') in fake_kv.sets


async def test_exchange_code_reports_fetch_error(monkeypatch):
    monkeypatch.setattr(gs, "db", FakeKV())

    class _Flow:
        def fetch_token(self, code):
            raise RuntimeError("codigo invalido")

    svc = GoogleService()
    svc._flow = _Flow()
    result = await svc.exchange_code("mal")
    assert result["success"] is False
    assert "codigo invalido" in result["message"]


# ---------------------------------------------------------------------------
# get_calendar_events
# ---------------------------------------------------------------------------


async def test_get_calendar_events_requires_auth():
    result = await GoogleService().get_calendar_events()
    assert result["success"] is False
    assert result["needs_auth"] is True


async def test_get_calendar_events_formats_items():
    class _Events:
        def list(self, **kwargs):
            return _Req(
                {
                    "items": [
                        {
                            "id": "e1",
                            "summary": "Reunion",
                            "start": {"dateTime": "2026-10-01T10:00:00"},
                            "end": {"dateTime": "2026-10-01T11:00:00"},
                            "description": "sala",
                            "htmlLink": "http://x",
                        },
                        {
                            "id": "e2",
                            "start": {"date": "2026-10-02"},
                            "end": {"date": "2026-10-03"},
                        },
                    ]
                }
            )

    svc = _ready_service(service=SimpleNamespace(events=lambda: _Events()))
    result = await svc.get_calendar_events(max_results=5)
    assert result["success"] is True
    assert result["count"] == 2
    assert result["events"][0]["title"] == "Reunion"
    assert result["events"][1]["title"] == "Sin titulo"
    assert result["events"][1]["start"] == "2026-10-02"


async def test_get_calendar_events_reports_errors():
    class _EventsHttp:
        def list(self, **kwargs):
            return _Req(error=_http_error(500, "boom"))

    svc = _ready_service(service=SimpleNamespace(events=lambda: _EventsHttp()))
    result = await svc.get_calendar_events()
    assert result["success"] is False
    assert "Error de API de Google" in result["message"]

    class _EventsBroken:
        def list(self, **kwargs):
            raise TypeError("api cambiada")

    svc = _ready_service(service=SimpleNamespace(events=lambda: _EventsBroken()))
    result = await svc.get_calendar_events()
    assert result["success"] is False
    assert "api cambiada" in result["message"]


# ---------------------------------------------------------------------------
# create_calendar_event
# ---------------------------------------------------------------------------


async def test_create_calendar_event_variants(monkeypatch):
    result = await GoogleService().create_calendar_event("X", "2099-10-01T10:00:00")
    assert result["needs_auth"] is True

    captured = {}

    class _Events:
        def insert(self, **kwargs):
            captured.update(kwargs)
            return _Req({"id": "nuevo", "htmlLink": "http://e"})

    svc = _ready_service(service=SimpleNamespace(events=lambda: _Events()))
    result = await svc.create_calendar_event("Reunion", "2099-10-01T10:00:00")
    assert result["success"] is True
    assert result["event_id"] == "nuevo"
    assert captured["body"]["end"]["dateTime"] == "2099-10-01T11:00:00"

    result = await svc.create_calendar_event("Rara", "no-es-fecha")
    assert result["success"] is True
    assert captured["body"]["end"]["dateTime"] == "no-es-fecha"


async def test_create_calendar_event_reports_errors():
    class _EventsHttp:
        def insert(self, **kwargs):
            return _Req(error=_http_error(403, "forbidden"))

    svc = _ready_service(service=SimpleNamespace(events=lambda: _EventsHttp()))
    result = await svc.create_calendar_event("X", "2030-01-01T10:00:00")
    assert result["success"] is False
    assert "Error de API" in result["message"]

    class _EventsBroken:
        def insert(self, **kwargs):
            raise ValueError("body invalido")

    svc = _ready_service(service=SimpleNamespace(events=lambda: _EventsBroken()))
    result = await svc.create_calendar_event("X", "2030-01-01T10:00:00")
    assert result["success"] is False
    assert "body invalido" in result["message"]


# ---------------------------------------------------------------------------
# set_calendar_id
# ---------------------------------------------------------------------------


async def test_set_calendar_id_error_mentions_share(monkeypatch):
    monkeypatch.setattr(gs, "db", FakeKV())
    monkeypatch.setattr(gs, "service_account_email", lambda: "sa@proyecto.iam")

    class _Calendars:
        def get(self, **kwargs):
            raise RuntimeError("404 not found")

    svc = _ready_service(service=SimpleNamespace(calendars=lambda: _Calendars()))
    result = await svc.set_calendar_id("tu@ejemplo.com")
    assert result["success"] is False
    assert "Compartiste ese calendario" in result["message"]
    assert "sa@proyecto.iam" in result["message"]

    result = await GoogleService().set_calendar_id("  ")
    assert result["success"] is False
    assert "/calendario" in result["message"]

    result = await GoogleService().set_calendar_id("tu@ejemplo.com")
    assert result["success"] is False
    assert "no está autenticado" in result["message"]


# ---------------------------------------------------------------------------
# search_drive
# ---------------------------------------------------------------------------


async def test_search_drive_variants():
    result = await GoogleService().search_drive("x")
    assert result["success"] is False

    svc = _ready_service(drive=SimpleNamespace())
    result = await svc.search_drive("   ")
    assert result["success"] is False
    assert "Indica qué buscar" in result["message"]

    captured = {}

    class _Files:
        def list(self, **kwargs):
            captured.update(kwargs)
            return _Req({"files": []})

    svc = _ready_service(drive=SimpleNamespace(files=lambda: _Files()))
    result = await svc.search_drive("factura")
    assert result["success"] is True
    assert "No encontré ficheros" in result["message"]
    assert "factura\\'" not in captured["q"]

    class _FilesHit:
        def list(self, **kwargs):
            return _Req({"files": [{"id": "f1", "name": "factura.pdf"}]})

    svc = _ready_service(drive=SimpleNamespace(files=lambda: _FilesHit()))
    result = await svc.search_drive("factura's")
    assert result["success"] is True
    assert "factura.pdf" in result["message"]

    class _FilesBroken:
        def list(self, **kwargs):
            raise RuntimeError("drive caido")

    svc = _ready_service(drive=SimpleNamespace(files=lambda: _FilesBroken()))
    result = await svc.search_drive("x")
    assert result["success"] is False
    assert "Error de Drive" in result["message"]


# ---------------------------------------------------------------------------
# read_drive_file
# ---------------------------------------------------------------------------


async def test_read_drive_file_edge_cases():
    result = await GoogleService().read_drive_file("f1")
    assert result["success"] is False

    svc = _ready_service(drive=SimpleNamespace())
    result = await svc.read_drive_file("  ")
    assert "Falta el id" in result["message"]


async def test_read_drive_file_exports_spreadsheet():
    captured = {}

    class _Files:
        def get(self, **kwargs):
            return _Req(
                {"id": "s1", "name": "hoja", "mimeType": "application/vnd.google-apps.spreadsheet"}
            )

        def export(self, **kwargs):
            captured.update(kwargs)
            return _Req("a,b\n1,2")

    svc = _ready_service(drive=SimpleNamespace(files=lambda: _Files()))
    result = await svc.read_drive_file("s1")
    assert result["success"] is True
    assert result["text"] == "a,b\n1,2"
    assert captured["mimeType"] == "text/csv"


async def test_read_drive_file_decodes_and_truncates():
    class _Files:
        def get(self, **kwargs):
            return _Req({"id": "b1", "name": "binario", "mimeType": "application/octet-stream"})

        def get_media(self, **kwargs):
            return _Req("texto plano muy largo " * 10)

    svc = _ready_service(drive=SimpleNamespace(files=lambda: _Files()))
    result = await svc.read_drive_file("b1", max_chars=10)
    assert result["success"] is True
    assert result["truncated"] is True
    assert len(result["text"]) == 10

    class _FilesBytes:
        def get(self, **kwargs):
            return _Req({"id": "b2", "name": "raw", "mimeType": "text/plain"})

        def get_media(self, **kwargs):
            return _Req(b"caf\xc3\xa9")

    svc = _ready_service(drive=SimpleNamespace(files=lambda: _FilesBytes()))
    result = await svc.read_drive_file("b2")
    assert result["text"] == "café"


async def test_read_drive_file_pdf_and_errors():
    class _FilesPdf:
        def get(self, **kwargs):
            return _Req({"id": "p1", "name": "doc.pdf", "mimeType": "application/pdf"})

        def get_media(self, **kwargs):
            return _Req(b"no soy un pdf")

    svc = _ready_service(drive=SimpleNamespace(files=lambda: _FilesPdf()))
    result = await svc.read_drive_file("p1")
    assert result["success"] is False
    assert "PDF no legible" in result["message"]

    from io import BytesIO

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buffer = BytesIO()
    writer.write(buffer)

    class _FilesRealPdf:
        def get(self, **kwargs):
            return _Req({"id": "p2", "name": "ok.pdf", "mimeType": "application/pdf"})

        def get_media(self, **kwargs):
            return _Req(buffer.getvalue())

    svc = _ready_service(drive=SimpleNamespace(files=lambda: _FilesRealPdf()))
    result = await svc.read_drive_file("p2")
    assert result["success"] is True
    assert result["mime_type"] == "application/pdf"

    class _FilesBroken:
        def get(self, **kwargs):
            raise RuntimeError("permiso denegado")

    svc = _ready_service(drive=SimpleNamespace(files=lambda: _FilesBroken()))
    result = await svc.read_drive_file("p3")
    assert result["success"] is False
    assert "permiso denegado" in result["message"]


async def test_save_token_encrypts_and_stores(monkeypatch):
    fake_kv = FakeKV()
    monkeypatch.setattr(gs, "db", fake_kv)
    monkeypatch.setattr(gs, "encrypt_value", lambda v: "cifrado")
    creds = SimpleNamespace(to_json=lambda: "{}")
    await GoogleService()._save_token(creds)
    assert fake_kv.sets == [("google_token", "cifrado")]
