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


# ---------- Degradación honesta (2026-10-04 /sync_google) ----------


class _FakeGmailCaido(_FakeServices):
    async def search_gmail(self, query="", max_results=5):
        raise ValueError("Error de Google (400) en buscar correos: failedPrecondition")


async def test_sync_degrada_gmail_sin_abortar(monkeypatch):
    """Una fuente caida no tumba el comando ni vacia su nota."""
    written = {}

    async def fake_overwrite(title, content, folder=""):
        written[title] = (content, folder)
        return {"success": True, "filepath": "/vault/Google/%s.md" % title}

    monkeypatch.setattr(sync_mod, "google_services", _FakeGmailCaido())
    monkeypatch.setattr(sync_mod, "overwrite_note", fake_overwrite)

    result = await sync_mod.sync_google_to_vault()

    assert result["success"] is True
    assert "Correo Google" not in written  # la nota previa se conserva
    assert set(written) == {
        "Contactos Google",
        "Calendario Google",
        "Drive Google",
        "Tareas Google",
    }
    assert result["omitidas"] == ["correos"]
    assert "Fuentes omitidas" in result["message"]
    assert "correos" in result["message"]
    assert "failedPrecondition" in result["message"]


async def test_sync_todo_falla_es_honesto(monkeypatch):
    class _TodoCaido(_FakeServices):
        async def list_all_contacts(self, max_results=2000):
            raise ValueError("contactos caidos")

        async def list_calendar_events(self, days=90, max_results=100):
            raise ValueError("calendario caido")

        async def list_drive(self, kind="all", max_results=50, folder_id=None, folder=None):
            raise ValueError("drive caido")

        async def list_tasks(self, show_completed=False, max_results=20):
            raise ValueError("tareas caido")

        async def search_gmail(self, query="", max_results=5):
            raise ValueError("gmail caido")

    written = []

    async def fake_overwrite(title, content, folder=""):
        written.append(title)
        return {"success": True, "filepath": "x"}

    monkeypatch.setattr(sync_mod, "google_services", _TodoCaido())
    monkeypatch.setattr(sync_mod, "overwrite_note", fake_overwrite)

    result = await sync_mod.sync_google_to_vault()

    assert result["success"] is False
    assert written == []  # nada que escribir: no se destruyen notas
    assert "No pude copiar nada de Google" in result["message"]
    assert "/setup_google" in result["message"]
    assert set(result["omitidas"]) == {
        "contactos",
        "calendario",
        "drive",
        "tareas",
        "correos",
    }
    assert "gmail caido" in result["message"]
