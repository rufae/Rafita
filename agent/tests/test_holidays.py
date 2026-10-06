"""Tests de utils/holidays.py (festivos Nager.Date) y de su línea en el briefing.

Todo mockeado: httpx se sustituye por un cliente falso (patrón del repo
`monkeypatch.setattr("httpx.AsyncClient", ...)`) y no se consulta la red.
"""

from datetime import date

import httpx
import pytest

from src.config import settings
from src.utils import holidays, proactive_briefing

_URL_ES_2026 = "https://date.nager.at/api/v3/PublicHolidays/2026/ES"

# 2026-10-12 (Fiesta Nacional) es lunes; 2026-12-06 es domingo.
_FESTIVOS_2026 = [
    {"date": "2026-01-01", "localName": "Año Nuevo", "name": "New Year's Day"},
    {
        "date": "2026-10-12",
        "localName": "Fiesta Nacional de España",
        "name": "National Day of Spain",
    },
    {"date": "2026-12-06", "localName": "Día de la Constitución", "name": "Constitution Day"},
]


@pytest.fixture(autouse=True)
def _cache_limpio():
    """El cache es de módulo: se limpia antes y después de cada test."""
    holidays._cache.clear()
    yield
    holidays._cache.clear()


# ---------- dobles de httpx ----------


class _Respuesta:
    """Respuesta httpx falsa: payload, estado HTTP o JSON roto."""

    def __init__(self, payload=None, status=200, json_roto=False):
        self._payload = payload
        self._status = status
        self._json_roto = json_roto

    def raise_for_status(self):
        if self._status >= 400:
            req = httpx.Request("GET", _URL_ES_2026)
            resp = httpx.Response(self._status, request=req)
            raise httpx.HTTPStatusError("HTTP %d" % self._status, request=req, response=resp)

    def json(self):
        if self._json_roto:
            raise ValueError("JSON invalido")
        return self._payload


class _ClienteFalso:
    """httpx.AsyncClient falso: registra URL y kwargs de cada consulta."""

    def __init__(self, respuesta=None, exc=None):
        self._respuesta = respuesta
        self._exc = exc
        self.llamadas = []  # [(url, kwargs)]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        self.llamadas.append((url, kwargs))
        if self._exc is not None:
            raise self._exc
        return self._respuesta


def _parchea_httpx(monkeypatch, respuesta=None, exc=None):
    """Sustituye httpx.AsyncClient por un doble; devuelve (cliente, kwargs)."""
    cliente = _ClienteFalso(respuesta, exc=exc)
    kwargs_registrados = []

    def _factory(**kwargs):
        kwargs_registrados.append(kwargs)
        return cliente

    monkeypatch.setattr("httpx.AsyncClient", _factory)
    return cliente, kwargs_registrados


# ---------- proximo_festivo ----------


async def test_proximo_festivo_devuelve_el_siguiente_posterior_a_hoy(monkeypatch):
    cliente, kwargs_reg = _parchea_httpx(monkeypatch, _Respuesta(_FESTIVOS_2026))

    assert await holidays.proximo_festivo("ES", hoy=date(2026, 10, 1)) == (
        date(2026, 10, 12),
        "Fiesta Nacional de España",
    )
    # El propio día también cuenta (>= hoy) y el resto sale del cache.
    assert await holidays.proximo_festivo("ES", hoy=date(2026, 10, 12)) == (
        date(2026, 10, 12),
        "Fiesta Nacional de España",
    )
    assert await holidays.proximo_festivo("ES", hoy=date(2026, 10, 13)) == (
        date(2026, 12, 6),
        "Día de la Constitución",
    )

    assert len(cliente.llamadas) == 1
    url, _kwargs_get = cliente.llamadas[0]
    assert url == _URL_ES_2026  # https://date.nager.at/api/v3/PublicHolidays/{año}/{pais}
    # El timeout va al constructor del AsyncClient (registrado por _factory).
    assert kwargs_reg[0].get("timeout") == 5.0

    # País por defecto (ES) y cache por (país, año).
    assert await holidays.proximo_festivo(hoy=date(2026, 12, 31)) is None
    assert len(cliente.llamadas) == 1


