"""trigger_n8n y briefing/recordatorios proactivos (mejoras 1, 2 y 6)."""

from datetime import UTC

from src.config import settings
from src.handlers import chat as chat_mod
from src.utils import proactive_briefing


class _FakeBot:
    def __init__(self):
        self.messages = []

    async def send_proactive_message(self, chat_id, text):
        self.messages.append((chat_id, text))


# ---------- trigger_n8n ----------


async def test_trigger_n8n_resolves_named_workflow(monkeypatch):
    monkeypatch.setattr(settings, "n8n_webhooks", '{"facturas": "http://n8n.home/webhook/abc"}')
    calls = {}

    async def fake_post(url, payload):
        calls["url"] = url
        calls["payload"] = payload
        return True, "HTTP 200"

    monkeypatch.setattr(chat_mod, "_post_n8n_webhook", fake_post)
    result = await chat_mod._trigger_n8n({"workflow": "facturas", "payload": {"mes": 9}})
    assert result["success"]
    assert calls["url"] == "http://n8n.home/webhook/abc"
    assert calls["payload"] == {"mes": 9}


async def test_trigger_n8n_unknown_workflow_explains(monkeypatch):
    monkeypatch.setattr(settings, "n8n_webhooks", "{}")
    result = await chat_mod._trigger_n8n({"workflow": "inventada"})
    assert not result["success"]
    assert "N8N_WEBHOOKS" in result["message"]


async def test_trigger_n8n_accepts_full_url(monkeypatch):
    calls = {}

    async def fake_post(url, payload):
        calls["url"] = url
        return True, "HTTP 204"

    monkeypatch.setattr(chat_mod, "_post_n8n_webhook", fake_post)
    result = await chat_mod._trigger_n8n({"workflow": "http://n8n.home/webhook/xyz"})
    assert result["success"]
    assert calls["url"] == "http://n8n.home/webhook/xyz"


# ---------- briefing ----------


async def test_send_briefing_composes_and_sends(monkeypatch):
    monkeypatch.setattr(settings, "briefing_enabled", True)
    monkeypatch.setattr(settings, "admin_ids", [7])

    async def fake_weather():
        return "🌡 12-20 °C"

    async def fake_agenda(days):
        return ["  • 10:00 — Dentista"]

    async def fake_mail():
        return ["  • Factura (banco)"]

    monkeypatch.setattr(proactive_briefing, "_weather_summary", fake_weather)
    monkeypatch.setattr(proactive_briefing, "_agenda_lines", fake_agenda)
    monkeypatch.setattr(proactive_briefing, "_mail_lines", fake_mail)

    bot = _FakeBot()
    sent = await proactive_briefing.send_briefing(bot)
    assert sent == 1
    text = bot.messages[0][1]
    assert "Dentista" in text
    assert "Factura" in text
    assert "12-20" in text


async def test_send_briefing_disabled(monkeypatch):
    monkeypatch.setattr(settings, "briefing_enabled", False)
    bot = _FakeBot()
    assert await proactive_briefing.send_briefing(bot) == 0
    assert not bot.messages


# ---------- recordatorios proactivos ----------


async def test_reminders_send_once_per_event(monkeypatch):
    from datetime import datetime, timedelta

    monkeypatch.setattr(settings, "admin_ids", [7])
    future = (datetime.now(UTC) + timedelta(hours=5)).isoformat()

    class _FakeGS:
        is_ready = True

        async def initialize(self):
            return True

        async def list_calendar_events(self, days=2, max_results=25):
            return {"success": True, "events": [{"title": "Cena", "start": future}]}

        async def list_tasks(self):
            return {"success": True, "tasks": []}

    stored = {}

    async def fake_kv_get(key, default=None):
        return stored.get(key, default)

    async def fake_kv_set(key, value, expires_at=None):
        stored[key] = value

    monkeypatch.setattr(proactive_briefing, "google_services", _FakeGS(), raising=False)
    monkeypatch.setattr(
        "src.services.google_services_manager.google_services",
        _FakeGS(),
    )
    monkeypatch.setattr("src.database.db.kv_get", fake_kv_get)
    monkeypatch.setattr("src.database.db.kv_set", fake_kv_set)

    bot = _FakeBot()
    first = await proactive_briefing.send_proactive_reminders(bot)
    second = await proactive_briefing.send_proactive_reminders(bot)
    assert first == 1
    assert second == 0  # no repite el mismo evento
    assert "Cena" in bot.messages[0][1]
