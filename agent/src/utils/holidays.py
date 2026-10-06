"""Proximo festivo via Nager.Date (API publica, sin clave).

Consulta `https://date.nager.at/api/v3/PublicHolidays/{anio}/{pais}` con
httpx (timeout 5 s) y devuelve el proximo festivo >= hoy, o None si no queda
ninguno. CUALQUIER fallo (red, 4xx/5xx, JSON inesperado) se degrada en
silencio: `None` + logger.debug, para que el briefing y el chat nunca se
caigan por un servicio externo.

Los resultados exitosos se cachean en memoria por (pais, anio) en `_cache`
(module-level; los tests pueden llamar a `_cache.clear()` para aislar casos).
"""

from datetime import date
from typing import Any

import httpx

from src.logger import logger

_BASE_URL = "https://date.nager.at/api/v3/PublicHolidays"
# Consulta corta: es un dato decorativo del briefing, no puede frenar el envio.
_TIMEOUT = 5.0

# Cache modulo-level por (pais, anio). Solo se guardan consultas exitosas
# (un fallo de red puntual no debe tumbar los festivos del ano entero).
_cache: dict[tuple[str, int], list[tuple[date, str]]] = {}


async def _fetch_festivos(pais: str, anio: int) -> list[dict[str, Any]] | None:
    """Lista cruda de festivos del ano, o None ante cualquier error.

    Separada de `proximo_festivo` para que los tests puedan mockear httpx
    (o esta propia funcion) sin tocar la logica de filtrado.
    """
    url = "%s/%d/%s" % (_BASE_URL, anio, pais)
    datos: Any = None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            respuesta = await client.get(url)
            respuesta.raise_for_status()
            datos = respuesta.json()
    except Exception as e:
        logger.debug("Festivos: consulta fallida (%s/%s): %s", pais, anio, e)
        return None
    if not isinstance(datos, list):
        logger.debug("Festivos: respuesta inesperada (%s/%s): %r", pais, anio, datos)
        return None
    return datos


async def proximo_festivo(pais: str = "ES", hoy: date | None = None) -> tuple[date, str] | None:
    """Proximo festivo >= hoy, como (fecha, nombre).

    Devuelve None si no queda ninguno en el ano o si hay cualquier error
    (degradacion silenciosa: solo logger.debug, nunca excepcion).
    """
    desde = hoy if hoy is not None else date.today()
    anio = desde.year
    clave = (pais, anio)
    festivos = _cache.get(clave)
    if festivos is None:
        crudos = await _fetch_festivos(pais, anio)
        if crudos is None:
            return None
        parseados: list[tuple[date, str]] = []
        for item in crudos:
            try:
                fecha = date.fromisoformat(str(item.get("date") or ""))
                nombre = str(item.get("localName") or item.get("name") or "").strip()
            except (AttributeError, TypeError, ValueError):
                continue
            if not nombre:
                continue
            parseados.append((fecha, nombre))
        parseados.sort(key=lambda par: par[0])
        festivos = parseados
        _cache[clave] = festivos
    for fecha, nombre in festivos:
        if fecha >= desde:
            return fecha, nombre
    return None
