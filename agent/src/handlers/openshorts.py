"""Comando /openshorts <url>: crea clips automaticos de un video.

Pasa la URL a la automatizacion de n8n ("Rafita · 11 OpenShorts · Clips"),
que llama a OpenShorts, espera los clips y los manda a Telegram uno a uno
para que se aprueben (✅ subir a redes / ❌ descartar).
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
from telegram import Update
from telegram.ext import ContextTypes

from src.config import settings
from src.logger import logger


def _webhook_url() -> str:
    url = (settings.openshorts_n8n_webhook or "").strip()
    if url:
        return url
    base = (settings.n8n_base_url or "http://n8n:5678").rstrip("/")
    return f"{base}/webhook/openshorts"


def _url_valida(texto: str) -> bool:
    try:
        p = urlparse(texto.strip())
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


async def openshorts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    args = context.args or []
    if not args or not _url_valida(args[0]):
        await message.reply_text(
            "Usa: /openshorts <url>\n"
            "Ejemplo: `/openshorts https://www.youtube.com/watch?v=...`\n\n"
            "Generaré los clips del vídeo y te los iré mandando a Telegram "
            "para que apruebes cuáles subir a redes."
        )
        return

    url = args[0].strip()
    chat_id = update.effective_chat.id if update.effective_chat else None
    payload = {"url": url, "chat_id": chat_id}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                _webhook_url(),
                json=payload,
                headers={"X-OpenShorts-Secret": settings.openshorts_webhook_secret},
            )
            resp.raise_for_status()
    except Exception as e:
        logger.error("OpenShorts: no se pudo encolar el trabajo: %s", e)
        await message.reply_text(
            "⚠️ No pude encolar el trabajo de clips (automatización no disponible)."
        )
        return

    await message.reply_text(
        "🎬 Trabajo de clips creado.\n"
        "Procesaré el vídeo y te iré mandando los clips a Telegram con botones "
        "para que decidas cuáles subir a redes. Puede tardar varios minutos."
    )
    logger.info("OpenShorts: trabajo encolado para %s", url[:120])
