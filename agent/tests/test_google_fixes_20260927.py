"""Arreglos Google 2026-09-27: bandeja principal, contactos con acentos y Drive."""

from types import SimpleNamespace

import pytest

from src.services.google_services_manager import GoogleServicesManager


@pytest.fixture(autouse=True)
def _google_ready(monkeypatch):
    """Estos tests ejercitan la ruta Google: forzamos is_ready=True."""
    monkeypatch.setattr(GoogleServicesManager, "is_ready", property(lambda self: True))


class _Req:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


def _manager() -> GoogleServicesManager:
    manager = GoogleServicesManager()
    manager._ready = True
    return manager


async def test_search_gmail_defaults_to_primary_inbox():
    manager = _manager()
    captured = {}

    class _Messages:
        def list(self, **kwargs):
            captured.update(kwargs)
            return _Req({"messages": []})

    manager._gmail = SimpleNamespace(users=lambda: SimpleNamespace(messages=lambda: _Messages()))
    await manager.search_gmail("")
    assert "in:inbox" in captured["q"]
    assert "category:primary" in captured["q"]


async def test_search_gmail_keeps_explicit_location():
    manager = _manager()
    captured = {}

    class _Messages:
        def list(self, **kwargs):
            captured.update(kwargs)
            return _Req({"messages": []})

    manager._gmail = SimpleNamespace(users=lambda: SimpleNamespace(messages=lambda: _Messages()))
    await manager.search_gmail("in:anywhere from:uber")
    assert captured["q"] == "in:anywhere from:uber"


async def test_find_contact_is_accent_insensitive():
    manager = _manager()

    class _Connections:
        def list(self, **_kwargs):
            return _Req(
                {
                    "connections": [
                        {
                            "names": [{"displayName": "Aa Mama"}],
                            "phoneNumbers": [{"value": "600000000"}],
                        }
                    ]
                }
            )

    manager._people = SimpleNamespace(
        people=lambda: SimpleNamespace(connections=lambda: _Connections())
    )
    for query in ("mama", "mamá", "MAMÁ", "Aa Mama"):
        result = await manager.find_contact(query)
        assert result["contacts"], "no encontrado con %r" % query
        assert result["contacts"][0]["phone"] == "600000000"


async def test_find_contact_paginates_all_connections():
    manager = _manager()
    pages = {
        None: {"connections": [], "nextPageToken": "p2"},
        "p2": {
            "connections": [
                {
                    "names": [{"displayName": "Aa Mama"}],
                    "phoneNumbers": [{"value": "600999888"}],
                }
            ]
        },
    }
    seen = []

    class _Connections:
        def list(self, **kwargs):
            seen.append(kwargs.get("pageToken"))
            return _Req(pages[kwargs.get("pageToken")])

    manager._people = SimpleNamespace(
        people=lambda: SimpleNamespace(connections=lambda: _Connections())
    )
    result = await manager.find_contact("Aa Mama")
    assert seen == [None, "p2"]
    assert result["contacts"][0]["phone"] == "600999888"


async def test_find_contact_matches_stt_variants():
    """Bug 2026-09-27: el STT de llamada transcribe 'Aa Mama' como 'A A mamá'."""

    manager = _manager()

    class _Connections:
        def list(self, **_kwargs):
            return _Req(
                {
                    "connections": [
                        {
                            "names": [{"displayName": "Aa Mama"}],
                            "phoneNumbers": [{"value": "655-225-607"}],
                        }
                    ]
                }
            )

    manager._people = SimpleNamespace(
        people=lambda: SimpleNamespace(connections=lambda: _Connections())
    )
    for query in ("A A mamá", "a a mama", "A.A. Mama", "aa  mama", "Aa Mama"):
        result = await manager.find_contact(query)
        assert result["contacts"], "no encontrado con %r" % query
        assert result["contacts"][0]["name"] == "Aa Mama"


