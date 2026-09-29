"""Core orchestrator — shared AI response pipeline for Telegram and Voice.

Both the Telegram bot (chat.py) and the voice stream (voice_stream/server.py)
consume this module to ensure consistent behavior: same system prompt, same
tool definitions, same RAG access (search_second_brain), same model.

This avoids duplicating the prompt/rules/tools logic across interfaces.
"""

import asyncio
import json
import re
import time as _time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.config import settings
from src.database import db
from src.handlers.chat_tools import TOOLS_DEFINITIONS, select_tools_semantic
from src.i18n import language_name, language_rule, reply_instruction
from src.logger import logger
from src.models.schemas import MessageRole
from src.ollama_client import OllamaClientError, llm
from src.services.google_services_manager import google_services
from src.utils.telemetry import get_correlation_id, metrics
from src.vault_config import get_taxonomy

# Afirmaciones de accion sin herramienta (alucinaciones 2026-09-29: la llamada
# dijo "Buscando un correo..." sin buscar y el chat dijo "He guardado la tarea"
# sin guardarla). Si el modelo afirma una accion y no hubo tool_calls, se
# reintenta una vez obligandole a usar la herramienta o a desdecirse.
_ACTION_CLAIM_RE = re.compile(
    r"\b(?:he guardado|he creado|he a[nñ]adido|he apuntado|he anotado|he tomado nota|"
    r"he enviado|he completado|he registrado|he encontrado|tom[oé] nota|"
    r"guardando|buscando|search\w*|busc\w+|encontr[eé]|"
    r"enviando|apuntando|anotando|completando|un momento|te muestro|te busco|"
    r"aqu[ií] tienes|resultados?)\b",
    re.IGNORECASE,
)
# Correo electronico en la respuesta: si no lo dijo el usuario, es inventado.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

HONEST_FALLBACK = (
    "No he podido comprobar esa informacion con mis herramientas y no quiero "
    "darte datos inventados. ¿Puedes darme mas detalle o lo intento de otra forma?"
)


def _hallucination_risk(content: str, user_text: str) -> bool:
    """True si la respuesta afirma acciones o cita correos que no vinieron de tools."""
    if _ACTION_CLAIM_RE.search(content or ""):
        return True
    texto_usuario = (user_text or "").lower()
    for match in _EMAIL_RE.finditer(content or ""):
        if match.group().lower() not in texto_usuario:
            return True
    return False


SYSTEM_PROMPT_VOICE = (
    f"{language_rule()}"
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
    f"- Ideas y conceptos nuevos -> {get_taxonomy().path('zettelkasten')}/ (tipo: nota-atomica)\n"
    f"- Decisiones de proyecto -> {get_taxonomy().path('projects')}/ (tipo: proyecto)\n"
    f"- Datos personales (salud, preferencias) -> {get_taxonomy().path('areas')}/ (tipo: area)\n"
    f"- Aprendizajes tecnicos -> {get_taxonomy().path('resources')}/ (tipo: recurso)\n"
    "Confirma brevemente: 'He guardado esto en tu segundo cerebro'.\n"
    f"Eres {settings.assistant_name}, un asistente virtual personal. "
    f"Responde en {language_name()} de forma conversacional, clara y concisa. "
    "Tienes acceso a herramientas que debes invocar automaticamente cuando "
    "el usuario lo necesite.\n\n"
    "Herramientas disponibles:\n"
    "- search_second_brain (busqueda semantica con citacion)\n"
    "- ask_deep_knowledge_base (segundo cerebro completo)\n"
    "- save_expense / get_finance_summary\n"
    "- manage_obsidian_note / search_obsidian_vault\n"
    "- search_web / remember_fact / search_knowledge\n"
    "- create_event / create_alert / manage_google_calendar\n"
    "- manage_google_tasks (tareas: guardar, listar, completar)\n"
    "- search_gmail / send_gmail (correo)\n"
    "- find_contact (telefonos y correos de contactos)\n"
    "- manage_crm (clientes y pipeline)\n"
    "- search_google_drive / read_google_drive_file\n"
    "- generate_google_auth_link / save_google_verification_code\n\n"
    "Cuando invoques una herramienta, confirma al usuario lo realizado de forma breve. "
    f"{reply_instruction()}"
)


