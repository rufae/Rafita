"""Cobertura de google_services_manager.py: Drive, Tasks, Gmail, Fitness y diagnostico."""

from types import SimpleNamespace

import httplib2
from googleapiclient.errors import HttpError

import src.services.google_services_manager as gsm
from src.services.google_services_manager import GoogleServiceError, GoogleServicesManager


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


def _manager(**attrs) -> GoogleServicesManager:
    manager = GoogleServicesManager()
    manager._ready = True
    for key, value in attrs.items():
        setattr(manager, key, value)
    return manager


def _files_service(captured, result):
    class _Files:
        def list(self, **kwargs):
            captured.update(kwargs)
            return _Req(result)

        def get(self, **kwargs):
            captured.update(kwargs)
            return _Req(result)

        def get_media(self, **kwargs):
            captured.update(kwargs)
            return _Req(result)

        def export(self, **kwargs):
            captured.update(kwargs)
            return _Req(result)

        def delete(self, **kwargs):
            captured.update(kwargs)
            return _Req({})

    return SimpleNamespace(files=lambda: _Files())


# ---------------------------------------------------------------------------
# _resolve_drive_folder
# ---------------------------------------------------------------------------


async def test_resolve_drive_folder_empty_returns_none():
    manager = _manager()
    assert await manager._resolve_drive_folder("") is None
    assert await manager._resolve_drive_folder("   ") is None


async def test_resolve_drive_folder_accepts_id_like():
    manager = _manager()
    folder_id = "1AaBbCcDdEeFfGgHhIiJjKk"
    assert await manager._resolve_drive_folder(folder_id) == folder_id


async def test_resolve_drive_folder_by_name_and_missing():
    captured = {}
    manager = _manager(
        _drive=_files_service(captured, {"files": [{"id": "F1", "name": "Proyectos"}]})
    )
    assert await manager._resolve_drive_folder("Proyectos") == "F1"
    assert "'Proyectos' in parents" not in captured.get("q", "")
    assert "name = 'Proyectos'" in captured["q"]

    manager = _manager(_drive=_files_service({}, {"files": []}))
    assert await manager._resolve_drive_folder("Inexistente") is None


async def test_resolve_drive_folder_escapes_quotes():
    captured = {}
    manager = _manager(_drive=_files_service(captured, {"files": [{"id": "F2"}]}))
    await manager._resolve_drive_folder("Ana y Pepe's")
    assert "Ana y Pepe\\'s" in captured["q"]


# ---------------------------------------------------------------------------
# search_files
# ---------------------------------------------------------------------------


async def test_search_files_with_folder_and_escaping():
    captured = {}
    manager = _manager(
        _drive=_files_service(captured, {"files": [{"id": "D1", "name": "nota.md"}]})
    )
    files = await manager.search_files("it's", max_results=999, folder_id="Carpeta1")
    assert files == [{"id": "D1", "name": "nota.md"}]
    assert "it\\'s" in captured["q"]
    assert "'Carpeta1' in parents" in captured["q"]
    assert captured["pageSize"] == 50


async def test_search_files_uses_configured_folder(monkeypatch):
    captured = {}
    monkeypatch.setattr(gsm.settings, "google_drive_folder_id", "FOLDER9")
    manager = _manager(_drive=_files_service(captured, {"files": []}))
    await manager.search_files("factura", max_results=0)
    assert "'FOLDER9' in parents" in captured["q"]
    assert captured["pageSize"] == 1


# ---------------------------------------------------------------------------
# send_email (ramas que faltan)
# ---------------------------------------------------------------------------


async def test_send_email_requires_valid_address():
    manager = _manager()

    async def no_contacts(query, max_results=5):
        raise RuntimeError("people caido")

    manager.find_contact = no_contacts
    result = await manager.send_email("mama", "hola", "cuerpo")
    assert result["success"] is False
    assert "No encontre el correo" in result["message"]

    result = await manager.send_email("", "hola", "cuerpo")
    assert result["success"] is False
    assert "Necesito una direccion" in result["message"]


