import asyncio
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes

from src.config import settings
from src.core.orchestrator import build_system_prompt, date_context_line
from src.database import db
from src.handlers.chat_tools import (
    TOOLS_DEFINITIONS,
    WRITE_TOOLS,
    select_tools_semantic,
)
from src.i18n import currency_symbol, language_name, reply_instruction
from src.logger import logger
from src.models.schemas import COMMANDS_REGISTRY, MessageRole
from src.ollama_client import OllamaClientError, llm
from src.services.google_service import google_service
from src.services.google_services_manager import google_services
from src.utils import obsidian_manager as ob
from src.utils import severity
from src.utils import workspace_manager as wm
from src.utils.citations import citations
from src.utils.google_calendar_manager import gcal
from src.utils.obsidian_manager import move_or_rename_file as obsidian_move_rename
from src.utils.telemetry import metrics, new_correlation_id
from src.utils.vector_manager import vector_db
from src.utils.web_search import format_search_results, search_duckduckgo
from src.vault_config import get_taxonomy


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    welcome = (
        f"¡Hola {user.first_name}! Soy {settings.assistant_name}, tu asistente virtual personal.\n\n"
        "Estoy potenciado por IA local (Ollama) y todo se ejecuta en tus equipos, "
        "de forma privada.\n\n"
        "Usa /ayuda para ver todos los comandos disponibles.\n\n"
        "Este es el estado de tu instalación (onboarding guiado):"
    )
    await update.effective_message.reply_text(welcome)
    try:
        from src.utils.onboarding import collect_onboarding_status, render_onboarding
        from src.utils.telegram_fmt import reply_md

        checklist = await collect_onboarding_status()
        await reply_md(update.effective_message, render_onboarding(checklist))
    except Exception as e:
        logger.warning("Onboarding no disponible: %s", e)
        await update.effective_message.reply_text(
            "Usa /ayuda para ver todos los comandos disponibles."
        )
    logger.info("User %d started the bot", user.id)


def build_ayuda_text() -> str:
    """Texto de ayuda de comandos (Telegram y web)."""
    from src.utils.telegram_fmt import escape_md

    lines = ["*Comandos disponibles:*\n"]
    for cmd in COMMANDS_REGISTRY:
        # escape_md: el Markdown legado de Telegram revienta con '_' suelto
        # (modo_voz) y el comando entero se caia con "Can't parse".
        lines.append("/%s - %s" % (escape_md(cmd.command), escape_md(cmd.description)))
    lines.append(
        "\n*Chat libre:* También puedes escribir cualquier mensaje y yo lo "
        "procesaré con IA, incluyendo acciones como registrar gastos, "
        "crear eventos o alertas automáticamente."
    )
    return "\n".join(lines)


async def ayuda_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from src.utils.telegram_fmt import reply_md

    await reply_md(update.effective_message, build_ayuda_text())


async def chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    args = context.args
    if not args:
        await message.reply_text(
            "Usa: /chat <tu mensaje>\nEjemplo: `/chat ¿Cuál es el clima en la CDMX?`"
        )
        return
    user_text = " ".join(args)
    await _process_ai_message(update, user_text, context)


async def limpiar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    await db.clear_chat_history(user.id)
    await update.effective_message.reply_text("Historial de conversación eliminado exitosamente.")
    logger.info("Chat history cleared for user %d", user.id)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.text:
        return
    user = update.effective_user
    if not user:
        return
    text = message.text.strip()
    if not text:
        return
    # Asistente privado (Fase 5, 5.5.6): whitelist por ADMIN_IDS + rate limit.
    from src.utils.access_control import chat_limiter, is_allowed_user

    if not is_allowed_user(user.id):
        logger.info("Acceso denegado a usuario no autorizado: %s", user.id)
        await message.reply_text("Este asistente es privado y no está autorizado para ti.")
        return
    if not chat_limiter.allow(str(user.id)):
        logger.info("Rate limit alcanzado para chat %s", user.id)
        await message.reply_text("Vas muy rápido 🐢. Espera un momento y vuelve a intentarlo.")
        return
    new_correlation_id()
    await _process_ai_message(update, text, context)


TOOL_INTENT_KEYWORDS = [
    "gast",
    "pagu",
    "compr",
    "pagar",
    "gasto",
    "egreso",
    "desembols",
    "evento",
    "cita",
    "reunion",
    "reunión",
    "recordatorio",
    "calendario",
    "agenda",
    "alerta",
    "notificar",
    "notifícame",
    "avísame",
    "recuérdame",
    "recuerdame",
    "finanzas",
    "balance",
    "ingresos",
    "resumen financiero",
    "recuerda que",
    "mi nombre es",
    "mi dirección",
    "me gusta",
    "guarda este dato",
    "memoriza",
    "qué sabes de mí",
    "que sabes de mi",
    "qué sabes sobre mí",
    "busca en internet",
    "busca en la web",
    "googlea",
    "buscar online",
    "noticias",
    "apunta",
    "guarda una nota",
    "crea una nota",
    "lee la nota",
    "borra la nota",
    "busca en obsidian",
    "qué escribí",
    "que escribi",
    "encuentra la nota",
    "archivos del proyecto",
    "explora el proyecto",
    "qué archivos",
    "que archivos",
    "estado del sistema",
    "cómo estás funcionando",
    "ha habido errores",
    "mueve la nota",
    "renombra",
    "mueve el archivo",
    "documentos indexados",
    "busca en los pdf",
    "busca en los documentos",
    "google calendar",
    "calendario de google",
    "agendar en google",
    "conectar google",
    "autorizar google",
    "sincroniza el calendario",
    # Google: correo, contactos, tareas, drive y fitness (2026-09-27)
    "correo",
    "gmail",
    "email",
    "bandeja",
    "telefono",
    "teléfono",
    "contacto",
    "tarea",
    "google tasks",
    "drive",
    "google drive",
    "hoja de calculo",
    "hoja de cálculo",
    "pasos",
    "fitness",
    # Edicion de la boveda
    "edita la nota",
    "editar la nota",
    "reemplaza la nota",
    "modifica la nota",
    "actualiza la nota",
    "guarda este correo",
    "guarda el correo",
    "cada día",
    "cada semana",
    "cada hora",
    "diariamente",
    "semanalmente",
    "exportar",
    "backup",
    "respaldo",
    "conectarte google",
    "conectarme google",
    "vincular google",
    "acceder a mi google",
    "cuenta de google",
    "acceder a google",
    "segundo cerebro",
    "busca en mis notas",
    "que sabes de",
    "qué sabes de",
    "que tengo sobre",
    "qué tengo sobre",
    "que escribi sobre",
    "qué escribí sobre",
    "mis apuntes",
    "mis notas",
    "mi vault",
    "mi bóveda",
    "mi boveda",
    "en que proyecto",
    "en qué proyecto",
    "que proyecto",
    "qué proyecto",
    "zettle",
    "zettelkasten",
    "diario",
    "journal",
]

TOOL_INTENT_SINGLE_WORDS = {kw for kw in TOOL_INTENT_KEYWORDS if " " not in kw}


def _detect_tool_intent(text: str) -> bool:
    text_lower = text.lower().strip()
    if len(text_lower) < 3:
        return False
    for keyword in TOOL_INTENT_KEYWORDS:
        if " " in keyword:
            words = keyword.split()
            if all(w in text_lower for w in words):
                return True
        else:
            if keyword in text_lower:
                return True
    return False


