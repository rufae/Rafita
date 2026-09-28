"""Cobertura de main.py: ProactiveWorker, BackgroundIndexer y utilidades."""

import asyncio
import os
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.main as main_mod
from src.main import Application, BackgroundIndexer, ProactiveWorker


class _FixedDT:
    """Sustituye a main.datetime para controlar la ventana de GC."""

    _now = datetime(2026, 1, 5, 3, 5)

    @classmethod
    def now(cls):
        return cls._now


class _NoonDT:
    """Fuera de la ventana de GC (3:00-3:10)."""

    _now = datetime(2026, 1, 5, 12, 0)

    @classmethod
    def now(cls):
        return cls._now


class FakeDB:
    def __init__(self):
        self.executed = []
        self.vacuum_error = None
        self.chat_ids = [1, 2]
        self.expiring_events = {}
        self.unread = {}
        self.expiring_alerts = {}
        self.due_recurring = []
        self.next_run = "2026-01-06 09:00"
        self.updated = []
        self.marked_read = []
        self.raise_chat_ids = None

    async def get_all_chat_ids(self):
        if self.raise_chat_ids:
            raise self.raise_chat_ids
        return self.chat_ids

    async def get_expiring_events(self, days):
        return self.expiring_events.get(days, [])

    async def get_unread_alert_count(self, chat_id):
        return self.unread.get(chat_id, 0)

    async def get_expiring_alerts(self, days):
        return self.expiring_alerts.get(days, [])

    async def get_due_recurring_alerts(self):
        return self.due_recurring

    async def compute_next_run(self, pattern, current_run):
        return self.next_run

    async def update_alert_next_run(self, alert_id, next_run):
        self.updated.append((alert_id, next_run))

    async def mark_alert_read(self, alert_id):
        self.marked_read.append(alert_id)

    async def execute(self, sql, params=()):
        self.executed.append(sql)
        if self.vacuum_error:
            raise self.vacuum_error


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_proactive_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        return True


def _install(monkeypatch):
    fake_db = FakeDB()
    fake_bot = FakeBot()
    monkeypatch.setattr(main_mod, "db", fake_db)
    monkeypatch.setattr(main_mod, "bot", fake_bot)
    return fake_db, fake_bot


def _sent_texts(fake_bot):
    return [text for _chat, text in fake_bot.sent]


# ---------------------------------------------------------------------------
# ProactiveWorker: notificaciones
# ---------------------------------------------------------------------------


async def test_notify_expiring_events_labels(monkeypatch):
    fake_db, fake_bot = _install(monkeypatch)
    fake_db.expiring_events = {
        30: [{"chat_id": 1, "title": "Aniversario", "event_datetime": "2026-02-01"}],
        1: [{"chat_id": 1, "title": "Cita medica", "event_datetime": "2026-01-05 12:00"}],
    }
    worker = ProactiveWorker()
    await worker._notify_expiring_events(1)
    texts = _sent_texts(fake_bot)
    assert len(texts) == 2
    assert any("en 30 días" in t for t in texts)
    assert any("hoy" in t for t in texts)
    assert any("Cita medica" in t for t in texts)


async def test_notify_expiring_events_skips_other_chats(monkeypatch):
    fake_db, fake_bot = _install(monkeypatch)
    fake_db.expiring_events = {7: [{"chat_id": 99, "title": "Ajena", "event_datetime": "x"}]}
    worker = ProactiveWorker()
    await worker._notify_expiring_events(1)
    assert fake_bot.sent == []


async def test_notify_unread_alerts_singular_plural_and_none(monkeypatch):
    fake_db, fake_bot = _install(monkeypatch)
    worker = ProactiveWorker()

    fake_db.unread = {1: 0}
    await worker._notify_unread_alerts(1)
    assert fake_bot.sent == []

    fake_db.unread = {1: 1}
    await worker._notify_unread_alerts(1)
    assert "1 alerta pendiente" in fake_bot.sent[-1][1]
    assert "alertas pendientes" not in fake_bot.sent[-1][1]

    fake_db.unread = {1: 3}
    await worker._notify_unread_alerts(1)
    assert "3 alertas pendientes" in fake_bot.sent[-1][1]


async def test_notify_expiring_alerts_labels(monkeypatch):
    fake_db, fake_bot = _install(monkeypatch)
    fake_db.expiring_alerts = {
        7: [{"chat_id": 1, "message": "Renovar seguro", "expires_at": "2026-01-12"}],
        1: [{"chat_id": 1, "message": "Pagar luz", "expires_at": "2026-01-05"}],
    }
    worker = ProactiveWorker()
    await worker._notify_expiring_alerts(1)
    texts = _sent_texts(fake_bot)
    assert any("en 7 días" in t and "Renovar seguro" in t for t in texts)
    assert any("hoy" in t and "Pagar luz" in t for t in texts)


