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
import unicodedata
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from src.config import settings
from src.logger import logger

# Capitales de provincia -> (codigo INE, zona CAP meteoalerta si conocida).
# La zona CAP se puede cambiar en .env (AEMET_AREA; anexo 2 del Plan
# Meteoalerta). 61 = Andalucia (verificado contra la API de AEMET).
CIUDADES: dict[str, tuple[str, str]] = {
    "sevilla": ("41091", "61"),
    "madrid": ("28079", ""),
    "barcelona": ("08019", ""),
    "valencia": ("46250", ""),
    "zaragoza": ("50297", ""),
    "malaga": ("29067", "61"),
    "murcia": ("30030", ""),
    "alicante": ("03014", ""),
    "cordoba": ("14021", "61"),
    "granada": ("18087", "61"),
    "valladolid": ("47186", ""),
    "bilbao": ("48020", ""),
    "vitoria": ("01059", ""),
    "pamplona": ("31201", ""),
    "santander": ("39075", ""),
    "oviedo": ("33044", ""),
    "a coruna": ("15030", ""),
    "vigo": ("36057", ""),
    "palma": ("07040", ""),
    "las palmas": ("35016", ""),
    "santa cruz de tenerife": ("38038", ""),
    "cadiz": ("11012", "61"),
    "huelva": ("21041", "61"),
    "salamanca": ("37274", ""),
    "toledo": ("45168", ""),
    "badajoz": ("06015", ""),
    "logrono": ("26089", ""),
    "leon": ("24089", ""),
    "burgos": ("09059", ""),
    "albacete": ("02003", ""),
    "jaen": ("23050", "61"),
    "almeria": ("04013", "61"),
    "marbella": ("29069", "61"),
    "jerez": ("11020", "61"),
}


async def _location() -> tuple[str, str, str]:
    """(municipio_ine, zona_cap, nombre) con override en BD si existe."""
    municipio = (settings.briefing_municipio or "").strip()
    area = (settings.aemet_area or "").strip()
    nombre = ""
    try:
        from src.database import db

        stored_m = await db.kv_get("briefing_municipio")
        stored_a = await db.kv_get("aemet_area")
        stored_n = await db.kv_get("ubicacion_nombre")
        if stored_m:
            municipio = str(stored_m).strip()
        if stored_a:
            area = str(stored_a).strip()
        if stored_n:
            nombre = str(stored_n).strip()
    except Exception:
        pass
    return municipio, area, nombre


async def set_location(ciudad: str) -> dict[str, Any]:
    """Guarda la ubicacion (ciudad del mapa o codigo INE de 5 digitos)."""
    from src.database import db

    raw = (ciudad or "").strip()
    if not raw:
        return {
            "success": False,
            "message": (
                "Dime tu ciudad, por ejemplo: /ubicacion Sevilla "
                "(o el codigo INE de 5 digitos, ej: /ubicacion 41091)."
            ),
        }
    key = _normalize_location(raw)
    if key.isdigit() and len(key) == 5:
        municipio, area = key, ""
        nombre = "INE %s" % key
    elif key in CIUDADES:
        municipio, area = CIUDADES[key]
        nombre = raw.title()
    else:
        return {
            "success": False,
            "message": (
                "No tengo '%s' en mi lista de capitales. Busca tu codigo INE "
                "(5 digitos) y usa /ubicacion <codigo>." % raw
            ),
        }
    try:
        await db.kv_set("briefing_municipio", municipio)
        await db.kv_set("ubicacion_nombre", nombre)
        if area:
            await db.kv_set("aemet_area", area)
    except Exception as e:
        return {"success": False, "message": "No pude guardar la ubicacion: %s" % str(e)[:120]}
    extra = (
        " Avisos AEMET activados para la zona %s." % area
        if area
        else " (los avisos CAP usan AEMET_AREA del .env)."
    )
    return {
        "success": True,
        "message": "📍 Ubicación guardada: %s (INE %s).%s" % (nombre, municipio, extra),
    }


