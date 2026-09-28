"""Cobertura de utils/proactive_briefing.py: tiempo, agenda, correo y BriefingWorker."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from src.config import settings
from src.utils import proactive_briefing


async def _afn(result):
    return result


class _FakeBot:
    def __init__(self, exc=None):
        self.messages = []
        self._exc = exc

    async def send_proactive_message(self, chat_id, text):
        if self._exc is not None:
            raise self._exc
        self.messages.append((chat_id, text))


class _FakeWeatherClient:
    def __init__(self, payload=None, exc=None):
        self._payload = payload or {}
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url):
        if self._exc is not None:
            raise self._exc
        return SimpleNamespace(json=lambda: self._payload)


class _FakeGS:
    def __init__(self, ready=True, events=None, tasks=None, exc=None):
        self.is_ready = ready
        self._events = events or []
        self._tasks = tasks or []
        self._exc = exc

    async def initialize(self):
        if self._exc is not None:
            raise self._exc
        return True

    async def list_calendar_events(self, days=2, max_results=25):
        return {"success": True, "events": self._events}

    async def list_tasks(self):
        return {"success": True, "tasks": self._tasks}

    async def search_gmail(self, query="", max_results=5):
        return {"success": True, "messages": [{"subject": "Factura", "from": "banco"}]}


def _patch_google(monkeypatch, fake):
    monkeypatch.setattr("src.services.google_services_manager.google_services", fake)


def _kv(monkeypatch, store):
    async def kv_get(key, default=None):
        return store.get(key, default)

    async def kv_set(key, value, expires_at=None):
        store[key] = value

    monkeypatch.setattr(proactive_briefing.db, "kv_get", kv_get)
    monkeypatch.setattr(proactive_briefing.db, "kv_set", kv_set)


# ---------- _weather_summary ----------


async def test_weather_summary_without_coords_is_empty(monkeypatch):
    monkeypatch.setattr(settings, "briefing_lat", 0.0)
    monkeypatch.setattr(settings, "briefing_lon", 0.0)
    assert await proactive_briefing._weather_summary() == ""


async def test_weather_summary_formats_daily(monkeypatch):
    monkeypatch.setattr(settings, "briefing_lat", 40.4)
    monkeypatch.setattr(settings, "briefing_lon", -3.7)
    payload = {
        "daily": {
            "temperature_2m_max": [20.0],
            "temperature_2m_min": [12.0],
            "precipitation_probability_max": [30.0],
        }
    }
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: _FakeWeatherClient(payload))
    text = await proactive_briefing._weather_summary()
    assert "12-20" in text
    assert "30% de lluvia" in text


async def test_weather_summary_handles_missing_fields(monkeypatch):
    monkeypatch.setattr(settings, "briefing_lat", 40.4)
    monkeypatch.setattr(settings, "briefing_lon", -3.7)
    monkeypatch.setattr(
        "httpx.AsyncClient",
        lambda **kwargs: _FakeWeatherClient({"daily": {"temperature_2m_max": [None]}}),
    )
    assert await proactive_briefing._weather_summary() == ""

    monkeypatch.setattr(
        "httpx.AsyncClient",
        lambda **kwargs: _FakeWeatherClient(
            {"daily": {"temperature_2m_max": [18.0], "temperature_2m_min": [None]}}
        ),
    )
    text = await proactive_briefing._weather_summary()
    assert "18-18" in text
    assert "lluvia" not in text


async def test_weather_summary_swallows_http_errors(monkeypatch):
    monkeypatch.setattr(settings, "briefing_lat", 40.4)
    monkeypatch.setattr(settings, "briefing_lon", -3.7)
    monkeypatch.setattr(
        "httpx.AsyncClient", lambda **kwargs: _FakeWeatherClient(exc=RuntimeError("sin red"))
    )
    assert await proactive_briefing._weather_summary() == ""


# ---------- _agenda_lines / _mail_lines ----------


async def test_agenda_lines_formats_events(monkeypatch):
    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    _patch_google(
        monkeypatch,
        _FakeGS(events=[{"title": "Dentista", "start": future}, {"title": "Rara", "start": "???"}]),
    )
    lines = await proactive_briefing._agenda_lines(days=1)
    assert any("Dentista" in line for line in lines)
    assert any("Rara" in line for line in lines)


async def test_agenda_lines_not_ready_and_errors(monkeypatch):
    _patch_google(monkeypatch, _FakeGS(ready=False))
    assert await proactive_briefing._agenda_lines(days=1) == []
    _patch_google(monkeypatch, _FakeGS(exc=RuntimeError("google caido")))
    assert await proactive_briefing._agenda_lines(days=1) == []


async def test_mail_lines_variants(monkeypatch):
    _patch_google(monkeypatch, _FakeGS())
    lines = await proactive_briefing._mail_lines()
    assert lines and "Factura" in lines[0]

    _patch_google(monkeypatch, _FakeGS(ready=False))
    assert await proactive_briefing._mail_lines() == []

    async def boom(**kwargs):
        raise RuntimeError("gmail caido")

    _patch_google(monkeypatch, SimpleNamespace(is_ready=True, search_gmail=boom))
    assert await proactive_briefing._mail_lines() == []


# ---------- send_briefing ----------


async def test_send_briefing_counts_only_delivered(monkeypatch):
    monkeypatch.setattr(settings, "briefing_enabled", True)
    monkeypatch.setattr(settings, "admin_ids", [7, 8])
    monkeypatch.setattr(proactive_briefing, "_weather_summary", lambda: _afn(""))
    monkeypatch.setattr(proactive_briefing, "_agenda_lines", lambda days: _afn([]))
    monkeypatch.setattr(proactive_briefing, "_mail_lines", lambda: _afn([]))

    class _PartialBot(_FakeBot):
        async def send_proactive_message(self, chat_id, text):
            if chat_id == 8:
                raise RuntimeError("bloqueado")
            self.messages.append((chat_id, text))

    bot = _PartialBot()
    sent = await proactive_briefing.send_briefing(bot)
    assert sent == 1
    assert "Nada previsto" in bot.messages[0][1]


# ---------- send_proactive_reminders ----------


async def test_reminders_require_google(monkeypatch):
    _patch_google(monkeypatch, _FakeGS(ready=False))
    assert await proactive_briefing.send_proactive_reminders(_FakeBot()) == 0
    _patch_google(monkeypatch, _FakeGS(exc=RuntimeError("sin google")))
    assert await proactive_briefing.send_proactive_reminders(_FakeBot()) == 0


async def test_reminders_skip_bad_dates_and_out_of_window(monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", [7])
    past = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    far = (datetime.now(UTC) + timedelta(hours=48)).isoformat()
    _patch_google(
        monkeypatch,
        _FakeGS(
            events=[
                {"title": "Roto", "start": "no-es-fecha"},
                {"title": "Antiguo", "start": past},
                {"title": "Lejano", "start": far},
            ]
        ),
    )
    store = {}
    _kv(monkeypatch, store)
    bot = _FakeBot()
    assert await proactive_briefing.send_proactive_reminders(bot) == 0
    assert not bot.messages


async def test_reminders_send_tasks_once(monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", [7])
    _patch_google(monkeypatch, _FakeGS(tasks=[{"id": "t1", "title": "Comprar pan"}]))
    store = {}
    _kv(monkeypatch, store)
    bot = _FakeBot()
    first = await proactive_briefing.send_proactive_reminders(bot)
    second = await proactive_briefing.send_proactive_reminders(bot)
    assert first == 1
    assert second == 0
    assert "Comprar pan" in bot.messages[0][1]


async def test_reminders_survive_delivery_errors(monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", [7])
    future = (datetime.now(UTC) + timedelta(hours=5)).isoformat()
    _patch_google(
        monkeypatch,
        _FakeGS(
            events=[{"title": "Cena", "start": future}], tasks=[{"id": "t1", "title": "Tarea"}]
        ),
    )
    store = {}
    _kv(monkeypatch, store)
    bot = _FakeBot(exc=RuntimeError("telegram caido"))
    assert await proactive_briefing.send_proactive_reminders(bot) == 0
    assert store


# ---------- BriefingWorker ----------


async def test_worker_start_and_stop():
    worker = proactive_briefing.BriefingWorker()
    assert worker._task is None
    shutdown = asyncio.Event()
    await worker.start(shutdown)
    await asyncio.sleep(0)
    assert worker._task is not None
    await worker.stop()
    assert worker._task is None


async def test_worker_run_loop_ticks_then_stops(monkeypatch):
    worker = proactive_briefing.BriefingWorker()
    shutdown = asyncio.Event()
    worker._shutdown_event = shutdown
    ticks = []

    async def fake_tick():
        ticks.append(1)

    sleeps = {"n": 0}

    async def fake_sleep(delay):
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            shutdown.set()

    monkeypatch.setattr(worker, "_tick", fake_tick)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    await worker._run_loop()
    assert ticks == [1]
    assert worker._failures == 0


async def test_worker_run_loop_retries_after_failure(monkeypatch):
    worker = proactive_briefing.BriefingWorker()
    shutdown = asyncio.Event()
    worker._shutdown_event = shutdown
    calls = {"n": 0}

    async def fake_tick():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("tick roto")

    sleeps = {"n": 0}

    async def fake_sleep(delay):
        sleeps["n"] += 1
        if sleeps["n"] >= 4:
            shutdown.set()

    monkeypatch.setattr(worker, "_tick", fake_tick)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    await worker._run_loop()
    assert calls["n"] == 2
    assert worker._failures == 0


async def test_worker_stop_swallows_task_cancellation(monkeypatch):
    worker = proactive_briefing.BriefingWorker()

    async def bare_loop():
        await asyncio.sleep(30)

    monkeypatch.setattr(worker, "_run_loop", bare_loop)
    await worker.start(asyncio.Event())
    await asyncio.sleep(0)
    await worker.stop()
    assert worker._task is None


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 28, 8, 15, tzinfo=tz)


async def test_worker_tick_sends_briefing_once_per_day(monkeypatch):
    monkeypatch.setattr(proactive_briefing, "datetime", _FixedDateTime)
    monkeypatch.setattr(settings, "briefing_time", "08:00")
    monkeypatch.setattr(proactive_briefing, "send_proactive_reminders", lambda bot: _afn(0))
    briefings = []

    async def fake_briefing(bot):
        briefings.append(1)
        return 1

    monkeypatch.setattr(proactive_briefing, "send_briefing", fake_briefing)
    monkeypatch.setattr("src.bot.bot", _FakeBot())

    worker = proactive_briefing.BriefingWorker()
    await worker._tick()
    await worker._tick()
    assert briefings == [1]


async def test_worker_tick_handles_bad_briefing_time(monkeypatch):
    class _Morning(_FixedDateTime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 28, 8, 5, tzinfo=tz)

    monkeypatch.setattr(proactive_briefing, "datetime", _Morning)
    monkeypatch.setattr(settings, "briefing_time", "no-es-hora")
    monkeypatch.setattr(proactive_briefing, "send_proactive_reminders", lambda bot: _afn(0))
    briefings = []

    async def fake_briefing(bot):
        briefings.append(1)
        return 1

    monkeypatch.setattr(proactive_briefing, "send_briefing", fake_briefing)
    monkeypatch.setattr("src.bot.bot", _FakeBot())
    await proactive_briefing.BriefingWorker()._tick()
    assert briefings == [1]


async def test_worker_tick_skips_briefing_outside_window(monkeypatch):
    class _Noon(_FixedDateTime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 28, 8, 45, tzinfo=tz)

    monkeypatch.setattr(proactive_briefing, "datetime", _Noon)
    monkeypatch.setattr(settings, "briefing_time", "08:00")
    monkeypatch.setattr(proactive_briefing, "send_proactive_reminders", lambda bot: _afn(0))
    briefings = []

    async def fake_briefing(bot):
        briefings.append(1)
        return 1

    monkeypatch.setattr(proactive_briefing, "send_briefing", fake_briefing)
    monkeypatch.setattr("src.bot.bot", _FakeBot())
    await proactive_briefing.BriefingWorker()._tick()
    assert not briefings
