"""Cobertura de bot.py: RateLimiter, wrap de handlers, polling y mensajes proactivos.

Sin red: httpx y telegram.ext se sustituyen por falsos; el polling se congela.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from telegram import Bot

from src.bot import RafitaBot, RateLimiter


class FakeResp:
    def __init__(self, payload, status=200, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or str(payload)

    def json(self):
        return self._payload


class FakeAsyncClient:
    """httpx.AsyncClient falso: rutas por subcadena de URL."""

    def __init__(self, routes=None, **_kwargs):
        self._routes = routes or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url, params=None, **_kwargs):
        for key, resp in self._routes.items():
            if key in url:
                if isinstance(resp, list):
                    return resp.pop(0)
                return resp
        raise httpx.ConnectError("unreachable")


def make_update(text="hola", user_id=42, with_message=True, reply=None):
    message = SimpleNamespace(reply_text=reply or AsyncMock())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id) if with_message else None,
        effective_message=message,
    )
    return update


def make_bot():
    return RafitaBot()


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


def test_rate_limiter_allows_then_blocks(monkeypatch):
    limiter = RateLimiter(max_per_minute=2)
    clock = {"now": 1000.0}
    monkeypatch.setattr("src.bot.time", SimpleNamespace(time=lambda: clock["now"]))

    assert limiter.is_allowed(1) is True
    assert limiter.is_allowed(1) is True
    assert limiter.is_allowed(1) is False
    assert limiter.is_allowed(2) is True

    clock["now"] += 61
    assert limiter.is_allowed(1) is True


# ---------------------------------------------------------------------------
# _wrap / replies
# ---------------------------------------------------------------------------


async def test_wrap_delegates_to_handler():
    bot = make_bot()
    seen = {}

    async def handler(update, context):
        seen["user"] = update.effective_user.id
        return "resultado"

    wrapped = bot._wrap(handler)
    update = make_update()
    assert await wrapped(update, object()) == "resultado"
    assert seen["user"] == 42


async def test_wrap_without_user_is_noop():
    bot = make_bot()
    called = {"n": 0}

    async def handler(update, context):
        called["n"] += 1

    wrapped = bot._wrap(handler)
    await wrapped(SimpleNamespace(effective_user=None, effective_message=None), object())
    assert called["n"] == 0


async def test_wrap_rate_limited_replies(monkeypatch):
    bot = make_bot()

    async def handler(update, context):
        raise AssertionError("no debe ejecutarse")

    monkeypatch.setattr(bot._rate_limiter, "is_allowed", lambda _uid: False)
    wrapped = bot._wrap(handler)
    update = make_update()
    await wrapped(update, object())
    (text,), _kwargs = update.effective_message.reply_text.call_args
    assert "Demasiadas solicitudes" in text


async def test_wrap_swallows_handler_errors():
    bot = make_bot()

    async def handler(update, context):
        raise ValueError("boom")

    wrapped = bot._wrap(handler)
    update = make_update()
    assert await wrapped(update, object()) is None
    (text,), _kwargs = update.effective_message.reply_text.call_args
    assert "error interno" in text


async def test_reply_helpers_and_error_handler():
    bot = make_bot()
    update = make_update()

    await RafitaBot._reply(update, "texto")
    update.effective_message.reply_text.assert_awaited_with("texto", disable_web_page_preview=True)

    await RafitaBot._reply_markdown(update, "**md**")
    update.effective_message.reply_text.assert_awaited_with(
        "**md**", parse_mode="Markdown", disable_web_page_preview=True
    )

    await RafitaBot._reply_html(update, "<b>html</b>")
    update.effective_message.reply_text.assert_awaited_with(
        "<b>html</b>", parse_mode="HTML", disable_web_page_preview=True
    )

    empty = SimpleNamespace(effective_message=None)
    await RafitaBot._reply(empty, "x")
    await RafitaBot._reply_markdown(empty, "x")
    await RafitaBot._reply_html(empty, "x")

    await bot._error_handler(update, SimpleNamespace(error=ValueError("x")))


# ---------------------------------------------------------------------------
# polling_status / send_proactive_message
# ---------------------------------------------------------------------------


def test_polling_status_branches():
    bot = make_bot()
    assert bot.polling_status() == {"status": "error", "detail": "bot not initialized"}

    bot._app = MagicMock()
    assert bot.polling_status() == {"status": "error", "detail": "polling not started"}

    bot._app_started.set()
    assert bot.polling_status() == {"status": "error", "detail": "polling task not running"}

    bot._polling_task = MagicMock()
    bot._polling_task.done.return_value = True
    assert bot.polling_status() == {"status": "error", "detail": "polling task not running"}

    bot._polling_task.done.return_value = False
    assert bot.polling_status() == {"status": "ok"}


async def test_send_proactive_message_paths():
    bot = make_bot()
    assert await bot.send_proactive_message(1, "hola") is False

    bot._app = MagicMock()
    bot._app.bot.send_message = AsyncMock()
    bot._app_started.set()
    assert await bot.send_proactive_message(7, "*alerta*") is True
    bot._app.bot.send_message.assert_awaited_with(chat_id=7, text="*alerta*", parse_mode="Markdown")

    bot._app.bot.send_message = AsyncMock(side_effect=RuntimeError("flood"))
    assert await bot.send_proactive_message(7, "otra") is False


# ---------------------------------------------------------------------------
# initialize / register
# ---------------------------------------------------------------------------


def _fake_app():
    app = MagicMock()
    app.initialize = AsyncMock()
    app.start = AsyncMock()
    app.stop = AsyncMock()
    app.shutdown = AsyncMock()
    app.process_update = AsyncMock()
    app.bot = MagicMock(spec=Bot)
    app.bot.set_my_commands = AsyncMock()
    app.bot.delete_webhook = AsyncMock()
    app.bot.send_message = AsyncMock()
    return app


async def test_initialize_registers_commands_and_handlers(monkeypatch):
    bot = make_bot()
    app = _fake_app()
    builder = MagicMock()
    builder.build.return_value = app
    monkeypatch.setattr("src.bot.ApplicationBuilder", MagicMock(return_value=builder))
    logged_in = AsyncMock()
    monkeypatch.setattr(bot, "_ensure_logged_in", logged_in)

    await bot.initialize()

    logged_in.assert_awaited_once()
    app.initialize.assert_awaited_once()
    app.bot.set_my_commands.assert_awaited_once()
    assert app.add_handler.call_count >= 30
    app.add_error_handler.assert_called_once()
    assert bot._app is app


async def test_initialize_survives_command_registration_failure(monkeypatch):
    bot = make_bot()
    app = _fake_app()
    app.bot.set_my_commands = AsyncMock(side_effect=RuntimeError("api down"))
    builder = MagicMock()
    builder.build.return_value = app
    monkeypatch.setattr("src.bot.ApplicationBuilder", MagicMock(return_value=builder))
    monkeypatch.setattr(bot, "_ensure_logged_in", AsyncMock())

    await bot.initialize()
    app.add_handler.assert_called()


async def test_ensure_logged_in_paths(monkeypatch):
    bot = make_bot()

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: FakeAsyncClient({"getMe": FakeResp({"ok": True})})
    )
    await bot._ensure_logged_in()

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: FakeAsyncClient(
            {
                "getMe": FakeResp({"ok": False}, status=401),
                "getUpdates": FakeResp({"ok": True}),
            }
        ),
    )
    await bot._ensure_logged_in()

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: FakeAsyncClient(
            {
                "getMe": FakeResp({"ok": False}, status=401),
                "getUpdates": FakeResp({"ok": False}, status=401, text="unauthorized"),
            }
        ),
    )
    await bot._ensure_logged_in()


# ---------------------------------------------------------------------------
# start / poll loop / stop / destroy
# ---------------------------------------------------------------------------


async def test_start_requires_initialize():
    with pytest.raises(RuntimeError, match="not initialized"):
        await make_bot().start()


async def test_start_stop_and_destroy(monkeypatch):
    bot = make_bot()
    app = _fake_app()
    app.bot.delete_webhook = AsyncMock(side_effect=RuntimeError("already deleted"))
    bot._app = app

    async def frozen_poll():
        await asyncio.Event().wait()

    monkeypatch.setattr(bot, "_raw_poll_loop", frozen_poll)
    await bot.start()

    assert bot._app_started.is_set()
    assert bot.polling_status() == {"status": "ok"}
    app.bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=True)

    await bot.stop()
    assert bot._polling_task.cancelled() or bot._polling_task.done()

    app.stop = AsyncMock(side_effect=RuntimeError("stop failed"))
    app.shutdown = AsyncMock(side_effect=RuntimeError("shutdown failed"))
    await bot.stop()
    await bot.destroy()


async def test_stop_and_destroy_without_app():
    bot = make_bot()
    await bot.stop()
    await bot.destroy()


async def test_raw_poll_loop_processes_updates(monkeypatch):
    bot = make_bot()
    bot._app = _fake_app()
    update_payload = {
        "update_id": 7,
        "message": {
            "message_id": 1,
            "date": 0,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "is_bot": False, "first_name": "x"},
            "text": "hola",
        },
    }
    calls = {"n": 0}

    async def fake_get(self, url, params=None, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResp({"ok": True, "result": [update_payload]})
        if calls["n"] == 2:
            raise RuntimeError("red caida")
        raise asyncio.CancelledError

    monkeypatch.setattr(FakeAsyncClient, "get", fake_get)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: FakeAsyncClient())
    processed = []

    async def record(upd):
        processed.append(upd)

    monkeypatch.setattr(bot, "_process_raw_update", record)
    slept = []

    async def instant_sleep(delay):
        slept.append(delay)

    monkeypatch.setattr(asyncio, "sleep", instant_sleep)

    await bot._raw_poll_loop()

    assert processed == [update_payload]
    assert slept == [5]


async def test_raw_poll_loop_ignores_empty_results(monkeypatch):
    bot = make_bot()
    bot._app = _fake_app()
    calls = {"n": 0}

    async def fake_get(self, url, params=None, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResp({"ok": False})
        raise asyncio.CancelledError

    monkeypatch.setattr(FakeAsyncClient, "get", fake_get)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: FakeAsyncClient())

    async def instant_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", instant_sleep)
    processed = []

    async def record(upd):
        processed.append(upd)

    monkeypatch.setattr(bot, "_process_raw_update", record)

    await bot._raw_poll_loop()
    assert processed == []


async def test_process_raw_update_variants(monkeypatch):
    bot = make_bot()
    bot._app = _fake_app()

    await bot._process_raw_update({"update_id": 1})
    bot._app.process_update.assert_not_awaited()

    built = object()
    monkeypatch.setattr("telegram.Update.de_json", staticmethod(lambda data, _bot: built))
    payload = {
        "update_id": 2,
        "edited_message": {
            "message_id": 9,
            "chat": {"id": 5, "type": "private"},
            "from": {"id": 5, "is_bot": False, "username": "u"},
            "photo": [{"file_id": "p"}],
        },
    }
    await bot._process_raw_update(payload)
    bot._app.process_update.assert_awaited_once_with(built)

    voice_payload = {
        "update_id": 3,
        "message": {
            "message_id": 10,
            "chat": {"id": 6, "type": "private"},
            "from": {"id": 6, "is_bot": False},
            "voice": {"file_id": "v"},
        },
    }
    await bot._process_raw_update(voice_payload)
    assert bot._app.process_update.await_count == 2