async def test_send_email_defaults_subject_and_sends():
    captured = {}

    class _Messages:
        def send(self, **kwargs):
            captured.update(kwargs)
            return _Req({"id": "m9"})

    manager = _manager(
        _gmail=SimpleNamespace(users=lambda: SimpleNamespace(messages=lambda: _Messages()))
    )
    result = await manager.send_email("tu@ejemplo.com", "  ", "cuerpo")
    assert result["success"] is True
    assert "(sin asunto)" in result["message"]
    assert captured["userId"] == "me"
    assert "raw" in captured["body"]


# ---------------------------------------------------------------------------
# list_calendar_events
# ---------------------------------------------------------------------------


async def test_list_calendar_events_supports_all_day(monkeypatch):
    monkeypatch.setattr(gsm.settings, "timezone", "Europe/Madrid")
    captured = {}

    class _Events:
        def list(self, **kwargs):
            captured.update(kwargs)
            return _Req(
                {
                    "items": [
                        {
                            "summary": "Todo el dia",
                            "start": {"date": "2026-10-01"},
                            "end": {"date": "2026-10-02"},
                        },
                        {
                            "start": {"dateTime": "2026-10-02T10:00:00"},
                            "end": {"dateTime": "2026-10-02T11:00:00"},
                            "description": "con hora",
                        },
                    ]
                }
            )

    manager = _manager(_calendar=SimpleNamespace(events=lambda: _Events()), _calendar_id="primary")
    result = await manager.list_calendar_events(days=5, max_results=999)
    assert result["success"] is True
    assert result["events"][0] == {
        "id": "",
        "title": "Todo el dia",
        "start": "2026-10-01",
        "end": "2026-10-02",
        "updated": "",
        "description": "",
    }
    assert result["events"][1]["title"] == "Sin titulo"
    assert result["events"][1]["start"] == "2026-10-02T10:00:00"
    assert captured["maxResults"] == 250
    assert captured["calendarId"] == "primary"


# ---------------------------------------------------------------------------
# fitness_daily_steps
# ---------------------------------------------------------------------------


async def test_fitness_daily_steps_sums_buckets(monkeypatch):
    monkeypatch.setattr(gsm.settings, "timezone", "Europe/Madrid")
    captured = {}

    class _Dataset:
        def aggregate(self, **kwargs):
            captured.update(kwargs)
            return _Req(
                {
                    "bucket": [
                        {
                            "dataset": [
                                {
                                    "point": [
                                        {"value": [{"intVal": 3000}]},
                                        {"value": [{"intVal": 12}]},
                                    ]
                                }
                            ]
                        }
                    ]
                }
            )

    manager = _manager(
        _fitness=SimpleNamespace(users=lambda: SimpleNamespace(dataset=lambda: _Dataset()))
    )
    result = await manager.fitness_daily_steps()
    assert result["success"] is True
    assert result["steps"] == 3012
    assert "startTimeMillis" in captured["body"]


async def test_fitness_daily_steps_without_data(monkeypatch):
    monkeypatch.setattr(gsm.settings, "timezone", "Europe/Madrid")

    class _Dataset:
        def aggregate(self, **kwargs):
            return _Req({"bucket": []})

    manager = _manager(
        _fitness=SimpleNamespace(users=lambda: SimpleNamespace(dataset=lambda: _Dataset()))
    )
    result = await manager.fitness_daily_steps()
    assert result["steps"] == 0


# ---------------------------------------------------------------------------
# Eventos y tareas
# ---------------------------------------------------------------------------


async def test_delete_event_uses_calendar_id():
    captured = {}

    class _Events:
        def delete(self, **kwargs):
            captured.update(kwargs)
            return _Req({})

    manager = _manager(_calendar=SimpleNamespace(events=lambda: _Events()), _calendar_id="cal1")
    result = await manager.delete_event("ev1")
    assert result["success"] is True
    assert "ev1" in result["message"]
    assert captured == {"calendarId": "cal1", "eventId": "ev1"}


