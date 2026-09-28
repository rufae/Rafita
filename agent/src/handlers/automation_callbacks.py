"""Callbacks de las automatizaciones (2026-09-28).

Botones con accion real en los mensajes que envia n8n con el token del bot:
- `inbox_send:<token>`: envia por Gmail el borrador guardado por inbox-scan.
- `brief_reagendar`: muestra la agenda y prepara el cambio de un evento.
"""

import json
import re

from telegram import Update
from telegram.ext import ContextTypes

from src.logger import logger

_EMAIL_RE = re.compile(r"<([^>]+@[^>]+)>")


def _extract_email(from_header: str) -> str:
    match = _EMAIL_RE.search(from_header or "")
    if match:
        return match.group(1).strip()
    candidate = (from_header or "").strip()
    return candidate if "@" in candidate else ""


async def inbox_send_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Envia el borrador de respuesta guardado por el Inbox Zero."""
    query = update.callback_query
    if not query or not query.message:
        return
    await query.answer()
    token = (query.data or "").split(":", 1)[-1]
    from src.database import db

    try:
        raw = await db.kv_get("inbox:draft:%s" % token)
    except Exception:
        raw = None
    if not raw:
        await query.edit_message_text("Ese borrador ya no está disponible.")
        return
    try:
        draft = json.loads(raw)
    except (ValueError, TypeError):
        await query.edit_message_text("El borrador está corrupto; genera otro con el Inbox.")
        return

    to = _extract_email(draft.get("from", ""))
    if not to:
        await query.edit_message_text("No pude sacar la dirección del remitente original.")
        return
    subject = draft.get("subject", "")
    if not subject.lower().startswith("re:"):
        subject = "Re: " + subject

    from src.services.google_services_manager import google_services

    result = await google_services.send_email(
        to=to, subject=subject, body=draft.get("borrador", "")
    )
    if result.get("success"):
        await query.edit_message_text("✅ Respuesta enviada a %s (%s)" % (to, subject))
        logger.info("Inbox: respuesta enviada a %s desde el boton", to)
    else:
        await query.edit_message_text("⚠️ No se pudo enviar: %s" % result.get("message", "error"))


async def brief_reagendar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra la agenda y pide qué evento mover (accion real: move)."""
    query = update.callback_query
    if not query or not query.message:
        return
    message = query.message
    chat_id = getattr(message, "chat_id", None)
    reply_text = getattr(message, "reply_text", None)
    if chat_id is None or reply_text is None:
        return
    await query.answer()
    from src.handlers.chat import _execute_tool

    result = await _execute_tool(chat_id, "manage_google_calendar", {"action": "list"})
    agenda = result.get("message", "No hay eventos próximos.")
    await reply_text(
        "%s\n\nDime cuál muevo y cuándo, por ejemplo: «mueve dentista al viernes a las 10»."
        % agenda
    )
