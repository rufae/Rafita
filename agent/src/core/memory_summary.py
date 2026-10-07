"""Resumen rodante por chat (V3, 2026-10-07).

Antes de cada llamada se podaba el historial por antigüedad
(`db.delete_stale_chat_history`) y lo antiguo se perdía para siempre. Aquí se
resumen esos turnos con el propio LLM y se guarda en `chat_summaries`; el
orquestador inyecta ese resumen como mensaje de sistema, así la llamada
recupera contexto ("me hablaste de X hace una hora") sin cargar 50 turnos.

Si el LLM no responde en `timeout_s`, se cae a la poda clásica: la llamada
funciona igual que antes, solo que sin memoria del pasado.
"""

import asyncio

from src.database import db
from src.logger import logger
from src.ollama_client import llm

_SUMMARY_PROMPT = (
    "Resumen conciso (maximo 150 palabras, en español, sin saludos) de esta "
    "conversación pasada, para que un asistente de voz retome el hilo:\n\n"
)

# Maximo de caracteres del resumen inyectado en el system prompt.
SUMMARY_MAX_CHARS = 1400

# Guard anti-duplicado: si ya hay un resumen en vuelo para este chat, no se
# lanza otro (los /call/start se solapan con reconexiones).
_inflight: set[int] = set()


async def resumener_y_poda(chat_id: int, hours: int = 2) -> bool:
    """Resume los mensajes de más de `hours` horas y los borra.

    Devuelve True si hubo resumen (los mensajes antiguos ahora están en
    `chat_summaries`), False si no había nada que resumir o falló (en cuyo
    caso NO se poda: mejor conservar el historial crudo que perderlo).
    """
    if chat_id in _inflight:
        return False
    _inflight.add(chat_id)
    try:
        stale = await db.get_stale_chat_messages(chat_id, hours=hours)
        if not stale:
            return False

        transcript = "\n".join("%s: %s" % (row["role"], str(row["content"])[:500]) for row in stale)
        try:
            nuevo = await asyncio.wait_for(
                llm.chat(
                    [{"role": "system", "content": _SUMMARY_PROMPT + transcript}],
                    temperature=0.2,
                    max_tokens=300,
                ),
                timeout=30.0,
            )
        except Exception as exc:  # noqa: BLE001 — cualquier fallo => sin poda
            logger.warning("memory_summary: LLM fallo, se conserva historial: %s", exc)
            return False

        texto = (nuevo or "").strip()
        if not texto:
            return False

        anterior = await db.get_chat_summary(chat_id)
        combinado = texto if not anterior else f"{anterior}\n{texto}"
        if len(combinado) > SUMMARY_MAX_CHARS * 2:
            combinado = combinado[-SUMMARY_MAX_CHARS * 2 :]
        await db.upsert_chat_summary(chat_id, combinado)
        eliminado_rows = await db.delete_stale_chat_history(chat_id, hours=hours)
        logger.info(
            "memory_summary: chat %s resumido (%s filas podadas, resumen %s chars)",
            chat_id,
            eliminado_rows,
            len(texto),
        )
        return True
    except Exception as exc:  # noqa: BLE001 — la llamada no puede fallar por esto
        logger.warning("memory_summary: fallo inesperado chat %s: %s", chat_id, exc)
        return False
    finally:
        _inflight.discard(chat_id)


async def get_summary_block(chat_id: int) -> str:
    """Resumen del chat listo para inyectar en el system prompt ("" si vacío)."""
    try:
        resumen = (await db.get_chat_summary(chat_id)).strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_summary: no se pudo leer resumen: %s", exc)
        return ""
    if not resumen:
        return ""
    if len(resumen) > SUMMARY_MAX_CHARS:
        resumen = resumen[:SUMMARY_MAX_CHARS]
    return (
        f"\n\nResumen de lo hablado antes de esta sesión (usa solo si el tema encaja):\n{resumen}"
    )
