"""Core orchestrator — shared AI response pipeline for Telegram and Voice.

Both the Telegram bot (chat.py) and the voice stream (voice_stream/server.py)
consume this module to ensure consistent behavior: same system prompt, same
tool definitions, same RAG access (search_second_brain), same model.

This avoids duplicating the prompt/rules/tools logic across interfaces.
"""

import asyncio
import json
import time as _time

from src.database import db
from src.handlers.chat_tools import TOOLS_DEFINITIONS
from src.logger import logger
from src.models.schemas import MessageRole
from src.ollama_client import OllamaClientError, llm
from src.utils.telemetry import get_correlation_id, metrics

SYSTEM_PROMPT_VOICE = (
    "STRICT_LANGUAGE_RULE: Tu idioma es EXCLUSIVAMENTE el espanol. "
    "Queda prohibido el uso de caracteres chinos, japoneses o ingles.\n"
    "SECOND_BRAIN_RULE: Tienes acceso al segundo cerebro del usuario a traves de "
    "search_second_brain. DEBES usarlo antes de responder cualquier pregunta que "
    "pueda estar relacionada con informacion personal del usuario: proyectos, "
    "finanzas, notas tecnicas, diario, ideas, apuntes. NO improvises datos "
    "personales. Siempre cita la fuente exacta de la nota (note_path) en tu "
    "respuesta para que el usuario pueda verificar. Si no encuentras nada en "
    "el segundo cerebro, dilo claramente y ofrece buscar en internet.\n"
    "PROACTIVE_BRAIN_RULE: Eres el guardian del segundo cerebro. Cuando en una "
    "conversacion el usuario mencione una IDEA NUEVA, una DECISION IMPORTANTE, "
    "un DATO PERSONAL RELEVANTE, o un APRENDIZAJE TECNICO, debes guardarlo "
    "PROACTIVAMENTE en Obsidian usando manage_obsidian_note (create) o "
    "ingest_file SIN que el usuario te lo pida. Usa estas carpetas:\n"
    "- Ideas y conceptos nuevos -> 05-Zettelkasten/ (tipo: nota-atomica)\n"
    "- Decisiones de proyecto -> 01-Proyectos/ (tipo: proyecto)\n"
    "- Datos personales (salud, preferencias) -> 02-Areas/ (tipo: area)\n"
    "- Aprendizajes tecnicos -> 03-Recursos/ (tipo: recurso)\n"
    "Confirma brevemente: 'He guardado esto en tu segundo cerebro'.\n"
    "Eres Rafita, un asistente virtual personal. "
    "Responde en espanol de forma conversacional, clara y concisa. "
    "Tienes acceso a herramientas que debes invocar automaticamente cuando "
    "el usuario lo necesite.\n\n"
    "Herramientas disponibles:\n"
    "- search_second_brain (busqueda semantica con citacion)\n"
    "- ask_deep_knowledge_base (segundo cerebro completo)\n"
    "- save_expense / get_finance_summary\n"
    "- manage_obsidian_note / search_obsidian_vault\n"
    "- search_web / remember_fact / search_knowledge\n"
    "- create_event / create_alert\n"
    "- manage_google_calendar / create_google_calendar_event\n"
    "- generate_google_auth_link / save_google_verification_code\n\n"
    "Cuando invoques una herramienta, confirma al usuario lo realizado de forma breve. "
    "Responde siempre en espanol."
)


async def generate_response(text: str, chat_id: int) -> str:
    """Generate an AI response with tools and RAG for any interface.

    Offers tools to the LLM in a single call. If the model decides to invoke
    a tool (search_second_brain, save_expense, etc.), a second LLM call is
    made with the tool results. For greetings or small talk with no tool
    invocations, only 1 LLM call is made.
    """
    await db.save_chat_message(chat_id, MessageRole.user.value, text)

    history = await db.get_chat_history(chat_id, 6)
    trimmed_history = []
    for h in history:
        c = h.get("content", "")
        trimmed_history.append(
            {
                "role": h["role"],
                "content": c[:500] + ("..." if len(c) > 500 else ""),
            }
        )

    messages_for_llm = [{"role": "system", "content": SYSTEM_PROMPT_VOICE}]
    for msg in trimmed_history:
        messages_for_llm.append({"role": msg["role"], "content": msg["content"]})

    _t_start = _time.time()
    logger.info(
        "[ORCHESTRATOR] chat_id=%d cid=%s tools=%d history=%d chars=%d",
        chat_id,
        get_correlation_id(),
        len(TOOLS_DEFINITIONS),
        len(trimmed_history),
        len(text),
    )

    try:
        content, tool_calls = await asyncio.wait_for(
            llm.chat_with_tools(
                messages=messages_for_llm,
                tools=TOOLS_DEFINITIONS,
                max_tokens=512,
            ),
            timeout=600.0,
        )
    except TimeoutError:
        logger.error("[ORCHESTRATOR] timeout for chat_id=%d", chat_id)
        return (
            "Lo siento, el modelo tardo demasiado en responder. Intenta con un mensaje mas corto."
        )
    except OllamaClientError as e:
        logger.error("[ORCHESTRATOR] Ollama error: %s", e)
        return f"Error del modelo: {e}"
    except Exception:
        logger.exception("[ORCHESTRATOR] AI error for chat_id=%d", chat_id)
        return "Error interno al procesar la respuesta."

    _elapsed = _time.time() - _t_start
    metrics.observe("llm_chat_latency", _elapsed)
    logger.info(
        "[ORCHESTRATOR] response in %.1fs content_len=%d tool_calls=%s",
        _elapsed,
        len(content or ""),
        bool(tool_calls),
    )

    if tool_calls:
        results = []
        for tc in tool_calls:
            func_name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, KeyError):
                args = {}
            logger.info(
                "[ORCHESTRATOR] Tool call: chat_id=%d tool=%s args=%s",
                chat_id,
                func_name,
                str(args)[:200],
            )
            from src.handlers.chat import _execute_tool

            result = await _execute_tool(chat_id, func_name, args)
            results.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id", func_name),
                    "name": func_name,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

        if results:
            messages_for_llm.append(
                {
                    "role": "assistant",
                    "content": content or "",
                    "tool_calls": tool_calls,
                }
            )
            messages_for_llm.extend(results)

            try:
                content, _ = await asyncio.wait_for(
                    llm.chat_with_tools(
                        messages=messages_for_llm,
                        tools=TOOLS_DEFINITIONS,
                        max_tokens=512,
                    ),
                    timeout=600.0,
                )
            except Exception:
                content = "Consulta completada. Revisa el resultado de las herramientas."

    if content:
        await db.save_chat_message(chat_id, MessageRole.assistant.value, content[:2000])

    return content or "No pude generar una respuesta."
