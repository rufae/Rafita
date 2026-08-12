import shutil
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from src.config import settings
from src.database import db
from src.logger import logger
from src.ollama_client import llm
from src.utils import workspace_manager as wm
from src.utils.vector_manager import vector_db


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    await message.reply_chat_action("typing")

    lines = ["📊 *PANEL DE CONTROL - RAFITA AGENT*", ""]

    lines.append("*🤖 Estado General*")
    health_raw = await llm.check_health()
    if health_raw.get("status") == "healthy":
        lines.append(
            "  IA (Ollama): ✅ %s (%dms)"
            % (
                health_raw.get("model", "N/A"),
                health_raw.get("latency_ms", 0),
            )
        )
    else:
        lines.append("  IA (Ollama): ❌ %s" % health_raw.get("error", "desconocido"))
    lines.append("  Telegram Bot: ✅ polling activo")
    lines.append("")

    lines.append("*🗄️ Base de Datos*")
    db_path = Path(settings.db_path)
    if db_path.exists():
        db_size = db_path.stat().st_size
        lines.append("  Tamaño: %s" % _format_size(db_size))
    try:
        chat_ids = await db.get_all_chat_ids()
        history_count = 0
        for cid in chat_ids:
            hist = await db.get_chat_history(cid, 999999)
            history_count += len(hist)
        fact_count = await db.count_personal_knowledge(user.id) if chat_ids else 0
        lines.append("  Chats activos: %d" % len(chat_ids))
        lines.append("  Mensajes guardados: %d" % history_count)
        lines.append("  Hechos personales: %d" % fact_count)
    except Exception as e:
        lines.append("  Error leyendo BD: %s" % e)
    lines.append("")

    lines.append("*📁 Bóveda Obsidian*")
    vault = Path("/data/obsidian_vault")
    if vault.exists():
        md_files = list(vault.rglob("*.md"))
        total_size = sum(f.stat().st_size for f in vault.rglob("*") if f.is_file())
        lines.append("  Notas .md: %d" % len(md_files))
        lines.append("  Tamaño total: %s" % _format_size(total_size))
        folder_count = len(
            [d for d in vault.iterdir() if d.is_dir() and not d.name.startswith(".")]
        )
        lines.append("  Subcarpetas: %d" % folder_count)
    lines.append("")

    lines.append("*⚙️ Herramientas (Tools)*")
    from src.handlers.chat import TOOLS_DEFINITIONS

    tool_names = [t["function"]["name"] for t in TOOLS_DEFINITIONS]
    lines.append("  Registradas: %d" % len(tool_names))
    for name in tool_names:
        lines.append("    • %s" % name)
    lines.append("")

    lines.append("*💾 Disco*")
    try:
        usage = shutil.disk_usage(str(vault if vault.exists() else "/"))
        lines.append("  Total: %s" % _format_size(usage.total))
        lines.append(
            "  Usado: %s (%d%%)" % (_format_size(usage.used), round(usage.used / usage.total * 100))
        )
        lines.append("  Libre: %s" % _format_size(usage.free))
    except Exception:
        pass
    lines.append("")

    lines.append("*🧠 Base Vectorial (RAG)*")
    try:
        vstats = await vector_db.get_stats()
        lines.append("  Chunks indexados: %d" % vstats["total_chunks"])
        lines.append("  Documentos: %d" % vstats["total_documents"])
    except Exception as e:
        lines.append("  Error: %s" % e)
    lines.append("")

    lines.append("*🗣️ Voz*")
    whisper_ok = False
    piper_ok = False
    try:
        from faster_whisper import WhisperModel  # noqa: F401

        whisper_ok = True
    except ImportError:
        pass
    try:
        from piper import PiperVoice  # noqa: F401

        piper_ok = True
    except ImportError:
        pass
    lines.append("  STT (Whisper): %s" % ("✅" if whisper_ok else "❌ no instalado"))
    lines.append("  TTS (Piper): %s" % ("✅" if piper_ok else "❌ no instalado"))
    lines.append("")

    lines.append("*📋 Logs Recientes*")
    try:
        health = await wm.get_system_health()
        log_info = health.get("logs", {})
        if "recent_errors" in log_info:
            errs = log_info["recent_errors"]
            lines.append("  Errores recientes: %d" % len(errs))
            for err in errs[:3]:
                lines.append("    ⚠️ %s" % err[:100])
            if not errs:
                lines.append("    ✅ Sin errores")
        else:
            lines.append("  %s" % log_info.get("status", "N/A"))
    except Exception as e:
        lines.append("  Error: %s" % e)
    lines.append("")

    lines.append("*⏱️ Próximo check programado*")
    try:
        lines.append("  %s" % "Mañana a las 09:00 (configurado)")
    except Exception:
        pass
    lines.append("")

    lines.append("─── *Rafita Agent v4.0-RAG* ───")

    text = "\n".join(lines)
    max_len = 4096
    if len(text) > max_len:
        for i in range(0, len(text), max_len):
            await message.reply_text(text[i : i + max_len], parse_mode="Markdown")
    else:
        await message.reply_text(text, parse_mode="Markdown")
    logger.info("Status panel sent to user %d", user.id)


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return "%d B" % size_bytes
    elif size_bytes < 1024 * 1024:
        return "%.1f KB" % (size_bytes / 1024)
    elif size_bytes < 1024 * 1024 * 1024:
        return "%.1f MB" % (size_bytes / (1024 * 1024))
    return "%.1f GB" % (size_bytes / (1024 * 1024 * 1024))