async def _process_ai_message(
    update: Update, user_text: str, context, from_voice: bool = False
) -> str | None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return None

    chat_id = user.id

    await db.save_chat_message(chat_id, MessageRole.user.value, user_text)

    # 2026-09-27: sin filtros por palabras; el modelo decide con las
    # herramientas disponibles (todas).
    needs_tools = True

    # Mas memoria de conversacion (bug 2026-09-27: "se pierde en el contexto").
    history_limit = 10 if needs_tools else 6
    history = await db.get_chat_history(chat_id, history_limit)

    MAX_CONTENT_LEN = 800
    trimmed_history = []
    for h in history:
        c = h.get("content", "")
        trimmed_history.append(
            {
                "role": h["role"],
                "content": c[:MAX_CONTENT_LEN] + ("..." if len(c) > MAX_CONTENT_LEN else ""),
            }
        )
    history = trimmed_history

    if needs_tools:
        messages_for_llm = [
            {
                "role": "system",
                "content": (
                    f"{build_system_prompt()}"
                    "AUDIO_RULE: Si el usuario te pide explicitamente en su mensaje que le "
                    "respondas por audio, nota de voz o que hables, debes envolver OBLIGATORIAMENTE "
                    "tu respuesta completa dentro de las etiquetas [Audio] y [/Audio] para activar "
                    "el sintetizador local.\n"
                    "GOOGLE_WORKSPACE_RULE: Tienes acceso a las herramientas de Google. Si el usuario "
                    "te pide ver su calendario, crear un evento o acceder a sus datos de Google y la "
                    "API arroja una excepcion de 'No autenticado', debes ejecutar inmediatamente "
                    "generate_google_auth_link, facilitarle el enlace al usuario con un mensaje claro "
                    "y explicarle que debe darte el codigo de vuelta para conectarlo todo.\n"
                    "PROACTIVE_OBSIDIAN_RULE: Cada vez que realices una accion en Google (crear un "
                    "evento, modificar una tarea), estas obligado a sincronizar y dejar constancia "
                    "de esa accion en su nota correspondiente de Obsidian de forma autonoma.\n"
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
                    "CREDENTIAL_RULE: El usuario puede guardar claves, API keys y contraseñas "
                    "de forma segura con /guardar_clave (cifrado Fernet). Si el usuario "
                    "menciona una API key (Gemini, OpenAI, etc.), una contraseña de WiFi, "
                    "o credenciales de cualquier servicio, sugierele guardarlas con "
                    "/guardar_clave <servicio> <valor>. NUNCA muestres el valor completo "
                    "de una clave en tus respuestas. Si necesitas usar una clave guardada, "
                    "usa /clave <servicio> para obtenerla.\n"
                    "FINANCE_STORAGE_RULE: No usas Excel para el control financiero. Gestionas el "
                    "historico financiero estrictamente en la nota de Obsidian "
                    f"'{get_taxonomy().path('areas_finanzas')}/Control_Financiero_2026.md'. "
                    "Toda transaccion se registra "
                    "en una tabla Markdown con las columnas: "
                    f"| Fecha | Concepto | Categoria | Ingreso/Gasto ({settings.default_currency}) | Saldo |. "
                    "Si el usuario pregunta como llevas el control o como gestionas las finanzas, "
                    "respondele explicando esta estructura exacta y muestrale un ejemplo de la tabla. "
                    "Cada vez que registres un gasto con save_expense, estas obligado a sincronizarlo "
                    "en esa nota de Obsidian.\n\n"
                    f"Eres {settings.assistant_name}, un asistente virtual personal experto en "
                    f"productividad, finanzas y organización. Respondes en {language_name()} de "
                    "manera clara y concisa. "
                    "Tienes acceso a herramientas que debes invocar automáticamente cuando "
                    "el usuario lo necesite.\n\n"
                    "Herramientas disponibles:\n"
                    "- save_expense / create_event / create_alert / get_finance_summary\n"
                    "- remember_fact / search_knowledge / search_web\n"
                    "- manage_obsidian_note / search_obsidian_vault\n"
                    "- inspect_project_files / analyze_system_logs\n"
                    "- ask_deep_knowledge_base (tu segundo cerebro: busca en todas tus notas y documentos)\n"
                    "- search_second_brain (busqueda semantica avanzada con filtro por etiquetas y citacion)\n"
                    "- ingest_file (registra archivos en el segundo cerebro con metadatos)\n"
                    "- manage_google_calendar / set_recurring_reminder\n"
                    "- generate_google_auth_link / save_google_verification_code\n"
                    "- get_google_calendar_events / create_google_calendar_event\n\n"
                    "Cuando invoques una herramienta, confirma al usuario lo realizado de forma breve. "
                    f"{reply_instruction()}"
                ),
            }
        ]
    else:
        messages_for_llm = [
            {
                "role": "system",
                "content": (
                    f"{build_system_prompt()}"
                    "AUDIO_RULE: Si el usuario te pide explicitamente en su mensaje que le "
                    "respondas por audio, nota de voz o que hables, debes envolver OBLIGATORIAMENTE "
                    "tu respuesta completa dentro de las etiquetas [Audio] y [/Audio] para activar "
                    "el sintetizador local.\n"
                    "FINANCE_STORAGE_RULE: No usas Excel para el control financiero. Gestionas el "
                    "historico financiero estrictamente en la nota de Obsidian "
                    f"'{get_taxonomy().path('areas_finanzas')}/Control_Financiero_2026.md'. "
                    "Toda transaccion se registra "
                    "en una tabla Markdown con las columnas: "
                    f"| Fecha | Concepto | Categoria | Ingreso/Gasto ({settings.default_currency}) | Saldo |. "
                    "Si el usuario pregunta como llevas el control, explicale esta estructura.\n\n"
                    f"Eres {settings.assistant_name}, un asistente virtual personal. "
                    f"Respondes en {language_name()} de manera clara, concisa y amigable. "
                    "Mantén las respuestas breves a menos que el usuario pida detalle."
                ),
            }
        ]
    for msg in history:
        messages_for_llm.append(
            {
                "role": msg["role"],
                "content": msg["content"],
            }
        )
    # Refuerzo de fecha en el último mensaje del usuario (auditoría 2026-09-27)
    for index in range(len(messages_for_llm) - 1, -1, -1):
        if messages_for_llm[index]["role"] == "user":
            messages_for_llm[index]["content"] = "%s %s" % (
                date_context_line(),
                messages_for_llm[index]["content"],
            )
            break
    else:
        # Garantiza que la pregunta actual llegue al modelo aunque el
        # historial no la incluya.
        messages_for_llm.append(
            {"role": "user", "content": "%s %s" % (date_context_line(), user_text)}
        )

    await message.reply_chat_action("typing")

    import time as _time

    _ts_b = _time.strftime("%H:%M:%S") + ".%03d" % int((_time.time() % 1) * 1000)
    _t_start = _time.time()

    if needs_tools:
        import json as _json

        _tools_size = len(_json.dumps(TOOLS_DEFINITIONS, ensure_ascii=False))
        logger.info(
            "[TELEMETRY B] Con tools [%s] modelo=%s tools=%d chars=%d history=%d",
            _ts_b,
            settings.ollama_model,
            len(TOOLS_DEFINITIONS),
            _tools_size,
            len(history),
        )
        try:
            content, tool_calls = await asyncio.wait_for(
                llm.chat_with_tools(
                    messages=messages_for_llm,
                    tools=await select_tools_semantic(user_text),
                    max_tokens=512,
                ),
                timeout=600.0,
            )
        except TimeoutError:
            logger.error("[TIMEOUT] chat_with_tools supero 120s para user %d", chat_id)
            is_vision = context is not None and context.user_data.get("processing_image", False)
            if is_vision:
                await message.reply_text(
                    "⚠️ El procesamiento de la imagen está tardando demasiado. "
                    "La imagen fue guardada en Obsidian."
                )
            else:
                await message.reply_text(
                    "⚠️ La respuesta de texto está tardando demasiado debido a la carga del modelo local. "
                    "Intenta con un mensaje más corto."
                )
            return "timeout"
        except OllamaClientError as e:
            error_msg = f"⚠️ {e}"
            await message.reply_text(error_msg)
            return error_msg
        except Exception as e:
            logger.exception("AI processing error for user %d", chat_id)
            error_msg = "⚠️ Error interno en el procesador de chat: %s" % str(e)[:200]
            await message.reply_text(error_msg)
            return error_msg
    else:
        logger.info(
            "[TELEMETRY B] Sin tools (fast path) [%s] modelo=%s msgs=%d history=%d",
            _ts_b,
            settings.ollama_model,
            len(messages_for_llm),
            len(history),
        )
        try:
            content = await asyncio.wait_for(
                llm.chat(messages=messages_for_llm, max_tokens=256),
                timeout=60.0,
            )
            tool_calls = None
        except TimeoutError:
            logger.error("[TIMEOUT] chat supero 60s para user %d", chat_id)
            is_vision = context is not None and context.user_data.get("processing_image", False)
            if is_vision:
                await message.reply_text(
                    "⚠️ El procesamiento post-imagen está tardando demasiado. "
                    "El modelo de visión fue descargado, pero Qwen necesita más tiempo."
                )
            else:
                await message.reply_text(
                    "⚠️ La respuesta de texto está tardando demasiado debido a la carga del modelo local. "
                    "Intenta de nuevo."
                )
            return "timeout"
        except OllamaClientError as e:
            error_msg = f"⚠️ {e}"
            await message.reply_text(error_msg)
            return error_msg
        except Exception as e:
            logger.exception("AI processing error for user %d", chat_id)
            error_msg = "⚠️ Error interno en el procesador de chat: %s" % str(e)[:200]
            await message.reply_text(error_msg)
            return error_msg

    _elapsed = _time.time() - _t_start
    metrics.observe("llm_chat_latency", _elapsed)
    _ts_c = _time.strftime("%H:%M:%S") + ".%03d" % int((_time.time() % 1) * 1000)
    logger.info(
        "[TELEMETRY C] Ollama ha respondido tras %.1f segundos [%s] content_len=%d tool_calls=%s",
        _elapsed,
        _ts_c,
        len(content or ""),
        bool(tool_calls),
    )

    if tool_calls:
        results = []
        for tc in tool_calls:
            fn = tc.get("function") or {}
            func_name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            logger.info(
                "Tool call: user=%d tool=%s args=%s",
                chat_id,
                func_name,
                args,
            )
            result = await execute_tool_measured(chat_id, func_name, args)
            if func_name == "search_second_brain":
                try:
                    search_result = await vector_db.query(
                        args.get("query", ""),
                        top_k=min(int(args.get("top_k", 6)), 10),
                    )
                    relevance = 0.0
                    for r in search_result.get("results", []):
                        relevance = max(relevance, float(r.get("relevance", 0)))
                    await db.log_second_brain_query(
                        args.get("query", "")[:300],
                        chat_id,
                        search_result.get("notes_found", []),
                        len(search_result.get("results", [])),
                        relevance,
                    )
                except Exception:
                    pass
            results.append(result)

        confirmation_parts = []
        for r in results:
            if r["success"]:
                confirmation_parts.append(r["message"])
            else:
                confirmation_parts.append(f"Error: {r['message']}")

        # Segunda llamada al modelo para REDACTAR la respuesta final con los
        # resultados (permite tablas Markdown y respuestas profesionales;
        # 2026-09-27). Se pasan como mensaje de usuario: el rol `tool` sin
        # herramientas hace que gemma4 responda vacio (probado).
        final_text = ""
        try:
            summary_lines = []
            for tool_call, tool_result in zip(tool_calls, results):
                summary_lines.append(
                    "- %s: %s"
                    % (
                        tool_call["function"]["name"],
                        json.dumps(tool_result, ensure_ascii=False)[:3000],
                    )
                )
            followup: list[dict[str, Any]] = list(messages_for_llm)
            followup.append(
                {
                    "role": "user",
                    "content": (
                        "Resultados de las herramientas:\n%s\n\nRedacta ahora la "
                        "respuesta final al usuario usando estos datos. Si pidio una "
                        "tabla, estadisticas o una comparativa, responde con una "
                        "tabla Markdown real (| columna | columna |). HONESTIDAD: si "
                        "algun resultado empieza por Error o success=false, di "
                        "claramente que la accion NO se pudo completar y por que; "
                        "jamas afirmes que algo se hizo si la herramienta fallo."
                        % "\n".join(summary_lines)
                    ),
                }
            )
            final_text = await asyncio.wait_for(
                llm.chat(messages=followup, max_tokens=512),
                timeout=300.0,
            )
        except Exception as e:
            logger.warning("No se pudo redactar la respuesta final con el modelo: %s", e)

        if final_text and final_text.strip():
            text_to_save = final_text.strip()
        elif content and content.strip():
            text_to_save = f"{content}\n\n" + "\n".join(confirmation_parts)
        else:
            text_to_save = "\n".join(confirmation_parts)

        await db.save_chat_message(chat_id, MessageRole.assistant.value, text_to_save)
        await _send_response_with_audio_interceptor(update, context, text_to_save)
        response = text_to_save
    else:
        response = content if content else "No generé una respuesta. ¿Podrías reformular?"
        await db.save_chat_message(chat_id, MessageRole.assistant.value, response)
        await _send_response_with_audio_interceptor(update, context, response)

    if len(response) > 4096:
        return response

    _ = asyncio.create_task(_save_diary_entry(chat_id, user_text, response[:300]))

    return response


async def _save_diary_entry(chat_id: int, user_msg: str, bot_response: str) -> None:
    if not settings.persist_to_brain:
        logger.debug("Diary entry skipped (PERSIST_TO_BRAIN=false)")
        return
    try:
        from src.utils.obsidian_manager import create_or_append_note

        now = datetime.now()
        note_title = now.strftime("%Y-%m-%d")
        user_preview = user_msg[:120].replace("\n", " ").strip()
        entry = ("\n## %s - Conversacion\n**Usuario:** %s\n**%s:** %s\n") % (
            now.strftime("%H:%M"),
            user_preview,
            settings.assistant_name,
            bot_response[:200].replace("\n", " ").strip(),
        )
        await create_or_append_note(
            title=note_title,
            content=entry,
            folder=get_taxonomy().path("diary"),
        )
    except Exception:
        pass


async def _send_response_with_audio_interceptor(update, context, text: str) -> None:
    import re

    message = update.effective_message
    if not message:
        return

    audio_pattern = re.compile(r"\[Audio\](.*?)\[/Audio\]", re.DOTALL | re.IGNORECASE)
    audio_matches = audio_pattern.findall(text)

    if audio_matches:
        clean_text = audio_pattern.sub("", text).strip()

        for audio_text in audio_matches:
            audio_text = audio_text.strip()
            if not audio_text:
                continue
            wav_path = None
            try:
                import io as _io

                from src.utils.tts_manager import convert_to_ogg, text_to_speech

                wav_path = await text_to_speech(audio_text)
                if wav_path is None:
                    logger.warning("[AUDIO INTERCEPTOR] TTS fallo para texto de audio")
                    if not clean_text:
                        await message.reply_text(audio_text)
                    continue
                ogg_path = await convert_to_ogg(wav_path)
                if ogg_path and ogg_path.exists():
                    audio_data = ogg_path.read_bytes()
                    buf = _io.BytesIO(audio_data)
                    await message.reply_voice(voice=buf, read_timeout=60, write_timeout=60)
                    logger.info(
                        "[AUDIO INTERCEPTOR] Nota de voz enviada (%d bytes)", len(audio_data)
                    )
                else:
                    logger.warning("[AUDIO INTERCEPTOR] Conversion OGG fallo")
                    if not clean_text:
                        await message.reply_text(audio_text)
            except Exception as e:
                logger.exception("[AUDIO INTERCEPTOR] Error generando audio: %s", e)
                if not clean_text:
                    await message.reply_text(audio_text)
            finally:
                if wav_path is not None:
                    from src.utils.tts_manager import cleanup_tts_dir

                    cleanup_tts_dir(wav_path)

        if clean_text:
            await _reply_formatted(message, clean_text)
    else:
        await _reply_formatted(message, text)


def _md_to_html(text: str) -> str:
    """Convierte Markdown basico a HTML de Telegram (escapa primero)."""
    import html
    import re

    out = html.escape(text)
    out = re.sub(r"(?m)^\s{0,3}#{1,6}\s*(.+?)\s*$", r"<b>\1</b>", out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"__(.+?)__", r"<b>\1</b>", out)
    out = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', out)
    out = re.sub(r"(?m)^(\s*)[-*]\s+", r"\1• ", out)
    out = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", out)
    return out


def format_telegram_response(text: str) -> tuple[str, str | None]:
    """Prepara la respuesta para Telegram (auditoria 2026-09-27).

    - Tablas Markdown -> <pre> (monoespaciado, se ven alineadas).
    - Markdown basico (negritas, cursivas, encabezados, codigo, enlaces y
      vinietas) -> HTML de Telegram (antes se veian los `**` y `#` crudos).
    - Si no hay nada que formatear, se envia texto plano como antes.
    """
    import re

    lines = text.split("\n")
    has_table = any(re.match(r"^\s*\|.*\|\s*$", line) for line in lines)
    has_markdown = bool(
        re.search(r"\*\*|^\s{0,3}#{1,6}\s|`|^\s*[-*]\s+|\]\(http", text, re.MULTILINE)
    )
    if not has_table and not has_markdown:
        return text, None
    if not has_table:
        return _md_to_html(text), "HTML"
    out: list[str] = []
    in_table = False
    for line in lines:
        is_row = bool(re.match(r"^\s*\|.*\|\s*$", line))
        if is_row and not in_table:
            out.append("<pre>")
            in_table = True
        elif not is_row and in_table:
            out.append("</pre>")
            in_table = False
        out.append(_md_to_html(line))
    if in_table:
        out.append("</pre>")
    return "\n".join(out), "HTML"


