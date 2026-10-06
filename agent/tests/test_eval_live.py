"""Eval suite live (Fase 1): seleccion de herramientas con el LLM real.

Requiere Ollama disponible y ``EVAL_LIVE=1``::

    EVAL_LIVE=1 OLLAMA_HOST=http://127.0.0.1:11434 pytest -m eval \\
        agent/tests/test_eval_live.py -v

Al terminar escribe el informe de aciertos en
``daily-work/eval_report.json`` (offline + live de la sesion).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.core import orchestrator as orch
from src.handlers.chat_tools import TOOLS_DEFINITIONS

REPO_ROOT = Path(__file__).resolve().parents[2]
CASOS_PATH = REPO_ROOT / "evals" / "cases" / "pipeline.yaml"

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(
        os.getenv("EVAL_LIVE") != "1",
        reason="eval live: requiere EVAL_LIVE=1 y Ollama (pytest -m eval)",
    ),
]

CASOS = [
    c
    for c in yaml.safe_load(CASOS_PATH.read_text(encoding="utf-8"))["casos"]
    if c.get("modo") == "live"
]


@pytest.fixture(scope="session")
def informe_eval() -> Any:
    resultados: list[dict[str, Any]] = []
    yield resultados
    salida = REPO_ROOT / "daily-work" / "eval_report.json"
    try:
        salida.parent.mkdir(parents=True, exist_ok=True)
        aciertos = sum(1 for r in resultados if r.get("ok"))
        salida.write_text(
            json.dumps(
                {
                    "total": len(resultados),
                    "aciertos": aciertos,
                    "precision": round(aciertos / len(resultados), 3) if resultados else None,
                    "casos": resultados,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


@pytest.mark.parametrize("caso", CASOS, ids=[c["id"] for c in CASOS])
async def test_caso_live(caso: dict[str, Any], informe_eval: list[dict[str, Any]]) -> None:
    esperado = caso["esperado"]["tool"]
    alternativas: list[str] = caso["esperado"].get("alternativas", [])
    try:
        _content, tool_calls = await orch.llm.chat_with_tools(
            messages=[
                {"role": "system", "content": orch.build_system_prompt()},
                {"role": "user", "content": "%s %s" % (orch.date_context_line(), caso["entrada"])},
            ],
            tools=TOOLS_DEFINITIONS,
            max_tokens=512,
        )
        nombres = [tc["function"]["name"] for tc in (tool_calls or [])]
    except Exception as exc:  # sin Ollama o modelo caido: fallo explicito
        informe_eval.append({"id": caso["id"], "ok": False, "error": str(exc)[:200]})
        pytest.fail("eval live sin poder ejecutar: %s" % exc)

    if esperado == "NINGUNA":
        ok = not nombres
    else:
        ok = esperado in nombres or any(a in nombres for a in alternativas)
    informe_eval.append({"id": caso["id"], "ok": ok, "esperado": esperado, "obtenido": nombres})
    assert ok, "%s: esperaba %s, obtuve %s" % (caso["id"], esperado, nombres)
