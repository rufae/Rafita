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


# Margen bajo el limite duro de Telegram (4096): deja sitio a entidades que
# crecen al escapar y a cabeceras que el llamante anada despues.
MAX_CHUNK = 4000


def split_html(text: str, max_len: int = MAX_CHUNK) -> list[str]:
    """Parte un texto (HTML o plano) en bloques <= max_len sin perder nada.

    Corta por lineas (respeta tablas `<pre>` en lo posible) y, si una linea
    individual excede max_len, la trocea de forma dura. A diferencia de un
    `text[:4096]`, aqui no se descarta contenido.
    """
    text = text or ""
    if len(text) <= max_len:
        return [text] if text else []
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > max_len:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:max_len])
            line = line[max_len:]
        if current and len(current) + len(line) + 1 > max_len:
            chunks.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        chunks.append(current)
    return chunks


def strip_tags(text: str) -> str:
    """Quita etiquetas HTML para el respaldo en texto plano."""
    return re.sub(r"<[^>]+>", "", text or "")


async def _send_html_or_plain(send, chunk: str, **kwargs) -> None:
    """Envia un bloque como HTML y, si Telegram no lo parsea, en plano.

    Nunca relanza errores de parseo (un mensaje nunca se pierde por
    formato); otros BadRequest (chat_id invalido, etc.) si suben.
    """
    try:
        await send(chunk, parse_mode="HTML", **kwargs)
    except BadRequest as e:
        if "parse" not in str(e).lower():
            raise
        logger.warning("HTML no parseable, bloque reenviado en texto plano: %s", e)
        await send(strip_tags(chunk), **kwargs)


async def reply_rich(message, text: str, **kwargs) -> None:
    """`reply_text` HTML robusto: parte en bloques y respalda a texto plano.

    Sustituye a los bucles de troceado repartidos por handlers (chat,
    dashboard): un solo punto con el fallback de parseo que antes faltaba.
    """
    if message is None:
        return
    for chunk in split_html(text):
        await _send_html_or_plain(lambda t, **kw: message.reply_text(t, **kw), chunk, **kwargs)


async def send_rich(bot, chat_id: int, text: str, **kwargs) -> None:
    """`send_message` HTML robusto: parte en bloques y respalda a texto plano."""
    if bot is None:
        return
    for chunk in split_html(text):
        await _send_html_or_plain(
            lambda t, **kw: bot.send_message(chat_id=chat_id, text=t, **kw), chunk, **kwargs
        )