async def _reply_formatted(message, text: str) -> None:
    """Envia la respuesta respetando tablas Markdown y el limite de Telegram."""
    formatted, mode = format_telegram_response(text)
    max_len = 4000
    if len(formatted) <= max_len:
        await message.reply_text(formatted, parse_mode=mode)
        return
    chunks: list[str] = []
    current = ""
    for line in formatted.split("\n"):
        if current and len(current) + len(line) + 1 > max_len:
            chunks.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        chunks.append(current)
    for chunk in chunks:
        await message.reply_text(chunk, parse_mode=mode)


_KINSHIP_KEYS = (
    "madre",
    "mama",
    "mamá",
    "padre",
    "papa",
    "papá",
    "hermano",
    "hermana",
    "abuela",
    "abuelo",
    "pareja",
    "mujer",
    "marido",
)


_RELLENO_TAREA_RE = re.compile(
    r"\b(?:marca|marcala|marcalo|marcar|marcada|completa|completala|completar|"
    r"borra|borrala|borralo|borrar|elimina|eliminala|eliminar|quita|quitarla|"
    r"como|hecha|hecho|realizada|realizado|pendiente|la|el|los|las|una|un|"
    r"tarea|tareas|de|del|que|por favor)\b",
    re.IGNORECASE,
)


def _limpiar_consulta_contacto(query: str) -> str:
    """Quita relleno de la consulta de contactos (bug 2026-09-30).

    El modelo a veces pasa la frase entera ("busca un contacto con el nombre
    Ana que tiene puesto su correo") y el buscador no encontraba nada. Se
    quita el relleno de delante/detras y, si queda vacio, se usa la original.
    """
    original = (query or "").strip()
    if not original:
        return original
    q = original
    q = re.sub(
        r"^\s*(?:busca|búscame|buscame|buscar|encuentra|encuéntrame|encuentrame)\s+",
        "",
        q,
        flags=re.IGNORECASE,
    )
    q = re.sub(r"^\s*(?:un|una|el|la)?\s*contactos?\s+", "", q, flags=re.IGNORECASE)
    q = re.sub(
        r"^\s*(?:con\s+el\s+nombre\s+|que\s+se\s+llama\s+|llamad[oa]\s+|de\s+nombre\s+)",
        "",
        q,
        flags=re.IGNORECASE,
    )
    q = re.sub(
        r"\s+(?:que\s+)?(?:tiene|tienen)\s+(?:puesto\s+)?(?:su\s+)?"
        r"(?:correo|email|e-mail|tel[eé]fono).*$",
        "",
        q,
        flags=re.IGNORECASE,
    )
    q = re.sub(r"\s+(?:en|de)\s+mis\s+contactos.*$", "", q, flags=re.IGNORECASE)
    q = re.sub(r"\s+por\s+favor\s*$", "", q, flags=re.IGNORECASE)
    q = q.strip(" ,.;:¡!¿?")
    return q or original


def _limpiar_titulo_tarea(titulo: str) -> str:
    """Quita palabras de instruccion del 'titulo' que pasa el modelo.

    Bug 2026-09-30: llegaba 'Marcala como realizada' como titulo de tarea; el
    nombre real era otro. Se usa como aguja de busqueda, no como titulo final.
    """
    limpio = _RELLENO_TAREA_RE.sub(" ", (titulo or "").lower())
    return " ".join(limpio.split()).strip()


def _local_vault_files(query: str = "") -> list[dict[str, str]]:
    """Lista ficheros de la boveda (modo local sin Google, 2026-09-28)."""
    from src.utils.obsidian_manager import OBSIDIAN_VAULT

    base = OBSIDIAN_VAULT
    results: list[dict[str, str]] = []
    if not base.exists():
        return results
    needle = (query or "").lower()
    for path in sorted(base.rglob("*.md")):
        rel = str(path.relative_to(base))
        if needle and needle not in rel.lower():
            continue
        results.append({"name": rel, "id": rel, "mimeType": "text/markdown"})
        if len(results) >= 30:
            break
    return results


async def _post_n8n_webhook(url: str, payload: dict[str, Any]) -> tuple[bool, str]:
    """POST a un webhook de n8n. Devuelve (ok, mensaje)."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload or {})
    except Exception as e:
        return False, "No pude contactar con n8n: %s" % str(e)[:150]
    if resp.status_code >= 400:
        return False, "n8n respondio HTTP %s: %s" % (resp.status_code, resp.text[:200])
    return True, "HTTP %s" % resp.status_code


async def _trigger_n8n(args: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta una automatizacion de n8n por nombre (N8N_WEBHOOKS) o URL."""
    workflow = (args.get("workflow") or "").strip()
    payload = args.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {"value": payload}
    # Catalogo (tarea 13): si el nombre es una automatizacion conocida, pasa
    # por la capa de seleccion + permisos en vez del webhook suelto.
    if not workflow.startswith("http"):
        from src.core.orchestration import resolve as _resolve_automation
        from src.core.orchestration import run_automation as _run_automation

        spec = _resolve_automation(workflow)
        if spec:
            return await _run_automation(spec.key, payload, bool(args.get("confirm")))
    url = workflow if workflow.startswith("http") else ""
    if not url:
        import json as _json

        try:
            mapping = _json.loads(settings.n8n_webhooks or "{}")
        except _json.JSONDecodeError:
            mapping = {}
        url = mapping.get(workflow.lower()) or mapping.get(workflow) or ""
    if not url:
        return {
            "success": False,
            "message": (
                "No conozco la automatizacion '%s'. El usuario debe definirla en "
                "N8N_WEBHOOKS (.env) con su nombre y URL de webhook." % workflow
            ),
        }
    ok, detail = await _post_n8n_webhook(url, payload)
    return {
        "success": ok,
        "message": (
            "Automatizacion '%s' ejecutada en n8n (%s)." % (workflow, detail)
            if ok
            else "No se pudo ejecutar '%s': %s" % (workflow, detail)
        ),
    }


async def _resolve_contact_alias(chat_id: int, query: str) -> str:
    """Resuelve parentescos ('mi madre') con alias aprendidos (remember_fact).

    Si el usuario dijo 'mi madre es Aa Mama', quedo guardado en
    personal_knowledge ('madre' -> 'Aa Mama') y aqui se usa para buscar el
    contacto real.
    """
    from src.database import db

    normalized = (query or "").lower()
    # 'mama'/'mamá' se guardan como 'madre' y viceversa: buscamos ambas claves.
    canon = {"mama": "madre", "mamá": "madre", "papa": "padre", "papá": "padre"}
    keys_to_try: list[str] = []
    for key in _KINSHIP_KEYS:
        if key in normalized:
            keys_to_try.append(key)
            if key in canon:
                keys_to_try.append(canon[key])
    for key in dict.fromkeys(keys_to_try):
        for scope in (chat_id, 0):
            try:
                rows = await db.search_personal_knowledge(scope, key)
            except Exception:
                return ""
            for row in rows:
                value = str(row.get("value") or "").strip()
                if value:
                    logger.info("find_contact: alias '%s' -> '%s'", key, value)
                    return value
    return ""


async def execute_tool_measured(
    chat_id: int, func_name: str, args: dict[str, Any]
) -> dict[str, Any]:
    """Ejecuta una herramienta midiendo latencia y llamadas (mejora 2).

    Los datos acaban en `/metrics` (latencia global y por herramienta).
    """
    t0 = time.perf_counter()
    try:
        return await _execute_tool(chat_id, func_name, args)
    finally:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        metrics.observe("tool_latency_ms", elapsed_ms)
        metrics.observe("tool_latency_ms.%s" % func_name, elapsed_ms)
        metrics.inc("tool_calls")
        metrics.inc("tool_calls.%s" % func_name)


_EMAIL_FROM_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")


def _email_from(origen: str) -> str:
    """Extrae la dirección de un header From tipo «Ana <ana@x.com>»."""
    match = _EMAIL_FROM_RE.search(origen or "")
    return match.group(0) if match else ""