def date_context_line() -> str:
    """Línea de contexto con la fecha/hora real (se inyecta en el mensaje)."""
    try:
        now = datetime.now(ZoneInfo(settings.timezone))
    except Exception:
        now = datetime.now()
    return "[Contexto: hoy es %s (%s)]" % (
        now.strftime("%A %d/%m/%Y %H:%M"),
        settings.timezone,
    )


# Reglas de grounding compartidas por chat y voz (Bloque 1, 2026-09-29):
# el modelo solo puede afirmar lo que devuelvan las herramientas o el usuario.
GROUNDING_RULES = (
    "GROUNDING_RULE (critica): responde SOLO con informacion que provenga de la "
    "salida real de las herramientas o del mensaje del usuario. Si una "
    "herramienta devuelve una lista vacia, un error o no esta disponible, di "
    "exactamente que no hay resultados o que no pudiste comprobarlo; nunca "
    "completes huecos con suposiciones ni ejemplos.\n"
    "TRUTH_RULE (critica): NUNCA inventes datos. No inventes correos, "
    "remitentes, direcciones, fechas, importes, eventos ni tareas. Si una "
    "herramienta no devuelve resultados o falla, di exactamente que no hay "
    "resultados (o que no pudiste comprobarlo); jamas rellenes con "
    "suposiciones. Solo puedes afirmar algo si viene de una herramienta o "
    "del propio mensaje del usuario.\n"
    "TOOL_HONESTY_RULE: no afirmes haber hecho una accion (guardar, crear, "
    "enviar, anadir, completar) si no la ha ejecutado una herramienta en "
    "este turno. Si no puedes ejecutarla, dilo claramente.\n"
    "TASK_RULE: cuando el usuario pida guardar, apuntar o recordar una "
    "tarea ('guarda esta tarea', 'apunta que...', 'recuérdame...', 'para "
    "mañana'), llama SIEMPRE a manage_google_tasks (action=create) con el "
    "titulo completo y, si dio una fecha relativa, en `due` tal cual "
    "('mañana', 'el viernes'). Confirma solo si la herramienta responde "
    "con exito.\n"
    "EMAIL_RULE: para cualquier pregunta sobre correos usa SIEMPRE "
    "search_gmail antes de responder y cita unicamente remitente, asunto "
    "y fecha que devuelva la herramienta. Si no hay resultados, dilo; "
    "nunca inventes un correo ni un remitente. Busca de inmediato con lo "
    "que el usuario haya dicho (nombre, parte del nombre, asunto...): NO "
    "pidas confirmacion ni el nombre completo antes de buscar.\n"
)


def build_system_prompt(voice: bool = False) -> str:
    """Prompt del sistema con contexto dinámico (auditoría 2026-09-26).

    Añade la fecha/hora actual (evita fechas inventadas) y el estado real de
    Google (evita que el modelo diga que no está conectado cuando sí lo está).
    Con `voice=True` sustituye las reglas de formato de chat por las de voz
    (1-3 frases, sin Markdown/tablas) para el modo llamada.
    """
    try:
        now = datetime.now(ZoneInfo(settings.timezone))
    except Exception:
        now = datetime.now()
    fecha = now.strftime("%A, %d/%m/%Y %H:%M")
    if google_services.is_ready:
        google_state = "conectado (calendario: %s)" % google_services.calendar_id
    else:
        google_state = (
            "no configurado; si el usuario pide calendario/Drive, indica que use "
            "/setup_google o /calendario <correo>"
        )
    contexto = (
        f"CONTEXTO_ACTUAL: hoy es {fecha} (zona horaria {settings.timezone}). "
        "Usa SIEMPRE esta fecha para calcular 'hoy', 'mañana', 'pasado mañana', "
        "'el viernes', etc. Nunca inventes fechas.\n"
        f"GOOGLE_RULE: Google está {google_state}. Las herramientas de Google "
        "(calendario y Drive) están disponibles: NO ofrezcas enlaces de "
        "autorización si ya está conectado; úsalas directamente.\n"
        "ACTION_RULE: si la peticion del usuario esta cubierta por una "
        "herramienta, invocala directamente sin pedir confirmacion ni "
        "preguntar detalles que puedas asumir razonablemente.\n"
        + GROUNDING_RULES
        + "CONTACT_RULE: si el usuario pide el telefono, movil, correo o "
        "direccion de una persona (mama, papa, Ana, un amigo...), usa SIEMPRE "
        "find_contact (Google Contacts) ANTES de responder. NUNCA uses "
        "search_knowledge ni search_second_brain para telefonos ni correos: "
        "find_contact ya busca en tus contactos de Google. Si el usuario dice "
        "'mi madre/padre/hermano...', find_contact lo resuelve por variantes y "
        "alias; si devuelve varios, pregunta cual. Si no lo encuentra, dilo y "
        "pide el nombre exacto con el que lo tiene guardado. Cuando el usuario "
        "diga 'X es mi madre/padre/...', guardalo con remember_fact para "
        "resolverlo la proxima vez.\n"
    )
    format_rule = (
        "FORMAT_RULE: cuando el usuario pida una tabla, estadisticas, "
        "comparativas o un listado estructurado (finanzas, actividad, eventos, "
        "contactos...), responde con una **tabla Markdown** real "
        "(| columna | columna | y su fila de separacion |---|---|). Nunca "
        "respondas con descripciones de imagenes ni remitas a graficos: texto "
        "formateado en Markdown. Para datos del gimnasio/actividad usa "
        "fitness_daily_steps y/o el segundo cerebro y presenta la tabla.\n"
    )
    if voice:
        return contexto + VOICE_RULE + SYSTEM_PROMPT_VOICE
    return contexto + format_rule + SYSTEM_PROMPT_VOICE