async def test_create_event_defaults_end_on_bad_start():
    captured = {}

    class _Events:
        def insert(self, **kwargs):
            captured.update(kwargs)
            return _Req({"id": "ev2", "htmlLink": "http://x"})

    manager = _manager(_calendar=SimpleNamespace(events=lambda: _Events()))
    result = await manager.create_event("Reunion", "no-es-fecha")
    assert result["success"] is True
    assert captured["body"]["end"]["dateTime"] == "no-es-fecha"


async def test_task_crud_uses_task_parameter():
    captured = {}

    class _Tasks:
        def insert(self, **kwargs):
            captured["insert"] = kwargs
            return _Req({"id": "t1", "title": "Comprar pan"})

        def patch(self, **kwargs):
            captured["patch"] = kwargs
            return _Req({})

        def delete(self, **kwargs):
            captured["delete"] = kwargs
            return _Req({})

        def list(self, **kwargs):
            captured["list"] = kwargs
            return _Req(
                {
                    "items": [
                        {
                            "id": "t1",
                            "title": "Comprar pan",
                            "due": "2026-10-01",
                            "status": "needsAction",
                        }
                    ]
                }
            )

    manager = _manager(_tasks=SimpleNamespace(tasks=lambda: _Tasks()))
    created = await manager.create_task("Comprar pan")
    assert created == {"success": True, "id": "t1", "title": "Comprar pan"}
    assert captured["insert"]["tasklist"] == "@default"
    assert captured["insert"]["body"] == {"title": "Comprar pan"}

    con_fecha = await manager.create_task("Pagar recibo", due="2026-10-01")
    assert con_fecha["due"] == "2026-10-01"
    assert captured["insert"]["body"]["due"] == "2026-10-01T00:00:00.000Z"

    completed = await manager.complete_task("t1")
    assert completed["success"] is True
    assert captured["patch"]["body"] == {"status": "completed"}
    assert captured["patch"]["task"] == "t1"

    deleted = await manager.delete_task("t1")
    assert deleted["success"] is True
    # La API espera `task` (no `taskId`) — bug E2E 2026-09-27.
    assert captured["delete"]["task"] == "t1"
    assert "taskId" not in captured["delete"]

    listing = await manager.list_tasks(show_completed=True, max_results=99)
    assert listing["tasks"][0]["title"] == "Comprar pan"
    assert captured["list"]["showCompleted"] is True
    assert captured["list"]["maxResults"] == 50


async def test_list_tasklists_returns_items():
    class _Tasklists:
        def list(self, **kwargs):
            return _Req({"items": [{"id": "@default", "title": "Mis tareas"}]})

    manager = _manager(_tasks=SimpleNamespace(tasklists=lambda: _Tasklists()))
    assert await manager.list_tasklists() == [{"id": "@default", "title": "Mis tareas"}]


# ---------------------------------------------------------------------------
# set_calendar_id
# ---------------------------------------------------------------------------


async def test_set_calendar_id_requires_name():
    manager = _manager()
    result = await manager.set_calendar_id("  ")
    assert result["success"] is False
    assert "/calendario" in result["message"]


async def test_set_calendar_id_reports_missing_auth():
    manager = _manager(_ready=False)

    async def fake_initialize(force=False):
        return False

    manager.initialize = fake_initialize
    result = await manager.set_calendar_id("tu@ejemplo.com")
    assert result["success"] is False
    assert "no está autenticado" in result["message"]


async def test_set_calendar_id_reports_access_error():
    class _Calendars:
        def get(self, **kwargs):
            return _Req(error=_http_error(404, "not found"))

    manager = _manager(_calendar=SimpleNamespace(calendars=lambda: _Calendars()))
    result = await manager.set_calendar_id("tu@ejemplo.com")
    assert result["success"] is False
    assert "No encontrado" in result["message"]
    assert "leer calendario" in result["message"]


# ---------------------------------------------------------------------------
# status() / diagnostico
# ---------------------------------------------------------------------------