async def test_proximo_festivo_sin_restantes_devuelve_none(monkeypatch):
    _parchea_httpx(monkeypatch, _Respuesta(_FESTIVOS_2026))
    assert await holidays.proximo_festivo("ES", hoy=date(2026, 12, 31)) is None


async def test_proximo_festivo_lista_vacia_devuelve_none(monkeypatch):
    cliente, _ = _parchea_httpx(monkeypatch, _Respuesta([]))
    assert await holidays.proximo_festivo("ES", hoy=date(2026, 10, 1)) is None
    # Una respuesta vacía PERO válida sí se cachea: no se vuelve a preguntar.
    assert await holidays.proximo_festivo("ES", hoy=date(2026, 10, 2)) is None
    assert len(cliente.llamadas) == 1


async def test_proximo_festivo_error_de_red_devuelve_none(monkeypatch):
    cliente, _ = _parchea_httpx(monkeypatch, exc=httpx.ConnectError("sin red"))
    assert await holidays.proximo_festivo("ES", hoy=date(2026, 10, 1)) is None
    # El fallo NO se cachea: se reintenta en la siguiente llamada.
    assert holidays._cache == {}
    assert await holidays.proximo_festivo("ES", hoy=date(2026, 10, 1)) is None
    assert len(cliente.llamadas) == 2


async def test_proximo_festivo_error_http_devuelve_none(monkeypatch):
    for status in (404, 500, 503):
        _parchea_httpx(monkeypatch, _Respuesta(_FESTIVOS_2026, status=status))
        assert await holidays.proximo_festivo("ES", hoy=date(2026, 10, 1)) is None
    assert holidays._cache == {}


async def test_proximo_festivo_json_invalido_devuelve_none(monkeypatch):
    # El cuerpo no es JSON parseable.
    _parchea_httpx(monkeypatch, _Respuesta(json_roto=True))
    assert await holidays.proximo_festivo("ES", hoy=date(2026, 10, 1)) is None

    # JSON válido pero no es una lista (p. ej. el error 404 de Nager).
    _parchea_httpx(monkeypatch, _Respuesta({"status": 404, "message": "Country not found"}))
    assert await holidays.proximo_festivo("XX", hoy=date(2026, 10, 1)) is None

    assert holidays._cache == {}


async def test_proximo_festivo_ignora_entradas_malas(monkeypatch):
    crudos = [
        {"date": "no-es-fecha", "localName": "Roto"},
        {"localName": "Sin fecha"},
        "esto-no-es-un-dict",
        {"date": "2026-10-12"},  # sin nombre
        {"date": "2026-10-12", "localName": "Fiesta Nacional de España"},
    ]
    _parchea_httpx(monkeypatch, _Respuesta(crudos))
    assert await holidays.proximo_festivo("ES", hoy=date(2026, 10, 1)) == (
        date(2026, 10, 12),
        "Fiesta Nacional de España",
    )


async def test_proximo_festivo_cachea_por_pais_y_anio(monkeypatch):
    cliente, _ = _parchea_httpx(monkeypatch, _Respuesta(_FESTIVOS_2026))

    assert await holidays.proximo_festivo("ES", hoy=date(2026, 10, 1)) == (
        date(2026, 10, 12),
        "Fiesta Nacional de España",
    )
    assert await holidays.proximo_festivo("ES", hoy=date(2026, 10, 13)) == (
        date(2026, 12, 6),
        "Día de la Constitución",
    )
    assert len(cliente.llamadas) == 1  # segunda llamada: cache

    # Otro país sí consulta de nuevo.
    await holidays.proximo_festivo("FR", hoy=date(2026, 10, 1))
    assert len(cliente.llamadas) == 2
    assert cliente.llamadas[1][0].endswith("/2026/FR")

    # El cache se puede limpiar a mano (así lo hacen los tests).
    holidays._cache.clear()
    await holidays.proximo_festivo("ES", hoy=date(2026, 10, 1))
    assert len(cliente.llamadas) == 3