VOICE_RULE = (
    "VOICE_RULE: esto es una CONVERSACION DE VOZ en tiempo real. Responde en "
    "1-3 frases cortas y naturales, como una persona hablando. PROHIBIDO: "
    "Markdown, tablas, vinetas, emojis, URLs, IDs, listados largos y "
    "tecnicismos. Si el dato es largo, da lo esencial y ofrece el resto por "
    "escrito. Si falta un dato imprescindible, pregunta en una frase. No "
    "repitas palabras ni frases (evita bucles). Termina siempre con una frase "
    "completa.\n"
)


async def _prepare_tool_phase(
    text: str, chat_id: int, voice: bool = False
) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Fase comun (Telegram y voz): historial + herramientas + ejecucion.

    Devuelve (messages_for_llm, content, tool_calls, tools_for_call). Si hubo
    herramientas, `messages_for_llm` ya incluye sus resultados para que cada
    interfaz haga su composicion final (chat normal o streaming de voz).

    En voz (chat_id 0) las herramientas que guardan datos (tareas, eventos,
    gastos, CRM) se ejecutan con el chat del administrador, para que lo
    apuntado por llamada aparezca en Telegram y en el briefing.
    """
    tool_chat_id = chat_id
    if voice and chat_id == 0 and settings.admin_ids:
        tool_chat_id = settings.admin_ids[0]
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

    messages_for_llm: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(voice=voice)}
    ]
    for msg in trimmed_history:
        messages_for_llm.append({"role": msg["role"], "content": msg["content"]})
    # Refuerzo de fecha en el último mensaje del usuario (los modelos pequeños
    # atienden mejor al final de la conversación que al prompt del sistema).
    for index in range(len(messages_for_llm) - 1, -1, -1):
        if messages_for_llm[index]["role"] == "user":
            messages_for_llm[index]["content"] = "%s %s" % (
                date_context_line(),
                messages_for_llm[index]["content"],
            )
            break
    else:
        # Garantiza que la pregunta actual llegue al modelo aunque el
        # historial no la incluya (o este vacio).
        messages_for_llm.append({"role": "user", "content": "%s %s" % (date_context_line(), text)})

    tools_for_call = await select_tools_semantic(text)

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
                tools=tools_for_call,
                max_tokens=512,
            ),
            timeout=600.0,
        )
    except TimeoutError:
        logger.error("[ORCHESTRATOR] timeout for chat_id=%d", chat_id)
        return (
            messages_for_llm,
            "Lo siento, el modelo tardo demasiado en responder. Intenta con un mensaje mas corto.",
            [],
            [],
        )
    except OllamaClientError as e:
        logger.error("[ORCHESTRATOR] Ollama error: %s", e)
        return messages_for_llm, f"Error del modelo: {e}", [], []
    except Exception:
        logger.exception("[ORCHESTRATOR] AI error for chat_id=%d", chat_id)
        return messages_for_llm, "Error interno al procesar la respuesta.", [], []

    _elapsed = _time.time() - _t_start
    metrics.observe("llm_chat_latency", _elapsed)
    logger.info(
        "[ORCHESTRATOR] response in %.1fs content_len=%d tool_calls=%s",
        _elapsed,
        len(content or ""),
        bool(tool_calls),
    )

    # Guardia de honestidad: si afirma una accion sin haber usado herramienta,
    # reintentar una vez para que la ejecute o se desdecida.
    if not tool_calls and _hallucination_risk(content or "", text):
        logger.warning(
            "[ORCHESTRATOR] accion afirmada sin herramienta (chat_id=%d); reintentando",
            chat_id,
        )
        messages_for_llm.append(
            {
                "role": "system",
                "content": (
                    "AVISO: has respondido como si hubieras hecho una accion o has "
                    "citado datos (correos) sin llamar a ninguna herramienta. Si el "
                    "usuario pidio buscar, guardar, crear, completar o enviar algo, "
                    "llama AHORA a la herramienta adecuada (search_gmail para correos, "
                    "manage_google_tasks para tareas). No inventes datos; si no puedes, "
                    "di claramente que no lo has hecho."
                ),
            }
        )
        try:
            content_retry, tool_calls_retry = await asyncio.wait_for(
                llm.chat_with_tools(
                    messages=messages_for_llm,
                    tools=tools_for_call,
                    max_tokens=512,
                ),
                timeout=600.0,
            )
            if tool_calls_retry:
                content, tool_calls = content_retry, tool_calls_retry
                logger.info(
                    "[ORCHESTRATOR] reintento con herramienta: %s",
                    [tc["function"]["name"] for tc in tool_calls],
                )
            elif _hallucination_risk(content_retry or "", text):
                # El reintento tampoco uso herramientas: mejor honestidad que datos falsos.
                logger.warning(
                    "[ORCHESTRATOR] reintento sin herramienta; respuesta honesta (chat_id=%d)",
                    chat_id,
                )
                content = HONEST_FALLBACK
            elif content_retry:
                content = content_retry
        except Exception as e:
            logger.warning("[ORCHESTRATOR] reintento de herramienta fallo: %s", e)

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

            result = await _execute_tool(tool_chat_id, func_name, args)
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

    return messages_for_llm, content or "", tool_calls or [], tools_for_call


async def generate_response(text: str, chat_id: int) -> str:
    """Generate an AI response with tools and RAG for any interface.

    Offers tools to the LLM in a single call. If the model decides to invoke
    a tool (search_second_brain, save_expense, etc.), a second LLM call is
    made with the tool results. For greetings or small talk with no tool
    invocations, only 1 LLM call is made.
    """
    messages_for_llm, content, tool_calls, tools_for_call = await _prepare_tool_phase(text, chat_id)

    if tool_calls:
        try:
            content, _ = await asyncio.wait_for(
                llm.chat_with_tools(
                    messages=messages_for_llm,
                    tools=tools_for_call,
                    max_tokens=512,
                ),
                timeout=600.0,
            )
        except Exception:
            content = "Consulta completada. Revisa el resultado de las herramientas."

    if content:
        await db.save_chat_message(chat_id, MessageRole.assistant.value, content[:2000])

    return content or "No pude generar una respuesta."


def _chunk_words(text: str):
    """Trocea texto en palabras (para emitir una respuesta ya generada)."""
    words = text.split()
    for i, word in enumerate(words):
        yield word + (" " if i < len(words) - 1 else "")


async def generate_response_stream(text: str, chat_id: int, voice: bool = True):
    """Genera la respuesta emitiendo tokens reales (modo llamada de voz).

    - Con herramientas: la composicion final se hace con `chat_stream_tokens`
      (el primer token llega en cuanto el modelo empieza a escribir, sin
      esperar a la respuesta completa).
    - Sin herramientas: la respuesta ya esta generada; se trocea al vuelo.
    """
    messages_for_llm, content, tool_calls, _tools = await _prepare_tool_phase(
        text, chat_id, voice=voice
    )

    full = ""
    if tool_calls:
        try:
            async for token in llm.chat_stream_tokens(
                messages=messages_for_llm,
                max_tokens=180 if voice else 512,
                repeat_penalty=1.15 if voice else None,
            ):
                full += token
                yield token
        except Exception as e:
            logger.warning("[ORCHESTRATOR] streaming fallo, uso texto completo: %s", e)
            full = content or "Consulta completada. Revisa el resultado."
            yield full
    elif content:
        full = content
        for chunk in _chunk_words(content):
            yield chunk
    else:
        full = "No pude generar una respuesta."
        yield full

    if full:
        await db.save_chat_message(chat_id, MessageRole.assistant.value, full[:2000])
