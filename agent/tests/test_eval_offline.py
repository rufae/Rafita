"""Eval suite offline (Fase 1, mejoras.md §5).

Ejecuta los casos `modo: offline` de ``evals/cases/pipeline.yaml`` contra el
pipeline real con un LLM guionizado: guardias anti-alucinacion, reintento
honesto, argumentos de herramienta y permisos de automatizaciones. Corre en
cada CI (determinista, sin Ollama). El informe de aciertos de la sesion lo
escribe el runner live (``test_eval_live.py``) en ``daily-work/``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.core import orchestration as orch_auto
from src.core import orchestrator as orch

REPO_ROOT = Path(__file__).resolve().parents[2]
CASOS_PATH = REPO_ROOT / "evals" / "cases" / "pipeline.yaml"


def _casos_offline() -> list[dict[str, Any]]:
    datos = yaml.safe_load(CASOS_PATH.read_text(encoding="utf-8"))
    return [c for c in datos["casos"] if c.get("modo") == "offline"]


CASOS = _casos_offline()


class _FakeLLM:
    """LLM guionizado: cada llamada consume un paso de ``guion``."""

    def __init__(self, guion: list[dict[str, Any]]) -> None:
        self._guion = guion
        self.calls: list[dict[str, Any]] = []

    async def chat_with_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], max_tokens: int = 512
    ) -> tuple[str | None, list[dict[str, Any]] | None]:
        paso = self._guion[min(len(self.calls), len(self._guion) - 1)]
        self.calls.append(paso)
        return paso.get("content"), paso.get("tool_calls")

    async def chat(self, messages: list[dict[str, Any]], max_tokens: int = 512) -> str:
        return "NINGUNA"


def _patch_comun(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    async def guardar(*args: Any, **kwargs: Any) -> None:
        return None

    async def historial(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def select_tools(text: str) -> list[dict[str, Any]]:
        return []

    async def best_tools(text: str, k: int = 3) -> tuple[list[dict[str, Any]], float]:
        return [], 0.0

    ejecutados: list[tuple[str, dict[str, Any]]] = []

    async def fake_execute(chat_id: int, func_name: str, args: dict[str, Any]) -> dict[str, Any]:
        ejecutados.append((func_name, args))
        return {"success": True, "message": "ok"}

    monkeypatch.setattr(orch.db, "save_chat_message", guardar)
    monkeypatch.setattr(orch.db, "get_chat_history", historial)
    monkeypatch.setattr(orch, "select_tools_semantic", select_tools)
    monkeypatch.setattr(orch, "best_tools_for_message", best_tools)
    monkeypatch.setattr("src.handlers.chat._execute_tool", fake_execute)
    return ejecutados


def _args_ok(obtenido: dict[str, Any], esperado: dict[str, Any]) -> bool:
    return all(obtenido.get(k) == v for k, v in esperado.items())


@pytest.mark.parametrize("caso", CASOS, ids=[c["id"] for c in CASOS])
async def test_caso_offline(caso: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    tipo = caso["tipo"]
    esperado: dict[str, Any] = caso["esperado"]

    if tipo == "hallucination_risk":
        riesgo = orch._hallucination_risk(caso["content"], caso["entrada"])
        assert riesgo is esperado["riesgo"], caso["id"]
        return

    if tipo == "automation_permission":
        spec = orch_auto.resolve(caso["clave"])
        assert spec is not None, caso["id"]
        dec = orch_auto.authorize(spec, confirm=caso["confirm"])
        for clave, valor in esperado.items():
            assert dec.get(clave) == valor, "%s: %s" % (caso["id"], clave)
        return

    if tipo == "automation_resolve":
        assert orch_auto.resolve(caso["clave"]) is esperado["resolve"], caso["id"]
        return

    assert tipo in {"honesty_retry", "negative_no_tool", "tool_args"}, tipo
    ejecutados = _patch_comun(monkeypatch)
    llm = _FakeLLM(caso["guion"])
    monkeypatch.setattr(orch, "llm", llm)

    _, content, tool_calls, _ = await orch._prepare_tool_phase(caso["entrada"], 1)

    assert len(llm.calls) == esperado["llamadas"], "%s: llamadas" % caso["id"]

    if tipo == "negative_no_tool":
        assert tool_calls == [], caso["id"]
        assert ejecutados == [], caso["id"]
        assert esperado["content_contenga"] in (content or ""), caso["id"]
        return

    esperados_ejec = esperado["ejecutados"]
    assert len(ejecutados) == len(esperados_ejec), "%s: ejecuciones" % caso["id"]
    for obtenido, esperado_e in zip(ejecutados, esperados_ejec):
        assert obtenido[0] == esperado_e["tool"], caso["id"]
        assert _args_ok(obtenido[1], esperado_e.get("args", {})), "%s: args" % caso["id"]
    if tipo == "tool_args":
        assert tool_calls, caso["id"]
        assert json.loads(tool_calls[0]["function"]["arguments"]) is not None
