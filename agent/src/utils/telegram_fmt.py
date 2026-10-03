"""Envíos a Telegram a prueba de errores de parseo (modo demo: nada se cae).

El Markdown legado de Telegram revienta con caracteres sueltos (`_`, `*`,
`` ` ``, `[`) en texto dinámico (nombres de comandos, contactos, correos,
salidas de herramientas): error «Can't parse entities». Estas funciones
escapan el texto dinámico y, si aun así falla el parseo, reenvían el mensaje
en texto plano. En una demo ningún comando puede caerse por formato.
"""

import re

from telegram.error import BadRequest

from src.logger import logger

_MD_CHARS_RE = re.compile(r"([*_`\[\]])")


def escape_md(text: str) -> str:
    """Escapa los caracteres especiales del Markdown legado de Telegram."""
    return _MD_CHARS_RE.sub(r"\\\1", text) if text else text


def strip_md(text: str) -> str:
    """Convierte el Markdown en texto plano legible para el respaldo.

    Los pares (`*negrita*`, `_cursiva_`, `` `código` ``) se deshacen; los
    caracteres sueltos se conservan (p. ej. `modo_voz` no debe perder su '_').
    """
    out = text or ""
    out = re.sub(r"\*([^*\n]+)\*", r"\1", out)
    out = re.sub(r"_([^_\n]+)_", r"\1", out)
    out = re.sub(r"`([^`\n]+)`", r"\1", out)
    return out.replace("[", "").replace("]", "")


async def reply_md(message, text: str, **kwargs) -> None:
    """`reply_text` con Markdown y respaldo en texto plano si no se parsea."""
    if message is None:
        return
    try:
        await message.reply_text(text, parse_mode="Markdown", **kwargs)
    except BadRequest as e:
        if "parse" not in str(e).lower():
            raise
        logger.warning("Markdown no parseable, reenvío en texto plano: %s", e)
        await message.reply_text(strip_md(text), **kwargs)


async def send_md(bot, chat_id: int, text: str, **kwargs) -> None:
    """`send_message` con Markdown y respaldo en texto plano si no se parsea."""
    if bot is None:
        return
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", **kwargs)
    except BadRequest as e:
        if "parse" not in str(e).lower():
            raise
        logger.warning("Markdown no parseable, reenvío en texto plano: %s", e)
        await bot.send_message(chat_id=chat_id, text=strip_md(text), **kwargs)
