"""Arreglos Google 2026-09-27: bandeja principal, contactos con acentos y Drive."""

from types import SimpleNamespace

from src.services.google_services_manager import GoogleServicesManager


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
        1, "manage_google_calendar", {"action": "delete", "title": "lunes triste"}
    )
    assert result["success"]
    assert deleted["id"] == "ev1"


async def test_manage_calendar_delete_unknown_title(monkeypatch):
    from src.handlers import chat as chat_mod

    async def fake_list(max_results=10):
        return [{"id": "ev1", "title": "Otra cosa", "start": "2026-09-28T00:00:00"}]

    monkeypatch.setattr(chat_mod.gcal, "list_upcoming_events", fake_list)
    result = await chat_mod._execute_tool(
        1, "manage_google_calendar", {"action": "delete", "title": "lunes triste"}
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
            assert kwargs["query"] == "aa mama"
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
