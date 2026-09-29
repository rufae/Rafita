"""Auditoría del registro de tools (Bloque 1, 2026-09-29).

Garantiza que:
- cada tool anunciada al modelo tiene su rama en `_execute_tool` (y viceversa);
- los esquemas de function calling son válidos;
- los errores de tool son estructurados y serializables para el LLM.
"""

import ast
import json
from pathlib import Path

from src.handlers.chat_tools import TOOLS_DEFINITIONS

CHAT_PY = Path(__file__).resolve().parents[1] / "src" / "handlers" / "chat.py"


def _ramas_de_execute_tool() -> set[str]:
    tree = ast.parse(CHAT_PY.read_text(encoding="utf-8"))
    nombres: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_execute_tool"
        ):
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Compare)
                    and isinstance(sub.left, ast.Name)
                    and sub.left.id == "func_name"
                ):
                    for comp in sub.comparators:
                        if isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                            nombres.add(comp.value)
    return nombres


def test_todas_las_tools_tienen_rama():
    definidas = {t["function"]["name"] for t in TOOLS_DEFINITIONS}
    implementadas = _ramas_de_execute_tool()
    faltan = sorted(definidas - implementadas)
    assert not faltan, "Tools anunciadas sin implementar en _execute_tool: %s" % faltan


def test_no_hay_ramas_huerfanas():
    definidas = {t["function"]["name"] for t in TOOLS_DEFINITIONS}
    implementadas = _ramas_de_execute_tool()
    huerfanas = sorted(implementadas - definidas)
    assert not huerfanas, "Ramas en _execute_tool sin tool anunciada: %s" % huerfanas


def test_esquemas_de_tools_validos():
    for tool in TOOLS_DEFINITIONS:
        assert tool.get("type") == "function", "tool sin type=function"
        fn = tool["function"]
        assert fn.get("name"), "tool sin nombre"
        assert fn.get("description"), "tool %s sin descripcion" % fn.get("name")
        params = fn.get("parameters")
        assert isinstance(params, dict) and params.get("type") == "object", (
            "%s: parameters debe ser object" % fn["name"]
        )
        props = params.get("properties", {})
        assert isinstance(props, dict)
        for requerido in params.get("required", []):
            assert requerido in props, "%s: required '%s' no esta en properties" % (
                fn["name"],
                requerido,
            )


async def test_error_de_tool_es_estructurado_y_serializable(monkeypatch):
    import src.handlers.chat as chat_mod

    class _Google:
        is_ready = False

    async def boom(*args, **kwargs):
        raise RuntimeError("red caida")

    monkeypatch.setattr(chat_mod, "google_services", _Google())
    monkeypatch.setattr(chat_mod.db, "list_tasks", boom)
    res = await chat_mod._execute_tool(1, "manage_google_tasks", {"action": "list"})
    assert res["success"] is False
    assert res["error"] == "tool_exception"
    assert res["tool"] == "manage_google_tasks"
    json.dumps(res)  # serializable para el modelo


async def test_tool_desconocida_error_estructurado():
    import src.handlers.chat as chat_mod

    res = await chat_mod._execute_tool(1, "no_existe_esta_tool", {})
    assert res["success"] is False
    assert res["error"] == "unknown_tool"
    json.dumps(res)