async def test_find_contact_resolves_kinship_variants():
    manager = _manager()

    class _Connections:
        def list(self, **_kwargs):
            return _Req(
                {
                    "connections": [
                        {
                            "names": [{"displayName": "Madre Ivi"}],
                            "phoneNumbers": [{"value": "600000003"}],
                        },
                        {
                            "names": [{"displayName": "Aa Mama"}],
                            "phoneNumbers": [{"value": "655-225-607"}],
                        },
                    ]
                }
            )

    manager._people = SimpleNamespace(
        people=lambda: SimpleNamespace(connections=lambda: _Connections())
    )
    result = await manager.find_contact("mi madre")
    names = [c["name"] for c in result["contacts"]]
    assert "Aa Mama" in names, "'mi madre' no resolvio a Aa Mama"
    assert "Madre Ivi" in names


async def test_find_contact_prefers_full_name_matches():
    manager = _manager()

    class _Connections:
        def list(self, **_kwargs):
            return _Req(
                {
                    "connections": [
                        {
                            "names": [{"displayName": "Aa Mama"}],
                            "phoneNumbers": [{"value": "655-225-607"}],
                        },
                        {
                            "names": [{"displayName": "Mama Raulito"}],
                            "phoneNumbers": [{"value": "600000004"}],
                        },
                    ]
                }
            )

    manager._people = SimpleNamespace(
        people=lambda: SimpleNamespace(connections=lambda: _Connections())
    )
    result = await manager.find_contact("mama raulito")
    assert result["contacts"][0]["name"] == "Mama Raulito"


async def test_find_contact_dispatch_uses_learned_alias(monkeypatch):
    from src.handlers import chat as chat_mod

    calls = []

    async def fake_find(query, max_results=5):
        calls.append(query)
        if query == "Aa Mama":
            return {
                "success": True,
                "contacts": [{"name": "Aa Mama", "email": "", "phone": "655-225-607"}],
            }
        return {"success": True, "contacts": []}

    async def fake_knowledge(chat_id, query, limit=20):
        return [{"key": "madre", "value": "Aa Mama", "category": "general"}]

    monkeypatch.setattr(chat_mod.google_services, "find_contact", fake_find)
    monkeypatch.setattr("src.database.db.search_personal_knowledge", fake_knowledge)

    result = await chat_mod._execute_tool(1, "find_contact", {"query": "el numero de mi madre"})
    assert result["success"]
    assert "Aa Mama" in result["message"]
    assert calls == ["Aa Mama"]


async def test_find_contact_dispatch_guides_when_missing(monkeypatch):
    from src.handlers import chat as chat_mod

    async def fake_find(query, max_results=5):
        return {"success": True, "contacts": []}

    async def fake_knowledge(chat_id, query, limit=20):
        return []

    monkeypatch.setattr(chat_mod.google_services, "find_contact", fake_find)
    monkeypatch.setattr("src.database.db.search_personal_knowledge", fake_knowledge)

    result = await chat_mod._execute_tool(1, "find_contact", {"query": "zzz"})
    assert result["success"]
    assert "nombre exacto" in result["message"]


async def test_find_contact_orders_closest_name_first():
    manager = _manager()

    class _Connections:
        def list(self, **_kwargs):
            return _Req(
                {
                    "connections": [
                        {
                            "names": [{"displayName": "Mama Raulito"}],
                            "phoneNumbers": [{"value": "600000002"}],
                        },
                        {
                            "names": [{"displayName": "Aa Mama"}],
                            "phoneNumbers": [{"value": "600000001"}],
                        },
                    ]
                }
            )

    manager._people = SimpleNamespace(
        people=lambda: SimpleNamespace(connections=lambda: _Connections())
    )
    result = await manager.find_contact("mama")
    assert result["contacts"][0]["name"] == "Aa Mama"