async def test_notify_recurring_alerts_updates_next_run(monkeypatch):
    fake_db, fake_bot = _install(monkeypatch)
    fake_db.due_recurring = [
        {"id": 7, "chat_id": 1, "message": "Regar plantas", "pattern": "daily", "next_run": "x"},
        {"id": 8, "chat_id": 2, "message": "Otra", "pattern": "daily", "next_run": "x"},
    ]
    worker = ProactiveWorker()
    await worker._notify_recurring_alerts(1)
    texts = _sent_texts(fake_bot)
    assert texts == ["🔔 *Recordatorio recurrente:* Regar plantas"]
    assert fake_db.updated == [(7, "2026-01-06 09:00")]
    assert fake_db.marked_read == []


async def test_notify_recurring_alerts_marks_read_without_next_run(monkeypatch):
    fake_db, fake_bot = _install(monkeypatch)
    fake_db.next_run = None
    fake_db.due_recurring = [
        {"id": 9, "chat_id": 1, "message": "Ultima vez", "pattern": "yearly", "next_run": "x"}
    ]
    worker = ProactiveWorker()
    await worker._notify_recurring_alerts(1)
    assert fake_db.marked_read == [9]
    assert fake_db.updated == []


# ---------------------------------------------------------------------------
# ProactiveWorker: check + garbage collection
# ---------------------------------------------------------------------------


async def test_check_and_notify_runs_gc_and_notifies(monkeypatch, tmp_path):
    fake_db, fake_bot = _install(monkeypatch)
    monkeypatch.setattr(main_mod, "datetime", _FixedDT)
    monkeypatch.setattr(main_mod, "TEMP_CLEANUP_DIRS", [tmp_path])
    monkeypatch.setattr(main_mod, "GC_HOUR", 3)
    monkeypatch.setattr(main_mod, "GC_MINUTE", 0)

    old_file = tmp_path / "viejo.ogg"
    old_file.write_bytes(b"x")
    new_file = tmp_path / "nuevo.wav"
    new_file.write_bytes(b"x")
    wrong_suffix = tmp_path / "viejo.txt"
    wrong_suffix.write_bytes(b"x")
    old_ts = datetime(2026, 1, 1).timestamp()
    new_ts = datetime(2026, 1, 5, 2, 0).timestamp()
    os.utime(old_file, (old_ts, old_ts))
    os.utime(new_file, (new_ts, new_ts))
    os.utime(wrong_suffix, (old_ts, old_ts))

    fake_db.chat_ids = [1]
    fake_db.expiring_events = {1: [{"chat_id": 1, "title": "Hoy", "event_datetime": "x"}]}
    fake_db.unread = {1: 1}
    fake_db.expiring_alerts = {1: [{"chat_id": 1, "message": "Luz", "expires_at": "x"}]}
    fake_db.due_recurring = [
        {"id": 3, "chat_id": 1, "message": "Plantas", "pattern": "daily", "next_run": "x"}
    ]

    worker = ProactiveWorker()
    worker._gc_run_count = 6
    await worker._check_and_notify()

    assert not old_file.exists()
    assert new_file.exists()
    assert wrong_suffix.exists()
    assert worker._gc_run_count == 7
    assert fake_db.executed == ["VACUUM"]
    texts = _sent_texts(fake_bot)
    assert any("hoy" in t for t in texts)
    assert any("1 alerta pendiente" in t for t in texts)
    assert any("Luz" in t for t in texts)
    assert any("Plantas" in t for t in texts)


async def test_check_and_notify_swallows_errors(monkeypatch):
    fake_db, _fake_bot = _install(monkeypatch)
    monkeypatch.setattr(main_mod, "datetime", _NoonDT)
    fake_db.raise_chat_ids = RuntimeError("bd caida")
    worker = ProactiveWorker()
    await worker._check_and_notify()
    assert worker._gc_run_count == 0


async def test_run_garbage_collection_vacuum_failure(monkeypatch, tmp_path):
    fake_db, _fake_bot = _install(monkeypatch)
    monkeypatch.setattr(main_mod, "datetime", _FixedDT)
    monkeypatch.setattr(main_mod, "TEMP_CLEANUP_DIRS", [tmp_path])
    fake_db.vacuum_error = RuntimeError("bloqueada")
    worker = ProactiveWorker()
    worker._gc_run_count = 6
    await worker._run_garbage_collection()
    assert fake_db.executed == ["VACUUM"]


