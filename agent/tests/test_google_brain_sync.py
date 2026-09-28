"""Copia local de Google en el segundo cerebro (2026-09-27)."""

import src.utils.google_brain_sync as sync_mod


class _FakeServices:
    is_ready = True

    async def initialize(self):
        return True

    async def list_all_contacts(self, max_results=2000):
        return {
            "success": True,
            "contacts": [{"name": "Mama", "email": "", "phone": "600111222"}],
        }

    async def list_calendar_events(self, days=90, max_results=100):
        return {
            "success": True,
            "events": [
                {
                    "title": "Dentista",
                    "start": "2026-10-01T10:00:00+02:00",
                    "end": "",
                    "description": "",
                }
            ],
        }

    async def list_tasks(self, show_completed=False, max_results=20):
        return {"success": True, "tasks": [{"id": "t1", "title": "Tarea A"}]}

    async def search_gmail(self, query="", max_results=5):
        return {
            "success": True,
            "messages": [{"subject": "Asunto", "from": "alguien@x.com", "date": "hoy"}],
        }

    async def list_drive(self, kind="all", max_results=50, folder_id=None, folder=None):
        return {
            "success": True,
            "files": [
                {
                    "name": "Titulaciones",
                    "mimeType": "application/vnd.google-apps.folder",
                    "modifiedTime": "2026-09-26T00:00:00Z",
                }
            ],
        }


async def test_sync_google_to_vault_writes_notes(monkeypatch):
    written = {}

    async def fake_overwrite(title, content, folder=""):
        written[title] = (content, folder)
        return {"success": True, "filepath": "/vault/Google/%s.md" % title}

    monkeypatch.setattr(sync_mod, "google_services", _FakeServices())
    monkeypatch.setattr(sync_mod, "overwrite_note", fake_overwrite)

    result = await sync_mod.sync_google_to_vault()

    assert result["success"]
    assert set(written) == {
        "Contactos Google",
        "Calendario Google",
        "Drive Google",
        "Tareas Google",
        "Correo Google",
    }
    assert all(folder == "Google" for _content, folder in written.values())
    assert "Mama" in written["Contactos Google"][0]
    assert "Dentista" in written["Calendario Google"][0]
    assert "carpeta" in written["Drive Google"][0]


async def test_sync_google_requires_connection(monkeypatch):
    class _Offline(_FakeServices):
        is_ready = False

        async def initialize(self):
            return False

    monkeypatch.setattr(sync_mod, "google_services", _Offline())
    result = await sync_mod.sync_google_to_vault()
    assert not result["success"]
    assert "/setup_google" in result["message"]
