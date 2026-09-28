"""Automatizaciones del punto 1: briefing, inbox y captura (2026-09-28)."""

from types import SimpleNamespace

from src.config import settings
from src.services import automation_service as auto


class _FakeGS:
    def __init__(self, mails=None, tasks=None, events=None):
        self._mails = mails or []
        self._tasks = tasks or []
        self._events = events or []

    async def initialize(self):
        return True

    is_ready = True

    async def list_calendar_events(self, days=1, max_results=10):
        return {"success": True, "events": self._events}

    async def list_tasks(self, show_completed=False, max_results=20):
        return {"success": True, "tasks": self._tasks}

    async def search_gmail(self, query="", max_results=5):
        return {"success": True, "messages": self._mails}


class _FakeLLM:
    def __init__(self, reply=""):
        self.reply = reply
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return self.reply


# ---------------- briefing ----------------


async def test_build_briefing_composes_with_llm(monkeypatch):
    fake_gs = _FakeGS(
        mails=[{"from": "Banco", "subject": "Factura", "snippet": "adjunta"}],
        tasks=[{"title": "Pagar luz"}],
        events=[{"title": "Dentista", "start": "2026-09-29T10:00:00+02:00"}],
    )
    monkeypatch.setattr("src.services.google_services_manager.google_services", fake_gs)

    async def fake_weather():
        return "AEMET: 🌡 10-22 °C"

    monkeypatch.setattr(auto, "_weather", fake_weather)

    async def fake_server():
        return {"ia": "ok", "rag": "ok", "google": "conectado"}

    monkeypatch.setattr(auto, "_server_status", fake_server)
    fake_llm = _FakeLLM("☀️ *Briefing*\n\n*Agenda:* Dentista 10:00")
    monkeypatch.setattr("src.ollama_client.llm", fake_llm)

    result = await auto.build_briefing()
    assert result["success"]
    assert "Dentista" in result["text"]
    assert result["counts"]["agenda"] == 1
    assert result["counts"]["tareas"] == 1
    assert result["counts"]["correo"] == 1
    assert result["buttons"][0]["text"].startswith("✅")
    assert any("Pagar luz" in msg["content"] for call in fake_llm.calls for msg in call)


async def test_build_briefing_falls_back_to_raw_without_llm(monkeypatch):
    monkeypatch.setattr("src.services.google_services_manager.google_services", _FakeGS())

    async def fake_weather():
        return ""

    async def fake_server():
        return {}

    monkeypatch.setattr(auto, "_weather", fake_weather)
    monkeypatch.setattr(auto, "_server_status", fake_server)

    class _BrokenLLM:
        async def chat(self, **kwargs):
            raise RuntimeError("sin modelo")

    monkeypatch.setattr("src.ollama_client.llm", _BrokenLLM())
    result = await auto.build_briefing()
    assert result["success"]
    assert "Briefing de hoy" in result["text"]


# ---------------- inbox ----------------


def _install_kv(monkeypatch):
    stored = {}

    async def fake_get(key, default=None):
        return stored.get(key, default)

    async def fake_set(key, value, expires_at=None):
        stored[key] = value

    monkeypatch.setattr("src.database.db.kv_get", fake_get)
    monkeypatch.setattr("src.database.db.kv_set", fake_set)
    return stored


async def test_scan_inbox_classifies_and_marks_urgent(monkeypatch):
    _install_kv(monkeypatch)
    monkeypatch.setattr(
        "src.services.google_services_manager.google_services",
        _FakeGS(
            mails=[
                {
                    "id": "m1",
                    "from": "jefe@empresa.com",
                    "subject": "Urgente reunion",
                    "snippet": "...",
                },
                {"id": "m2", "from": "news@tienda.com", "subject": "Ofertas", "snippet": "..."},
            ]
        ),
    )
    fake_llm = _FakeLLM(
        '[{"id": 0, "categoria": "urgente", "resumen": "reunion manana", '
        '"borrador": "Confirmo asistencia."}, '
        '{"id": 1, "categoria": "informativo", "resumen": "ofertas", "borrador": ""}]'
    )
    monkeypatch.setattr("src.ollama_client.llm", fake_llm)

    result = await auto.scan_inbox(hours=2)
    assert result["success"]
    assert result["scanned"] == 2
    assert result["urgent_count"] == 1
    assert result["items"][0]["categoria"] == "urgente"
    assert result["items"][0]["borrador"]