async def test_list_all_contacts_paginates():
    manager = _manager()
    pages = {
        None: {
            "connections": [
                {"names": [{"displayName": "Ana"}], "phoneNumbers": [{"value": "600000001"}]}
            ],
            "nextPageToken": "p2",
        },
        "p2": {
            "connections": [
                {"names": [{"displayName": "Mama"}], "phoneNumbers": [{"value": "600111222"}]}
            ]
        },
    }

    class _Connections:
        def list(self, **kwargs):
            return _Req(pages[kwargs.get("pageToken")])

    manager._people = SimpleNamespace(
        people=lambda: SimpleNamespace(connections=lambda: _Connections())
    )
    result = await manager.list_all_contacts()
    assert [c["name"] for c in result["contacts"]] == ["Ana", "Mama"]


async def test_list_calendar_events_maps_fields():
    manager = _manager()
    captured = {}

    class _Events:
        def list(self, **kwargs):
            captured.update(kwargs)
            return _Req(
                {
                    "items": [
                        {
                            "summary": "Cita",
                            "start": {"dateTime": "2026-10-01T10:00:00+02:00"},
                            "end": {"dateTime": "2026-10-01T11:00:00+02:00"},
                        }
                    ]
                }
            )

    manager._calendar = SimpleNamespace(events=lambda: _Events())
    result = await manager.list_calendar_events()
    assert result["events"][0]["title"] == "Cita"
    assert captured["calendarId"] == manager.calendar_id


async def test_list_drive_lists_folder_contents():
    manager = _manager()
    queries = []

    class _Files:
        def list(self, **kwargs):
            queries.append(kwargs["q"])
            if "and name" in kwargs["q"]:
                return _Req({"files": [{"id": "F1", "name": "Titulaciones"}]})
            return _Req({"files": [{"id": "A1", "name": "TFM.pdf", "mimeType": "application/pdf"}]})

    manager._drive = SimpleNamespace(files=lambda: _Files())
    result = await manager.list_drive(folder="Titulaciones")
    assert result["success"]
    assert result["files"][0]["name"] == "TFM.pdf"
    assert "'F1' in parents" in queries[-1]


async def test_list_drive_unknown_folder_reports_error():
    manager = _manager()

    class _Files:
        def list(self, **_kwargs):
            return _Req({"files": []})

    manager._drive = SimpleNamespace(files=lambda: _Files())
    result = await manager.list_drive(folder="NoExiste")
    assert not result["success"]
    assert "NoExiste" in result["message"]


async def test_send_email_builds_raw_message():
    import base64

    manager = _manager()
    captured = {}

    class _Messages:
        def send(self, **kwargs):
            captured.update(kwargs)
            return _Req({"id": "m1"})

    manager._gmail = SimpleNamespace(users=lambda: SimpleNamespace(messages=lambda: _Messages()))
    result = await manager.send_email("mama@example.com", "Hola", "Te quiero mucho")
    assert result["success"]
    raw = base64.urlsafe_b64decode(captured["body"]["raw"]).decode()
    assert "To: mama@example.com" in raw
    assert "Subject: Hola" in raw
    assert "Te quiero mucho" in raw


async def test_send_email_resolves_contact_name():
    manager = _manager()
    captured = {}

    class _Connections:
        def list(self, **_kwargs):
            return _Req(
                {
                    "connections": [
                        {
                            "names": [{"displayName": "Aa Mama"}],
                            "emailAddresses": [{"value": "mama@example.com"}],
                        }
                    ]
                }
            )

    class _Messages:
        def send(self, **kwargs):
            captured.update(kwargs)
            return _Req({"id": "m2"})

    manager._people = SimpleNamespace(
        people=lambda: SimpleNamespace(connections=lambda: _Connections())
    )
    manager._gmail = SimpleNamespace(users=lambda: SimpleNamespace(messages=lambda: _Messages()))
    result = await manager.send_email("Aa Mama", "Hola", "Te quiero")
    assert result["success"]
    assert "mama@example.com" in result["message"]


async def test_send_email_reports_missing_contact_email():
    manager = _manager()

    class _Connections:
        def list(self, **_kwargs):
            return _Req({"connections": []})

    manager._people = SimpleNamespace(
        people=lambda: SimpleNamespace(connections=lambda: _Connections())
    )
    result = await manager.send_email("Desconocido", "Hola", "Cuerpo")
    assert not result["success"]


