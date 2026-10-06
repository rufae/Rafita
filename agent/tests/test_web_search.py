"""Búsqueda web (DDGS + fetch de página): sin red, con dobles.

Cubre las 4 funciones de web_search: formato de resultados, fetch con
limpieza de HTML, búsqueda con hilo y sus rutas de error honestas.
"""

from types import SimpleNamespace

import src.utils.web_search as ws


class _RespuestaFalsa:
    def __init__(self, html="", status_error=False):
        self.text = html
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error:
            raise RuntimeError("HTTP 500")


class _ClienteFalso:
    def __init__(self, respuesta):
        self._respuesta = respuesta

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, headers=None):
        return self._respuesta


def test_format_search_results_ordenado_y_completo():
    texto = ws.format_search_results(
        [
            {"title": "Titulo 1", "snippet": "Resumen", "url": "https://x.test/a"},
            {"title": "", "snippet": "", "url": ""},
        ]
    )
    assert "1. Titulo 1" in texto
    assert "Resumen" in texto
    assert "Fuente: https://x.test/a" in texto
    assert "2." in texto


def test_format_search_results_vacio_es_honesto():
    assert ws.format_search_results([]) == "No se encontraron resultados."


async def test_fetch_page_text_limpia_html_y_recorta(monkeypatch):
    html = (
        "<html><head><script>malicioso()</script><style>.x{}</style></head>"
        "<body><nav>menu</nav><header>cab</header><footer>pie</footer>"
        "<p>Contenido util</p></body></html>"
    )
    monkeypatch.setattr(ws.httpx, "AsyncClient", lambda **k: _ClienteFalso(_RespuestaFalsa(html)))
    texto = await ws.fetch_page_text("https://x.test/pagina")
    assert "Contenido util" in texto
    assert "malicioso" not in texto
    assert "menu" not in texto
    assert "pie" not in texto


async def test_fetch_page_text_recorta_en_max_chars(monkeypatch):
    html = "<p>" + ("a" * (ws.MAX_CONTENT_CHARS + 500)) + "</p>"
    monkeypatch.setattr(ws.httpx, "AsyncClient", lambda **k: _ClienteFalso(_RespuestaFalsa(html)))
    texto = await ws.fetch_page_text("https://x.test/largo")
    assert len(texto) == ws.MAX_CONTENT_CHARS


async def test_fetch_page_text_error_devuelve_vacio(monkeypatch):
    def _cliente(**k):
        return _ClienteFalso(_RespuestaFalsa(status_error=True))

    monkeypatch.setattr(ws.httpx, "AsyncClient", _cliente)
    assert await ws.fetch_page_text("https://x.test/caido") == ""


async def test_search_duckduckgo_formatea_resultados(monkeypatch):
    class _DDGS:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def text(self, query, max_results=5):
            return [{"title": "T", "href": "https://u", "body": "B"}]

    monkeypatch.setitem(__import__("sys").modules, "duckduckgo_search", SimpleNamespace(DDGS=_DDGS))
    filas = await ws.search_duckduckgo("rafita")
    assert filas == [{"title": "T", "url": "https://u", "snippet": "B"}]


async def test_search_duckduckgo_fallo_devuelve_vacio(monkeypatch):
    class _DDGSRoto:
        def __enter__(self):
            raise RuntimeError("sin red")

        def __exit__(self, *args):
            return None

    monkeypatch.setitem(
        __import__("sys").modules,
        "duckduckgo_search",
        SimpleNamespace(DDGS=_DDGSRoto),
    )
    assert await ws.search_duckduckgo("rafita") == []