def _normalize_location(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip()


# ---------------------------------------------------------------- tiempo


async def _aemet_forecast(code: str, day_index: int = 0) -> str:
    """Prediccion AEMET para un municipio (dia 0=hoy, 1=mañana)."""
    key = (settings.aemet_api_key or "").strip()
    code = (code or "").strip()
    if not key or not code:
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
                logger.warning(
                    "AEMET: respuesta sin datos (%s) para %s",
                    first.get("descripcion", "?"),
                    code,
                )
                return ""
            payload = json.loads((await client.get(datos_url)).content.decode("latin-1"))
        dias = payload[0]["prediccion"]["dia"]
        dia = dias[min(max(day_index, 0), len(dias) - 1)]
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
    code, _area, _nombre = await _location()
    aemet = await _aemet_forecast(code, 0)
    if aemet:
        return "AEMET: " + aemet
    from src.utils.proactive_briefing import _weather_summary

    return await _weather_summary()


# ------------------------------------------------------------------ tiempo
# Cache en memoria (TTL 15 min): AEMET tiene limites de peticiones y el
# briefing ya consume su cuota.
_WEATHER_CACHE: dict[str, tuple[float, str]] = {}
_WEATHER_TTL_S = 900.0

# Codigos WMO de open-meteo -> descripcion en espanol.
_WMO_ES = {
    0: "despejado",
    1: "poco nuboso",
    2: "nubes y claros",
    3: "nublado",
    45: "niebla",
    48: "niebla helada",
    51: "llovizna debil",
    53: "llovizna",
    55: "llovizna fuerte",
    61: "lluvia debil",
    63: "lluvia",
    65: "lluvia fuerte",
    71: "nieve debil",
    73: "nieve",
    75: "nieve fuerte",
    80: "chubascos",
    81: "chubascos moderados",
    82: "chubascos fuertes",
    95: "tormenta",
    96: "tormenta con granizo",
    99: "tormenta fuerte con granizo",
}


def _norm_ciudad(texto: str) -> str:
    decomposed = unicodedata.normalize("NFKD", (texto or "").lower().strip())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


async def _openmeteo_forecast(lat: float, lon: float, day_index: int) -> str:
    import httpx

    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=%s&longitude=%s"
        "&daily=temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max,weathercode"
        "&timezone=auto&forecast_days=%d" % (lat, lon, max(1, day_index + 1))
    )
    async with httpx.AsyncClient(timeout=10.0) as client:
        data = (await client.get(url)).json()
    daily = data.get("daily", {}) or {}
    idx = min(day_index, max(0, len(daily.get("time", [])) - 1))
    tmax = (daily.get("temperature_2m_max") or [None])[idx]
    tmin = (daily.get("temperature_2m_min") or [None])[idx]
    rain = (daily.get("precipitation_probability_max") or [None])[idx]
    wmo = (daily.get("weathercode") or [None])[idx]
    if tmax is None:
        return ""
    parts = ["🌡 %.0f-%.0f °C" % (tmin if tmin is not None else tmax, tmax)]
    if rain is not None:
        parts.append("%.0f%% de lluvia" % rain)
    if wmo is not None and wmo in _WMO_ES:
        parts.append(_WMO_ES[wmo])
    return ", ".join(parts)


async def _geocode(ciudad: str) -> tuple[float, float, str] | None:
    import httpx

    url = (
        "https://geocoding-api.open-meteo.com/v1/search"
        "?name=%s&count=1&language=es&format=json" % ciudad
    )
    async with httpx.AsyncClient(timeout=10.0) as client:
        data = (await client.get(url)).json()
    results = data.get("results") or []
    if not results:
        return None
    top = results[0]
    return float(top["latitude"]), float(top["longitude"]), top.get("name", ciudad)