async def test_run_garbage_collection_missing_dirs(monkeypatch, tmp_path):
    _fake_db, _fake_bot = _install(monkeypatch)
    monkeypatch.setattr(main_mod, "datetime", _FixedDT)
    monkeypatch.setattr(main_mod, "TEMP_CLEANUP_DIRS", [tmp_path / "no-existe"])
    worker = ProactiveWorker()
    await worker._run_garbage_collection()
    assert worker._gc_run_count == 1


# ---------------------------------------------------------------------------
# ProactiveWorker: ciclo de vida y bucle
# ---------------------------------------------------------------------------


async def test_proactive_worker_start_and_stop():
    worker = ProactiveWorker()
    shutdown = asyncio.Event()
    await worker.start(shutdown)
    assert worker._task is not None
    assert worker._shutdown_event is shutdown
    await worker.stop()
    assert worker._task is None


async def test_run_loop_stops_on_shutdown(monkeypatch):
    worker = ProactiveWorker()
    shutdown = asyncio.Event()
    shutdown.set()
    worker._shutdown_event = shutdown
    slept = []

    async def fake_sleep(target):
        slept.append(target)

    async def fake_check():
        raise AssertionError("no deberia comprobarse")

    monkeypatch.setattr(worker, "_sleep_until", fake_sleep)
    monkeypatch.setattr(worker, "_check_and_notify", fake_check)
    await worker._run_loop()
    assert len(slept) == 1


async def test_run_loop_retries_after_failure(monkeypatch):
    worker = ProactiveWorker()
    shutdown = asyncio.Event()
    worker._shutdown_event = shutdown

    async def fake_sleep(target):
        return None

    async def fake_check():
        shutdown.set()
        raise RuntimeError("fallo transitorio")

    async def fake_asyncio_sleep(_seconds):
        return None

    monkeypatch.setattr(worker, "_sleep_until", fake_sleep)
    monkeypatch.setattr(worker, "_check_and_notify", fake_check)
    monkeypatch.setattr(main_mod.asyncio, "sleep", fake_asyncio_sleep)
    await worker._run_loop()
    assert worker._consecutive_failures == 1


async def test_run_loop_tolerates_wait_timeout(monkeypatch):
    worker = ProactiveWorker()
    shutdown = asyncio.Event()
    worker._shutdown_event = shutdown

    async def fake_sleep(target):
        raise TimeoutError

    async def fake_check():
        shutdown.set()

    monkeypatch.setattr(worker, "_sleep_until", fake_sleep)
    monkeypatch.setattr(worker, "_check_and_notify", fake_check)
    await worker._run_loop()


async def test_run_loop_survives_bad_check_time(monkeypatch):
    monkeypatch.setattr(main_mod.settings, "proactive_check_time", "no-es-una-hora")
    worker = ProactiveWorker()
    shutdown = asyncio.Event()
    worker._shutdown_event = shutdown
    await worker._run_loop()


# ---------------------------------------------------------------------------
# ProactiveWorker: _sleep_until
# ---------------------------------------------------------------------------


async def test_sleep_until_returns_when_target_passed():
    worker = ProactiveWorker()
    await worker._sleep_until(datetime.now() - timedelta(seconds=1))


async def test_sleep_until_waits_briefly():
    worker = ProactiveWorker()
    target = datetime.now() + timedelta(seconds=0.05)
    await asyncio.wait_for(worker._sleep_until(target), timeout=2)


async def test_sleep_until_is_cancelable():
    worker = ProactiveWorker()
    task = asyncio.create_task(worker._sleep_until(datetime.now() + timedelta(hours=1)))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# BackgroundIndexer (wrapper de VaultIndexer)
# ---------------------------------------------------------------------------


class FakeIndexer:
    def __init__(self):
        self.calls = []

    async def start(self, shutdown_event):
        self.calls.append(("start", shutdown_event))

    async def stop(self):
        self.calls.append(("stop",))

    async def index_all(self):
        self.calls.append(("index_all",))
        return {"message": "backfill ok"}


async def test_background_indexer_delegates(monkeypatch):
    monkeypatch.setattr("src.utils.vault_indexer.VaultIndexer", FakeIndexer)
    indexer = BackgroundIndexer()
    assert isinstance(indexer._indexer, FakeIndexer)
    shutdown = asyncio.Event()
    await indexer.start(shutdown)
    assert await indexer.index_all() == {"message": "backfill ok"}
    await indexer.stop()
    assert indexer._indexer.calls == [("start", shutdown), ("index_all",), ("stop",)]


