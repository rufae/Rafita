"""Cobertura de handlers/settings.py (comando /modo_voz)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Bot, Chat, Message, Update, User

from src.handlers import settings as settings_mod


class FakeDB:
    """Reemplaza src.database.db (la BD no esta inicializada en tests)."""

    def __init__(self, current=False):
        self.current = current
        self.get_calls = []
        self.set_calls = []

    async def get_preference(self, chat_id, key, default=None):
        self.get_calls.append((chat_id, key, default))
        return self.current

    async def set_preference(self, chat_id, key, value):
        self.set_calls.append((chat_id, key, value))


def make_update(text, user_id=12345):
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()
    bot.send_chat_action = AsyncMock()
    user = User(id=user_id, first_name="Test", is_bot=False, username="testuser")
    chat = Chat(id=user_id, type="private")
    message = Message(message_id=1, date=None, chat=chat, from_user=user, text=text)
    message._bot = bot
    return Update(update_id=1, message=message), bot


def make_context(args=None):
    context = MagicMock()
    context.args = args or []
    return context


def install_db(monkeypatch, current=False):
    fake_db = FakeDB(current=current)
    monkeypatch.setattr(settings_mod, "db", fake_db)
    return fake_db


async def test_modo_voz_shows_current_state_off(monkeypatch):
    update, bot = make_update("/modo_voz")
    fake_db = install_db(monkeypatch, current=False)

    await settings_mod.modo_voz_command(update, make_context())

    assert fake_db.get_calls == [(12345, "voice_replies", False)]
    text = bot.send_message.call_args.kwargs["text"]
    assert "desactivado" in text
    assert "/modo_voz on" in text


async def test_modo_voz_shows_current_state_on(monkeypatch):
    update, bot = make_update("/modo_voz")
    install_db(monkeypatch, current=True)

    await settings_mod.modo_voz_command(update, make_context())

    text = bot.send_message.call_args.kwargs["text"]
    assert "activado" in text


async def test_modo_voz_on_enables_replies(monkeypatch):
    update, bot = make_update("/modo_voz on")
    fake_db = install_db(monkeypatch)

    await settings_mod.modo_voz_command(update, make_context(["on"]))

    assert fake_db.set_calls == [(12345, "voice_replies", True)]
    text = bot.send_message.call_args.kwargs["text"]
    assert "activado" in text


async def test_modo_voz_off_disables_replies(monkeypatch):
    update, bot = make_update("/modo_voz off")
    fake_db = install_db(monkeypatch, current=True)

    await settings_mod.modo_voz_command(update, make_context(["off"]))

    assert fake_db.set_calls == [(12345, "voice_replies", False)]
    text = bot.send_message.call_args.kwargs["text"]
    assert "desactivado" in text


@pytest.mark.parametrize("arg", ["1", "true", "si"])
async def test_modo_voz_accepts_truthy_aliases(monkeypatch, arg):
    update, _bot = make_update("/modo_voz " + arg)
    fake_db = install_db(monkeypatch)

    await settings_mod.modo_voz_command(update, make_context([arg]))

    assert fake_db.set_calls == [(12345, "voice_replies", True)]


@pytest.mark.parametrize("arg", ["0", "false", "no"])
async def test_modo_voz_accepts_falsy_aliases(monkeypatch, arg):
    update, _bot = make_update("/modo_voz " + arg)
    fake_db = install_db(monkeypatch, current=True)

    await settings_mod.modo_voz_command(update, make_context([arg]))

    assert fake_db.set_calls == [(12345, "voice_replies", False)]


async def test_modo_voz_unknown_arg_shows_usage(monkeypatch):
    update, bot = make_update("/modo_voz quizas")
    fake_db = install_db(monkeypatch)

    await settings_mod.modo_voz_command(update, make_context(["quizas"]))

    assert fake_db.set_calls == []
    text = bot.send_message.call_args.kwargs["text"]
    assert "/modo_voz on" in text


async def test_modo_voz_without_message_is_noop(monkeypatch):
    update = MagicMock()
    update.effective_message = None
    update.effective_user = User(id=1, first_name="T", is_bot=False)
    fake_db = install_db(monkeypatch)

    await settings_mod.modo_voz_command(update, make_context())

    assert fake_db.get_calls == []
    assert fake_db.set_calls == []


async def test_modo_voz_without_user_is_noop(monkeypatch):
    update = MagicMock()
    update.effective_message = MagicMock()
    update.effective_user = None
    fake_db = install_db(monkeypatch)

    await settings_mod.modo_voz_command(update, make_context())

    assert fake_db.get_calls == []
    assert fake_db.set_calls == []
