"""Servidor MCP (Model Context Protocol) de Rafita — SOLO LECTURA.

Publica una lista CURADA de herramientas propias del agente a clientes MCP
(opencode, Claude Desktop, Cursor...) mediante Streamable HTTP en `mcp_port`,
protegido con `Authorization: Bearer <MCP_TOKEN>` (comparacion constante) y
antirrebind DNS (Host restringido a loopback).

Decision de seguridad (leccion ClawHavoc: 341 skills maliciosas): Rafita NO
incluye un cliente MCP. No se ejecutan herramientas ni scripts de terceros;
este servidor solo expone capacidades ya auditadas del propio agente.
Arranque: main.py crea la tarea solo si MCP_ENABLED=true; sin token no arranca.
"""

import hmac
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from src.config import settings
from src.database import db
from src.logger import logger
from src.utils import obsidian_manager
from src.utils.page_fetch import fetch_page
from src.utils.skills_manager import get_skill, list_skills
from src.utils.vector_manager import vector_db
from src.utils.web_search import format_search_results, search_duckduckgo

_mcp = FastMCP(
    "Rafita AVP",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*"],
        allowed_origins=[],
    ),
)


def _chat_id_admin() -> int:
    return settings.admin_ids[0] if settings.admin_ids else 0


@_mcp.tool()
async def buscar_en_la_boveda(consulta: str) -> str:
    """Busca en el segundo cerebro (busqueda semantica RAG) y devuelve los
    fragmentos de nota mas relevantes con su ruta y relevancia."""
    try:
        result = await vector_db.query(consulta, top_k=5)
    except Exception as e:
        return f"Error consultando la boveda: {e}"
    if not result.get("success"):
        return f"Error consultando la boveda: {result.get('message', 'desconocido')}"
    if not result.get("results"):
        return "Sin resultados relevantes en la boveda para esa consulta."
    lineas = []
    for i, r in enumerate(result["results"], 1):
        ruta = r.get("note_path", r.get("source", "desconocido"))
        heading = r.get("heading", "")
        relevancia = r.get("relevance", "N/A")
        titulo = f"{ruta} ({heading})" if heading else str(ruta)
        fragmento = str(r.get("content", "")).strip()[:400]
        porcentaje = (
            f"{float(relevancia) * 100:.0f}%"
            if isinstance(relevancia, (int, float))
            else relevancia
        )
        lineas.append(f"{i}. {titulo} [{porcentaje}]\n   {fragmento}")
    return "\n\n".join(lineas)


@_mcp.tool()
async def leer_nota(titulo: str, carpeta: str = "") -> str:
    """Lee una nota de la boveda Obsidian por titulo (preview de 50 lineas).
    `carpeta` es opcional (ej. 'Briefings')."""
    res = await obsidian_manager.read_note(titulo, carpeta)
    if not res.get("success"):
        return str(res.get("message", "No se pudo leer la nota."))
    return str(res.get("content", ""))


@_mcp.tool()
async def buscar_historial(consulta: str, dias: int = 30) -> str:
    """Busca texto literal en el historial de chat del admin (ultimo mes por
    defecto)."""
    rows = await db.search_chat_history(_chat_id_admin(), consulta, days=dias)
    if not rows:
        return f"Sin coincidencias en el historial (ultimos {dias} dias)."
    return "\n\n".join(
        f"[{r.get('created_at', '')}] {r.get('role', '')}: {str(r.get('content', ''))[:300]}"
        for r in rows
    )


@_mcp.tool()
async def buscar_en_la_web(consulta: str) -> str:
    """Busca en internet (DuckDuckGo) y devuelve titulos, resumenes y fuentes."""
    results = await search_duckduckgo(consulta, max_results=5)
    if not results:
        return "Sin resultados en la web para esa consulta."
    return format_search_results(results)


@_mcp.tool()
async def fetch_pagina(url: str) -> str:
    """Lee el contenido real de una pagina web (renderiza JavaScript con
    Obscura, con modo estatico de respaldo). Solo lectura: no rellena
    formularios ni pulsa botones."""
    res = await fetch_page(url)
    if not res.get("success"):
        return str(res.get("message", "No pude leer la pagina."))
    metodo = (
        "Obscura" if res.get("method") == "obscura" else "HTML estatico (Obscura no disponible)"
    )
    return f"[{metodo}]\n\n{res.get('text', '')}"


@_mcp.tool()
async def listar_skills() -> str:
    """Lista las skills (procedimientos en Markdown) disponibles de Rafita."""
    skills = list_skills()
    if not skills:
        return "No hay skills registradas."
    return "\n".join(f"- {s['name']}: {s['description']}" for s in skills)


@_mcp.tool()
async def cargar_skill(nombre: str) -> str:
    """Devuelve el contenido completo de una skill por nombre."""
    skill = get_skill(nombre)
    if not skill:
        return f"No existe la skill '{nombre}'. Usa listar_skills para ver las disponibles."
    return f"# {skill['name']}\n{skill['description']}\n\n{skill['body']}"


class BearerTokenMiddleware:
    """ASGI: exige `Authorization: Bearer <MCP_TOKEN>` en cada request HTTP."""

    def __init__(self, app: Any, token: str) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        if scope["method"] == "OPTIONS":
            await self._app(scope, receive, send)
            return
        auth = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")
        esperado = f"Bearer {self._token}"
        if not auth or not hmac.compare_digest(auth, esperado):
            await self._no_autorizado(send)
            return
        await self._app(scope, receive, send)

    @staticmethod
    async def _no_autorizado(send: Any) -> None:
        body = b'{"error":"unauthorized","message":"Se requiere Authorization: Bearer <MCP_TOKEN>"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def create_app() -> Any:
    """App ASGI (Starlette del transporte + auth). Falla sin token."""
    if not settings.mcp_token:
        raise RuntimeError("MCP_ENABLED=true pero MCP_TOKEN esta vacio: sin token no arranca.")
    return BearerTokenMiddleware(_mcp.streamable_http_app(), settings.mcp_token)


async def start_mcp_server(host: str = "0.0.0.0", port: int | None = None) -> None:
    import uvicorn

    app = create_app()
    puerto = port if port is not None else settings.mcp_port
    config_obj = uvicorn.Config(
        app,
        host=host,
        port=puerto,
        log_level="info",
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config_obj)
    logger.info("MCP server escuchando en %s:%d/mcp (solo lectura, token Bearer)", host, puerto)
    await server.serve()
