"""E2E mínimo de la web (Fase 5): login → chat → respuesta y Baúl (abrir y
cerrar una nota).

Requiere una instancia accesible y credenciales por variables de entorno:
    RAFITA_WEB_URL=http://192.168.1.129:8010/app/
    RAFITA_WEB_EMAIL=admin@ejemplo.com
    RAFITA_WEB_PASSWORD=...
Sin ellas (p. ej. en CI) el test se salta: es una prueba de integración, no
una unitaria.
"""

import os

import pytest

pytest.importorskip("playwright")

from playwright.sync_api import sync_playwright  # noqa: E402

URL = os.environ.get("RAFITA_WEB_URL", "")
EMAIL = os.environ.get("RAFITA_WEB_EMAIL", "")
PASSWORD = os.environ.get("RAFITA_WEB_PASSWORD", "")

pytestmark = pytest.mark.skipif(
    not (URL and EMAIL and PASSWORD),
    reason="Faltan RAFITA_WEB_URL/EMAIL/PASSWORD (E2E contra instancia real)",
)


@pytest.fixture(scope="module")
def navegador():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True, args=["--no-sandbox"])
        except Exception:
            pytest.skip("Chromium de Playwright no disponible")
        yield b
        b.close()


def test_flujo_completo(navegador):
    ctx = navegador.new_context()
    pg = ctx.new_page()
    errores = []
    pg.on("pageerror", lambda e: errores.append(str(e)))

    # login
    pg.goto(URL, wait_until="load")
    pg.fill("#login-email", EMAIL)
    pg.fill("#login-password", PASSWORD)
    pg.click("#login-form button[type=submit]")
    pg.wait_for_selector("#app:not(.hidden)", timeout=20000)

    # chat: enviar un mensaje y esperar respuesta del cerebro real
    pg.fill("#chat-input", "Responde solo con la palabra: hola")
    pg.click("#chat-form button[type=submit]")
    pg.wait_for_function(
        "() => document.querySelectorAll('#chat-messages .bubble.bot').length >= 2",
        timeout=120000,
    )
    respuestas = pg.eval_on_selector_all(
        "#chat-messages .bubble.bot", "els => els.map(e => e.textContent)"
    )
    assert any("hola" in r.lower() for r in respuestas), respuestas

    # baúl: crear, guardar, abrir y borrar una nota (con modal propio)
    pg.click('[data-view="vault"]')
    pg.wait_for_timeout(800)
    pg.click("#vault-new")
    pg.fill("#note-path", "00-Inbox/e2e-fase5.md")
    pg.fill("#note-content", "# E2E\nnota de prueba")
    pg.click("#note-save")
    pg.wait_for_timeout(1000)
    pg.fill("#vault-search", "e2e-fase5")
    pg.wait_for_timeout(900)
    pg.click("#vault-list li")
    pg.wait_for_timeout(500)
    assert "nota de prueba" in pg.input_value("#note-content")
    pg.click("#note-delete")
    pg.wait_for_selector("#modal:not(.hidden)")
    pg.click("#modal-aceptar")
    pg.wait_for_timeout(800)

    assert not errores, "errores de pagina: %s" % errores
    ctx.close()