# ---------- _holiday_line / send_briefing ----------


async def _afn(valor):
    return valor


async def test_holiday_line_formateada_en_espanol(monkeypatch):
    async def proximo(pais="ES", hoy=None):
        return date(2026, 10, 12), "Fiesta Nacional de España"

    monkeypatch.setattr(proactive_briefing, "proximo_festivo", proximo)
    assert await proactive_briefing._holiday_line() == (
        "Próximo festivo: lunes 12 de octubre (Fiesta Nacional de España)"
    )


async def test_holiday_line_none_si_no_queda_ninguno_o_falla(monkeypatch):
    async def sin_festivos(pais="ES", hoy=None):
        return None

    monkeypatch.setattr(proactive_briefing, "proximo_festivo", sin_festivos)
    assert await proactive_briefing._holiday_line() is None

    async def caida(pais="ES", hoy=None):
        raise httpx.ConnectError("sin red")

    monkeypatch.setattr(proactive_briefing, "proximo_festivo", caida)
    assert await proactive_briefing._holiday_line() is None


class _FakeBot:
    def __init__(self):
        self.messages = []

    async def send_proactive_message(self, chat_id, text):
        self.messages.append((chat_id, text))


def _parchea_briefing(monkeypatch):
    monkeypatch.setattr(settings, "briefing_enabled", True)
    monkeypatch.setattr(settings, "admin_ids", [1])
    monkeypatch.setattr(proactive_briefing, "_weather_summary", lambda: _afn("🌡 12-20 °C"))
    monkeypatch.setattr(proactive_briefing, "_agenda_lines", lambda days: _afn([]))
    monkeypatch.setattr(proactive_briefing, "_mail_lines", lambda: _afn([]))


async def test_send_briefing_incluye_la_linea_de_festivo(monkeypatch):
    _parchea_briefing(monkeypatch)
    monkeypatch.setattr(
        proactive_briefing,
        "_holiday_line",
        lambda: _afn("Próximo festivo: lunes 12 de octubre (Fiesta Nacional de España)"),
    )

    bot = _FakeBot()
    sent = await proactive_briefing.send_briefing(bot)

    assert sent == 1
    texto = bot.messages[0][1]
    assert "*🎉 Próximo festivo:* lunes 12 de octubre (Fiesta Nacional de España)" in texto
    # Tras la línea de Tiempo y antes de la agenda, como el resto de secciones.
    assert (
        texto.index("*Tiempo:*")
        < texto.index("*🎉 Próximo festivo:*")
        < texto.index("*📅 Agenda de hoy:*")
    )


async def test_send_briefing_sobrevive_si_la_linea_de_festivos_falla(monkeypatch):
    _parchea_briefing(monkeypatch)

    async def roto():
        raise RuntimeError("nager caido")

    monkeypatch.setattr(proactive_briefing, "_holiday_line", roto)

    bot = _FakeBot()
    sent = await proactive_briefing.send_briefing(bot)

    assert sent == 1
    texto = bot.messages[0][1]
    assert "Próximo festivo" not in texto
    assert "*📅 Agenda de hoy:*" in texto
    assert "12-20" in texto


async def test_send_briefing_sin_festivos_mantiene_el_formato(monkeypatch):
    """Sin línea de festivo el briefing es idéntico al de siempre."""
    _parchea_briefing(monkeypatch)
    monkeypatch.setattr(proactive_briefing, "_holiday_line", lambda: _afn(None))

    bot = _FakeBot()
    assert await proactive_briefing.send_briefing(bot) == 1
    texto = bot.messages[0][1]
    assert "Próximo festivo" not in texto
    assert texto.index("*Tiempo:*") < texto.index("*📅 Agenda de hoy:*")