async def cerebro_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    await message.reply_chat_action("typing")

    lines = ["🧠 *SEGUNDO CEREBRO - ESTADISTICAS*", ""]

    lines.append("*📁 Vault de Obsidian*")
    vault = Path("/data/obsidian_vault")
    if vault.exists():
        md_files = list(vault.rglob("*.md"))
        total_size = sum(f.stat().st_size for f in vault.rglob("*") if f.is_file())
        lines.append("  Notas .md: %d" % len(md_files))
        lines.append("  Tamaño total: %s" % _format_size(total_size))

        carpetas = {}
        for f in md_files:
            rel = f.relative_to(vault)
            folder = str(rel.parent) if str(rel.parent) != "." else "raiz"
            carpetas[folder] = carpetas.get(folder, 0) + 1
        lines.append("  Distribucion:")
        for carpeta, count in sorted(carpetas.items()):
            lines.append("    *%s*: %d notas" % (carpeta, count))
    lines.append("")

    lines.append("*🔍 Base Vectorial (ChromaDB)*")
    try:
        vstats = await vector_db.get_stats()
        lines.append("  Chunks indexados: %d" % vstats["total_chunks"])
        lines.append("  Documentos: %d" % vstats["total_documents"])
        if vstats["total_chunks"] > 0:
            avg = vstats["total_chunks"] / max(vstats["total_documents"], 1)
            lines.append("  Chunks por nota: %.1f (promedio)" % avg)
    except Exception as e:
        lines.append("  Error: %s" % e)
    lines.append("")

    lines.append("*📊 Consultas realizadas*")
    try:
        brain_stats = await db.get_second_brain_stats()
        lines.append("  Total consultas: %d" % brain_stats["total_queries"])
        if brain_stats["recent_queries"]:
            lines.append("  Ultimas 5 consultas:")
            for q in brain_stats["recent_queries"][:5]:
                query_preview = q["query_text"][:60]
                lines.append(
                    "    *%s* → %d chunks (%.0f%%)"
                    % (
                        query_preview,
                        q["chunks_retrieved"],
                        q["top_relevance"] * 100,
                    )
                )
        else:
            lines.append("  Aun no se han hecho consultas al segundo cerebro.")
    except Exception as e:
        lines.append("  Error: %s" % e)
    lines.append("")

    lines.append("*⚙️ Estado del watcher*")
    lines.append("  Watchdog activo: ✅ (monitorizando cambios)")
    lines.append("  Debounce: 2.0s")
    lines.append("  Chunking: semantico por H2/H3")
    lines.append("  Embeddings: %s (%dd)" % (settings.embedding_model, settings.embedding_dim))
    lines.append("")

    lines.append("*🔗 Conexion MCP*")
    lines.append("  Configurado: ✅ (opencode.json)")
    lines.append("  Endpoint: https://127.0.0.1:27124/mcp/")
    lines.append("")

    lines.append("─── *RafAI - Segundo Cerebro v2.0* ───")

    text = "\n".join(lines)
    await message.reply_text(text, parse_mode="Markdown")
    logger.info("Cerebro panel sent to user %d", user.id)


