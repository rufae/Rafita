"""Escaneo batch de mensajes para extraer datos estructurados al segundo cerebro.

Permite procesar mensajes acumulados (offline) y extraer:
- Enlaces → 03-Recursos
- Fechas/eventos → 02-Areas/Agenda
- Datos financieros → 02-Areas/Finanzas
- Ideas → 05-Zettelkasten
"""

from datetime import datetime, timedelta
from typing import Any

from src.database import db
from src.logger import logger


async def _extract_batch_with_llm(
    chat_id: int,
    messages: list[str],
) -> list[dict[str, Any]]:
    """Envía un lote de mensajes al LLM para extraer datos estructurados."""
    from src.ollama_client import llm

    if not messages:
        return []

    batch_text = "\n".join("[%d] %s" % (i, msg[:500]) for i, msg in enumerate(messages))

    prompt = (
        "Eres un extractor de datos para un segundo cerebro personal (PARA + Zettelkasten). "
        "Analiza los siguientes mensajes de Telegram del usuario y extrae SOLO "
        "informacion estructurada que merezca ser guardada. Ignora saludos, charla casual, "
        "y mensajes sin valor informativo.\n\n"
        "MENSAJES A ANALIZAR:\n%s\n\n"
        "EXTRAE y clasifica en estas categorias:\n"
        "1. ENLACES: URLs utiles, articulos, herramientas → guardar en 03-Recursos\n"
        "2. IDEAS: conceptos, aprendizajes, insights → guardar en 05-Zettelkasten\n"
        "3. EVENTOS: fechas, recordatorios, citas → guardar en 02-Areas/Agenda\n"
        "4. GASTOS: menciones de dinero, compras, pagos → guardar en 02-Areas/Finanzas\n"
        "5. DATOS: informacion personal nueva (preferencias, datos de salud, contactos)\n\n"
        "Para cada item extraido, usa las herramientas disponibles para guardarlo "
        "en la nota y carpeta correspondiente de Obsidian. "
        "Usa manage_obsidian_note con action='create' para notas nuevas, "
        "o search_second_brain para verificar si ya existe algo similar.\n\n"
        "Se CONCISO. Solo guarda lo que realmente aporte valor al segundo cerebro. "
        "No guardes duplicados. Si un mensaje no tiene nada valioso, ignoralo.\n\n"
        "Responde en español confirmando brevemente que extrajiste y donde guardaste cada cosa."
    ) % batch_text[:6000]

    try:
        messages_for_llm = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": "Extrae y guarda la informacion relevante de estos mensajes en el segundo cerebro.",
            },
        ]
        content = await llm.chat(
            messages=messages_for_llm,
            temperature=0.3,
            max_tokens=1024,
        )
        return [{"type": "llm_response", "content": content}]
    except Exception as e:
        logger.warning("Batch extraction failed: %s", e)
        return []


async def scan_messages(
    chat_id: int,
    since: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Escanea mensajes del historial y extrae datos al segundo cerebro.

    Args:
        chat_id: ID del chat a escanear
        since: Fecha desde la cual escanear (YYYY-MM-DD). Default: desde último scan.
        limit: Máximo de mensajes a procesar

    Returns:
        Dict con resultados del escaneo
    """
    if since:
        try:
            since_dt = datetime.strptime(since, "%Y-%m-%d")
        except ValueError:
            return {"success": False, "message": "Formato de fecha invalido. Usa YYYY-MM-DD."}
    else:
        last_scan = await db.kv_get("last_scan_timestamp")
        if last_scan:
            try:
                since_dt = datetime.fromisoformat(last_scan)
            except ValueError:
                since_dt = datetime.now() - timedelta(hours=24)
        else:
            since_dt = datetime.now() - timedelta(hours=24)

    since_str = since_dt.strftime("%Y-%m-%d %H:%M:%S")
    logger.info(
        "Scanning messages for chat %d since %s (limit %d)",
        chat_id,
        since_str,
        limit,
    )

    history = await db.get_chat_history(chat_id, limit=9999)
    recent = [msg for msg in history if msg["role"] == "user" and msg["created_at"] >= since_str]
    recent = recent[-limit:]

    if not recent:
        return {
            "success": True,
            "message": "No hay mensajes nuevos desde %s." % since_str[:10],
            "messages_scanned": 0,
            "extracted": [],
        }

    messages = [msg["content"] for msg in recent]

    extracted = await _extract_batch_with_llm(chat_id, messages)

    now = datetime.now().isoformat()
    await db.kv_set("last_scan_timestamp", now)

    return {
        "success": True,
        "message": "Escaneados %d mensajes desde %s." % (len(messages), since_str[:10]),
        "messages_scanned": len(messages),
        "extracted": extracted,
        "scan_until": now,
    }
