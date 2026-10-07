"""Tests del servidor MCP (solo lectura): herramientas curadas, auth Bearer
y arranque."""

import sys
import types
from typing import Any

import pytest
from starlette.testclient import TestClient

from src.utils import mcp_server

HERRAMIENTAS_ESPERADAS = {
    "buscar_en_la_boveda",
    "leer_nota",
    "buscar_historial",
    "buscar_en_la_web",
    "fetch_pagina",
    "listar_skills",
    "cargar_skill",
}


async def test_herramientas_curadas_solo_lectura() -> None:
    tools = await mcp_server._mcp.list_tools()
    nombres = {t.name for t in tools}
    assert nombres == HERRAMIENTAS_ESPERADAS


def test_token_requerido_para_arrancar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_server.settings, "mcp_token", "")
    with pytest.raises(RuntimeError, match="MCP_TOKEN"):
        mcp_server.create_app()


def test_create_app_con_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_server.settings, "mcp_token", "tok")
    app = mcp_server.create_app()
    assert isinstance(app, mcp_server.BearerTokenMiddleware)


async def _dummy_app(scope: Any, receive: Any, send: Any) -> None:
    body = b"ok"
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


def test_auth_bearer_401_sin_o_con_token_mal() -> None:
    app = mcp_server.BearerTokenMiddleware(_dummy_app, "secreto")
    client = TestClient(app)
    assert client.get("/x").status_code == 401
    assert client.get("/x", headers={"Authorization": "Bearer mal"}).status_code == 401
    assert client.get("/x", headers={"Authorization": "secreto"}).status_code == 401
    assert client.get("/x", headers={"Authorization": "Bearer secreto"}).status_code == 200


def test_auth_401_indica_esquema_bearer() -> None:
    app = mcp_server.BearerTokenMiddleware(_dummy_app, "secreto")
    client = TestClient(app)
    r = client.get("/x")
    assert r.headers.get("www-authenticate") == "Bearer"
    assert r.json()["error"] == "unauthorized"


def test_handshake_initialize_con_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_server.settings, "mcp_token", "tok")
    app = mcp_server.create_app()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }
    headers = {
        "Authorization": "Bearer tok",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    with TestClient(app, base_url="http://localhost:8020") as client:
        r = client.post("/mcp", json=payload, headers=headers)
        assert r.status_code == 200
        # Sin token: rechazado por la capa propia de auth.
        r2 = client.post("/mcp", json=payload, headers={"Accept": headers["Accept"]})
        assert r2.status_code == 401


async def test_buscar_en_la_boveda(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_query(query: str, top_k: int = 5) -> dict[str, Any]:
        return {
            "success": True,
            "results": [
                {
                    "note_path": "Notas/clave.md",
                    "heading": "Resumen",
                    "relevance": 0.91,
                    "content": "contenido de prueba",
                }
            ],
            "notes_found": ["Notas/clave.md"],
        }

    monkeypatch.setattr(mcp_server.vector_db, "query", fake_query)
    texto = await mcp_server.buscar_en_la_boveda("prueba")
    assert "Notas/clave.md" in texto
    assert "91%" in texto


async def test_buscar_en_la_boveda_vacia(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_query(query: str, top_k: int = 5) -> dict[str, Any]:
        return {"success": True, "results": []}

    monkeypatch.setattr(mcp_server.vector_db, "query", fake_query)
    texto = await mcp_server.buscar_en_la_boveda("prueba")
    assert "Sin resultados" in texto


async def test_buscar_en_la_boveda_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_query(query: str, top_k: int = 5) -> dict[str, Any]:
        raise RuntimeError("chroma caido")

    monkeypatch.setattr(mcp_server.vector_db, "query", fake_query)
    texto = await mcp_server.buscar_en_la_boveda("prueba")
    assert "Error" in texto and "chroma" in texto


async def test_leer_nota(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_read(title: str, folder: str = "") -> dict[str, Any]:
        return {"success": True, "content": "# Hola\nlinea"}

    monkeypatch.setattr(mcp_server.obsidian_manager, "read_note", fake_read)
    assert "# Hola" in await mcp_server.leer_nota("Hola")


async def test_leer_nota_no_existe(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_read(title: str, folder: str = "") -> dict[str, Any]:
        return {"success": False, "message": "No encontré la nota."}

    monkeypatch.setattr(mcp_server.obsidian_manager, "read_note", fake_read)
    assert "No encontré" in await mcp_server.leer_nota("nada")


async def test_buscar_historial(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_server.settings, "admin_ids", [42])

    async def fake_search(
        chat_id: int, query: str, days: int = 30, limit: int = 20
    ) -> list[dict[str, Any]]:
        assert chat_id == 42
        return [{"role": "user", "content": "recuerda esto", "created_at": "2026-10-07"}]

    monkeypatch.setattr(mcp_server.db, "search_chat_history", fake_search)
    texto = await mcp_server.buscar_historial("recuerda")
    assert "recuerda esto" in texto


async def test_buscar_historial_vacio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_server.settings, "admin_ids", [42])

    async def fake_search(
        chat_id: int, query: str, days: int = 30, limit: int = 20
    ) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(mcp_server.db, "search_chat_history", fake_search)
    assert "Sin coincidencias" in await mcp_server.buscar_historial("nada")


async def test_buscar_en_la_web(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
        return [{"title": "T", "url": "https://t", "snippet": "s"}]

    monkeypatch.setattr(mcp_server, "search_duckduckgo", fake_search)
    texto = await mcp_server.buscar_en_la_web("algo")
    assert "https://t" in texto


async def test_buscar_en_la_web_vacia(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
        return []

    monkeypatch.setattr(mcp_server, "search_duckduckgo", fake_search)
    assert "Sin resultados" in await mcp_server.buscar_en_la_web("algo")


async def test_fetch_pagina_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(url: str, max_chars: int = 0) -> dict[str, Any]:
        return {"success": True, "method": "obscura", "text": "# Pagina"}

    monkeypatch.setattr(mcp_server, "fetch_page", fake_fetch)
    texto = await mcp_server.fetch_pagina("https://example.com")
    assert "Obscura" in texto and "# Pagina" in texto


async def test_fetch_pagina_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(url: str, max_chars: int = 0) -> dict[str, Any]:
        return {"success": False, "message": "URL no valida."}

    monkeypatch.setattr(mcp_server, "fetch_page", fake_fetch)
    assert "URL no valida" in await mcp_server.fetch_pagina("foo")


async def test_listar_y_cargar_skills() -> None:
    texto = await mcp_server.listar_skills()
    assert "captura-segundo-cerebro" in texto
    cuerpo = await mcp_server.cargar_skill("captura-segundo-cerebro")
    assert "captura" in cuerpo.lower()


async def test_cargar_skill_inexistente() -> None:
    assert "No existe" in await mcp_server.cargar_skill("no-existe-xyz")


async def test_start_mcp_server_arranca_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_server.settings, "mcp_token", "tok")
    visto: dict[str, Any] = {}

    class FakeConfig:
        def __init__(self, app: Any = None, **kwargs: Any) -> None:
            visto.update(kwargs)

    class FakeServer:
        def __init__(self, config: Any) -> None:
            visto["server"] = True

        async def serve(self) -> None:
            visto["served"] = True

    fake = types.SimpleNamespace(Config=FakeConfig, Server=FakeServer)
    monkeypatch.setitem(sys.modules, "uvicorn", fake)
    await mcp_server.start_mcp_server(host="127.0.0.1", port=18020)
    assert visto.get("served") is True
    assert visto.get("port") == 18020
