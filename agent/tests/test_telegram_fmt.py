"""Tests del envío seguro de Markdown (demo: ningún comando se cae por formato)."""

import pytest
from telegram.error import BadRequest

from src.utils.telegram_fmt import (
    escape_md,
    reply_md,
    reply_rich,
    send_md,
    send_rich,
    split_html,
    strip_md,
    strip_tags,
)


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


# ---------- send_rich / split_html (auditoría 2026-10: envío HTML robusto) ----------


class _HtmlFailMsg:
    """Mensaje que rechaza SIEMPRE el parseo HTML pero acepta texto plano."""

    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        if kwargs.get("parse_mode") == "HTML":
            raise BadRequest("Can't parse entities: Can't find end of the starting tag")
        self.replies.append((text, kwargs))


class _HtmlFailBot:
    """Bot que rechaza SIEMPRE el parseo HTML pero acepta texto plano."""

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id=None, text=None, **kwargs):
        if kwargs.get("parse_mode") == "HTML":
            raise BadRequest("Can't parse entities: Can't find end of the starting tag")
        self.sent.append((chat_id, text, kwargs))


def test_split_html_parte_en_bloques_sin_perder_nada():
    texto = "\n".join("linea %d" % i for i in range(2000))  # ~14k chars
    chunks = split_html(texto, max_len=4000)
    assert all(len(c) <= 4000 for c in chunks)
    assert "\n".join(chunks) == texto


def test_split_html_linea_gigante_se_trocea():
    chunks = split_html("x" * 9000, max_len=4000)
    assert [len(c) for c in chunks] == [4000, 4000, 1000]
    assert "".join(chunks) == "x" * 9000


def test_split_html_texto_corto_y_vacio():
    assert split_html("hola") == ["hola"]
    assert split_html("") == []


def test_strip_tags_quita_html():
    assert strip_tags("<b>hola</b> mundo<pre>x</pre>") == "hola mundox"
    assert strip_tags("sin etiquetas") == "sin etiquetas"


async def test_reply_rich_envia_html_y_respeta_kwargs():
    msg = _Msg()
    await reply_rich(msg, "<b>hola</b>", disable_web_page_preview=True)
    assert msg.replies[0][0] == "<b>hola</b>"
    assert msg.replies[0][1]["parse_mode"] == "HTML"
    assert msg.replies[0][1]["disable_web_page_preview"] is True


async def test_reply_rich_fallback_a_plano_por_bloque():
    msg = _HtmlFailMsg()
    await reply_rich(msg, "<b>hola</b>")
    assert len(msg.replies) == 1
    text, kwargs = msg.replies[0]
    assert "hola" in text
    assert "parse_mode" not in kwargs  # reenviado sin parseo, no perdido


async def test_reply_rich_parte_y_fallback_bloque_a_bloque():
    msg = _HtmlFailMsg()
    texto = "<b>%s</b>" % ("\n".join("linea %d" % i for i in range(1200)))
    await reply_rich(msg, texto)
    assert len(msg.replies) >= 2
    assert all("parse_mode" not in kw for _, kw in msg.replies)
    # Nada perdido: los bloques reensamblados (con los \n de frontera)
    # reproducen el original sin etiquetas.
    assert strip_tags(texto) == strip_tags("\n".join(t for t, _ in msg.replies))


async def test_reply_rich_con_mensaje_none_no_falla():
    await reply_rich(None, "<b>x</b>")


async def test_send_rich_envia_a_chat_correcto():
    bot = _Bot()
    await send_rich(bot, 42, "<b>hola</b>")
    assert bot.sent[0][0] == 42
    assert bot.sent[0][2]["parse_mode"] == "HTML"


async def test_send_rich_fallback_a_plano():
    bot = _HtmlFailBot()
    await send_rich(bot, 42, "<b>hola</b>")
    assert len(bot.sent) == 1
    chat_id, text, kwargs = bot.sent[0]
    assert chat_id == 42
    assert "hola" in text
    assert "parse_mode" not in kwargs


async def test_send_rich_con_bot_none_no_falla():
    await send_rich(None, 42, "x")