# ---------------------------------------------------------------------------
# Application: shutdown y utilidades (sin arranque completo)
# ---------------------------------------------------------------------------


async def test_application_shutdown_stops_everything(monkeypatch):
    fake_db, fake_bot = _install(monkeypatch)
    fake_llm = SimpleNamespace(close=AsyncMock())
    fake_vector = SimpleNamespace(close=AsyncMock())
    fake_gcal = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(main_mod, "llm", fake_llm)
    monkeypatch.setattr(main_mod, "vector_db", fake_vector)
    monkeypatch.setattr(main_mod, "gcal", fake_gcal)
    fake_connector = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr("src.utils.app_connector.connector", fake_connector)

    app = Application()
    app._proactive_worker = SimpleNamespace(stop=AsyncMock())
    app._briefing_worker = SimpleNamespace(stop=AsyncMock())
    app._brain_maintainer = SimpleNamespace(stop=AsyncMock())
    app._indexer = SimpleNamespace(stop=AsyncMock())
    gateway_task = _AwaitableTask()
    voice_task = _AwaitableTask()
    app._gateway_task = gateway_task
    app._voice_stream_task = voice_task

    fake_bot.stop = AsyncMock()
    fake_bot.destroy = AsyncMock()
    fake_db.close = AsyncMock()

    await app.shutdown()

    assert gateway_task.cancelled and voice_task.cancelled
    app._indexer.stop.assert_awaited_once()
    app._proactive_worker.stop.assert_awaited_once()
    app._briefing_worker.stop.assert_awaited_once()
    app._brain_maintainer.stop.assert_awaited_once()
    fake_connector.close.assert_awaited_once()
    fake_bot.stop.assert_awaited_once()
    fake_bot.destroy.assert_awaited_once()
    fake_llm.close.assert_awaited_once()
    fake_vector.close.assert_awaited_once()
    fake_gcal.close.assert_awaited_once()
    fake_db.close.assert_awaited_once()


class _AwaitableTask:
    def __init__(self):
        self.cancelled = False

    def done(self):
        return False

    def cancel(self):
        self.cancelled = True

    def __await__(self):
        async def _noop():
            return None

        return _noop().__await__()


async def test_application_shutdown_survives_component_errors(monkeypatch):
    fake_db, fake_bot = _install(monkeypatch)
    fake_llm = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("cayo")))
    fake_vector = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("cayo")))
    fake_gcal = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("cayo")))
    monkeypatch.setattr(main_mod, "llm", fake_llm)
    monkeypatch.setattr(main_mod, "vector_db", fake_vector)
    monkeypatch.setattr(main_mod, "gcal", fake_gcal)
    fake_connector = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("cayo")))
    monkeypatch.setattr("src.utils.app_connector.connector", fake_connector)

    app = Application()
    app._proactive_worker = SimpleNamespace(stop=AsyncMock(side_effect=RuntimeError("cayo")))
    app._briefing_worker = SimpleNamespace(stop=AsyncMock(side_effect=RuntimeError("cayo")))
    app._brain_maintainer = SimpleNamespace(stop=AsyncMock(side_effect=RuntimeError("cayo")))
    app._indexer = SimpleNamespace(stop=AsyncMock(side_effect=RuntimeError("cayo")))
    fake_bot.stop = AsyncMock(side_effect=RuntimeError("cayo"))
    fake_bot.destroy = AsyncMock(side_effect=RuntimeError("cayo"))
    fake_db.close = AsyncMock(side_effect=RuntimeError("cayo"))

    await app.shutdown()


async def test_wait_with_shutdown_returns():
    app = Application()
    app._shutdown_event = asyncio.Event()
    app._shutdown_event.set()
    await asyncio.wait_for(app._wait_with_shutdown(30), timeout=2)


async def test_wait_with_shutdown_without_signal_returns():
    app = Application()
    app._shutdown_event = asyncio.Event()
    await asyncio.wait_for(app._wait_with_shutdown(1), timeout=2)


# ---------------------------------------------------------------------------
# _health_monitor y _catch_up_scan
# ---------------------------------------------------------------------------


