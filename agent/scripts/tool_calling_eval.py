"""Tool-calling evaluation with a real LLM (task 1.7).

Sends one Spanish prompt per tool to the configured Ollama model using the
production `TOOLS_DEFINITIONS`, checks whether the expected tool was invoked
with valid arguments and, in an isolated temp environment, executes it and
records the result. Includes negative controls (prompts that should not call
any tool).

Usage (inside the project image, with Ollama reachable):
    OLLAMA_HOST=http://127.0.0.1:11435 OLLAMA_MODEL=gemma4:12b \
    EMBEDDING_MODEL=bge-m3 python agent/scripts/tool_calling_eval.py \
        --attempts 2 --json /tmp/tool_eval.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

EVAL_VAULT_PATH = AGENT_DIR / "tests" / "rag_eval" / "vault"

AUTH_TOOLS = {
    "generate_google_auth_link",
    "save_google_verification_code",
    "get_google_calendar_events",
    "create_google_calendar_event",
    "manage_google_calendar",
}

# The audit flagged redundant tools. These sets encode functional equivalence:
# any of the alternatives satisfies the user intent, so picking one of them is
# not a tool-selection failure.
RAG_TOOLS = ["ask_deep_knowledge_base", "search_second_brain", "search_obsidian_vault"]
GOOGLE_LIST_TOOLS = ["get_google_calendar_events", "manage_google_calendar"]
GOOGLE_CREATE_TOOLS = ["create_google_calendar_event", "manage_google_calendar"]

CASES: list[dict[str, Any]] = [
    {
        "id": "save_expense",
        "tool": "save_expense",
        "prompt": "Gasté 45 euros en gasolina esta mañana.",
    },
    {
        "id": "create_event",
        "tool": "create_event",
        "prompt": "Apúntame una cita con el dentista el 3 de octubre a las 17:00.",
    },
    {
        "id": "create_alert",
        "tool": "create_alert",
        "prompt": "Recuérdame que tengo que renovar el DNI, es urgente.",
    },
    {
        "id": "get_finance_summary",
        "tool": "get_finance_summary",
        "prompt": "¿Cuánto llevo gastado este mes?",
    },
    {
        "id": "remember_fact",
        "tool": "remember_fact",
        "prompt": "Guarda que mi color favorito es el azul.",
    },
    {
        "id": "search_knowledge",
        "tool": "search_knowledge",
        "prompt": "¿Qué datos personales tienes guardados sobre mí?",
    },
    {
        "id": "search_web",
        "tool": "search_web",
        "prompt": "Busca en internet el precio actual del bitcoin.",
    },
    {
        "id": "manage_obsidian_note",
        "tool": "manage_obsidian_note",
        "prompt": "Crea una nota en 00-Inbox titulada Ideas de verano con el texto: probar escalada.",
    },
    {
        "id": "search_obsidian_vault",
        "tool": "search_obsidian_vault",
        "prompt": "Busca en Obsidian dónde hablo de presupuesto.",
        "accept": RAG_TOOLS,
    },
    {
        "id": "inspect_project_files",
        "tool": "inspect_project_files",
        "prompt": "Muéstrame los archivos del proyecto en agent/src.",
    },
    {
        "id": "analyze_system_logs",
        "tool": "analyze_system_logs",
        "prompt": "¿Cómo está el sistema? ¿Ha habido errores?",
    },
    {
        "id": "move_or_rename_file",
        "tool": "move_or_rename_file",
        "prompt": "Mueve la nota Ideas de verano de 00-Inbox a 05-Zettelkasten.",
    },
    {
        "id": "ask_deep_knowledge_base",
        "tool": "ask_deep_knowledge_base",
        "prompt": "¿Qué información guardo en mis apuntes sobre el huerto urbano?",
        "accept": RAG_TOOLS,
    },
    {
        "id": "search_second_brain",
        "tool": "search_second_brain",
        "prompt": "¿Qué tengo apuntado en mis notas sobre mi alergia?",
        "accept": RAG_TOOLS,
    },
    {
        "id": "manage_google_calendar",
        "tool": "manage_google_calendar",
        "prompt": "Mira qué eventos tengo en Google Calendar.",
        "accept": GOOGLE_LIST_TOOLS,
    },
    {
        "id": "set_recurring_reminder",
        "tool": "set_recurring_reminder",
        "prompt": "Recuérdame cada lunes a las 9:00 hacer la compra.",
    },
    {
        "id": "generate_google_auth_link",
        "tool": "generate_google_auth_link",
        "prompt": "Quiero conectar mi cuenta de Google, ¿me das el enlace de autorización?",
    },
    {
        "id": "save_google_verification_code",
        "tool": "save_google_verification_code",
        "prompt": "El código que me ha dado Google es 4/0Aabc123def.",
    },
    {
        "id": "get_google_calendar_events",
        "tool": "get_google_calendar_events",
        "prompt": "¿Qué tengo mañana en mi agenda de Google?",
    },
    {
        "id": "create_google_calendar_event",
        "tool": "create_google_calendar_event",
        "prompt": "Añade a mi Google Calendar una reunión el 2 de octubre a las 10:00.",
        "accept": GOOGLE_CREATE_TOOLS,
    },
    {
        "id": "ingest_file",
        "tool": "ingest_file",
        "prompt": "Registra en el segundo cerebro el archivo presupuesto_2026.pdf de la carpeta 02-Areas/Finanzas.",
    },
]

NEGATIVE_CASES: list[dict[str, Any]] = [
    {"id": "no_tool_greeting", "tool": None, "prompt": "Hola, buenos días."},
    {"id": "no_tool_thanks", "tool": None, "prompt": "Gracias, hasta luego."},
]


def required_args_by_tool(tools: list[dict[str, Any]]) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for tool in tools:
        fn = tool["function"]
        mapping[fn["name"]] = list(fn.get("parameters", {}).get("required", []))
    return mapping


def score_attempt(
    expected: str | None,
    called: list[str],
    args_ok: bool,
    accept: list[str] | None = None,
) -> dict[str, Any]:
    """Classify one attempt: correct tool with valid args, wrong tool or no tool.

    `accept` lists functionally equivalent tools (redundant definitions): any
    of them satisfies the user intent, so selecting one is not a failure.
    """
    if expected is None:
        if called:
            return {"correct": False, "mode": "unexpected_tool"}
        return {"correct": True, "mode": "ok"}
    acceptable = {expected, *(accept or [])}
    if any(name in acceptable for name in called):
        if args_ok:
            return {"correct": True, "mode": "ok"}
        return {"correct": False, "mode": "invalid_args"}
    if called:
        return {"correct": False, "mode": "wrong_tool"}
    return {"correct": False, "mode": "no_tool"}


def _args_ok(name: str, raw_args: str, required: dict[str, list[str]]) -> bool:
    try:
        parsed = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict):
        return False
    return all(key in parsed for key in required.get(name, []))


def _prepare_isolated_env() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="tool_eval_"))
    from src.config import settings

    settings.data_dir = str(tmp / "data")
    settings.db_path = str(tmp / "data" / "db" / "rafita.db")
    settings.excel_dir = str(tmp / "data" / "excels")
    settings.export_dir = str(tmp / "data" / "exports")
    settings.log_dir = str(tmp / "data" / "logs")
    settings.obsidian_vault_dir = str(tmp / "vault")
    settings.vector_db_dir = str(tmp / "vector_db")
    shutil.copytree(EVAL_VAULT_PATH, tmp / "vault")

    from src.utils import obsidian_manager

    obsidian_manager.OBSIDIAN_VAULT = tmp / "vault"
    return tmp


async def _setup_runtime(tmp: Path) -> None:
    from src.config import settings
    from src.database import db
    from src.ollama_client import llm
    from src.utils import vault_indexer as vi
    from src.utils.obsidian_manager import initialize_vault_structure
    from src.utils.vector_manager import vector_db

    await initialize_vault_structure()
    await db.initialize()
    await llm.initialize()
    await vector_db.initialize()
    vi.VAULT_PATH = Path(settings.obsidian_vault_dir)
    vi.VAULT_NAME = "tool_eval"
    await vi.VaultIndexer().index_all()


async def run_case(llm: Any, case: dict[str, Any], attempts: int, execute: bool) -> dict[str, Any]:
    from src.core.orchestrator import SYSTEM_PROMPT_VOICE
    from src.handlers.chat import TOOLS_DEFINITIONS, _execute_tool

    required = required_args_by_tool(TOOLS_DEFINITIONS)
    results: list[dict[str, Any]] = []
    for _ in range(attempts):
        content, tool_calls = await llm.chat_with_tools(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_VOICE},
                {"role": "user", "content": case["prompt"]},
            ],
            tools=TOOLS_DEFINITIONS,
            max_tokens=512,
        )
        calls = tool_calls or []
        called = [call["function"]["name"] for call in calls]
        accept = case.get("accept")
        args_ok = True
        if called:
            first = calls[0]["function"]
            args_ok = _args_ok(first["name"], first.get("arguments", ""), required)
        score = score_attempt(case["tool"], called, args_ok, accept)
        acceptable = {case["tool"], *(accept or [])} if case["tool"] else set()
        execution: dict[str, Any] | None = None
        if execute and called and called[0] in acceptable:
            function = calls[0]["function"]
            call_name = function["name"]
            try:
                args = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if call_name in AUTH_TOOLS:
                execution = {"skipped": "auth tool (no credentials in eval)"}
            else:
                result = await _execute_tool(999001, call_name, args)
                execution = {
                    "success": bool(result.get("success")),
                    "message": str(result.get("message", ""))[:160],
                }
        results.append(
            {
                "called": called,
                "args_ok": args_ok,
                **score,
                "execution": execution,
                "content_head": (content or "")[:80],
            }
        )
    return {
        "id": case["id"],
        "expected": case["tool"],
        "prompt": case["prompt"],
        "attempts": results,
    }


def build_report(cases_results: list[dict[str, Any]]) -> dict[str, Any]:
    per_tool = []
    total_correct = 0
    total_attempts = 0
    failure_modes: dict[str, int] = {}
    for case in cases_results:
        attempts = case["attempts"]
        correct = sum(1 for attempt in attempts if attempt["correct"])
        total_correct += correct
        total_attempts += len(attempts)
        for attempt in attempts:
            if not attempt["correct"]:
                failure_modes[attempt["mode"]] = failure_modes.get(attempt["mode"], 0) + 1
        per_tool.append(
            {
                "id": case["id"],
                "expected": case["expected"],
                "correct": correct,
                "attempts": len(attempts),
                "rate": correct / len(attempts) if attempts else 0.0,
            }
        )
    return {
        "total_correct": total_correct,
        "total_attempts": total_attempts,
        "overall_rate": total_correct / total_attempts if total_attempts else 0.0,
        "failure_modes": failure_modes,
        "per_tool": per_tool,
        "cases": cases_results,
    }


async def main_async(attempts: int, execute: bool) -> dict[str, Any]:
    from src.config import settings
    from src.handlers.chat import TOOLS_DEFINITIONS
    from src.ollama_client import llm

    tmp = _prepare_isolated_env()
    await _setup_runtime(tmp)

    chars = len(json.dumps(TOOLS_DEFINITIONS, ensure_ascii=False))
    print(
        "model=%s embedding=%s tools=%d tools_json_chars=%d"
        % (settings.ollama_model, settings.embedding_model, len(TOOLS_DEFINITIONS), chars)
    )

    results = []
    for case in CASES + NEGATIVE_CASES:
        case_result = await run_case(llm, case, attempts, execute)
        results.append(case_result)
        ok = all(attempt["correct"] for attempt in case_result["attempts"])
        print(
            "[%s] %-26s called=%s"
            % (
                "OK  " if ok else "FAIL",
                case["id"],
                [attempt["called"] for attempt in case_result["attempts"]],
            ),
            flush=True,
        )
    report = build_report(results)

    print("\ntool                          correct/attempts  rate")
    for row in report["per_tool"]:
        print(
            "%-28s %d/%d              %5.0f%%"
            % (row["id"], row["correct"], row["attempts"], row["rate"] * 100)
        )
    print(
        "\nOVERALL: %d/%d (%.0f%%) failure_modes=%s"
        % (
            report["total_correct"],
            report["total_attempts"],
            report["overall_rate"] * 100,
            report["failure_modes"],
        )
    )

    for case in report["cases"]:
        for index, attempt in enumerate(case["attempts"], 1):
            if not attempt["correct"]:
                print(
                    "[FAIL] %s attempt %d expected=%s called=%s mode=%s args_ok=%s exec=%s"
                    % (
                        case["id"],
                        index,
                        case["expected"],
                        attempt["called"],
                        attempt["mode"],
                        attempt["args_ok"],
                        attempt["execution"],
                    )
                )

    from src.database import db
    from src.ollama_client import llm as llm_client

    await db.close()
    await llm_client.close()
    report["tools_json_chars"] = chars
    report["model"] = settings.ollama_model
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Tool-calling evaluation with a real LLM")
    parser.add_argument("--attempts", type=int, default=2)
    parser.add_argument("--no-execute", action="store_true")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    report = asyncio.run(main_async(args.attempts, execute=not args.no_execute))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print("Saved to %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