async def weather_report(ciudad: str = "", dia: str = "hoy") -> dict[str, Any]:
    """Tiempo para el chat: AEMET (ciudad conocida y clave) u open-meteo.

    Devuelve {"success", "message"}; nunca lanza (el tool responde honesto).
    """
    import time as _time

    day_index = 1 if _norm_ciudad(dia).startswith("ma") else 0
    cache_key = "%s|%d" % (_norm_ciudad(ciudad), day_index)
    cached = _WEATHER_CACHE.get(cache_key)
    if cached and (_time.monotonic() - cached[0]) < _WEATHER_TTL_S:
        return {"success": True, "message": cached[1]}

    nombre = ""
    code = ""
    if ciudad.strip():
        nombre = ciudad.strip()
        code = CIUDADES.get(_norm_ciudad(nombre), ("", ""))[0]
    else:
        code, _area, nombre = await _location()
        nombre = nombre or "tu zona"

    texto = ""
    if code:
        texto = await _aemet_forecast(code, day_index)
        if texto:
            texto = "AEMET: " + texto
    if not texto:
        try:
            if ciudad.strip():
                geo = await _geocode(nombre)
            elif settings.briefing_lat and settings.briefing_lon:
                geo = (float(settings.briefing_lat), float(settings.briefing_lon), nombre)
            else:
                geo = await _geocode(nombre)
            if geo:
                texto = await _openmeteo_forecast(geo[0], geo[1], day_index)
        except Exception as e:
            logger.warning("Tiempo: open-meteo no disponible (%s)", str(e)[:120])

    if not texto:
        return {
            "success": False,
            "message": "No he podido consultar el tiempo ahora mismo. Intentalo de nuevo en un momento.",
        }
    cuando = "mañana" if day_index else "hoy"
    mensaje = "🌤 En %s %s: %s" % (nombre, cuando, texto)
    _WEATHER_CACHE[cache_key] = (_time.monotonic(), mensaje)
    return {"success": True, "message": mensaje}


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


async def _agenda_tasks_mail() -> tuple[list[str], list[str], list[str], list[dict[str, Any]]]:
    """(agenda proximas 24h, tareas pendientes, correo no leido 12h).

    Google opcional: sin Google conectado se usa el almacen local (eventos y
    tareas de la BD), igual de funcional.
    """
    agenda: list[str] = []
    tareas: list[str] = []
    correo: list[str] = []
    events: list[dict[str, Any]] = []
    from src.database import db

    try:
        from src.services.google_services_manager import google_services

        ready = await google_services.initialize() and google_services.is_ready
    except Exception:
        ready = False

    if ready:
        try:
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
            return agenda, tareas, correo, events
        except Exception as e:
            logger.warning("Briefing: datos Google no disponibles: %s", str(e)[:150])

    # Modo local (sin Google): eventos y tareas de la BD
    chat_id = (settings.admin_ids or [0])[0]
    try:
        rows = await db.get_upcoming_events(chat_id, limit=10)
        for r in rows:
            raw_dt = str(r.get("event_datetime", ""))
            events.append({"title": r.get("title") or "", "start": raw_dt.replace(" ", "T")})
            agenda.append("%s — %s" % (raw_dt[11:16], r.get("title") or ""))
    except Exception as e:
        logger.warning("Briefing: eventos locales no disponibles: %s", str(e)[:120])
    try:
        rows = await db.list_tasks(chat_id)
        tareas = [r.get("title", "") for r in rows[:10]]
    except Exception as e:
        logger.warning("Briefing: tareas locales no disponibles: %s", str(e)[:120])
    return agenda, tareas, correo, events