async def test_health_monitor_warns_on_failures_and_empty_db(monkeypatch):
    real_sleep = asyncio.sleep
    calls = []

    async def fake_sleep(seconds):
        calls.append(seconds)
        if len(calls) >= 2:
            raise asyncio.CancelledError
        return None

    async def fake_stats():
        return {"total_chunks": 0, "total_documents": 0}

    fake_vector = SimpleNamespace(get_stats=fake_stats)
    monkeypatch.setattr("src.utils.vector_manager.vector_db", fake_vector)
    monkeypatch.setattr(
        main_mod,
        "metrics",
        SimpleNamespace(snapshot=lambda: {"counters": {"tool_calls_failed": 15}}),
    )
    monkeypatch.setattr(main_mod.asyncio, "sleep", fake_sleep)

    task = asyncio.create_task(main_mod._health_monitor())
    with pytest.raises(asyncio.CancelledError):
        await task
    await real_sleep(0)
    assert calls == [300, 300]


async def test_health_monitor_swallows_check_errors(monkeypatch):
    real_sleep = asyncio.sleep
    calls = []

    async def fake_sleep(seconds):
        calls.append(seconds)
        if len(calls) >= 2:
            raise asyncio.CancelledError
        return None

    async def fake_stats():
        raise RuntimeError("vector caido")

    fake_vector = SimpleNamespace(get_stats=fake_stats)
    monkeypatch.setattr("src.utils.vector_manager.vector_db", fake_vector)
    monkeypatch.setattr(main_mod, "metrics", SimpleNamespace(snapshot=lambda: {}))
    monkeypatch.setattr(main_mod.asyncio, "sleep", fake_sleep)

    task = asyncio.create_task(main_mod._health_monitor())
    with pytest.raises(asyncio.CancelledError):
        await task
    await real_sleep(0)


async def test_catch_up_scan_processes_chats(monkeypatch):
    scanned = []

    async def fake_get_all_chat_ids():
        return [1, 2]

    async def fake_scan(chat_id, limit=30):
        scanned.append((chat_id, limit))
        return {"messages_scanned": 3 if chat_id == 1 else 0}

    monkeypatch.setattr("src.database.db", SimpleNamespace(get_all_chat_ids=fake_get_all_chat_ids))
    monkeypatch.setattr("src.utils.message_scanner.scan_messages", fake_scan)
    await main_mod._catch_up_scan()
    assert scanned == [(1, 30), (2, 30)]


async def test_catch_up_scan_swallows_errors(monkeypatch):
    async def fake_get_all_chat_ids():
        raise RuntimeError("bd caida")

    monkeypatch.setattr("src.database.db", SimpleNamespace(get_all_chat_ids=fake_get_all_chat_ids))
    await main_mod._catch_up_scan()


# ---------------------------------------------------------------------------
# _ensure_embedding_model (sin red real)
# ---------------------------------------------------------------------------


def _fake_httpx(models=None, fail=False):
    pulls = []

    class _Resp:
        def __init__(self, payload=None):
            self._payload = payload or {}

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url):
            if fail:
                raise RuntimeError("ollama caido")
            return _Resp({"models": [{"name": m} for m in (models or [])]})

        async def post(self, url, json=None, timeout=None):
            pulls.append(json)
            return _Resp({})

    return _Client, pulls


async def test_ensure_embedding_model_pulls_only_missing(monkeypatch):
    monkeypatch.setattr(main_mod.settings, "embedding_model", "embed-x")
    monkeypatch.setattr(main_mod.settings, "ollama_model", "chat-x")
    monkeypatch.setattr(main_mod.settings, "ollama_vision_model", "vision-x")
    client_cls, pulls = _fake_httpx(models=["embed-x"])
    monkeypatch.setattr("httpx.AsyncClient", client_cls)

    app = Application.__new__(Application)
    await app._ensure_embedding_model()

    assert [p["name"] for p in pulls] == ["chat-x", "vision-x"]


async def test_ensure_embedding_model_skips_when_all_present(monkeypatch):
    monkeypatch.setattr(main_mod.settings, "embedding_model", "embed-x")
    monkeypatch.setattr(main_mod.settings, "ollama_model", "chat-x")
    monkeypatch.setattr(main_mod.settings, "ollama_vision_model", "vision-x")
    client_cls, pulls = _fake_httpx(models=["embed-x", "chat-x", "vision-x"])
    monkeypatch.setattr("httpx.AsyncClient", client_cls)

    app = Application.__new__(Application)
    await app._ensure_embedding_model()
    assert pulls == []


async def test_ensure_embedding_model_swallows_errors(monkeypatch):
    client_cls, _pulls = _fake_httpx(fail=True)
    monkeypatch.setattr("httpx.AsyncClient", client_cls)

    app = Application.__new__(Application)
    await app._ensure_embedding_model()
