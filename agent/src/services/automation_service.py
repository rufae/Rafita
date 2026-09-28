"""Automatizaciones de nivel profesional (punto 1 de docs/automatizaciones.md).

Rafita aporta datos + IA (ya tiene OAuth de Google, vault y LLM local) y n8n
orquesta los disparos y la entrega (Telegram con botones). Los endpoints del
gateway son HMAC y devuelven JSON listo para n8n:

- `build_briefing()`      -> briefing ejecutivo (agenda, tareas, correo,
                             tiempo AEMET, estado del servidor) + botones.
- `scan_inbox()`          -> clasificacion de correo no leido + borradores.
- `capture_to_vault()`    -> nota .md con frontmatter/tags en la boveda.
"""

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.config import settings
from src.logger import logger

# ---------------------------------------------------------------- tiempo


async def _aemet_weather() -> str:
    """Prediccion de hoy con AEMET (gratis, requiere AEMET_API_KEY)."""
    key = (settings.aemet_api_key or "").strip()
    if not key:
        return ""
    code = (settings.briefing_municipio or "").strip()
    if not code:
        return ""
    try:
        import httpx

        url = (
            "https://opendata.aemet.es/opendata/api/prediccion/especifica/"
            "municipio/diaria/%s?api_key=%s" % (code, key)
        )
        async with httpx.AsyncClient(timeout=15.0) as client:
            # AEMET sirve ISO-8859-15 (latin-1): decodificar antes de parsear.
            first = json.loads((await client.get(url)).content.decode("latin-1"))
            datos_url = first.get("datos")
            if not datos_url:
                return ""
            payload = json.loads((await client.get(datos_url)).content.decode("latin-1"))
        dia = payload[0]["prediccion"]["dia"][0]
        # AEMET devuelve numeros (no strings) para las temperaturas.
        tmax = str(dia.get("temperatura", {}).get("maxima") or "").strip()
        tmin = str(dia.get("temperatura", {}).get("minima") or "").strip()
        precip = dia.get("probPrecipitacion") or []
        rain = max((int(p.get("value") or 0) for p in precip), default=0)
        parts = []
        if tmax or tmin:
            parts.append("🌡 %s-%s °C" % (tmin or "?", tmax or "?"))
        parts.append("%d%% de lluvia" % rain)
        cielo = dia.get("estadoCielo") or [{}]
        desc = next((c.get("descripcion") for c in cielo if c.get("descripcion")), "")
        if desc:
            parts.append(desc.lower())
        return ", ".join(parts)
    except Exception as e:
        logger.warning("Briefing: AEMET no disponible (%s); uso open-meteo", str(e)[:120])
        return ""


async def _weather() -> str:
    """Tiempo: AEMET si hay clave; si no, open-meteo (sin clave)."""
    aemet = await _aemet_weather()
    if aemet:
        return "AEMET: " + aemet
    from src.utils.proactive_briefing import _weather_summary

    return await _weather_summary()


# ---------------------------------------------------------------- datos


async def _server_status() -> dict[str, Any]:
    """Estado breve del servidor para el briefing."""
    status: dict[str, Any] = {}
    try:
        from src.ollama_client import llm

        health = await llm.check_health()
        status["ia"] = health.get("status", "?")
    except Exception:
        status["ia"] = "error"
    try:
        from src.utils.vector_manager import vector_db

        vector_health = await vector_db.health()
        status["rag"] = vector_health.get("status", "?")
        status["chunks"] = vector_health.get("chunks")
    except Exception:
        status["rag"] = "error"
    try:
        from src.services.google_services_manager import google_services

        status["google"] = "conectado" if google_services.is_ready else "sin conexion"
    except Exception:
        status["google"] = "?"
    return status