def _status_manager(auth_method):
    captured = {}

    def _calendar():
        return SimpleNamespace(
            calendarList=lambda: SimpleNamespace(list=lambda **_k: _Req({"items": []}))
        )

    class _DriveFiles:
        def list(self, **kwargs):
            raise TypeError("credenciales sin permiso de ficheros")

    def _sheets():
        return SimpleNamespace(
            spreadsheets=lambda: SimpleNamespace(
                get=lambda **_k: _Req(error=_http_error(403, "SERVICE_DISABLED has not been used"))
            )
        )

    def _docs():
        return SimpleNamespace(
            documents=lambda: SimpleNamespace(
                get=lambda **_k: _Req(error=_http_error(403, "forbidden by policy"))
            )
        )

    def _tasks():
        return SimpleNamespace(tasklists=lambda: SimpleNamespace(list=lambda **_k: _Req({})))

    def _gmail():
        return SimpleNamespace(users=lambda: SimpleNamespace(getProfile=lambda **_k: _Req({})))

    manager = _manager(
        _auth_method=auth_method,
        _calendar=_calendar(),
        _drive=SimpleNamespace(files=lambda: _DriveFiles()),
        _sheets=_sheets(),
        _docs=_docs(),
        _tasks=_tasks(),
        _gmail=_gmail(),
    )
    return manager, captured


async def test_status_service_account_skips_oauth_services():
    manager, _captured = _status_manager("service_account")
    result = await manager.status()
    assert result["authenticated"] is True
    assert result["auth_method"] == "service_account"
    assert result["services"]["tasks"] == "requires_oauth"
    assert result["services"]["gmail"] == "requires_oauth"
    assert result["services"]["calendar"] == "ok"
    assert result["services"]["sheets"] == "api_disabled"
    assert result["services"]["docs"] == "forbidden"
    assert result["services"]["drive"].startswith("unavailable:")


async def test_status_oauth_reports_each_outcome(monkeypatch):
    monkeypatch.setattr(gsm.time, "sleep", lambda _s: None)
    manager, _captured = _status_manager("oauth")

    class _GmailProbe:
        def getProfile(self, **kwargs):
            return _Req(error=_http_error(401, "invalid credentials"))

    manager._gmail = SimpleNamespace(users=lambda: _GmailProbe())

    class _DriveFiles:
        def list(self, **kwargs):
            return _Req(error=_http_error(400, "bad request"))

    manager._drive = SimpleNamespace(files=lambda: _DriveFiles())
    result = await manager.status()
    assert result["services"]["calendar"] == "ok"
    assert result["services"]["docs"] == "forbidden"
    assert result["services"]["gmail"] == "unauthorized"
    assert result["services"]["drive"].startswith("error:")
    assert result["services"]["sheets"] == "api_disabled"


async def test_status_treats_404_probe_as_ok():
    manager, _captured = _status_manager("oauth")

    class _Docs:
        def get(self, **kwargs):
            return _Req(error=_http_error(404, "not found"))

    manager._docs = SimpleNamespace(documents=lambda: _Docs())
    result = await manager.status()
    assert result["services"]["docs"] == "ok"


async def test_status_initializes_when_not_ready():
    manager, _captured = _status_manager("oauth")
    manager._ready = False

    async def fake_initialize(force=False):
        manager._ready = True
        return True

    manager.initialize = fake_initialize
    result = await manager.status()
    assert result["authenticated"] is True


# ---------------------------------------------------------------------------
# Propiedades, _ensure y utilidades
# ---------------------------------------------------------------------------


async def test_ensure_rejects_unknown_and_unauthenticated():
    manager = GoogleServicesManager()
    try:
        _ = manager.sheets
        raise AssertionError("deberia fallar sin autenticar")
    except GoogleServiceError as exc:
        assert "initialize" in str(exc)

    manager = _manager()
    try:
        manager._ensure("no-existe")
        raise AssertionError("deberia fallar")
    except GoogleServiceError as exc:
        assert "desconocido" in str(exc)


def test_ensure_builds_missing_services(monkeypatch):
    manager = _manager()
    built = []

    def fake_build(service, version):
        built.append((service, version))
        return SimpleNamespace(name=service)

    monkeypatch.setattr(manager, "_build", fake_build)
    assert manager.sheets.name == "sheets"
    assert manager.docs.name == "docs"
    assert manager.tasks.name == "tasks"
    assert manager.gmail.name == "gmail"
    assert manager.people.name == "people"
    assert manager.fitness.name == "fitness"
    assert ("gmail", "v1") in built