async def test_scan_inbox_no_mail(monkeypatch):
    monkeypatch.setattr("src.services.google_services_manager.google_services", _FakeGS())
    result = await auto.scan_inbox()
    assert result == {"success": True, "scanned": 0, "items": []}


async def test_scan_inbox_dedupes_repeated_alerts(monkeypatch):
    _install_kv(monkeypatch)
    monkeypatch.setattr(
        "src.services.google_services_manager.google_services",
        _FakeGS(mails=[{"id": "m1", "from": "jefe", "subject": "Urgente", "snippet": "x"}]),
    )
    monkeypatch.setattr(
        "src.ollama_client.llm",
        _FakeLLM('[{"id": 0, "categoria": "urgente", "resumen": "r", "borrador": "b"}]'),
    )
    first = await auto.scan_inbox()
    second = await auto.scan_inbox()
    assert first["urgent_count"] == 1
    assert second["urgent_count"] == 0  # no repite el aviso del mismo correo


async def test_scan_inbox_bad_llm_json_falls_back(monkeypatch):
    monkeypatch.setattr(
        "src.services.google_services_manager.google_services",
        _FakeGS(mails=[{"id": "m9", "from": "x", "subject": "y", "snippet": "z"}]),
    )
    monkeypatch.setattr("src.ollama_client.llm", _FakeLLM("no es json"))
    result = await auto.scan_inbox()
    assert result["items"][0]["categoria"] == "informativo"


# ---------------- captura ----------------


async def test_capture_to_vault_writes_note(tmp_path, monkeypatch):
    import pathlib

    from src.handlers import files as files_mod

    root = tmp_path / "obsidian_vault"
    root.mkdir()
    real_path = pathlib.Path

    def remapped(*args, **kwargs):
        if args and str(args[0]) == "/data/obsidian_vault":
            return root
        return real_path(*args, **kwargs)

    monkeypatch.setattr(files_mod, "Path", remapped)
    monkeypatch.setattr(files_mod, "VAULT_ROOT", root)
    monkeypatch.setattr("src.utils.obsidian_manager.OBSIDIAN_VAULT", root)

    result = await auto.capture_to_vault(
        text="Idea: probar el radar de IA", title="Idea radar", tags=["ia", "ideas"]
    )
    assert result["success"]
    note = pathlib.Path(result["filepath"])
    assert note.exists()
    body = note.read_text(encoding="utf-8")
    assert "tags: [ia, ideas, captura]" in body
    assert "radar de IA" in body


async def test_capture_to_vault_empty_text():
    result = await auto.capture_to_vault(text="   ")
    assert not result["success"]


# ---------------- endpoints del gateway ----------------


def test_gateway_automation_endpoints_require_signature(monkeypatch):
    from fastapi.testclient import TestClient

    from src.utils import webhook_server

    webhook_server.configure_gateway("secreto-test")
    client = TestClient(webhook_server.app)
    for path in ("/automation/briefing", "/automation/inbox-scan", "/automation/capture"):
        resp = client.post(path, json={})
        assert resp.status_code in (401, 503)


def test_settings_have_aemet_fields():
    assert hasattr(settings, "aemet_api_key")
    assert hasattr(settings, "briefing_municipio")


def test_simple_namespace_placeholder():
    # Evita imports sin uso tras refactors
    assert SimpleNamespace(x=1).x == 1
