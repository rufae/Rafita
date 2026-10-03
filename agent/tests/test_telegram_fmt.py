"""Tests del envío seguro de Markdown (demo: ningún comando se cae por formato)."""

import pytest
from telegram.error import BadRequest

from src.utils.telegram_fmt import escape_md, reply_md, send_md, strip_md


class _Msg:
    def __init__(self, fail_first=False):
        self.replies = []
        self.fail_first = fail_first

    async def reply_text(self, text, **kwargs):
        if self.fail_first and not self.replies:
            self.fail_first = False
            raise BadRequest("Can't parse entities: Can't find end of Italic entity")
        self.replies.append((text, kwargs))


class _Bot:
    def __init__(self, fail_first=False):
        self.sent = []
        self.fail_first = fail_first

    async def send_message(self, chat_id=None, text=None, **kwargs):
        if self.fail_first and not self.sent:
            self.fail_first = False
            raise BadRequest("Can't parse entities: Can't find end of Italic entity")
        self.sent.append((chat_id, text, kwargs))


def test_escape_md_escapa_caracteres_peligrosos():
    assert escape_md("modo_voz") == r"modo\_voz"
    assert escape_md("*negrita*") == r"\*negrita\*"
    assert escape_md("normal") == "normal"
    assert escape_md("") == ""


def test_strip_md_quita_marcadores():
    assert strip_md("*Hola* _mundo_") == "Hola mundo"
    assert strip_md("texto plano") == "texto plano"


async def test_reply_md_envia_con_markdown():
    msg = _Msg()
    await reply_md(msg, "*Hola*")
    assert msg.replies[0][0] == "*Hola*"
    assert msg.replies[0][1].get("parse_mode") == "Markdown"


async def test_reply_md_reenvia_en_plano_si_falla_el_parseo():
    # Reproduce el bug real: 'modo_voz' con '_' suelto tumbaba /ayuda entero.
    msg = _Msg(fail_first=True)
    await reply_md(msg, "/modo_voz - Activar respuestas")
    assert len(msg.replies) == 1
    texto, kwargs = msg.replies[0]
    assert "_voz" in texto
    assert "parse_mode" not in kwargs


async def test_reply_md_propaga_otros_errores():
    class _Msg404(_Msg):
        async def reply_text(self, text, **kwargs):
            raise BadRequest("Chat not found")

    with pytest.raises(BadRequest):
        await reply_md(_Msg404(), "hola")


async def test_reply_md_con_message_none_no_falla():
    await reply_md(None, "hola")


async def test_send_md_reenvia_en_plano_si_falla_el_parseo():
    bot = _Bot(fail_first=True)
    await send_md(bot, 123, "*roto _")
    assert bot.sent[0][0] == 123
    assert "parse_mode" not in bot.sent[0][2]


async def test_send_md_con_bot_none_no_falla():
    await send_md(None, 123, "hola")
