import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from src.config import settings
from src.database import db
from src.handlers.chat_tools import TOOLS_DEFINITIONS
from src.logger import logger
from src.models.schemas import COMMANDS_REGISTRY, MessageRole
from src.ollama_client import OllamaClientError, llm
from src.services.google_service import google_service
from src.utils import obsidian_manager as ob
from src.utils import workspace_manager as wm
from src.utils.google_calendar_manager import gcal
from src.utils.obsidian_manager import move_or_rename_file as obsidian_move_rename
from src.utils.telemetry import metrics, new_correlation_id
from src.utils.vector_manager import vector_db
from src.utils.web_search import format_search_results, search_duckduckgo


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    welcome = (
        f"¡Hola {user.first_name}! Soy Rafita, tu asistente virtual personal.\n\n"
        "Estoy potenciado por Qwen 2.5 7B (Ollama) para respuesta rápida en texto "
        "y Gemma 4 12B para análisis de imágenes, todo de forma local y privada.\n\n"
        "Usa /ayuda para ver todos los comandos disponibles."
    )
    await update.effective_message.reply_text(welcome)
    logger.info("User %d started the bot", user.id)


async def ayuda_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = ["*Comandos disponibles:*\n"]
    for cmd in COMMANDS_REGISTRY:
        lines.append(f"/{cmd.command} - {cmd.description}")
    lines.append(
        "\n*Chat libre:* También puedes escribir cualquier mensaje y yo lo "
        "procesaré con IA, incluyendo acciones como registrar gastos, "
        "crear eventos o alertas automáticamente."
    )
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


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

    needs_tools = _detect_tool_intent(user_text)

    history_limit = 6 if needs_tools else 4
    history = await db.get_chat_history(chat_id, history_limit)

    MAX_CONTENT_LEN = 500
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
                    "STRICT_LANGUAGE_RULE: Tu idioma es EXCLUSIVAMENTE el español. "
                    "Queda prohibido el uso de caracteres chinos, japoneses o inglés.\n"
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
                    "- Ideas y conceptos nuevos -> 05-Zettelkasten/ (tipo: nota-atomica)\n"
                    "- Decisiones de proyecto -> 01-Proyectos/ (tipo: proyecto)\n"
                    "- Datos personales (salud, preferencias) -> 02-Areas/ (tipo: area)\n"
                    "- Aprendizajes tecnicos -> 03-Recursos/ (tipo: recurso)\n"
                    "Confirma brevemente: 'He guardado esto en tu segundo cerebro'.\n"
                    "CREDENTIAL_RULE: El usuario puede guardar claves, API keys y contraseñas "
                    "de forma segura con /guardar_clave (cifrado AES-256). Si el usuario "
                    "menciona una API key (Gemini, OpenAI, etc.), una contraseña de WiFi, "
                    "o credenciales de cualquier servicio, sugierele guardarlas con "
                    "/guardar_clave <servicio> <valor>. NUNCA muestres el valor completo "
                    "de una clave en tus respuestas. Si necesitas usar una clave guardada, "
                    "usa /clave <servicio> para obtenerla.\n"
                    "FINANCE_STORAGE_RULE: No usas Excel para el control financiero. Gestionas el "
                    "historico financiero estrictamente en la nota de Obsidian "
                    "'02-Areas/Finanzas/Control_Financiero_2026.md'. Toda transaccion se registra "
                    "en una tabla Markdown con las columnas: "
                    "| Fecha | Concepto | Categoria | Ingreso/Gasto (EUR) | Saldo |. "
                    "Si el usuario pregunta como llevas el control o como gestionas las finanzas, "
                    "respondele explicando esta estructura exacta y muestrale un ejemplo de la tabla. "
                    "Cada vez que registres un gasto con save_expense, estas obligado a sincronizarlo "
                    "en esa nota de Obsidian.\n\n"
                    "Eres Rafita, un asistente virtual personal experto en productividad, "
                    "finanzas y organización. Respondes en español de manera clara y concisa. "
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
                    "Responde siempre en español."
                ),
            }
        ]
    else:
        messages_for_llm = [
            {
                "role": "system",
                "content": (
                    "STRICT_LANGUAGE_RULE: Tu idioma es EXCLUSIVAMENTE el español. "
                    "Queda prohibido el uso de caracteres chinos, japoneses o inglés.\n"
                    "AUDIO_RULE: Si el usuario te pide explicitamente en su mensaje que le "
                    "respondas por audio, nota de voz o que hables, debes envolver OBLIGATORIAMENTE "
                    "tu respuesta completa dentro de las etiquetas [Audio] y [/Audio] para activar "
                    "el sintetizador local.\n"
                    "FINANCE_STORAGE_RULE: No usas Excel para el control financiero. Gestionas el "
                    "historico financiero estrictamente en la nota de Obsidian "
                    "'02-Areas/Finanzas/Control_Financiero_2026.md'. Toda transaccion se registra "
                    "en una tabla Markdown con las columnas: "
                    "| Fecha | Concepto | Categoria | Ingreso/Gasto (EUR) | Saldo |. "
                    "Si el usuario pregunta como llevas el control, explicale esta estructura.\n\n"
                    "Eres Rafita, un asistente virtual personal. "
                    "Respondes en español de manera clara, concisa y amigable. "
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
                    tools=TOOLS_DEFINITIONS,
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
            func_name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                args = {}
            logger.info(
                "Tool call: user=%d tool=%s args=%s",
                chat_id,
                func_name,
                args,
            )
            result = await _execute_tool(chat_id, func_name, args)
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

        if content and content.strip():
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
    try:
        from src.utils.obsidian_manager import create_or_append_note

        now = datetime.now()
        note_title = now.strftime("%Y-%m-%d")
        user_preview = user_msg[:120].replace("\n", " ").strip()
        entry = ("\n## %s - Conversacion\n**Usuario:** %s\n**Rafita:** %s\n") % (
            now.strftime("%H:%M"),
            user_preview,
            bot_response[:200].replace("\n", " ").strip(),
        )
        await create_or_append_note(
            title=note_title,
            content=entry,
            folder="06-Diario",
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

        if clean_text:
            await message.reply_text(clean_text)
    else:
        await message.reply_text(text)


async def _execute_tool(chat_id: int, func_name: str, args: dict[str, Any]) -> dict[str, Any]:
    metrics.inc("tool_calls_total")
    metrics.inc("tool_calls_%s" % func_name)
    try:
        if func_name == "save_expense":
            amount = args.get("amount", 0)
            category = args.get("category", "otros")
            description = args.get("description")
            record_id = await db.add_finance_record(
                chat_id=chat_id,
                amount=float(amount),
                category="expense",
                subcategory=category,
                description=description,
                currency=settings.default_currency,
            )
            try:
                from datetime import datetime as _dt

                from src.utils.obsidian_manager import create_or_append_note

                date_str = _dt.now().strftime("%Y-%m-%d")
                table_row = "| %s | %s | %s | %.2f € | - |" % (
                    date_str,
                    (description or category)[:50],
                    category,
                    float(amount),
                )
                await create_or_append_note(
                    title="Control_Financiero_2026",
                    content="## Transacciones\n\n| Fecha | Concepto | Categoria | Ingreso/Gasto (EUR) | Saldo |\n|---|---|---|---|---|\n%s"
                    % table_row,
                    folder="02-Areas/Finanzas",
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
            event_datetime = args.get("event_datetime", "")
            description = args.get("description")
            if not event_datetime:
                return {
                    "success": False,
                    "message": "No se proporcionó una fecha válida para el evento.",
                }
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
            alert_type = args.get("alert_type", "info")
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
            lines = [
                f"📊 Resumen de {now.strftime('%B %Y')}:",
                f"   Ingresos: {summary['total_income']:,.2f} {settings.default_currency}",
                f"   Gastos: {summary['total_expenses']:,.2f} {settings.default_currency}",
                f"   Balance: {summary['balance']:,.2f} {settings.default_currency}",
                f"   Transacciones: {summary['transaction_count']}",
            ]
            return {
                "success": True,
                "message": "\n".join(lines),
            }

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
                if action == "create":
                    result = await ob.create_or_append_note(title, content, folder)
                else:
                    result = await ob.create_or_append_note(title, content, folder)
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
                    "message": "No encontre informacion relevante en tu segundo cerebro. Puedo buscar en internet si lo deseas.",
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
                        "No encontre informacion relevante en tu segundo cerebro "
                        "sobre '%s'. Puedo buscar en internet si lo deseas." % query[:100]
                    ),
                }
            lines = ["*Tu segundo cerebro dice:*\n"]
            for i, r in enumerate(result["results"], 1):
                relevance = float(r.get("relevance", 0))
                note_path = r.get("note_path", r.get("source", "desconocido"))
                heading = r.get("heading", "")
                obsidian_uri = r.get("obsidian_uri", "")
                content = r["content"].strip()[:600]
                heading_info = (" \u2192 %s" % heading) if heading else ""
                cite = " [abrir en Obsidian](%s)" % obsidian_uri if obsidian_uri else ""
                lines.append(
                    "%d. *%s*%s (%.0f%%)\n   > %s%s\n"
                    % (
                        i,
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
            return {"success": True, "message": "\n".join(lines)}

        elif func_name == "manage_google_calendar":
            action = args.get("action", "").strip().lower()
            if action == "create":
                title = args.get("title", "").strip()
                dt_str = args.get("datetime_str", "").strip()
                description = args.get("description", "").strip()
                if not title or not dt_str:
                    return {
                        "success": False,
                        "message": "Título y fecha/hora son obligatorios para crear un evento.",
                    }
                result = await gcal.add_event(title, dt_str, description=description)
                return result
            elif action == "list":
                events = await gcal.list_upcoming_events(max_results=10)
                if not events:
                    return {
                        "success": True,
                        "message": "No hay eventos próximos en Google Calendar.",
                    }
                lines = ["📅 *Próximos eventos en Google Calendar:*"]
                for ev in events:
                    lines.append("  • %s - %s" % (ev["title"], ev["start"]))
                return {"success": True, "message": "\n".join(lines)}
            elif action == "delete":
                event_id = args.get("event_id", "").strip()
                if not event_id:
                    return {"success": False, "message": "Se requiere el event_id para eliminar."}
                result = await gcal.delete_event(event_id)
                return result
            else:
                return {"success": False, "message": "Acción no válida. Usa create, list o delete."}

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

                    now = dt2.now()
                    hour, minute = map(int, time_str.split(":"))
                    first_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    if first_run <= now:
                        first_run = first_run.replace(day=first_run.day + 1)
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
            result = await google_service.get_calendar_events(max_results=max_results)
            if not result.get("success"):
                return result
            events = result.get("events", [])
            if not events:
                return {
                    "success": True,
                    "message": "No hay eventos proximos en tu Google Calendar.",
                }
            from src.utils.obsidian_manager import sync_calendar_to_obsidian

            sync_result = await sync_calendar_to_obsidian(events)
            lines = ["📅 *Proximos eventos de Google Calendar:*"]
            for ev in events:
                lines.append("  • %s - %s" % (ev["title"], ev["start"]))
            lines.append("\n%s" % sync_result.get("message", ""))
            return {"success": True, "message": "\n".join(lines)}

        elif func_name == "create_google_calendar_event":
            title = args.get("title", "").strip()
            start_dt = args.get("start_datetime", "").strip()
            end_dt = args.get("end_datetime", "").strip() or None
            description = args.get("description", "").strip() or None
            if not title or not start_dt:
                return {
                    "success": False,
                    "message": "Titulo y fecha/hora de inicio son obligatorios.",
                }
            result = await google_service.create_calendar_event(
                title=title,
                start_datetime=start_dt,
                end_datetime=end_dt,
                description=description,
            )
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

        elif func_name == "ingest_file":
            filename = args.get("filename", "").strip()
            folder = args.get("folder", "03-Recursos").strip()
            note_type = args.get("note_type", "recurso").strip()
            tags = args.get("tags", [])
            summary = args.get("summary", "").strip()
            content = args.get("content", "").strip()
            if not filename:
                return {"success": False, "message": "Se requiere el nombre del archivo."}
            from pathlib import Path as _Path

            from src.utils.obsidian_manager import create_or_append_note

            vault_path = _Path("/data/obsidian_vault")
            dest_dir = vault_path / folder
            dest_dir.mkdir(parents=True, exist_ok=True)
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
                "message": f"Función desconocida: {func_name}",
            }
    except Exception as e:
        logger.exception("Tool execution failed: %s %s", func_name, args)
        metrics.inc("tool_calls_failed")
        return {
            "success": False,
            "message": f"Error al ejecutar {func_name}: {e}",
        }