def _parse_cap_alerts(blob: bytes, province: str = "") -> list[str]:
    """Extrae avisos activos de un fichero CAP de AEMET (sin dependencias).

    AEMET puede devolver un tar (comprimido o no) con varios XML CAP o un XML
    directo; se aceptan ambos. Con `province` (2 digitos INE, p.ej. '41') solo
    se devuelven los avisos de esa provincia (el codigo de zona es
    [CCAA 2][provincia 2][zona 2], p.ej. 614102 = Sevilla). Devuelve lineas
    tipo '🟠 Severe — Lluvias — Campiña sevillana (hasta 20:00)'.
    """
    import io
    import tarfile
    import xml.etree.ElementTree as ET

    def _tag(elem) -> str:
        return elem.tag.split("}")[-1]

    def _find_all(root, name):
        return [e for e in root.iter() if _tag(e) == name]

    xml_blobs: list[bytes] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as tar:
            for member in tar.getmembers():
                if not member.name.lower().endswith(".xml"):
                    continue
                handle = tar.extractfile(member)
                if handle:
                    xml_blobs.append(handle.read())
    except Exception:
        # No es un tar: puede ser un XML CAP directo.
        xml_blobs = [blob]

    severity_icons = {"Moderate": "🟡", "Severe": "🟠", "Extreme": "🔴"}
    alerts: list[str] = []
    for xml_blob in xml_blobs:
        try:
            root = ET.fromstring(xml_blob)
        except Exception:
            continue
        for info in _find_all(root, "info"):
            # CAP puede traer el mismo aviso en varios idiomas: quedarse con es.
            languages = [(e.text or "").strip() for e in info if _tag(e) == "language"]
            language = languages[0] if languages else ""
            if language and not language.lower().startswith("es"):
                continue
            severity = ""
            event = ""
            expires = ""
            for child in info:
                name = _tag(child)
                text = (child.text or "").strip()
                if name == "severity":
                    severity = text
                elif name == "event":
                    event = text
                elif name == "expires":
                    expires = text
            # areaDesc va anidado en <area><areaDesc>
            areas = [(e.text or "").strip() for e in _find_all(info, "areaDesc")]
            area = areas[0] if areas else ""
            # Codigos de zona (geocode de 6 digitos) para filtrar por provincia.
            geocodes = [
                (e.text or "").strip()
                for e in _find_all(info, "value")
                if (e.text or "").strip().isdigit() and len((e.text or "").strip()) >= 6
            ]
            if province and geocodes and not any(g[2:4] == province for g in geocodes):
                continue
            if severity in severity_icons:
                when = expires[11:16] if len(expires) > 15 else ""
                alerts.append(
                    "%s %s — %s — %s%s"
                    % (
                        severity_icons[severity],
                        severity,
                        event or "aviso",
                        area or "tu zona",
                        " (hasta %s)" % when if when else "",
                    )
                )
    # Sin duplicados, maximo 3
    unique: list[str] = []
    for alert in alerts:
        if alert not in unique:
            unique.append(alert)
    return unique[:3]


async def _aemet_alerts() -> list[str]:
    """Avisos CAP oficiales de AEMET para la provincia configurada."""
    key = (settings.aemet_api_key or "").strip()
    code, area, _nombre = await _location()
    area = (area or "").strip()
    province = (code or "")[:2] if len(code or "") >= 2 else ""
    if not key or not area:
        return []
    try:
        import httpx

        url = (
            "https://opendata.aemet.es/opendata/api/avisos_cap/"
            "ultimoelaborado/area/%s?api_key=%s" % (area, key)
        )
        async with httpx.AsyncClient(timeout=20.0) as client:
            first = json.loads((await client.get(url)).content.decode("latin-1"))
            datos = first.get("datos")
            if not datos:
                return []
            blob = (await client.get(datos)).content
        return _parse_cap_alerts(blob, province=province)
    except Exception as e:
        logger.warning("AEMET CAP no disponible: %s", str(e)[:120])
        return []


async def _save_briefing_copy(text: str) -> None:
    """Guarda una copia del briefing en la boveda (historico)."""
    try:
        from src.utils.obsidian_manager import overwrite_note

        now = datetime.now(ZoneInfo(settings.timezone))
        await overwrite_note(
            "Briefing %s" % now.strftime("%Y-%m-%d"),
            "# Briefing %s\n\n%s\n" % (now.strftime("%d/%m/%Y"), text),
            folder="Briefings",
        )
    except Exception as e:
        logger.warning("Briefing: no se pudo guardar la copia en el vault: %s", str(e)[:120])