async def _agenda_tasks_mail() -> tuple[list[str], list[str], list[str]]:
    """(agenda proximas 24h, tareas pendientes, correo no leido 12h)."""
    agenda: list[str] = []
    tareas: list[str] = []
    correo: list[str] = []
    try:
        from src.services.google_services_manager import google_services

        if not await google_services.initialize() or not google_services.is_ready:
            return agenda, tareas, correo
        events = (await google_services.list_calendar_events(days=1, max_results=10)).get(
            "events", []
        )
        for ev in events:
            start = str(ev.get("start", ""))
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                when = dt.strftime("%H:%M")
            except Exception:
                when = start
            agenda.append("%s — %s" % (when, ev.get("title") or "sin titulo"))
        tasks = (await google_services.list_tasks()).get("tasks", [])
        tareas = [t.get("title", "") for t in tasks[:10]]
        mails = (
            await google_services.search_gmail(query="is:unread newer_than:12h", max_results=6)
        ).get("messages", [])
        correo = ["%s (de %s)" % (m.get("subject") or "?", m.get("from") or "?") for m in mails]
    except Exception as e:
        logger.warning("Briefing: datos Google no disponibles: %s", str(e)[:150])
    return agenda, tareas, correo


# ---------------------------------------------------------------- briefing


BRIEFING_PROMPT = (
    "Eres %s, el asistente personal del usuario. Redacta su BRIEFING MATUTINO "
    "en espanol, breve y jerarquizado por prioridad. Estructura: 1) una linea "
    "de resumen del dia; 2) Agenda (con horas); 3) Tareas pendientes; 4) Correo "
    "destacado; 5) Tiempo y estado del servidor si aportan algo. Usa negritas "
    "de Telegram (*texto*) para los titulos de seccion. Nada de Markdown de "
    "tablas. Maximo 15 lineas."
)


async def build_briefing() -> dict[str, Any]:
    """Briefing ejecutivo listo para n8n (texto + botones URL)."""
    agenda, tareas, correo = await _agenda_tasks_mail()
    weather = await _weather()
    server = await _server_status()

    raw = (
        "AGENDA (proximas 24h):\n%s\n\nTAREAS PENDIENTES:\n%s\n\n"
        "CORREO SIN LEER (12h):\n%s\n\nTIEMPO: %s\n\nESTADO DEL SERVIDOR: %s"
        % (
            "\n".join("- " + a for a in agenda) or "(nada)",
            "\n".join("- " + t for t in tareas) or "(ninguna)",
            "\n".join("- " + c for c in correo) or "(nada)",
            weather or "(sin datos)",
            json.dumps(server, ensure_ascii=False),
        )
    )
    text = ""
    try:
        from src.ollama_client import llm

        text = (
            await llm.chat(
                messages=[
                    {"role": "system", "content": BRIEFING_PROMPT % settings.assistant_name},
                    {"role": "user", "content": raw},
                ],
                max_tokens=400,
            )
        ).strip()
    except Exception as e:
        logger.warning("Briefing: LLM no disponible (%s); uso texto crudo", str(e)[:120])
    if not text:
        text = "☀️ *Briefing de hoy*\n\n" + raw

    return {
        "success": True,
        "text": text,
        "counts": {
            "agenda": len(agenda),
            "tareas": len(tareas),
            "correo": len(correo),
        },
        "buttons": [
            {"text": "✅ Ver tareas", "url": "https://tasks.google.com/"},
            {"text": "📅 Ver calendario", "url": "https://calendar.google.com/"},
        ],
    }


# ---------------------------------------------------------------- inbox


INBOX_PROMPT = (
    "Clasifica estos correos y responde SOLO con un JSON array, un objeto por "
    "correo, con las claves: id (el numero), categoria (una de: urgente, "
    "factura, cliente, informativo), resumen (max 12 palabras) y borrador "
    "(solo si categoria es urgente o cliente: respuesta breve y educada en "
    "espanol; si no, cadena vacia). No inventes datos."
)