async def test_send_email_rejects_invalid_address():
    manager = _manager()
    result = await manager.send_email("sin-arroba", "Hola", "Cuerpo")
    assert not result["success"]


async def test_manage_calendar_delete_by_title(monkeypatch):
    from src.handlers import chat as chat_mod

    deleted = {}

    async def fake_list(max_results=10):
        return [{"id": "ev1", "title": "Lunes triste", "start": "2026-09-28T00:00:00"}]

    async def fake_delete(event_id):
        deleted["id"] = event_id
        return {"success": True, "message": "Evento eliminado de Google Calendar"}

    monkeypatch.setattr(chat_mod.gcal, "list_upcoming_events", fake_list)
    monkeypatch.setattr(chat_mod.gcal, "delete_event", fake_delete)
    result = await chat_mod._execute_tool(
        1, "manage_google_calendar", {"action": "delete", "title": "lunes triste", "confirm": True}
    )
    assert result["success"]
    assert deleted["id"] == "ev1"


async def test_manage_calendar_delete_unknown_title(monkeypatch):
    from src.handlers import chat as chat_mod

    async def fake_list(max_results=10):
        return [{"id": "ev1", "title": "Otra cosa", "start": "2026-09-28T00:00:00"}]

    monkeypatch.setattr(chat_mod.gcal, "list_upcoming_events", fake_list)
    result = await chat_mod._execute_tool(
        1, "manage_google_calendar", {"action": "delete", "title": "lunes triste", "confirm": True}
    )
    assert not result["success"]
    assert "lunes triste" in result["message"]


async def test_find_contact_falls_back_to_other_contacts():
    manager = _manager()

    class _Connections:
        def list(self, **_kwargs):
            return _Req({"connections": []})

    class _Other:
        def search(self, **kwargs):
            assert kwargs["query"] == "Aa Mama"
            return _Req(
                {
                    "results": [
                        {
                            "names": [{"displayName": "Aa Mama"}],
                            "phoneNumbers": [{"value": "600111333"}],
                        }
                    ]
                }
            )

    manager._people = SimpleNamespace(
        people=lambda: SimpleNamespace(connections=lambda: _Connections()),
        otherContacts=lambda: _Other(),
    )
    result = await manager.find_contact("Aa Mama")
    assert result["contacts"][0]["name"] == "Aa Mama"
    assert result["contacts"][0]["phone"] == "600111333"


async def test_list_drive_filters_folders_and_files():
    manager = _manager()
    captured = {}

    class _Files:
        def list(self, **kwargs):
            captured.clear()
            captured.update(kwargs)
            return _Req({"files": []})

    manager._drive = SimpleNamespace(files=lambda: _Files())
    await manager.list_drive(kind="folders")
    assert "application/vnd.google-apps.folder" in captured["q"]
    assert captured["q"].startswith("mimeType =")
    await manager.list_drive(kind="files")
    assert "mimeType !=" in captured["q"]
    await manager.list_drive(kind="all")
    assert "q" not in captured


async def test_find_contact_prioriza_contactos_con_email():
    """Bug 2026-09-30: con varias 'Ana', la que tiene correo debe ir primero."""
    manager = _manager()

    class _Connections:
        def list(self, **_kwargs):
            return _Req(
                {
                    "connections": [
                        {"names": [{"displayName": "Ana Museo"}]},
                        {
                            "names": [{"displayName": "Ana!!"}],
                            "emailAddresses": [{"value": "anabel.84.amg@gmail.com"}],
                        },
                        {"names": [{"displayName": "Aitana"}]},
                    ]
                }
            )

    manager._people = SimpleNamespace(
        people=lambda: SimpleNamespace(connections=lambda: _Connections())
    )
    result = await manager.find_contact("Ana")
    assert result["contacts"]
    assert result["contacts"][0]["email"] == "anabel.84.amg@gmail.com"