def _is_weekend(now: datetime) -> bool:
    return now.weekday() >= 5


def _urgent_line(events: list[dict[str, Any]], now: datetime) -> str:
    """Si un evento empieza en menos de 2 h, se encabeza el briefing."""
    for ev in events:
        try:
            start = datetime.fromisoformat(str(ev.get("start", "")).replace("Z", "+00:00"))
        except Exception:
            continue
        hours = (start - now).total_seconds() / 3600.0
        if 0 <= hours < 2:
            mins = int(hours * 60)
            return "⏰ En %d min: %s (%s)" % (mins, ev.get("title"), start.strftime("%H:%M"))
    return ""


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
    """Briefing ejecutivo listo para n8n (texto + botones URL/callback)."""
    agenda, tareas, correo, events = await _agenda_tasks_mail()
    weather = await _weather()
    alerts = await _aemet_alerts()
    server = await _server_status()
    now = datetime.now(ZoneInfo(settings.timezone))
    weekend = _is_weekend(now)
    urgent = _urgent_line(events, now)

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
    if alerts:
        raw = "AVISOS AEMET (prioridad alta):\n%s\n\n%s" % (
            "\n".join("- " + a for a in alerts),
            raw,
        )
    prompt = BRIEFING_PROMPT % settings.assistant_name
    if weekend:
        prompt += (
            " Es FIN DE SEMANA: modo resumen (maximo 8 lineas; omite el listado "
            "de correo salvo que sea urgente)."
        )
    text = ""
    try:
        from src.ollama_client import llm

        text = (
            await llm.chat(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": raw},
                ],
                max_tokens=400,
            )
        ).strip()
    except Exception as e:
        logger.warning("Briefing: LLM no disponible (%s); uso texto crudo", str(e)[:120])
    if not text:
        text = "☀️ *Briefing de hoy*\n\n" + raw
    if urgent:
        text = urgent + "\n\n" + text
    if alerts:
        text = "🚨 *Avisos AEMET:*\n" + "\n".join("- " + a for a in alerts) + "\n\n" + text

    # Primera vez: pedir la ubicacion para el tiempo y los avisos.
    try:
        from src.database import db

        configured = (await db.kv_get("briefing_municipio")) or (
            settings.briefing_municipio or ""
        ).strip()
    except Exception:
        configured = None
    if not configured:
        text += (
            "\n\n📍 _Configura tu ubicación con /ubicacion <ciudad> para el "
            "tiempo y los avisos (ej: /ubicacion Sevilla)._"
        )

    await _save_briefing_copy(text)

    return {
        "success": True,
        "text": text,
        "counts": {
            "agenda": len(agenda),
            "tareas": len(tareas),
            "correo": len(correo),
            "avisos": len(alerts),
            "finde": weekend,
        },
        "buttons": [
            {"text": "✅ Ver tareas", "url": "https://tasks.google.com/"},
            {"text": "📅 Ver calendario", "url": "https://calendar.google.com/"},
            {"text": "⏰ Reagendar evento", "callback": "brief_reagendar"},
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
        # Boton "Enviar respuesta" (2026-09-28): guardamos el borrador con un
        # token para que el callback del bot lo recupere y lo envie por Gmail.
        if item.get("borrador"):
            token = "%s-%s" % (
                datetime.now().strftime("%H%M%S"),
                abs(hash(item.get("id") or item.get("subject", ""))) % 100000,
            )
            try:
                await db.kv_set(
                    "inbox:draft:%s" % token,
                    json.dumps(
                        {
                            "from": item.get("from", ""),
                            "subject": item.get("subject", ""),
                            "borrador": item["borrador"],
                        },
                        ensure_ascii=False,
                    ),
                )
                item["token"] = token
            except Exception:
                item["token"] = ""
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


# ---------------------------------------------------------------- radar IA

RADAR_DEFAULT_FEEDS = (
    "https://huggingface.co/blog/feed.xml",
    "https://realpython.com/atom.xml",
    "https://blog.n8n.io/rss/",
)

RADAR_PROMPT = (
    "Eres un filtro tecnologico para un desarrollador con este stack: Python, "
    "IA local (Ollama), RAG, automatizacion (n8n), Docker y .NET. Recibes "
    "titulares de repositorios y feeds. Devuelve SOLO un JSON array con "
    'maximo 3 objetos {"titulo": "...", "por_que": "...", '
    '"enlace": "..."} con lo mas relevante y PRACTICO. Descarta ruido, '
    "politica, marketing y noticias sin utilidad practica."
)


def _parse_rss_titles(xml_bytes: bytes, limit: int = 5) -> list[tuple[str, str]]:
    """Titulos+enlaces de un RSS/Atom (sin dependencias)."""
    import xml.etree.ElementTree as ET

    out: list[tuple[str, str]] = []
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return out
    for entry in root.iter():
        tag = entry.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        title = ""
        link = ""
        for child in entry:
            name = child.tag.split("}")[-1]
            if name == "title":
                title = (child.text or "").strip()
            elif name == "link":
                link = (child.get("href") or child.text or "").strip()
        if title:
            out.append((title, link))
        if len(out) >= limit:
            break
    return out


async def radar() -> dict[str, Any]:
    """Radar de IA/software: GitHub + feeds RSS filtrados por el LLM."""
    import httpx

    raw_lines: list[str] = []
    try:
        since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        url = (
            "https://api.github.com/search/repositories?q=created:>%s+language:python"
            "&sort=stars&order=desc&per_page=10" % since
        )
        async with httpx.AsyncClient(
            timeout=20.0, headers={"Accept": "application/vnd.github+json"}
        ) as client:
            data = (await client.get(url)).json()
        for repo in data.get("items", [])[:10]:
            raw_lines.append(
                "- [GitHub] %s (%s stars): %s — %s"
                % (
                    repo.get("full_name", "?"),
                    repo.get("stargazers_count", 0),
                    (repo.get("description") or "")[:120],
                    repo.get("html_url", ""),
                )
            )
    except Exception as e:
        logger.warning("Radar: GitHub no disponible: %s", str(e)[:120])

    feeds = (settings.radar_feeds or "").strip()
    feed_list = (
        [f.strip() for f in feeds.split(",") if f.strip()] if feeds else list(RADAR_DEFAULT_FEEDS)
    )
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            for feed in feed_list[:6]:
                try:
                    xml = (await client.get(feed)).content
                    for title, link in _parse_rss_titles(xml):
                        raw_lines.append("- [RSS] %s — %s" % (title, link))
                except Exception as e:
                    logger.info("Radar: feed %s fallo (%s)", feed, str(e)[:80])
    except Exception as e:
        logger.warning("Radar: feeds no disponibles: %s", str(e)[:120])

    if not raw_lines:
        return {"success": False, "message": "No pude obtener fuentes para el radar."}

    text = ""
    items: list[dict[str, str]] = []
    try:
        from src.ollama_client import llm

        out = await llm.chat(
            messages=[
                {"role": "system", "content": RADAR_PROMPT},
                {"role": "user", "content": "\n".join(raw_lines[:30])},
            ],
            max_tokens=500,
        )
        start, end = out.find("["), out.rfind("]")
        if start >= 0 and end > start:
            items = json.loads(out[start : end + 1])[:3]
    except Exception as e:
        logger.warning("Radar: filtro LLM fallo (%s)", str(e)[:120])

    if items:
        lines = ["🛰 *Radar de IA — lo relevante de hoy*", ""]
        for item in items:
            lines.append("*%s*" % item.get("titulo", "?"))
            if item.get("por_que"):
                lines.append("_%s_" % item["por_que"])
            if item.get("enlace"):
                lines.append(item["enlace"])
            lines.append("")
        text = "\n".join(lines).strip()
    else:
        text = "🛰 *Radar de IA (sin filtro)*\n\n" + "\n".join(raw_lines[:6])

    await _save_vault_note("Radar IA %s" % datetime.now().strftime("%Y-%m-%d"), text, "Radar")
    return {"success": True, "text": text, "items": items, "sources": len(raw_lines)}


# ---------------------------------------------------------------- infra


def _read_status_file(name: str) -> dict[str, Any]:
    from pathlib import Path

    path = Path(settings.data_dir) / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


async def infra_report() -> dict[str, Any]:
    """Informe de infraestructura: backups, disco, BD, RAG, IA (domingos)."""
    import shutil
    from pathlib import Path

    backup = _read_status_file("backup-status.json")
    drill = _read_status_file("restore-drill-status.json")

    disk = {}
    try:
        usage = shutil.disk_usage("/data")
        disk = {
            "total_gb": round(usage.total / 1e9, 1),
            "usado_gb": round(usage.used / 1e9, 1),
            "libre_gb": round(usage.free / 1e9, 1),
            "pct": round(usage.used * 100 / usage.total, 1),
        }
    except Exception:
        pass

    db_size = ""
    try:
        db_size = "%.1f MB" % (Path(settings.db_path).stat().st_size / 1e6)
    except Exception:
        pass

    server = await _server_status()
    raw = (
        "BACKUP:\n%s\n\nRESTORE-DRILL:\n%s\n\nDISCO (/data):\n%s\n\n"
        "TAMANO BD: %s\n\nSERVICIOS:\n%s"
        % (
            json.dumps(backup, ensure_ascii=False) or "(sin datos)",
            json.dumps(drill, ensure_ascii=False) or "(sin datos)",
            json.dumps(disk, ensure_ascii=False),
            db_size or "?",
            json.dumps(server, ensure_ascii=False),
        )
    )
    text = ""
    try:
        from src.ollama_client import llm

        text = (
            await llm.chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Eres el asistente de operaciones. Redacta un INFORME "
                            "SEMANAL DE INFRAESTRUCTURA en espanol, en 6-10 lineas, "
                            "con negritas de Telegram (*texto*). Incluye: estado de "
                            "los backups (fecha del ultimo snapshot y resultado de "
                            "la copia a Drive), resultado del restore-drill, disco "
                            "libre y servicios (IA/RAG/Google). Si algo falta o "
                            "fallo, destacalo como ALERTA al principio. HONESTIDAD: "
                            "si un dato no aparece o pone '(sin datos)', di "
                            "claramente que no hay datos; NUNCA inventes resultados "
                            "ni afirmes que algo fue bien sin evidencia."
                        ),
                    },
                    {"role": "user", "content": raw},
                ],
                max_tokens=350,
            )
        ).strip()
    except Exception as e:
        logger.warning("Infra: LLM no disponible (%s)", str(e)[:120])
    if not text:
        text = "🖥 *Informe de infraestructura*\n\n" + raw

    # Alertas criticas primero (sin depender del LLM)
    alerts: list[str] = []
    if not backup:
        alerts.append("No hay datos de backup (¿ha corrido el backup alguna vez?)")
    if disk and disk.get("pct", 0) > 85:
        alerts.append("Disco /data al %s%%" % disk.get("pct"))
    if drill.get("integrity") not in (None, "", "ok"):
        alerts.append("Restore-drill con integridad '%s'" % drill.get("integrity"))
    if alerts:
        text = "🚨 *ALERTAS:*\n" + "\n".join("- " + a for a in alerts) + "\n\n" + text

    return {"success": True, "text": text, "backup": backup, "drill": drill, "disk": disk}


async def _save_vault_note(title: str, text: str, folder: str) -> None:
    try:
        from src.utils.obsidian_manager import overwrite_note

        await overwrite_note(title, text, folder=folder)
    except Exception as e:
        logger.warning("No se pudo guardar la nota '%s': %s", title, str(e)[:120])


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