async def scan_inbox(hours: int = 2, max_results: int = 8) -> dict[str, Any]:
    """Clasifica el correo no leido reciente y propone borradores."""
    try:
        from src.services.google_services_manager import google_services

        if not await google_services.initialize() or not google_services.is_ready:
            return {"success": False, "message": "Google no conectado"}
        mails = (
            await google_services.search_gmail(
                query="is:unread newer_than:%dh" % max(1, int(hours)),
                max_results=max(1, min(int(max_results), 15)),
            )
        ).get("messages", [])
    except Exception as e:
        return {"success": False, "message": "No pude leer el correo: %s" % str(e)[:150]}

    if not mails:
        return {"success": True, "scanned": 0, "items": []}

    raw = "\n".join(
        "%d) De: %s | Asunto: %s | Extracto: %s"
        % (i, m.get("from", "?"), m.get("subject", "?"), (m.get("snippet") or "")[:200])
        for i, m in enumerate(mails)
    )
    classified: list[dict[str, Any]] = []
    try:
        from src.ollama_client import llm

        out = await llm.chat(
            messages=[
                {"role": "system", "content": INBOX_PROMPT},
                {"role": "user", "content": raw},
            ],
            max_tokens=700,
        )
        start, end = out.find("["), out.rfind("]")
        parsed = json.loads(out[start : end + 1]) if start >= 0 and end > start else []
        for item in parsed:
            idx = int(item.get("id", -1))
            if 0 <= idx < len(mails):
                mail = mails[idx]
                classified.append(
                    {
                        "id": mail.get("id", ""),
                        "from": mail.get("from", ""),
                        "subject": mail.get("subject", ""),
                        "categoria": str(item.get("categoria", "informativo")).lower(),
                        "resumen": item.get("resumen", ""),
                        "borrador": item.get("borrador", ""),
                    }
                )
    except Exception as e:
        logger.warning("Inbox: clasificacion con LLM fallo (%s); devuelvo crudo", str(e)[:120])
    if not classified:
        # Sin clasificacion valida (o JSON vacio): devolver los correos crudos.
        classified = [
            {
                "id": m.get("id", ""),
                "from": m.get("from", ""),
                "subject": m.get("subject", ""),
                "categoria": "informativo",
                "resumen": (m.get("snippet") or "")[:80],
                "borrador": "",
            }
            for m in mails
        ]

    # Deduplicacion (2026-09-28): el flujo corre cada 30 min y el correo sigue
    # sin leer; sin esto se avisaria del mismo correo una y otra vez.
    from src.database import db

    urgent: list[dict[str, Any]] = []
    for item in classified:
        if item["categoria"] not in ("urgente", "cliente"):
            continue
        key = "inbox:alerted:%s" % (item.get("id") or item.get("subject", ""))
        try:
            already = await db.kv_get(key)
        except Exception:
            already = None
        if already:
            continue
        urgent.append(item)
        try:
            await db.kv_set(key, "1")
        except Exception:
            logger.warning("Inbox: no pude registrar el aviso %s", key[:40])

    return {
        "success": True,
        "scanned": len(mails),
        "urgent_count": len(urgent),
        "items": classified,
    }


# ---------------------------------------------------------------- captura


async def capture_to_vault(
    text: str,
    title: str = "",
    tags: list[str] | None = None,
    source: str = "n8n",
) -> dict[str, Any]:
    """Crea una nota .md con frontmatter y etiquetas en la boveda."""
    content = (text or "").strip()
    if not content:
        return {"success": False, "message": "Texto vacio"}
    now = datetime.now(ZoneInfo(settings.timezone))
    clean_title = (title or "").strip() or "Captura %s" % now.strftime("%Y-%m-%d %H%M")
    tag_list = [t.strip().lstrip("#") for t in (tags or []) if t and t.strip()]
    frontmatter = [
        "---",
        "title: %s" % clean_title,
        "created: %s" % now.strftime("%Y-%m-%d %H:%M"),
        "source: %s" % source,
        "tags: [%s]" % ", ".join(tag_list + ["captura"]),
        "---",
        "",
    ]
    body = "\n".join(frontmatter) + content + "\n"
    try:
        from src.utils.obsidian_manager import overwrite_note

        result = await overwrite_note(clean_title, body, folder="00-Inbox")
    except Exception as e:
        return {"success": False, "message": "No pude escribir en la boveda: %s" % str(e)[:150]}
    logger.info("Captura a boveda desde %s: %s", source, result.get("filepath"))
    return {
        "success": bool(result.get("success")),
        "message": result.get("message", ""),
        "filepath": result.get("filepath", ""),
        "title": clean_title,
    }
