"""Tests de page_fetch (fase 3): validacion de URL, Obscura, fallback y fallos."""

import pytest

from src.utils import page_fetch


def test_url_valida_acepta_http_https() -> None:
    assert page_fetch.url_valida("https://example.com/path?q=1")
    assert page_fetch.url_valida("http://example.com")


def test_url_valida_rechaza_otros_esquemas() -> None:
    assert not page_fetch.url_valida("file:///etc/passwd")
    assert not page_fetch.url_valida("javascript:alert(1)")
    assert not page_fetch.url_valida("ftp://example.com")
    assert not page_fetch.url_valida("mailto:a@b.c")
    assert not page_fetch.url_valida("")


def test_url_rota_no_revienta() -> None:
    # urlparse lanza ValueError en IPv6 malformada; debe devolver False.
    assert not page_fetch.url_valida("http://[brok")


async def test_fetch_con_obscura(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(page_fetch, "obscura_binario", lambda: "/usr/bin/obscura")

    async def fake_render(url: str) -> str:
        return "# Hola\n\ncontenido renderizado"

    monkeypatch.setattr(page_fetch, "_render_obscura", fake_render)
    res = await page_fetch.fetch_page("https://example.com")
    assert res["success"] is True
    assert res["method"] == "obscura"
    assert "renderizado" in str(res["text"])


async def test_fetch_fallback_estatico_sin_obscura(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(page_fetch, "obscura_binario", lambda: None)

    async def fake_text(url: str, max_chars: int = 0) -> str:
        return "texto estatico"

    monkeypatch.setattr(page_fetch, "fetch_page_text", fake_text)
    res = await page_fetch.fetch_page("https://example.com")
    assert res["success"] is True
    assert res["method"] == "estatica"
    assert res["text"] == "texto estatico"


async def test_fetch_obscura_falla_y_usa_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    # /bin/false existe siempre: _render_obscura falla (exit != 0) y se cae
    # al modo estatico declarando el metodo.
    monkeypatch.setattr(page_fetch, "obscura_binario", lambda: "/bin/false")

    async def fake_text(url: str, max_chars: int = 0) -> str:
        return "respaldo"

    monkeypatch.setattr(page_fetch, "fetch_page_text", fake_text)
    res = await page_fetch.fetch_page("https://example.com")
    assert res["success"] is True
    assert res["method"] == "estatica"
    assert res["text"] == "respaldo"


async def test_fetch_todo_falla(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(page_fetch, "obscura_binario", lambda: None)

    async def fake_text(url: str, max_chars: int = 0) -> str:
        return ""

    monkeypatch.setattr(page_fetch, "fetch_page_text", fake_text)
    res = await page_fetch.fetch_page("https://example.com")
    assert res["success"] is False
    assert "no pude leer" in res["message"].lower()


async def test_fetch_trunca_a_max_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(page_fetch, "obscura_binario", lambda: None)

    async def fake_text(url: str, max_chars: int = 0) -> str:
        return "x" * 10_000

    monkeypatch.setattr(page_fetch, "fetch_page_text", fake_text)
    res = await page_fetch.fetch_page("https://example.com", max_chars=100)
    assert res["success"] is True
    assert len(str(res["text"])) == 100


async def test_render_obscura_sin_binario(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(page_fetch, "obscura_binario", lambda: None)
    with pytest.raises(RuntimeError, match="no encontrado"):
        await page_fetch._render_obscura("https://example.com")


async def test_render_obscura_exit_distinto_de_cero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(page_fetch, "obscura_binario", lambda: "/bin/false")
    with pytest.raises(RuntimeError, match="Obscura fallo"):
        await page_fetch._render_obscura("https://example.com")


async def test_render_obscura_exit_cero_pero_vacio(monkeypatch: pytest.MonkeyPatch) -> None:
    # /bin/true sale 0 sin stdout: se trata como fallo (honestidad, sin humo).
    monkeypatch.setattr(page_fetch, "obscura_binario", lambda: "/bin/true")
    with pytest.raises(RuntimeError, match="Obscura fallo"):
        await page_fetch._render_obscura("https://example.com")


def test_obscura_binario_ausente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(page_fetch.settings, "obscura_bin", "/no/existe/obscura")
    assert page_fetch.obscura_binario() is None
    monkeypatch.setattr(page_fetch.settings, "obscura_bin", "")
    assert page_fetch.obscura_binario() is None


def test_obscura_binario_por_nombre(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(page_fetch.settings, "obscura_bin", "sh")
    assert page_fetch.obscura_binario() is not None


def test_obscura_binario_path_directo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(page_fetch.settings, "obscura_bin", "/bin/sh")
    assert page_fetch.obscura_binario() == "/bin/sh"


@pytest.mark.parametrize("url", ["", "   ", "example.com"])
async def test_fetch_urls_sin_esquema_rechazadas(url: str) -> None:
    res = await page_fetch.fetch_page(url)
    assert res["success"] is False