def test_state_properties():
    manager = _manager(_auth_method="oauth", _sa_email="sa@x", _calendar_id="cal9")
    assert manager.is_ready is True
    assert manager.auth_method == "oauth"
    assert manager.service_account_email == "sa@x"
    assert manager.calendar_id == "cal9"
    assert manager.drive_folder_id == (gsm.settings.google_drive_folder_id or "").strip()


async def test_close_resets_state():
    manager = _manager(_calendar=1, _drive=2, _creds=object())
    await manager.close()
    assert manager._calendar is None
    assert manager._drive is None
    assert manager._creds is None
    assert manager.is_ready is False


async def test_oauth_missing_scopes_variants(monkeypatch):
    fake_db = SimpleNamespace(kv_get=lambda key: None)

    async def kv_get_none(key):
        return None

    fake_db.kv_get = kv_get_none
    monkeypatch.setattr(gsm, "db", fake_db)
    manager = _manager()
    assert len(await manager.oauth_missing_scopes()) == len(gsm.SCOPES)

    async def kv_get_token(key):
        return "cifrado"

    fake_db.kv_get = kv_get_token
    monkeypatch.setattr(
        gsm, "decrypt_value", lambda _v: '{"scopes": ["https://www.googleapis.com/auth/calendar"]}'
    )
    missing = await manager.oauth_missing_scopes()
    assert "https://www.googleapis.com/auth/calendar" not in missing
    assert "https://www.googleapis.com/auth/tasks" in missing

    monkeypatch.setattr(
        gsm, "decrypt_value", lambda _v: (_ for _ in ()).throw(RuntimeError("mala"))
    )
    assert len(await manager.oauth_missing_scopes()) == len(gsm.SCOPES)


def test_service_account_email_reads_file(tmp_path, monkeypatch):
    sa_file = tmp_path / "service_account.json"
    sa_file.write_text('{"client_email": "sa@proyecto.iam.gserviceaccount.com"}', encoding="utf-8")
    monkeypatch.setattr(gsm, "SERVICE_ACCOUNT_FILE", sa_file)
    assert gsm.service_account_email() == "sa@proyecto.iam.gserviceaccount.com"

    monkeypatch.setattr(gsm, "SERVICE_ACCOUNT_FILE", tmp_path / "no-existe.json")
    assert "credentials/service_account.json" in gsm.service_account_email()


def test_match_helpers_variants():
    assert GoogleServicesManager._normalize_match("Mamá") == "mama"
    assert GoogleServicesManager._compact_match("A A mamá") == "aamama"
    assert GoogleServicesManager._matches("", "x", "", "x")
    assert GoogleServicesManager._matches("mama", "aa mama", "mama", "aamama")
    assert not GoogleServicesManager._matches("aamama", "otra cosa", "aamama", "otracosa")
    assert GoogleServicesManager._matches(
        "mama raulito", "raulito mama", "mamaraulito", "raulitomama"
    )
    assert not GoogleServicesManager._matches("mama", "otra cosa", "mama", "otracosa")


def test_query_variants_include_kinship():
    variants = GoogleServicesManager._query_variants("dame el numero de mama")
    assert "mama" in variants
    assert "madre" in variants
    assert "dame" not in variants


# ---------------------------------------------------------------------------
# read_file / list_events
# ---------------------------------------------------------------------------


async def test_read_file_requires_id():
    manager = _manager()
    result = await manager.read_file("  ")
    assert result["success"] is False


async def test_read_file_exports_google_doc():
    captured = {}

    class _Files:
        def get(self, **kwargs):
            captured.update(kwargs)
            return _Req(
                {"id": "d1", "name": "doc", "mimeType": "application/vnd.google-apps.document"}
            )

        def export(self, **kwargs):
            captured.update(kwargs)
            return _Req("contenido del documento")

    manager = _manager(_drive=SimpleNamespace(files=lambda: _Files()))
    result = await manager.read_file("d1", max_chars=5)
    assert result["success"] is True
    assert result["text"] == "conte"
    assert result["truncated"] is True
    assert captured["mimeType"] == "text/plain"


