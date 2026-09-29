"""Tests de las funciones puras del JS de la web (Fase 5, 2026-09-29).

Se ejecutan en un navegador real (Playwright) cargando los ficheros locales:
sin backend y sin red. Si Playwright/Chromium no estan disponibles (p. ej. en
CI sin navegador), los tests se saltan solos.
"""

import pytest

pytest.importorskip("playwright")

from playwright.sync_api import sync_playwright  # noqa: E402

REPO = __import__("pathlib").Path(__file__).resolve().parents[2]
SPA_HTML = REPO / "web" / "app" / "index.html"
CALL_HTML = REPO / "web" / "call_rafita.html"


@pytest.fixture(scope="module")
def navegador():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True, args=["--no-sandbox"])
        except Exception:
            pytest.skip("Chromium de Playwright no disponible")
        yield b
        b.close()


@pytest.fixture(scope="module")
def pagina_spa(navegador):
    pg = navegador.new_page()
    pg.goto(SPA_HTML.as_uri(), wait_until="load")
    yield pg
    pg.close()


@pytest.fixture(scope="module")
def pagina_llamada(navegador):
    pg = navegador.new_page()
    pg.goto(CALL_HTML.as_uri(), wait_until="load")
    yield pg
    pg.close()


# ---------- web/app/app.js ----------


def test_format_duration(pagina_spa):
    assert pagina_spa.evaluate("formatDuration(0)") == "0 min 00 s"
    assert pagina_spa.evaluate("formatDuration(90)") == "1 min 30 s"
    assert pagina_spa.evaluate("formatDuration(3600)") == "60 min 00 s"


def test_escape_html(pagina_spa):
    salida = pagina_spa.evaluate("escapeHtml('<img src=x onerror=1>')")
    assert "<img" not in salida
    assert "&lt;img" in salida


def test_debounce_espera_y_llama_una_vez(pagina_spa):
    pagina_spa.evaluate("""
        window.__llamadas = 0;
        window.__fn = debounce(() => { window.__llamadas++; }, 120);
        window.__fn(); window.__fn(); window.__fn();
    """)
    pagina_spa.wait_for_timeout(300)
    assert pagina_spa.evaluate("window.__llamadas") == 1


def test_mejor_mime_grabacion(pagina_spa):
    mime = pagina_spa.evaluate("mejorMimeGrabacion()")
    assert mime is None or mime in (
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/mp4",
        "audio/ogg;codecs=opus",
    )


# ---------- web/call_rafita.html ----------


def test_rms_from_analyser_null(pagina_llamada):
    assert pagina_llamada.evaluate("rmsFromAnalyser(null)") == 0


def test_auth_headers_sin_y_con_token(pagina_llamada):
    vacio = pagina_llamada.evaluate("authHeaders()")
    assert vacio == {}
    con_token = pagina_llamada.evaluate("(() => { authToken = 'abc123'; return authHeaders(); })()")
    assert con_token == {"X-Call-Token": "abc123"}


def test_get_ws_base_sin_token_en_la_url(pagina_llamada):
    url = pagina_llamada.evaluate("getWsBase() + '/call/ws/x'")
    assert url.startswith("ws")
    assert "token=" not in url


def test_fetch_con_timeout_rechaza_lento(pagina_llamada):
    """El helper de timeout debe abortar y dar mensaje claro."""
    resultado = pagina_llamada.evaluate("""
        (async () => {
            // fetch simulado que tarda 5s pero respeta la señal de abort.
            window.fetch = (url, opts) => new Promise((res, rej) => {
                const t = setTimeout(() => res('llegó tarde'), 5000);
                if (opts && opts.signal) {
                    opts.signal.addEventListener('abort', () => {
                        clearTimeout(t);
                        const e = new Error('aborted');
                        e.name = 'AbortError';
                        rej(e);
                    });
                }
            });
            try {
                await fetchConTimeout('/lo-que-sea', {}, 100);
                return 'sin-error';
            } catch (e) {
                return e.message;
            }
        })()
    """)
    assert "tardó demasiado" in resultado
