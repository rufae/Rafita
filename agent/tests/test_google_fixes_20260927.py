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