async def test_read_file_pdf_and_bytes():
    class _FilesPdf:
        def get(self, **kwargs):
            return _Req({"id": "p1", "name": "x.pdf", "mimeType": "application/pdf"})

        def get_media(self, **kwargs):
            return _Req(b"pdf falso")

    manager = _manager(_drive=SimpleNamespace(files=lambda: _FilesPdf()))
    result = await manager.read_file("p1")
    assert result["success"] is False
    assert "PDF no legible" in result["message"]

    class _FilesText:
        def get(self, **kwargs):
            return _Req({"id": "t1", "name": "notas.txt", "mimeType": "text/plain"})

        def get_media(self, **kwargs):
            return _Req(b"contenido binario")

    manager = _manager(_drive=SimpleNamespace(files=lambda: _FilesText()))
    result = await manager.read_file("t1")
    assert result["text"] == "contenido binario"
    assert result["mime_type"] == "text/plain"


async def test_list_events_clamps_results(monkeypatch):
    monkeypatch.setattr(gsm.settings, "timezone", "Europe/Madrid")
    captured = {}

    class _Events:
        def list(self, **kwargs):
            captured.update(kwargs)
            return _Req({"items": [{"id": "e1"}]})

    manager = _manager(_calendar=SimpleNamespace(events=lambda: _Events()), _calendar_id="cal1")
    events = await manager.list_events(max_results=999, time_min="2026-10-01T00:00:00")
    assert events == [{"id": "e1"}]
    assert captured["maxResults"] == 50
    assert captured["calendarId"] == "cal1"
    assert captured["timeMin"].startswith("2026-10-01")


# ---------------------------------------------------------------------------
# Sheets / Docs / Gmail / Contactos
# ---------------------------------------------------------------------------


async def test_sheets_methods(monkeypatch):
    monkeypatch.setattr(gsm.settings, "google_drive_folder_id", "F1")
    captured = {}

    class _Spreadsheets:
        def create(self, **kwargs):
            captured["create"] = kwargs
            return _Req(
                {
                    "spreadsheetId": "s1",
                    "spreadsheetUrl": "http://hoja",
                    "sheets": [{"properties": {"title": "Datos"}}],
                }
            )

        def values(self):
            class _Values:
                def get(self, **kwargs):
                    captured["get"] = kwargs
                    return _Req({"values": [["a"]]})

                def append(self, **kwargs):
                    captured["append"] = kwargs
                    return _Req({"updates": {"updatedRows": 1}})

            return _Values()

    manager = _manager(_sheets=SimpleNamespace(spreadsheets=lambda: _Spreadsheets()))
    created = await manager.create_spreadsheet("Gastos")
    assert created["id"] == "s1"
    assert created["sheet"] == "Datos"
    assert captured["create"]["body"]["parents"] == ["F1"]

    read = await manager.read_range("s1", "A1:B2")
    assert read["values"] == [["a"]]
    appended = await manager.append_row("s1", [1, 2], sheet_name="Datos")
    assert appended["updated"] == 1
    assert captured["append"]["range"] == "Datos!A1"


async def test_docs_methods():
    captured = {}

    class _Documents:
        def create(self, **kwargs):
            captured["create"] = kwargs
            return _Req({"documentId": "doc1", "title": "Acta"})

        def get(self, **kwargs):
            captured["get"] = kwargs
            return _Req(
                {
                    "body": {
                        "content": [
                            {"paragraph": {"elements": [{"textRun": {"content": "Hola "}}]}},
                            {"table": {}},
                            {"paragraph": {"elements": [{"textRun": {"content": "mundo"}}]}},
                            {
                                "endIndex": 12,
                                "paragraph": {"elements": [{"textRun": {"content": ""}}]},
                            },
                        ],
                        "endIndex": 12,
                    }
                }
            )

        def batchUpdate(self, **kwargs):
            captured["update"] = kwargs
            return _Req({})

    manager = _manager(_docs=SimpleNamespace(documents=lambda: _Documents()))
    created = await manager.create_document("Acta")
    assert created["id"] == "doc1"
    read = await manager.read_document("doc1", max_chars=4)
    assert read["text"] == "Hola"
    assert read["truncated"] is True
    written = await manager.append_document_text("doc1", "fin")
    assert written["success"] is True
    assert captured["update"]["body"]["requests"][0]["insertText"]["location"]["index"] == 11