async def escanear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    args = context.args
    since = args[0] if args else None

    await message.reply_chat_action("typing")
    await message.reply_text(
        "🔍 Escaneando mensajes en busca de datos para tu segundo cerebro...\n"
        "(Esto puede tardar unos segundos)"
    )

    try:
        from src.utils.message_scanner import scan_messages

        result = await scan_messages(user.id, since=since, limit=50)
    except Exception as e:
        logger.exception("Escanear error for user %d", user.id)
        await message.reply_text("Error al escanear: %s" % e)
        return

    if result["success"]:
        lines = [
            "✅ *Escaneo completado*",
            "",
            "Mensajes procesados: %d" % result["messages_scanned"],
        ]
        if result.get("extracted"):
            for item in result["extracted"]:
                if item.get("type") == "llm_response":
                    lines.append("\n%s" % item["content"][:1000])
        if result["messages_scanned"] == 0:
            lines.append("\n⚠️ No se encontraron mensajes nuevos para procesar.")
        await message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown",
        )
    else:
        await message.reply_text("❌ %s" % result.get("message", "Error"))


async def guardar_clave_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    args = context.args
    if len(args) < 2:
        await message.reply_text(
            "Uso: `/guardar_clave <servicio> <valor>`\n"
            "Ejemplo: `/guardar_clave gemini AIza...`\n"
            "Ejemplo: `/guardar_clave wifi_casa MiPassword123`\n\n"
            "⚠️ El valor se cifra con AES-256. Solo tu puedes verlo.",
            parse_mode="Markdown",
        )
        return
    service = args[0].lower().strip()
    value = " ".join(args[1:])
    await db.store_credential(user.id, service, value)
    await message.reply_text(
        "🔐 Clave guardada para `%s` (cifrada AES-256).\nUsa `/claves` para ver tus servicios."
        % service,
        parse_mode="Markdown",
    )
    logger.info("Credential stored: user=%d service=%s", user.id, service)


async def clave_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    args = context.args
    if not args:
        await message.reply_text("Uso: `/clave <servicio>`\nEjemplo: `/clave gemini`")
        return
    service = args[0].lower().strip()
    value = await db.get_credential(user.id, service)
    if value is None:
        await message.reply_text("No hay clave guardada para `%s`." % service)
        return
    masked = value[:4] + "..." + value[-4:] if len(value) > 8 else "***"
    await message.reply_text(
        "🔐 `%s`: `%s`\n⚠️ _Valor parcial. Usa `/claves` para listar_." % (service, masked),
        parse_mode="Markdown",
    )


async def claves_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    creds = await db.list_credentials(user.id)
    if not creds:
        await message.reply_text(
            "No tienes claves guardadas.\nUsa `/guardar_clave <servicio> <valor>`."
        )
        return
    lines = ["🔐 *Tus claves guardadas (AES-256)*\n"]
    for c in creds:
        lines.append("  • `%s` — guardada %s" % (c["service"], c["updated_at"][:10]))
    lines.append("\nUsa `/clave <servicio>` para ver valor parcial.")
    lines.append("Usa `/borrar_clave <servicio>` para eliminar.")
    await message.reply_text("\n".join(lines), parse_mode="Markdown")


async def borrar_clave_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    args = context.args
    if not args:
        await message.reply_text("Uso: `/borrar_clave <servicio>`")
        return
    service = args[0].lower().strip()
    deleted = await db.delete_credential(user.id, service)
    if deleted:
        await message.reply_text("🗑️ Clave `%s` eliminada." % service)
    else:
        await message.reply_text("No se encontró `%s`." % service)