async def _execute_tool(chat_id: int, func_name: str, args: dict[str, Any]) -> dict[str, Any]:
    metrics.inc("tool_calls_total")
    metrics.inc("tool_calls_%s" % func_name)
    if not settings.persist_to_brain:
        action = str(args.get("action", "")).lower()
        if func_name == "manage_obsidian_note":
            is_write = action in {"create", "append", "overwrite", "delete"}
        else:
            is_write = func_name in WRITE_TOOLS
        if is_write:
            logger.info("Write tool blocked (PERSIST_TO_BRAIN=false): %s", func_name)
            return {
                "success": False,
                "error": "write_disabled",
                "tool": func_name,
                "message": (
                    "Escritura deshabilitada: PERSIST_TO_BRAIN=false (modo depuracion). "
                    "No se modifico el segundo cerebro."
                ),
            }
    try:
        if func_name == "save_expense":
            # Normalizar aqui: el modelo manda a veces el importe como string
            # ("150.50") y el f-string final (: .2f) reventaba con ValueError,
            # rechazando un gasto valido con "tool_exception".
            amount = args.get("amount", 0)
            try:
                amount = float(amount)
            except (TypeError, ValueError):
                amount = 0.0
            category = args.get("category", "otros")
            description = args.get("description")
            record_id = await db.add_finance_record(
                chat_id=chat_id,
                amount=amount,
                category="expense",
                subcategory=category,
                description=description,
                currency=settings.default_currency,
            )
            try:
                from datetime import datetime as _dt

                from src.utils.obsidian_manager import create_or_append_note

                date_str = _dt.now().strftime("%Y-%m-%d")
                table_row = "| %s | %s | %s | %.2f %s | - |" % (
                    date_str,
                    (description or category)[:50],
                    category,
                    amount,
                    currency_symbol(),
                )
                await create_or_append_note(
                    title="Control_Financiero_2026",
                    content=(
                        "## Transacciones\n\n"
                        "| Fecha | Concepto | Categoria | Ingreso/Gasto (%s) | Saldo |\n"
                        "|---|---|---|---|---|\n%s"
                    )
                    % (settings.default_currency, table_row),
                    folder=get_taxonomy().path("areas_finanzas"),
                )
            except Exception as sync_err:
                logger.warning("Obsidian finance sync failed: %s", sync_err)
            return {
                "success": True,
                "message": f"Gasto registrado: {amount:.2f} {settings.default_currency} "
                f"en {category} (ID: {record_id}). Sincronizado en Obsidian.",
            }

        elif func_name == "create_event":
            title = args.get("title", "Evento")
            # Acepta el nombre canonico (event_datetime) y los alias que el
            # modelo usa en otras tools ('when', 'start_datetime',
            # 'datetime_str'): el 2026-09-30 pasaba 'when' y fallaba con un
            # error enganoso ("fecha no valida") aunque la fecha era valida.
            event_datetime = str(
                args.get("event_datetime")
                or args.get("when")
                or args.get("start_datetime")
                or args.get("datetime_str")
                or ""
            ).strip()
            description = args.get("description")
            if event_datetime:
                from src.services.google_services_manager import parse_relative_datetime

                parsed = parse_relative_datetime(event_datetime)
                if parsed:
                    event_datetime = parsed.strftime("%Y-%m-%d %H:%M")
                else:
                    try:
                        event_datetime = datetime.fromisoformat(
                            event_datetime.replace("Z", "+00:00")
                        ).strftime("%Y-%m-%d %H:%M")
                    except ValueError:
                        pass
            if not event_datetime:
                return {
                    "success": False,
                    "message": "No se proporcionó una fecha válida para el evento. "
                    "Dime cuándo es (ej: 'mañana a las 10').",
                }
            # Si Google Calendar esta conectado, el evento va alli (fuente de
            # verdad del usuario; bug 2026-09-27: los eventos quedaban solo en
            # la agenda local y no aparecian en Google).
            if google_services.is_ready:
                try:
                    iso_start = datetime.strptime(event_datetime, "%Y-%m-%d %H:%M").isoformat()
                except ValueError:
                    iso_start = event_datetime
                google_result = await google_services.create_event(
                    title, iso_start, description=description
                )
                if google_result.get("success"):
                    return google_result
                logger.warning(
                    "No se pudo crear el evento en Google (%s); se guarda en local",
                    google_result.get("message"),
                )
            event_id = await db.add_event(
                chat_id=chat_id,
                title=title,
                event_datetime=event_datetime,
                description=description,
            )
            try:
                dt_obj = datetime.strptime(event_datetime, "%Y-%m-%d %H:%M")
                dt_str = dt_obj.strftime("%d/%m/%Y %H:%M")
            except ValueError:
                dt_str = event_datetime
            return {
                "success": True,
                "message": f"Evento creado: '{title}' para el {dt_str} (ID: {event_id})",
            }

        elif func_name == "create_alert":
            alert_message = args.get("message", "")
            alert_type = severity.normalize(args.get("alert_type", "info"))
            expires_at = args.get("expires_at")
            if expires_at:
                try:
                    exp_dt = datetime.strptime(expires_at, "%Y-%m-%d")
                    expires_at_str = exp_dt.strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    expires_at_str = None
            else:
                expires_at_str = None
            alert_id = await db.add_alert(
                chat_id=chat_id,
                message=alert_message,
                alert_type=alert_type,
                expires_at=expires_at_str,
            )
            return {
                "success": True,
                "message": f"Alerta creada: '{alert_message}' (tipo: {alert_type}, ID: {alert_id})",
            }

        elif func_name == "get_finance_summary":
            now = datetime.utcnow()
            start_date = now.replace(day=1).strftime("%Y-%m-%d 00:00:00")
            end_date = now.strftime("%Y-%m-%d 23:59:59")
            summary = await db.get_finance_summary(chat_id, start_date, end_date)
            if summary["transaction_count"] == 0:
                return {
                    "success": True,
                    "message": "No hay registros financieros este mes.",
                }
            cur = settings.default_currency
            lines = [
                f"📊 Resumen de {now.strftime('%B %Y')}:",
                "",
                "| Concepto | Importe |",
                "|---|---|",
                f"| Ingresos | {summary['total_income']:,.2f} {cur} |",
                f"| Gastos | {summary['total_expenses']:,.2f} {cur} |",
                f"| **Balance** | **{summary['balance']:,.2f} {cur}** |",
                f"| Transacciones | {summary['transaction_count']} |",
            ]
            expense_by_cat = summary.get("expense_by_category") or {}
            if expense_by_cat:
                lines.extend(["", "| Categoría (gasto) | Importe |", "|---|---|"])
                for cat, amount in sorted(expense_by_cat.items(), key=lambda kv: -kv[1]):
                    lines.append(f"| {cat} | {amount:,.2f} {cur} |")
            return {
                "success": True,
                "message": "\n".join(lines),
            }

        elif func_name == "get_alerts":
            alerts = await db.get_active_alerts(chat_id)
            if not alerts:
                return {"success": True, "message": "No tienes alertas pendientes."}
            lines = [
                "🔔 Alertas pendientes:",
                "",
                "| ID | Alerta | Tipo | Vence |",
                "|---|---|---|---|",
            ]
            for a in alerts:
                lines.append(
                    "| %s | %s | %s | %s |"
                    % (
                        a.get("id", "?"),
                        str(a.get("message", "")).replace("|", "/")[:80],
                        severity.normalize(
                            a.get("alert_type"), default=str(a.get("alert_type") or "info")
                        ),
                        a.get("expires_at") or "-",
                    )
                )
            return {"success": True, "message": "\n".join(lines)}

        elif func_name == "get_weather":
            from src.services.automation_service import weather_report

            return await weather_report(
                ciudad=str(args.get("ciudad", "") or ""),
                dia=str(args.get("dia", "hoy") or "hoy"),
            )

        elif func_name == "remember_fact":
            key = args.get("key", "").strip()
            value = args.get("value", "").strip()
            category = args.get("category", "general")
            if not key or not value:
                return {
                    "success": False,
                    "message": "Debes proporcionar key y value para guardar un hecho.",
                }
            await db.store_personal_knowledge(chat_id, key, value, category)
            total = await db.count_personal_knowledge(chat_id)
            return {
                "success": True,
                "message": f"Recordado: {key} = {value} (categoría: {category}). "
                f"Ahora sé {total} {'hecho' if total == 1 else 'hechos'} sobre ti.",
            }

        elif func_name == "search_knowledge":
            query = args.get("query", "").strip()
            if not query:
                results = await db.get_all_personal_knowledge(chat_id)
            else:
                results = await db.search_personal_knowledge(chat_id, query)
            if not results:
                return {
                    "success": True,
                    "message": "No tengo información almacenada sobre eso. "
                    "¿Quieres contármelo para que lo recuerde?",
                }
            lines = ["Esto es lo que sé:"]
            for r in results:
                lines.append(f"  • {r['key']}: {r['value']} ({r['category']})")
            return {
                "success": True,
                "message": "\n".join(lines),
            }

        elif func_name == "search_web":
            query = args.get("query", "").strip()
            if not query:
                return {
                    "success": False,
                    "message": "Debes proporcionar una consulta de búsqueda.",
                }
            raw_results = await search_duckduckgo(query, max_results=5)
            if not raw_results:
                return {
                    "success": False,
                    "message": "No encontré resultados en la web para tu consulta.",
                }
            formatted = format_search_results(raw_results)
            return {
                "success": True,
                "message": f"Resultados de búsqueda para '{query}':\n\n{formatted}",
            }

        elif func_name == "manage_obsidian_note":
            action = args.get("action", "").strip().lower()
            title = args.get("title", "").strip()
            content = args.get("content", "").strip()
            folder = args.get("folder", "").strip()
            if not title:
                return {"success": False, "message": "El título de la nota es obligatorio."}
            if action in ("create", "append"):
                if not content:
                    return {
                        "success": False,
                        "message": "El contenido es obligatorio para crear o añadir una nota.",
                    }
                result = await ob.create_or_append_note(title, content, folder)
            elif action == "overwrite":
                if not content:
                    return {
                        "success": False,
                        "message": "El contenido es obligatorio para reemplazar una nota.",
                    }
                result = await ob.overwrite_note(title, content, folder)
            elif action == "read":
                result = await ob.read_note(title, folder)
            elif action == "delete":
                result = await ob.delete_note(title, folder)
            else:
                return {
                    "success": False,
                    "message": f"Acción desconocida: {action}. Usa create, append, read o delete.",
                }
            return result

        elif func_name == "search_obsidian_vault":
            query = args.get("query", "").strip()
            if not query:
                return {"success": False, "message": "Proporciona una palabra clave para buscar."}
            result = await ob.search_notes_content(query)
            if not result["results"]:
                return {"success": True, "message": result["message"]}
            lines = [f"Resultados para '{query}':"]
            for r in result["results"][:10]:
                folder_tag = f" en {r['folder']}" if r.get("folder") else ""
                lines.append(
                    f"  📄 {r['title']}{folder_tag} ({r['match_count']} {'coincidencia' if r['match_count'] == 1 else 'coincidencias'})"
                )
                for s in r["snippets"][:2]:
                    lines.append(f"     ...{s}...")
            if len(result["results"]) > 10:
                lines.append(f"  ... y {len(result['results']) - 10} nota(s) más.")
            return {"success": True, "message": "\n".join(lines)}

        elif func_name == "inspect_project_files":
            path = args.get("path", "")
            result = await wm.list_workspace_files(path)
            if not result["success"]:
                return result
            items = result["items"]
            if not items:
                return {"success": True, "message": "El directorio está vacío: %s" % result["path"]}
            lines = ["Contenido de '%s':" % result["relative"]]
            for item in items:
                if item["type"] == "dir":
                    child_info = (
                        " (%d archivos)" % item["children"]
                        if isinstance(item["children"], int)
                        else ""
                    )
                    lines.append("  📁 %s/%s" % (item["name"], child_info))
                else:
                    lines.append("  📄 %s (%s)" % (item["name"], item["size"]))
            lines.append("")
            lines.append(result["summary"])
            return {"success": True, "message": "\n".join(lines)}

        elif func_name == "analyze_system_logs":
            health = await wm.get_system_health()
            lines = ["📊 *Estado del Sistema*"]
            lines.append("")
            lines.append("*Base de Datos:*")
            db_info = health.get("database", {})
            lines.append("  Tamaño: %s" % db_info.get("size", "N/A"))
            lines.append("")
            lines.append("*Disco:*")
            disk_info = health.get("disk", {})
            if "error" not in disk_info:
                lines.append("  Total: %s" % disk_info.get("total", "N/A"))
                lines.append(
                    "  Usado: %s (%s%%)"
                    % (disk_info.get("used", "N/A"), disk_info.get("percent_used", "N/A"))
                )
                lines.append("  Libre: %s" % disk_info.get("free", "N/A"))
            else:
                lines.append("  Error: %s" % disk_info["error"])
            lines.append("")
            lines.append("*Logs Recientes:*")
            log_info = health.get("logs", {})
            if "recent_errors" in log_info:
                errs = log_info["recent_errors"]
                if errs:
                    lines.append("  %d error(es) detectado(s) en logs:" % len(errs))
                    for err in errs[:5]:
                        lines.append("    ⚠️ %s" % err[:120])
                else:
                    lines.append("  Sin errores recientes ✅")
            else:
                lines.append("  %s" % log_info.get("status", "N/A"))
            lines.append("")
            chats_info = health.get("chats", {})
            if "active_chats" in chats_info:
                lines.append("*Chats Activos:* %d" % chats_info["active_chats"])
            lines.append("")
            lines.append("*Timestamp:* %s" % health.get("timestamp", "N/A"))
            overall = "✅ Saludable" if health.get("health") == "healthy" else "⚠️ Degradado"
            lines.append("*Estado General:* %s" % overall)
            return {"success": True, "message": "\n".join(lines)}

        elif func_name == "move_or_rename_file":
            source_path = args.get("source_path", "").strip()
            dest_folder = args.get("dest_folder", "").strip()
            new_name = args.get("new_name", "").strip()
            if not source_path:
                return {"success": False, "message": "La ruta de origen es obligatoria."}
            if not dest_folder:
                return {"success": False, "message": "La carpeta de destino es obligatoria."}
            full_source = "/data/obsidian_vault/" + source_path.lstrip("/")
            result = await obsidian_move_rename(
                full_source, dest_folder, new_name or Path(source_path).stem
            )
            return result

        elif func_name == "ask_deep_knowledge_base":
            query = args.get("query", "").strip()
            top_k = min(int(args.get("top_k", 5)), 10)
            if not query:
                return {
                    "success": False,
                    "message": "Proporciona una consulta para buscar en los documentos.",
                }
            result = await vector_db.query(query, top_k=top_k)
            if not result["success"]:
                return result
            if not result["results"]:
                return {
                    "success": True,
                    "message": (
                        "NO_ENCONTRADO: no hay informacion relevante en tu segundo cerebro. "
                        "Puedo buscar en internet si lo deseas."
                    ),
                }
            lines = ["*Resultados de tu segundo cerebro:*\n"]
            for i, r in enumerate(result["results"], 1):
                relevance = r.get("relevance", "N/A")
                note_path = r.get("note_path", r.get("source", "desconocido"))
                heading = r.get("heading", "")
                obsidian_uri = r.get("obsidian_uri", "")
                content = r["content"].strip()[:500]
                heading_info = " (%s)" % heading if heading else ""
                lines.append(
                    "%d. *%s%s* (%.0f%%)\n   > %s\n"
                    % (i, note_path, heading_info, float(relevance) * 100, content)
                )
            if result.get("notes_found"):
                lines.append("*Notas encontradas:* %s" % ", ".join(result["notes_found"]))
            return {"success": True, "message": "\n".join(lines)}

        elif func_name == "search_second_brain":
            query = args.get("query", "").strip()
            tags = args.get("tags")
            top_k = min(int(args.get("top_k", 6)), 10)
            if not query:
                return {
                    "success": False,
                    "message": "Proporciona una consulta para buscar en tu segundo cerebro.",
                }
            if tags and isinstance(tags, list) and len(tags) > 0:
                result = await vector_db.query(query, top_k=top_k, filter_tags=tags)
            else:
                result = await vector_db.query(query, top_k=top_k)
            if not result["success"]:
                return result
            if not result["results"]:
                return {
                    "success": True,
                    "message": (
                        "NO_ENCONTRADO: no hay informacion relevante en tu segundo cerebro "
                        "sobre '%s'. Puedo buscar en internet si lo deseas." % query[:100]
                    ),
                }
            lines = [
                "*Tu segundo cerebro dice:*\n",
                "Cada fragmento lleva su cita [S1], [S2]... Termina con su cita cada "
                "afirmacion que uses de aqui (ej: [S1]) y NO escribas ninguna seccion "
                "'Fuentes': la anade el sistema.\n",
            ]
            sources = []
            for r in result["results"]:
                relevance = float(r.get("relevance", 0))
                note_path = r.get("note_path", r.get("source", "desconocido"))
                heading = r.get("heading", "")
                obsidian_uri = r.get("obsidian_uri", "")
                content = r["content"].strip()[:600]
                heading_info = (" \u2192 %s" % heading) if heading else ""
                cite = " [abrir en Obsidian](%s)" % obsidian_uri if obsidian_uri else ""
                sid = citations.add(note_path, heading, obsidian_uri)
                sources.append(
                    {
                        "id": sid,
                        "note_path": note_path,
                        "heading": heading,
                        "obsidian_uri": obsidian_uri,
                    }
                )
                lines.append(
                    "[%s] *%s*%s (%.0f%%)\n   > %s%s\n"
                    % (
                        sid,
                        note_path,
                        heading_info,
                        relevance * 100,
                        content,
                        cite,
                    )
                )
            if result.get("notes_found"):
                lines.append("\n*Notas de origen:* %s" % ", ".join(result["notes_found"]))
            lines.append("\n_Puedes verificar esta informacion en tu vault de Obsidian._")
            return {"success": True, "message": "\n".join(lines), "sources": sources}

        elif func_name == "add_relation":
            rel = await db.add_relation(
                chat_id,
                str(args.get("subject", "") or "").strip(),
                str(args.get("predicate", "") or "").strip(),
                str(args.get("object", "") or "").strip(),
                source=str(args.get("source", "") or "conversacion"),
            )
            if not rel.get("success"):
                return rel
            data = rel.get("relation") or {}
            return {
                "success": True,
                "message": "Relacion guardada: %s — %s — %s (id %s)."
                % (
                    data.get("subject", "?"),
                    data.get("predicate", "?"),
                    data.get("object", "?"),
                    data.get("id", "?"),
                ),
            }

        elif func_name == "search_relations":
            relations = await db.search_relations(
                chat_id,
                query=str(args.get("query", "") or "").strip(),
                subject=str(args.get("subject", "") or "").strip(),
            )
            if not relations:
                return {
                    "success": True,
                    "message": "NO_ENCONTRADO: no hay relaciones guardadas que coincidan.",
                }
            lines = ["Relaciones en tu grafo de conocimiento:"]
            for rel in relations:
                lines.append(
                    "- [%s] %s — %s — %s"
                    % (rel.get("id"), rel.get("subject"), rel.get("predicate"), rel.get("object"))
                )
            return {"success": True, "message": "\n".join(lines), "relations": relations}

        elif func_name == "delete_relation":
            try:
                rel_id = int(args.get("relation_id"))
            except (TypeError, ValueError):
                return {"success": False, "message": "relation_id debe ser un numero entero."}
            deleted = await db.delete_relation(chat_id, rel_id)
            return {
                "success": deleted,
                "message": "Relacion %d borrada." % rel_id
                if deleted
                else "No existe la relacion %d." % rel_id,
            }

        elif func_name == "export_my_data":
            data = await db.export_user_data(chat_id)
            export_dir = Path(settings.data_path) / "exports"
            export_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_path = export_dir / ("rgpd_export_%d_%s.json" % (chat_id, stamp))
            file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            counts = {k: len(v) for k, v in data.items() if isinstance(v, list)}
            return {
                "success": True,
                "message": (
                    "Exportacion RGPD generada en %s. Registros: %s."
                    % (file_path, json.dumps(counts, ensure_ascii=False))
                ),
                "path": str(file_path),
                "counts": counts,
            }

        elif func_name == "run_backup":
            from src.utils.backup import trigger_system_backup

            return trigger_system_backup("tool")

        elif func_name == "get_backup_status":
            from src.utils.backup import system_backup_status

            return system_backup_status()

        elif func_name == "get_automation_runs":
            days = max(1, min(int(args.get("days", 7) or 7), 90))
            only_errors = bool(args.get("only_errors", False))
            rows = await db.list_automation_runs(days=days, only_errors=only_errors)
            if not rows:
                return {
                    "success": True,
                    "runs": [],
                    "errors": 0,
                    "message": (
                        "Sin ejecuciones de automatizaciones registradas en "
                        "los ultimos %d dias." % days
                    ),
                }
            errores = sum(1 for r in rows if r.get("status") == "error")
            lineas = []
            for r in rows[:15]:
                # Formato «• workflow · fecha · estado»: la viñeta permite que
                # _tool_lista_ignorada detecte si la respuesta ignora los datos
                # (2026-10-04: el modelo respondió un saludo con estas filas
                # delante) y el nombre extraído es el que el usuario espera.
                estado = str(r.get("status", "?"))
                nivel = severity.normalize(
                    r.get("severity"), default="info" if estado == "ok" else "error"
                )
                linea = "• %s · %s · %s [%s]" % (
                    str(r.get("workflow", "?")),
                    str(r.get("created_at", ""))[:16],
                    estado,
                    severity.tag(nivel),
                )
                if r.get("error"):
                    linea += " · %s" % str(r["error"])[:120]
                lineas.append(linea)
            return {
                "success": True,
                "runs": rows[:50],
                "errors": errores,
                "message": (
                    "%d ejecucion(es) en %d dia(s), %d fallo(s):\n%s"
                    % (len(rows), days, errores, "\n".join(lineas))
                ),
            }

        elif func_name == "get_week_plan":
            # 2.b.2 «Prepara mi semana»: agenda + tareas + CRM pendientes en
            # UNA sola llamada; el modelo compone el resumen y propone horario.
            try:
                days = int(args.get("days", 7) or 7)
            except (TypeError, ValueError):
                days = 7
            days = max(1, min(days, 14))
            try:
                ahora = datetime.now(ZoneInfo(settings.timezone))
            except Exception:
                ahora = datetime.now()
            fin = ahora + timedelta(days=days)
            corte = fin.strftime("%Y-%m-%d")
            avisos: list[str] = []
            eventos: list[dict[str, Any]] = []
            tareas: list[dict[str, Any]] = []
            clientes: list[dict[str, Any]] = []

            try:
                if google_services.is_ready:
                    crudos = await google_services.list_events(
                        max_results=50, time_min=ahora.isoformat()
                    )
                    for ev in crudos:
                        inicio = (
                            (ev.get("start") or {}).get("dateTime")
                            or (ev.get("start") or {}).get("date")
                            or ""
                        )
                        if inicio and inicio[:10] > corte:
                            continue
                        eventos.append(
                            {
                                "titulo": ev.get("summary") or "(sin titulo)",
                                "inicio": inicio,
                                "fin": (ev.get("end") or {}).get("dateTime")
                                or (ev.get("end") or {}).get("date")
                                or "",
                            }
                        )
                else:
                    for fila in await db.get_upcoming_events(chat_id, limit=50):
                        dt = str(fila.get("event_datetime") or "")
                        if dt and dt[:10] > corte:
                            continue
                        eventos.append(
                            {
                                "titulo": fila.get("title") or "(sin titulo)",
                                "inicio": dt,
                                "fin": "",
                            }
                        )
            except Exception as exc:
                avisos.append("agenda no disponible: %s" % exc)

            try:
                if google_services.is_ready:
                    paquete = await google_services.list_tasks()
                    filas_tareas = paquete.get("tasks", [])
                else:
                    filas_tareas = await db.list_tasks(chat_id)
                for tarea in filas_tareas:
                    vence = str(tarea.get("due") or "")
                    if vence and vence[:10] > corte:
                        continue
                    tareas.append(
                        {
                            "titulo": tarea.get("title") or "",
                            "vence": vence,
                        }
                    )
            except Exception as exc:
                avisos.append("tareas no disponibles: %s" % exc)

            try:
                from src.services.crm_service import listar as crm_listar

                for c in await crm_listar(None):
                    estado = str(c.get("estado") or "lead").strip().lower() or "lead"
                    if estado in ("cerrado", "perdido"):
                        continue
                    clientes.append(
                        {
                            "nombre": c.get("nombre") or "",
                            "estado": estado,
                            "proximo_paso": c.get("proximo_paso") or "",
                            "seguimiento": c.get("proximo_seguimiento") or "",
                        }
                    )
            except Exception as exc:
                avisos.append("CRM no disponible: %s" % exc)

            plan: dict[str, Any] = {
                "desde": ahora.strftime("%Y-%m-%d %H:%M"),
                "hasta": fin.strftime("%Y-%m-%d %H:%M"),
                "agenda": eventos,
                "tareas": tareas,
                "crm": clientes,
                "avisos": avisos,
            }
            return {
                "success": True,
                **plan,
                "message": (
                    "Plan %s a %s: %d evento(s), %d tarea(s), %d cliente(s) "
                    "abiertos%s. Con estos datos resume la semana y propone "
                    "un horario."
                    % (
                        plan["desde"],
                        plan["hasta"],
                        len(eventos),
                        len(tareas),
                        len(clientes),
                        ("; avisos: " + "; ".join(avisos)) if avisos else "",
                    )
                ),
            }

        elif func_name == "create_meeting_tasks":
            # 2.b.4 «Actas de reunión → seguimiento»: N tareas en una llamada,
            # con responsable antepuesto al titulo y fecha relativa resuelta.
            from src.services.google_services_manager import parse_relative_datetime

            raw = args.get("tasks") or args.get("items") or []
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except ValueError:
                    raw = []
            if isinstance(raw, dict):
                raw = [raw]
            if not isinstance(raw, list) or not raw:
                return {
                    "success": False,
                    "message": (
                        "Pasa las tareas del acta como lista (title, "
                        "responsable opcional, due opcional)."
                    ),
                }
            use_google = google_services.is_ready
            creadas: list[dict[str, Any]] = []
            fallidas: list[dict[str, Any]] = []
            for item in raw[:10]:
                if isinstance(item, str):
                    item = {"title": item}
                if not isinstance(item, dict):
                    continue
                titulo = str(item.get("title") or item.get("titulo") or "").strip()
                if not titulo:
                    continue
                responsable = str(item.get("responsable") or "").strip()
                if responsable:
                    titulo = "[%s] %s" % (responsable, titulo)
                when = str(item.get("due") or item.get("fecha") or "").strip()
                due_date = ""
                if when:
                    parsed = parse_relative_datetime(when)
                    if parsed:
                        due_date = parsed.date().isoformat()
                    else:
                        # Fecha absoluta ('2026-10-10'): parse_relative_datetime
                        # solo entiende relativas y la descartaba.
                        try:
                            due_date = datetime.fromisoformat(when[:10]).date().isoformat()
                        except ValueError:
                            due_date = ""
                try:
                    if use_google:
                        res = await google_services.create_task(titulo, due=due_date or None)
                        if not res.get("success"):
                            raise RuntimeError(
                                res.get("message") or "Google Tasks rechazo la tarea"
                            )
                    else:
                        await db.add_task(chat_id, titulo, due=due_date or None)
                    creadas.append({"titulo": titulo, "due": due_date or "sin fecha"})
                except Exception as exc:
                    fallidas.append({"titulo": titulo, "error": str(exc)})
            partes = ["creadas %d de %d" % (len(creadas), len(raw[:10]))]
            if creadas:
                detalles = "\n".join("• %s · %s" % (c["titulo"], c["due"]) for c in creadas)
                partes.append(detalles)
            if fallidas:
                partes.append(
                    "fallidas: "
                    + "; ".join("%s (%s)" % (f["titulo"], f["error"]) for f in fallidas)
                )
            return {
                "success": bool(creadas),
                "creadas": creadas,
                "fallidas": fallidas,
                "message": " — ".join(partes),
            }

        elif func_name == "triage_inbox":
            # 2.b.1 «Triaje de correo → borradores»: clasifica la bandeja no
            # leida (urgente/facturas/clientes/otro) y deja borradores
            # profesionales en Gmail SIN enviar para que el usuario revise.
            if not google_services.is_ready:
                return {
                    "success": False,
                    "message": (
                        "Gmail no está conectado: sin Google no puedo triar "
                        "la bandeja. Usa /setup_google para conectar."
                    ),
                }
            try:
                days = int(args.get("days", 3) or 3)
            except (TypeError, ValueError):
                days = 3
            days = max(1, min(days, 14))
            crudo_drafts = args.get("max_drafts")
            try:
                max_drafts = 5 if crudo_drafts is None else int(crudo_drafts)
            except (TypeError, ValueError):
                max_drafts = 5
            max_drafts = max(0, min(max_drafts, 10))
            query = "newer_than:%dd is:unread" % days
            extra = str(args.get("query") or "").strip()
            if extra:
                query = "%s %s" % (query, extra)
            res = await google_services.search_gmail(query=query, max_results=25)
            mensajes = res.get("messages", []) if isinstance(res, dict) else []

            clientes_email: set[str] = set()
            try:
                from src.services.crm_service import listar as crm_listar

                for c in await crm_listar(None):
                    for campo in ("nombre", "email"):
                        valor = str(c.get(campo) or "").strip().lower()
                        if valor:
                            clientes_email.add(valor)
            except Exception:
                pass

            # Contactos (People API) para distinguir PERSONAL de INFORMATIVO
            # (idea 14, taxonomia: urgente/clientes/facturas/personal/
            # informativo/sin_accion).
            contactos_email: set[str] = set()
            try:
                data = await google_services.list_contacts(page_size=50)
                for p in data.get("contacts", []) or []:
                    valor = str(p.get("email") or "").strip().lower()
                    if valor:
                        contactos_email.add(valor)
            except Exception:
                pass

            clasificados: list[dict[str, Any]] = []
            borradores = 0
            borrador_fallos: list[str] = []
            for m in mensajes:
                asunto = str(m.get("subject") or "(sin asunto)")
                origen = str(m.get("from") or "")
                snippet = str(m.get("snippet") or "")
                texto = ("%s %s" % (asunto, snippet)).lower()
                de = origen.lower()
                if any(
                    p in texto
                    for p in (
                        "urgente",
                        "asap",
                        "inmediato",
                        "cuanto antes",
                        "24 horas",
                        "48 horas",
                    )
                ):
                    categoria = "urgente"
                elif any(
                    p in texto
                    for p in (
                        "factura",
                        "facturación",
                        "facturacion",
                        "recibo",
                        "iban",
                        "nómina",
                        "nomina",
                        "hacienda",
                        "impuesto",
                        "transferencia",
                    )
                ):
                    categoria = "facturas"
                elif any(
                    nombre in de or nombre in texto for nombre in clientes_email if len(nombre) >= 4
                ):
                    categoria = "clientes"
                elif any(
                    p in texto or p in de
                    for p in (
                        "no-reply",
                        "no reply",
                        "no respondas",
                        "notificación automática",
                        "notificacion automatica",
                        "newsletter",
                        "promoción",
                        "promocion",
                        "unsubscribe",
                        "boletín",
                        "boletin",
                    )
                ):
                    categoria = "sin_accion"
                elif de and any(correo in de for correo in contactos_email if len(correo) >= 6):
                    categoria = "personal"
                else:
                    categoria = "informativo"
                entrada: dict[str, Any] = {
                    "categoria": categoria,
                    "de": origen,
                    "asunto": asunto,
                    "fecha": str(m.get("date") or ""),
                    "snippet": snippet[:160],
                }
                if (
                    max_drafts
                    and categoria in ("urgente", "facturas", "clientes")
                    and borradores < max_drafts
                ):
                    destino = _email_from(origen)
                    if destino:
                        cuerpo = (
                            "Hola:\n\nEn respuesta a tu correo «%s»:\n\n"
                            "[Indica aquí tu respuesta]\n\n"
                            "Saludos cordiales." % asunto
                        )
                        try:
                            draft = await google_services.create_draft(
                                to=destino,
                                subject=("Re: %s" % asunto).strip(),
                                body=cuerpo,
                            )
                            if draft.get("success"):
                                borradores += 1
                                entrada["borrador"] = draft.get("id") or "creado"
                            else:
                                borrador_fallos.append(
                                    "%s: %s" % (asunto, draft.get("message") or "fallo")
                                )
                        except Exception as exc:
                            borrador_fallos.append("%s: %s" % (asunto, exc))
                clasificados.append(entrada)

            if not clasificados:
                return {
                    "success": True,
                    "correos": [],
                    "borradores": 0,
                    "message": ("Sin correos no leídos en los últimos %d días." % days),
                }
            conteo: dict[str, int] = {}
            lineas = []
            for c in clasificados:
                conteo[c["categoria"]] = conteo.get(c["categoria"], 0) + 1
                linea = "• %s · %s · %s" % (c["categoria"], c["asunto"], c["de"])
                if c.get("borrador"):
                    linea += " · borrador listo"
                lineas.append(linea)
            resumen = ", ".join("%d %s" % (n, cat) for cat, n in sorted(conteo.items()))
            mensaje = "%d correo(s): %s.\n%s" % (
                len(clasificados),
                resumen,
                "\n".join(lineas),
            )
            if borradores:
                mensaje += "\n%d borrador(es) creados en Gmail (revisa y envía)." % borradores
            if borrador_fallos:
                mensaje += "\nBorradores fallidos: " + "; ".join(borrador_fallos)
            return {
                "success": True,
                "correos": clasificados,
                "borradores": borradores,
                "borradores_fallidos": borrador_fallos,
                "message": mensaje,
            }

        elif func_name == "delete_my_data":
            if args.get("confirm") is not True:
                return {
                    "success": False,
                    "message": (
                        "ATENCION: esta accion borra TODOS tus datos (historial de chat, "
                        "conocimiento personal, eventos, alertas, finanzas, relaciones, "
                        "reuniones y sesiones de voz) de forma irreversible. Si estas "
                        "seguro, vuelve a llamar con confirm=true."
                    ),
                }
            deleted = await db.delete_user_data(chat_id)
            return {
                "success": True,
                "message": "Datos borrados de forma irreversible: %s."
                % json.dumps(deleted, ensure_ascii=False),
            }

        elif func_name == "manage_crm":
            from src.services.crm_service import handle as crm_handle

            return await crm_handle(str(args.get("action", "")), args)

        elif func_name == "manage_sequences":
            from src.services.sequence_service import handle as seq_handle

            return await seq_handle(str(args.get("action", "")), args)

        elif func_name == "manage_google_calendar":
            action = args.get("action", "").strip().lower()
            # Alias de accion (mismo motivo que en tareas: gemma usa otros
            # nombres como 'add'/'create_event'/'update').
            action = {
                "add": "create",
                "create_event": "create",
                "add_event": "create",
                "new": "create",
                "remove": "delete",
                "delete_event": "delete",
                "remove_event": "delete",
                "update": "move",
                "reschedule": "move",
                "move_event": "move",
                "get": "list",
                "list_events": "list",
                "show": "list",
                "agenda": "list",
            }.get(action, action)
            # Google opcional (2026-09-28): si Google no esta conectado, el
            # calendario local de Rafita (SQLite) ofrece las mismas acciones.
            use_google = google_services.is_ready

            def _parse_when() -> str:
                when = args.get("when", "").strip()
                dt_str = args.get("datetime_str", "").strip()
                if when and not dt_str:
                    from src.services.google_services_manager import parse_relative_datetime

                    parsed = parse_relative_datetime(when)
                    if parsed:
                        dt_str = parsed.isoformat()
                return dt_str

            if action == "create":
                title = str(
                    args.get("title") or args.get("task_name") or args.get("name") or ""
                ).strip()
                dt_str = _parse_when()
                description = args.get("description", "").strip()
                if not title or not dt_str:
                    return {
                        "success": False,
                        "message": "Título y fecha/hora son obligatorios para crear un evento.",
                    }
                if use_google:
                    return await gcal.add_event(title, dt_str, description=description)
                event_id = await db.add_event(
                    chat_id, title, dt_str.replace("T", " ")[:19], description
                )
                return {
                    "success": True,
                    "message": "Evento guardado en tu calendario local: %s (%s)"
                    % (title, dt_str.replace("T", " ")[:16]),
                    "event_id": event_id,
                }
            elif action == "list":
                if use_google:
                    events = await gcal.list_upcoming_events(max_results=10)
                    items = [
                        {"id": e.get("id"), "title": e.get("title"), "start": e.get("start")}
                        for e in events
                    ]
                    source = "Google Calendar"
                else:
                    rows = await db.get_upcoming_events(chat_id, limit=10)
                    items = [
                        {
                            "id": r.get("id"),
                            "title": r.get("title"),
                            "start": r.get("event_datetime"),
                        }
                        for r in rows
                    ]
                    source = "calendario local"
                if not items:
                    return {
                        "success": True,
                        "message": "No hay eventos próximos en tu %s." % source,
                    }
                lines = ["📅 *Próximos eventos en tu %s:*" % source]
                for ev in items:
                    lines.append("  • %s - %s (id: %s)" % (ev["title"], ev["start"], ev["id"]))
                return {"success": True, "message": "\n".join(lines)}
            elif action == "delete":
                event_id = args.get("event_id", "").strip()
                title = args.get("title", "").strip()
                if not event_id and title:
                    needle = title.lower()
                    if use_google:
                        events = await gcal.list_upcoming_events(max_results=50)
                        matches = [e for e in events if needle in (e.get("title") or "").lower()]
                        if matches:
                            event_id = matches[0].get("id", "")
                    else:
                        rows = await db.get_upcoming_events(chat_id, limit=50)
                        matches = [r for r in rows if needle in (r.get("title") or "").lower()]
                        if matches:
                            event_id = str(matches[0].get("id", ""))
                    if not event_id:
                        return {
                            "success": False,
                            "message": "No encontre ningun evento proximo llamado '%s'." % title,
                        }
                if not event_id:
                    return {
                        "success": False,
                        "message": "Necesito el event_id o el titulo del evento para eliminarlo.",
                    }
                if use_google:
                    return await gcal.delete_event(event_id)
                await db.delete_event(chat_id, int(event_id))
                return {"success": True, "message": "Evento eliminado de tu calendario local."}
            elif action == "move":
                title = args.get("title", "").strip()
                event_id = args.get("event_id", "").strip()
                new_dt = _parse_when()
                if not new_dt:
                    return {
                        "success": False,
                        "message": "Dime a que fecha/hora moverlo (ej: 'el viernes a las 10').",
                    }
                if not event_id and title:
                    needle = title.lower()
                    if use_google:
                        events = await gcal.list_upcoming_events(max_results=50)
                        matches = [e for e in events if needle in (e.get("title") or "").lower()]
                        if matches:
                            event_id = matches[0].get("id", "")
                    else:
                        rows = await db.get_upcoming_events(chat_id, limit=50)
                        matches = [r for r in rows if needle in (r.get("title") or "").lower()]
                        if matches:
                            event_id = str(matches[0].get("id", ""))
                    if not event_id:
                        return {
                            "success": False,
                            "message": "No encontre ningun evento proximo llamado '%s'." % title,
                        }
                if not event_id:
                    return {"success": False, "message": "Necesito el titulo o el event_id."}
                if use_google:
                    return await gcal.move_event(event_id, new_dt)
                await db.update_event_datetime(
                    chat_id, int(event_id), new_dt.replace("T", " ")[:19]
                )
                return {
                    "success": True,
                    "message": "Evento movido a %s en tu calendario local."
                    % new_dt.replace("T", " ")[:16],
                }
            else:
                return {
                    "success": False,
                    "message": "Acción no válida. Usa create, list, delete o move.",
                }

        elif func_name == "set_recurring_reminder":
            pattern = args.get("pattern", "").strip().lower()
            message = args.get("message", "").strip()
            time_str = args.get("time_str", "").strip()
            if not pattern or not message:
                return {"success": False, "message": "Patrón y mensaje son obligatorios."}
            valid_patterns = {"daily", "weekly", "weekdays", "weekends"}
            if pattern not in valid_patterns and not pattern.startswith("every_"):
                return {
                    "success": False,
                    "message": "Patrón no válido. Usa: daily, weekly, every_X_hours, weekdays, weekends.",
                }
            if time_str:
                try:
                    from datetime import datetime as dt2
                    from datetime import timedelta as td2

                    now = dt2.now()
                    hour, minute = map(int, time_str.split(":"))
                    first_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    if first_run <= now:
                        # Con timedelta: replace(day+1) revienta a fin de mes.
                        first_run = first_run + td2(days=1)
                    first_run_str = first_run.strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    first_run_str = None
            else:
                first_run_str = None
            alert_id = await db.add_recurring_alert(
                chat_id=chat_id,
                message=message,
                pattern=pattern,
                first_run=first_run_str,
            )
            msg = "Recordatorio recurrente configurado: '%s' (patron: %s)" % (message, pattern)
            if first_run_str:
                msg += ". Primer aviso: %s" % first_run_str
            return {"success": True, "message": msg}

        elif func_name == "generate_google_auth_link":
            result = await google_service.generate_auth_url()
            if result.get("success"):
                return {
                    "success": True,
                    "message": "Para conectar tu Google Calendar, abre este enlace en tu navegador:\n\n%s\n\nAutoriza la aplicacion y copia el codigo que Google te da. Luego enviamelo por aqui."
                    % result["auth_url"],
                }
            return result

        elif func_name == "save_google_verification_code":
            auth_code = args.get("auth_code", "").strip()
            if not auth_code:
                return {
                    "success": False,
                    "message": "Debes proporcionar el codigo de verificacion de Google.",
                }
            result = await google_service.exchange_code(auth_code)
            return result

        elif func_name == "get_google_calendar_events":
            max_results = min(int(args.get("max_results", 10)), 25)
            try:
                days_ahead = int(args.get("days") or args.get("days_ahead") or 0)
            except (TypeError, ValueError):
                days_ahead = 0
            events = []
            if google_services.is_ready:
                # Bug 2026-10-03: antes se usaba `google_service` (la cuenta
                # de servicio, sin autenticar) y respondia "usa /setup_google"
                # aunque el usuario habia entrado con Google en la web.
                try:
                    raw = await google_services.list_events(max_results=max_results)
                    for item in raw:
                        inicio = item.get("start") or {}
                        events.append(
                            {
                                "title": item.get("summary", "(sin título)"),
                                "start": inicio.get("dateTime") or inicio.get("date") or "",
                            }
                        )
                except Exception as e:
                    logger.warning(
                        "get_google_calendar_events: Google falló (%s); uso la agenda local", e
                    )
                    events = []
            if not events:
                events = [
                    {"title": e.get("title", "?"), "start": e.get("event_datetime", "?")}
                    for e in await db.get_upcoming_events(chat_id)
                ]
            if days_ahead > 0:
                limite = datetime.utcnow() + timedelta(days=days_ahead)
                en_plazo = []
                for ev in events:
                    try:
                        cuando = datetime.fromisoformat(
                            str(ev.get("start") or "").replace("Z", "+00:00").replace(" ", "T")
                        ).replace(tzinfo=None)
                    except ValueError:
                        en_plazo.append(ev)
                        continue
                    if cuando <= limite:
                        en_plazo.append(ev)
                events = en_plazo
            if not events:
                return {
                    "success": True,
                    "message": (
                        "No hay eventos en ese periodo. Puedes crear uno con "
                        "'apunta una cita' o ver todos con /eventos."
                    ),
                }
            from src.utils.obsidian_manager import sync_calendar_to_obsidian

            sync_result = await sync_calendar_to_obsidian(events)
            label = "los próximos %d días" % days_ahead if days_ahead > 0 else "próximos"
            lines = ["📅 Eventos %s:" % label]
            for ev in events:
                lines.append("  • %s - %s" % (ev["title"], ev["start"]))
            lines.append("\n%s" % sync_result.get("message", ""))
            return {"success": True, "message": "\n".join(lines)}

        elif func_name == "create_google_calendar_event":
            title = args.get("title", "").strip()
            start_dt = args.get("start_datetime", "").strip()
            when = args.get("when", "").strip()
            if when and not start_dt:
                from src.services.google_services_manager import parse_relative_datetime

                parsed = parse_relative_datetime(when)
                if parsed:
                    start_dt = parsed.isoformat()
            end_dt = args.get("end_datetime", "").strip() or None
            description = args.get("description", "").strip() or None
            if not title or not start_dt:
                return {
                    "success": False,
                    "message": "Titulo y fecha/hora de inicio son obligatorios.",
                }
            result = {}
            if google_services.is_ready:
                # Mismo bug que get_google_calendar_events: google_service es
                # la cuenta de servicio sin autenticar.
                try:
                    result = await google_services.create_event(
                        title, start_dt, end_dt, description=description
                    )
                except Exception as e:
                    logger.warning("create_google_calendar_event: Google falló (%s)", e)
                    result = {}
            if not result.get("success"):
                event_id = await db.add_event(chat_id, title, start_dt, description)
                result = {
                    "success": True,
                    "message": (
                        "Evento '%s' guardado en tu agenda local (id %s).%s"
                        % (
                            title,
                            event_id,
                            ""
                            if google_services.is_ready
                            else " Google no está conectado; si lo conectas, se creará también allí.",
                        )
                    ),
                }
            if result.get("success"):
                from src.utils.obsidian_manager import sync_calendar_to_obsidian

                event_data = [
                    {
                        "title": title,
                        "start": start_dt,
                        "end": end_dt or start_dt,
                        "html_link": result.get("html_link", ""),
                    }
                ]
                sync_result = await sync_calendar_to_obsidian(event_data)
                result["message"] += "\n%s" % sync_result.get("message", "")
            return result

        elif func_name == "search_google_drive":
            # Bug 2026-09-28: se llamaba a google_services.search_drive(), que
            # no existe en el manager (daba AttributeError siempre).
            if not google_services.is_ready:
                files = _local_vault_files(args.get("query", ""))
                if not files:
                    return {
                        "success": True,
                        "files": [],
                        "message": "No encontré nada en tu bóveda para '%s'."
                        % args.get("query", ""),
                    }
                lines = ["🔍 En tu bóveda (modo local, sin Google):"]
                for f in files:
                    lines.append("  • %s" % f.get("name", "?"))
                return {"success": True, "files": files, "message": "\n".join(lines)}
            files = await google_services.search_files(
                query=args.get("query", ""),
                max_results=int(args.get("max_results", 10) or 10),
            )
            if not files:
                return {
                    "success": True,
                    "files": [],
                    "message": "No encontré nada en Drive para '%s'." % args.get("query", ""),
                }
            lines = ["🔍 En Drive:"]
            for f in files:
                lines.append("  • %s (id: %s)" % (f.get("name", "?"), f.get("id", "?")))
            return {"success": True, "files": files, "message": "\n".join(lines)}

        elif func_name == "list_google_drive":
            if not google_services.is_ready:
                files = _local_vault_files()
                if not files:
                    return {"success": True, "message": "Tu bóveda está vacía."}
                lines = ["📂 Ficheros de tu bóveda (modo local, sin Google):"]
                for f in files:
                    lines.append("  • 📄 %s" % f.get("name", "?"))
                return {"success": True, "files": files, "message": "\n".join(lines)}
            result = await google_services.list_drive(
                kind=args.get("kind", "all"),
                max_results=int(args.get("max_results", 20) or 20),
                folder=args.get("folder") or None,
            )
            if not result.get("success"):
                return result
            files = result.get("files", [])
            if not files:
                return {"success": True, "message": "No hay elementos en tu Drive."}
            lines = ["📂 Contenido de tu Google Drive:"]
            for item in files:
                is_folder = item.get("mimeType") == "application/vnd.google-apps.folder"
                lines.append(
                    "  • %s %s (id: %s)"
                    % ("📁" if is_folder else "📄", item.get("name", "?"), item.get("id", "?"))
                )
            return {"success": True, "files": files, "message": "\n".join(lines)}

        elif func_name == "read_google_drive_file":
            file_id = args.get("file_id", "")
            if google_services.is_ready:
                # Bug 2026-10-03: google_service.read_drive_file era la cuenta
                # de servicio sin autenticar; el manager (google_services) es
                # el que tiene la sesion del usuario.
                try:
                    result = await google_services.read_file(file_id=file_id)
                except Exception as e:
                    result = {
                        "success": False,
                        "message": "No pude leer el fichero: %s" % str(e)[:150],
                    }
            else:
                result = {
                    "success": False,
                    "message": (
                        "Google Drive no está conectado (has entrado con Google en la web "
                        "pero la sesión no está activa). Di 'conecta Google' o usa "
                        "/setup_google y lo reintento."
                    ),
                }
            if not result.get("success"):
                return result
            return {
                "success": True,
                "message": "Contenido de '%s'%s:\n\n%s"
                % (
                    result.get("name", ""),
                    " (recortado)" if result.get("truncated") else "",
                    result.get("text", ""),
                ),
            }

        elif func_name == "search_gmail":
            result = await google_services.search_gmail(
                query=args.get("query", ""),
                max_results=int(args.get("max_results", 5) or 5),
            )
            if not result.get("success"):
                return result
            messages = result.get("messages", [])
            if not messages:
                return {"success": True, "message": "No encontré correos para esa búsqueda."}
            lines = ["📧 Correos encontrados:"]
            for mail in messages:
                lines.append(
                    "  • %s — de %s (%s)\n    %s"
                    % (mail["subject"], mail["from"], mail["date"], mail["snippet"])
                )
            return {"success": True, "message": "\n".join(lines)}

        elif func_name == "trigger_n8n":
            return await _trigger_n8n(args)

        elif func_name == "list_automations":
            from src.core.orchestration import describe_automations

            return describe_automations()

        elif func_name == "run_automation":
            from src.core.orchestration import run_automation

            params = args.get("params")
            return await run_automation(
                (args.get("key") or "").strip(),
                params if isinstance(params, dict) else {},
                bool(args.get("confirm")),
            )

        elif func_name == "send_gmail":
            from src.utils.voice_text import normalize_dictated_email

            # Bug 2026-09-30: el correo dictado llegaba como "anabel arroba
            # gmail punto com" y el envio fallaba.
            to = normalize_dictated_email(args.get("to", "")) or ""
            to = to.strip()
            subject = args.get("subject", "").strip()
            body = args.get("body", "").strip()
            if not to or not body:
                return {"success": False, "message": "Necesito destinatario y cuerpo del correo."}
            result = await google_services.send_email(to=to, subject=subject, body=body)
            return result

        elif func_name == "draft_gmail":
            from src.utils.voice_text import normalize_dictated_email

            to = normalize_dictated_email(args.get("to", "")) or ""
            to = to.strip()
            subject = args.get("subject", "").strip()
            body = args.get("body", "").strip()
            if not to or not body:
                return {"success": False, "message": "Necesito destinatario y cuerpo del borrador."}
            result = await google_services.create_draft(to=to, subject=subject, body=body)
            return result

        elif func_name == "manage_google_tasks":
            action = args.get("action", "").strip().lower()
            # gemma4:12b usa nombres de accion propios (2026-09-30:
            # 'complete_task', 'add', action vacia): se aceptan como alias.
            action = {
                "add": "create",
                "create_task": "create",
                "add_task": "create",
                "new": "create",
                "apuntar": "create",
                "complete_task": "complete",
                "mark_done": "complete",
                "done": "complete",
                "completar": "complete",
                "marcar": "complete",
                "finish": "complete",
                "delete_task": "delete",
                "remove": "delete",
                "remove_task": "delete",
                "borrar": "delete",
                "eliminar": "delete",
                "list_tasks": "list",
                "listar": "list",
                "all": "list",
                "get": "list",
                "show": "list",
            }.get(action, action)
            use_google = google_services.is_ready
            if action == "list":
                if use_google:
                    tasks = (await google_services.list_tasks()).get("tasks", [])
                    source = "Google Tasks"
                else:
                    rows = await db.list_tasks(chat_id)
                    tasks = [{"id": str(r.get("id")), "title": r.get("title", "")} for r in rows]
                    source = "tareas locales"
                if not tasks:
                    return {"success": True, "message": "No tienes tareas pendientes."}
                lines = ["✅ Tareas (%s):" % source]
                for task in tasks:
                    lines.append("  • %s (id: %s)" % (task["title"], task["id"]))
                return {"success": True, "message": "\n".join(lines)}
            if action == "create":
                title = str(
                    args.get("title") or args.get("task_name") or args.get("name") or ""
                ).strip()
                if not title:
                    return {"success": False, "message": "Indica el título de la tarea."}
                due_date = ""
                when = str(args.get("due", "") or "").strip()
                if when:
                    from src.services.google_services_manager import parse_relative_datetime

                    parsed = parse_relative_datetime(when)
                    if parsed:
                        due_date = parsed.date().isoformat()
                    else:
                        # Fecha absoluta ('2026-10-01'): la descripcion de la
                        # tool la prometia y parse_relative_datetime la
                        # descartaba (bug 2026-10-04).
                        try:
                            due_date = datetime.fromisoformat(when[:10]).date().isoformat()
                        except ValueError:
                            due_date = ""
                if use_google:
                    result = await google_services.create_task(title, due=due_date or None)
                    if due_date and result.get("success"):
                        result["message"] = "Tarea guardada en Google Tasks para el %s: %s" % (
                            due_date,
                            title,
                        )
                    return result
                task_id = await db.add_task(chat_id, title, due=due_date or None)
                aviso = " para el %s" % due_date if due_date else ""
                return {
                    "success": True,
                    "message": "Tarea guardada localmente%s: %s (id: %s)" % (aviso, title, task_id),
                }
            if action in ("complete", "delete"):
                tarea_id = str(args.get("task_id", "") or "").strip()
                task_title = str(
                    args.get("task_title")
                    or args.get("title")
                    or args.get("task_name")
                    or args.get("name")
                    or ""
                ).strip()
                # Bug 2026-09-30: el modelo mete el TITULO en task_id
                # ('Programa Buena Tierra') o la instruccion entera como
                # titulo ('Marcala como realizada') y la tarea no se marcaba.
                # Un task_id real (Google) no tiene espacios.
                if tarea_id and " " in tarea_id and not task_title:
                    task_title = tarea_id
                    tarea_id = ""

                async def _pendientes() -> list[dict[str, str]]:
                    if use_google:
                        return [
                            {"id": str(t.get("id") or ""), "title": t.get("title") or ""}
                            for t in (await google_services.list_tasks()).get("tasks", [])
                        ]
                    return [
                        {"id": str(r.get("id")), "title": r.get("title", "")}
                        for r in await db.list_tasks(chat_id)
                    ]

                def _buscar_por_titulo(titulo: str, tareas: list[dict[str, str]]) -> str:
                    needle = _limpiar_titulo_tarea(titulo)
                    if not needle:
                        needle = titulo.lower().strip()
                    for t in tareas:
                        if needle and needle in (t["title"] or "").lower():
                            return t["id"]
                    # Segundo intento: todas las palabras (>=3) presentes.
                    palabras = [w for w in needle.split() if len(w) >= 3]
                    if len(palabras) >= 2:
                        for t in tareas:
                            titulo_t = (t["title"] or "").lower()
                            if all(w in titulo_t for w in palabras):
                                return t["id"]
                    return ""

                if not tarea_id and task_title:
                    tareas = await _pendientes()
                    tarea_id = _buscar_por_titulo(task_title, tareas)
                    if not tarea_id:
                        nombres = ", ".join(t["title"] for t in tareas[:10]) or "ninguna"
                        return {
                            "success": False,
                            "message": "No encontre ninguna tarea pendiente llamada '%s'. "
                            "Tareas pendientes: %s. Pide al usuario cual es."
                            % (task_title, nombres),
                        }
                if not tarea_id:
                    return {
                        "success": False,
                        "message": "Necesito el task_id o el titulo de la tarea.",
                    }
                if use_google:
                    try:
                        if action == "complete":
                            resultado = await google_services.complete_task(tarea_id)
                        else:
                            resultado = await google_services.delete_task(tarea_id)
                    except Exception as e:
                        resultado = {"success": False, "message": str(e)[:150]}
                    if resultado.get("success") is False and task_title:
                        # El id era invalido (p. ej. era el titulo): reintento
                        # resolviendo por titulo.
                        tareas = await _pendientes()
                        alt_id = _buscar_por_titulo(task_title, tareas)
                        if alt_id and alt_id != tarea_id:
                            if action == "complete":
                                return await google_services.complete_task(alt_id)
                            return await google_services.delete_task(alt_id)
                    return resultado
                try:
                    tid = int(tarea_id)
                except ValueError:
                    return {"success": False, "message": "task_id no válido: %s" % tarea_id}
                if action == "complete":
                    hechas = await db.complete_task(chat_id, tid)
                    if not hechas:
                        return {
                            "success": False,
                            "message": "No encontré ninguna tarea local con id %s "
                            "(¿ya estaba completada o borrada?)." % tarea_id,
                        }
                    return {"success": True, "message": "Tarea completada (local)."}
                borradas = await db.delete_task(chat_id, tid)
                if not borradas:
                    return {
                        "success": False,
                        "message": "No encontré ninguna tarea local con id %s "
                        "(¿ya estaba borrada?)." % tarea_id,
                    }
                return {"success": True, "message": "Tarea eliminada (local)."}
            return {
                "success": False,
                "message": "Acción no válida o vacía: usa exactamente list, create, "
                "complete o delete (no inventes otros nombres).",
            }

        elif func_name == "create_condition_rule":
            import json as _json

            from src.utils import conditional_rules as _rules

            action = str(args.get("action") or "").strip().lower()
            if action == "list":
                filas = await db.list_conditional_rules(chat_id)
                if not filas:
                    return {"success": True, "message": "No tienes reglas guardadas."}
                lines = ["📏 Reglas:"]
                for f in filas:
                    estado = "activa" if f.get("is_active") else "ya disparada"
                    lines.append(
                        "  • [%s] %s (%s)"
                        % (f["id"], f.get("message") or f.get("rule_json"), estado)
                    )
                return {"success": True, "message": "\n".join(lines)}
            if action == "delete":
                try:
                    rule_id = int(args.get("rule_id") or 0)
                except (TypeError, ValueError):
                    return {"success": False, "message": "Indica el rule_id de la regla."}
                borradas = await db.delete_conditional_rule(chat_id, rule_id)
                if not borradas:
                    return {"success": False, "message": "No encontré la regla %s." % rule_id}
                return {"success": True, "message": "Regla %s eliminada." % rule_id}
            if action == "create":
                kind = str(args.get("type") or "").strip().lower()
                if kind not in _rules.TYPES:
                    return {"success": False, "message": "type debe ser weather o metric."}
                rule: dict[str, Any] = {"type": kind}
                if kind == "weather":
                    keywords = [
                        str(k).strip() for k in (args.get("keywords") or []) if str(k).strip()
                    ]
                    if not keywords:
                        return {
                            "success": False,
                            "message": "Indica las palabras del pronóstico "
                            "(p. ej. lluvia, tormenta).",
                        }
                    rule["keywords"] = keywords
                    rule["city"] = str(args.get("city") or "").strip()
                    rule["day"] = str(args.get("day") or "hoy").strip().lower() or "hoy"
                else:
                    metric = str(args.get("metric") or "").strip()
                    op = str(args.get("op") or ">").strip()
                    if metric not in _rules.METRICS or op not in _rules.OPS:
                        return {"success": False, "message": "metric u op no válidos."}
                    try:
                        value = float(args.get("value"))
                    except (TypeError, ValueError):
                        return {"success": False, "message": "Indica el umbral (value)."}
                    rule.update({"metric": metric, "op": op, "value": value})
                hour = str(args.get("hour") or "").strip()
                if hour:
                    rule["hour"] = hour
                message = str(args.get("message") or "").strip() or (
                    "Se ha cumplido tu regla: %s" % _json.dumps(rule, ensure_ascii=False)
                )
                repeats = bool(args.get("repeats"))
                rule_id = await db.add_conditional_rule(
                    chat_id, _json.dumps(rule, ensure_ascii=False), message, repeats=repeats
                )
                return {
                    "success": True,
                    "message": "Regla guardada (id %s): %s" % (rule_id, message),
                }
            return {"success": False, "message": "Acción no válida: create, list o delete."}

        elif func_name == "find_contact":
            from src.utils.voice_text import normalize_dictated_email

            # El usuario puede dictar el correo ('ejemplo punto ejemplo arroba
            # gmail punto com') en vez del nombre: se normaliza antes de buscar.
            consulta_dictada = normalize_dictated_email(args.get("query", "")) or args.get(
                "query", ""
            )
            query = _limpiar_consulta_contacto(consulta_dictada)
            # Alias aprendidos (2026-09-27): 'mi madre' -> 'Aa Mama' (guardado
            # con remember_fact) resuelve sin ambiguedades. Solo se aplica a
            # expresiones con posesivo ('mi madre'), no a 'Mama Raulito'.
            import re as _re

            is_kinship_expr = bool(
                _re.search(
                    r"\b(mi|tu|su)\s+(madre|mama|mam\u00e1|padre|papa|pap\u00e1"
                    r"|hermano|hermana|abuela|abuelo|pareja|mujer|marido)\b",
                    query.lower(),
                )
            )
            alias = await _resolve_contact_alias(chat_id, query) if is_kinship_expr else ""
            result = await google_services.find_contact(alias or query)
            contacts = result.get("contacts", [])
            if not contacts:
                alias = alias or await _resolve_contact_alias(chat_id, query)
                if alias:
                    result = await google_services.find_contact(alias)
                    contacts = result.get("contacts", [])
            if not contacts:
                return {
                    "success": True,
                    "message": (
                        "No encontré '%s' en tus contactos de Google. Si es una "
                        "persona ('mi madre', 'un amigo'), dime el nombre exacto "
                        "con el que la tienes guardada y pruebo otra vez. Si me "
                        "dices 'mi madre es X', lo recuerdo para siempre." % query
                    ),
                }
            if len(contacts) > 1:
                hint = " (varios coinciden: si no es el primero, dime cuál)"
            else:
                hint = ""
            lines = ["👤 Contactos encontrados%s:" % hint]
            for contact in contacts:
                lines.append(
                    "  • %s — %s — %s"
                    % (
                        contact["name"],
                        contact["email"] or "sin correo",
                        contact["phone"] or "sin teléfono",
                    )
                )
            return {"success": True, "message": "\n".join(lines)}

        elif func_name == "fitness_daily_steps":
            result = await google_services.fitness_daily_steps()
            return {
                "success": True,
                "message": "Hoy llevas %s pasos." % result.get("steps", 0),
            }

        elif func_name == "ingest_file":
            filename = args.get("filename", "").strip()
            folder = args.get("folder", get_taxonomy().path("resources")).strip()
            note_type = args.get("note_type", "recurso").strip()
            tags = args.get("tags", [])
            summary = args.get("summary", "").strip()
            content = args.get("content", "").strip()
            if not filename:
                return {"success": False, "message": "Se requiere el nombre del archivo."}
            from src.utils.obsidian_manager import create_or_append_note

            file_link = "![[%s]]" % filename
            now = datetime.now()
            frontmatter = (
                "---\n"
                "id: %s\n"
                'title: "%s"\n'
                "type: %s\n"
                "tags: %s\n"
                "status: abierto\n"
                "created: %s\n"
                "updated: %s\n"
                "related: []\n"
                "source_file: %s\n"
                "---\n"
            ) % (
                now.strftime("%Y%m%d-%H%M"),
                filename.replace(".", " ").replace("_", " "),
                note_type,
                str(tags) if tags else "[]",
                now.strftime("%Y-%m-%d"),
                now.strftime("%Y-%m-%d"),
                filename,
            )
            body = "\n".join(
                [
                    "# %s\n" % filename,
                    "## Archivo\n",
                    "Archivo: %s" % file_link,
                    "Tipo: %s" % note_type,
                    "Carpeta: %s" % folder,
                    "",
                ]
            )
            if summary:
                body += "## Resumen\n\n%s\n\n" % summary
            if content:
                body += "## Contenido\n\n%s\n" % content[:8000]
            full_content = frontmatter + body
            result = await create_or_append_note(
                title=filename.replace(".", "_").replace(" ", "_"),
                content=full_content,
                folder=folder,
            )
            if result.get("success"):
                return {
                    "success": True,
                    "message": "Archivo '%s' registrado en el segundo cerebro como %s en %s. Tags: %s"
                    % (
                        filename,
                        note_type,
                        folder,
                        ", ".join(tags) if tags else "ninguna",
                    ),
                }
            return result

        else:
            return {
                "success": False,
                "error": "unknown_tool",
                "tool": func_name,
                "message": f"Función desconocida: {func_name}",
            }
    except Exception as e:
        logger.exception("Tool execution failed: %s %s", func_name, args)
        metrics.inc("tool_calls_failed")
        return {
            "success": False,
            "error": "tool_exception",
            "tool": func_name,
            "message": (
                "Error al ejecutar %s: %s. Informa al usuario del fallo y no "
                "inventes datos: si no hay resultado, dilo." % (func_name, e)
            ),
        }