async def test_gmail_profile_and_list_contacts():
    class _Gmail:
        def getProfile(self, **kwargs):
            return _Req({"emailAddress": "yo@x.com", "messagesTotal": 12})

    manager = _manager(_gmail=SimpleNamespace(users=lambda: _Gmail()))
    profile = await manager.gmail_profile()
    assert profile == {"success": True, "email": "yo@x.com", "messages": 12}

    class _Connections:
        def list(self, **kwargs):
            return _Req(
                {
                    "connections": [
                        {
                            "names": [{"displayName": "Ana"}],
                            "emailAddresses": [{"value": "ana@x.com"}],
                            "phoneNumbers": [{"value": "+34 600"}],
                        }
                    ]
                }
            )

    manager = _manager(
        _people=SimpleNamespace(people=lambda: SimpleNamespace(connections=lambda: _Connections()))
    )
    contacts = await manager.list_contacts(page_size=999)
    assert contacts["contacts"][0]["name"] == "Ana"
    assert contacts["contacts"][0]["phone"] == "+34 600"


async def test_search_gmail_reads_message_metadata():
    class _Messages:
        def list(self, **kwargs):
            return _Req({"messages": [{"id": "m1"}]})

        def get(self, **kwargs):
            return _Req(
                {
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Factura"},
                            {"name": "From", "value": "banco@x.com"},
                            {"name": "Date", "value": "hoy"},
                        ]
                    },
                    "snippet": "resumen",
                }
            )

    manager = _manager(
        _gmail=SimpleNamespace(users=lambda: SimpleNamespace(messages=lambda: _Messages()))
    )
    result = await manager.search_gmail("factura")
    assert result["count"] == 1
    assert result["messages"][0]["subject"] == "Factura"
    assert result["messages"][0]["from"] == "banco@x.com"


async def test_send_email_normaliza_correo_dictado():
    captured = {}

    class _Messages:
        def send(self, **kwargs):
            captured.update(kwargs)
            return _Req({"id": "m1"})

    manager = _manager(
        _gmail=SimpleNamespace(users=lambda: SimpleNamespace(messages=lambda: _Messages()))
    )

    async def no_contacts(query, max_results=5):
        raise AssertionError("no debe buscar contactos: el correo ya tiene @ tras normalizar")

    manager.find_contact = no_contacts
    result = await manager.send_email("anabel arroba gmail punto com", "Hola", "Cuerpo")
    assert result["success"] is True
    assert "anabel@gmail.com" in result["message"]


async def test_send_email_resuelve_nombre_por_contactos():
    class _Messages:
        def send(self, **kwargs):
            return _Req({"id": "m2"})

    manager = _manager(
        _gmail=SimpleNamespace(users=lambda: SimpleNamespace(messages=lambda: _Messages()))
    )

    async def fake_contacts(query, max_results=5):
        return {"contacts": [{"name": "Mama", "email": "mama@x.com", "phone": ""}]}

    manager.find_contact = fake_contacts
    result = await manager.send_email("mama", "hola", "cuerpo")
    assert result["success"] is True
    assert "mama@x.com" in result["message"]


async def test_create_draft_no_envia():
    captured = {}

    class _Drafts:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _Req({"id": "d1"})

    manager = _manager(
        _gmail=SimpleNamespace(users=lambda: SimpleNamespace(drafts=lambda: _Drafts()))
    )
    result = await manager.create_draft(
        "ejemplo punto ejemplo arroba gmail punto com", "Asunto", "Cuerpo"
    )
    assert result["success"] is True
    assert "NO se ha enviado" in result["message"]
    assert "ejemplo.ejemplo@gmail.com" in result["message"]
    assert "raw" in captured["body"]["message"]