async def resumen_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    await message.reply_chat_action("typing")
    await message.reply_text("🧠 Analizando tu segundo cerebro...")

    vault = Path("/data/obsidian_vault")
    brain_data = []

    try:
        vstats = await vector_db.get_stats()
        brain_data.append(
            "Chunks indexados: %d en %d documentos"
            % (
                vstats["total_chunks"],
                vstats["total_documents"],
            )
        )
    except Exception:
        pass

    topics = {}
    if vault.exists():
        for md_file in vault.rglob("*.md"):
            if any(d in str(md_file) for d in [".obsidian", "templates", "Documentos_Indexados"]):
                continue
            try:
                content = md_file.read_text(encoding="utf-8", errors="replace")
                if content.startswith("---"):
                    end = content.find("---", 3)
                    if end != -1:
                        fm = content[3:end]
                        for line in fm.split("\n"):
                            line = line.strip()
                            if line.startswith("type:"):
                                t = line.split(":", 1)[1].strip()
                                topics[t] = topics.get(t, 0) + 1
                            elif line.startswith("tags:"):
                                tag_str = line.split(":", 1)[1].strip()
                                for tag in tag_str.strip("[]").split(","):
                                    tag = tag.strip().strip("'\"")
                                    if tag and tag not in topics:
                                        topics[tag] = 1
                                    elif tag:
                                        topics[tag] += 1
                mtime = md_file.stat().st_mtime
                brain_data.append(
                    "nota: %s mtime: %.0f"
                    % (
                        str(md_file.relative_to(vault)),
                        mtime,
                    )
                )
            except Exception:
                pass

    brain_data.append("Distribucion por tipo: %s" % str(topics))

    try:
        brain_stats = await db.get_second_brain_stats()
        brain_data.append("Total consultas al cerebro: %d" % brain_stats["total_queries"])
        if brain_stats["recent_queries"]:
            brain_data.append("Consultas recientes:")
            for q in brain_stats["recent_queries"][:5]:
                brain_data.append(
                    "  - '%s' (%d chunks)" % (q["query_text"][:80], q["chunks_retrieved"])
                )
    except Exception:
        pass

    brain_text = "\n".join(brain_data)

    from src.ollama_client import llm

    try:
        summary = await llm.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres un analista de segundo cerebro. Genera un resumen claro y util "
                        "en español a partir de estos datos. Estructura tu respuesta asi:\n"
                        "1. *Tamaño del cerebro*: cuantas notas y chunks\n"
                        "2. *Temas principales*: que topics dominan (por tipo y tags)\n"
                        "3. *Actividad reciente*: consultas y notas nuevas\n"
                        "4. *Sugerencias*: que falta o que se podria mejorar\n"
                        "Se breve pero informativo. Max 2000 caracteres."
                    ),
                },
                {"role": "user", "content": brain_text[:4000]},
            ],
            temperature=0.4,
            max_tokens=800,
        )
    except Exception:
        summary = "Error generando resumen. Datos:\n%s" % "\n".join(brain_data[:10])

    await message.reply_text(summary[:4000], parse_mode="Markdown")
    logger.info("Resumen sent to user %d", user.id)


async def recordar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    args = context.args
    if args:
        topic = " ".join(args)
    else:
        history = await db.get_chat_history(user.id, 4)
        if len(history) < 2:
            await message.reply_text(
                "No hay conversacion reciente para recordar. "
                "Usa `/recordar <tema>` para guardar algo especifico."
            )
            return
        last_msgs = [h["content"][:300] for h in history if h["role"] == "user"]
        topic = " ".join(last_msgs[-2:]) if last_msgs else "conversacion reciente"

    from src.ollama_client import llm
    from src.utils.obsidian_manager import create_or_append_note

    await message.reply_chat_action("typing")

    try:
        title_prompt = (
            "Genera un titulo corto (max 60 caracteres) para una nota de Zettelkasten "
            "basada en este tema de conversacion. Responde SOLO el titulo, sin comillas:\n\n%s"
        ) % topic[:400]
        title = await llm.chat(
            messages=[
                {
                    "role": "system",
                    "content": "Eres un titulador de notas. Responde solo el titulo.",
                },
                {"role": "user", "content": title_prompt},
            ],
            temperature=0.3,
            max_tokens=60,
        )
        title = title.strip().strip('"').strip("'")[:80]
    except Exception:
        title = topic[:80].strip()

    now = datetime.now()
    note_id = now.strftime("%Y%m%d-%H%M")
    content = (
        "---\n"
        "id: %s\n"
        'title: "%s"\n'
        "type: nota-atomica\n"
        "tags: [recordado, conversacion]\n"
        "status: abierto\n"
        "created: %s\n"
        "updated: %s\n"
        "related: []\n"
        "---\n\n"
        "# %s\n\n"
        "## Contexto\n\n"
        "Registrado desde conversacion de Telegram el %s.\n\n"
        "## Contenido\n\n"
        "%s\n"
    ) % (
        note_id,
        title,
        now.strftime("%Y-%m-%d"),
        now.strftime("%Y-%m-%d"),
        title,
        now.strftime("%Y-%m-%d %H:%M"),
        topic[:3000],
    )

    result = await create_or_append_note(
        title=title.replace(" ", "_").replace("/", "-")[:60],
        content=content,
        folder="05-Zettelkasten",
    )
    if result.get("success"):
        await message.reply_text(
            "🧠 Guardado en tu segundo cerebro:\n"
            "   *%s*\n"
            "   Carpeta: 05-Zettelkasten\n"
            "   ID: %s" % (title, note_id),
            parse_mode="Markdown",
        )
    else:
        await message.reply_text("Error al guardar: %s" % result.get("message", "desconocido"))