async def test_create_draft_requiere_destinatario():
    manager = _manager()

    async def no_contacts(query, max_results=5):
        return {"contacts": []}

    manager.find_contact = no_contacts
    result = await manager.create_draft("alguien", "a", "b")
    assert result["success"] is False
    assert "No encontre el correo" in result["message"]


async def test_create_draft_respaldo_en_boveda_si_gmail_falla(monkeypatch):
    from src.services.google_services_manager import GoogleServiceError

    class _Drafts:
        def create(self, **kwargs):
            raise GoogleServiceError("permisos insuficientes (falta gmail.compose)")

    manager = _manager(
        _gmail=SimpleNamespace(users=lambda: SimpleNamespace(drafts=lambda: _Drafts()))
    )
    notas = []

    async def fake_note(title, content, folder=""):
        notas.append((title, folder))
        return {"success": True, "note_path": "00-Inbox/Borrador.md"}

    monkeypatch.setattr("src.utils.obsidian_manager.create_or_append_note", fake_note)
    result = await manager.create_draft("tu@ejemplo.com", "Asunto", "Cuerpo")
    assert result["success"] is True
    assert result["draft_location"] == "vault"
    assert "boveda" in result["message"].lower().replace("ó", "o")
    assert "/setup_google" in result["message"]
    assert notas and notas[0][1] == "00-Inbox"


async def test_oauth_refresca_sin_scopes_nuevos_si_google_los_rechaza(monkeypatch, tmp_path):
    # Regresion 2026-10-03: añadir gmail.compose a SCOPES hizo que Google
    # rechazara el refresco del token existente ('invalid_scope') y TODO
    # Google quedara caido. Ahora se reintenta sin los scopes nuevos.
    import json as _json

    from google.auth.exceptions import RefreshError

    import src.services.google_services_manager as gsm

    intentsos = []

    class _Creds:
        def __init__(self, scopes):
            self._scopes = list(scopes)
            self.expired = True
            self.refresh_token = "r"
            self.valid = True
            self.expired_ok = False

        def refresh(self, request):
            intentsos.append(list(self._scopes))
            if any("gmail.compose" in s for s in self._scopes):
                raise RefreshError("invalid_scope: Bad Request")
            self.expired = False

    monkeypatch.setattr(
        gsm,
        "Credentials",
        SimpleNamespace(from_authorized_user_info=lambda info, scopes: _Creds(scopes)),
    )
    monkeypatch.setattr(gsm, "OAUTH_TOKEN_FILE", tmp_path / "token.json")
    (tmp_path / "token.json").write_text(
        _json.dumps({"refresh_token": "r", "client_id": "x", "client_secret": "y"}),
        encoding="utf-8",
    )

    async def _kv_get(key):
        return None

    monkeypatch.setattr(gsm.db, "kv_get", _kv_get)

    manager = gsm.GoogleServicesManager()
    ok = await manager._load_oauth_sync()
    assert ok is True
    assert any("gmail.compose" in s for s in intentsos[0])
    assert all("gmail.compose" not in s for s in intentsos[1:])


async def test_initialize_resetea_servicios_cacheados(monkeypatch):
    """Tras un re-enlace OAuth los servicios lazy se reconstruyen.

    Antes solo se re construian calendar/drive: gmail seguia con la
    service_account y devolvia 400 failedPrecondition (vivo 2026-10-04).
    """
    manager = GoogleServicesManager()
    cache_sa = object()
    manager._gmail = cache_sa
    manager._tasks = cache_sa
    manager._ready = True

    async def fake_oauth():
        return True

    class _DB:
        async def kv_get(self, key):
            return None

    monkeypatch.setattr(manager, "_load_oauth_sync", fake_oauth)
    monkeypatch.setattr(manager, "_build", lambda service, version: SimpleNamespace(name=service))
    monkeypatch.setattr(gsm, "db", _DB())

    ok = await manager.initialize(force=True)

    assert ok is True
    assert manager._gmail is None  # cache SA descartada
    assert manager._tasks is None
    assert manager._calendar.name == "calendar"
    assert manager._drive.name == "drive"
    assert manager.gmail.name == "gmail"  # se reconstruye con las credenciales nuevas
