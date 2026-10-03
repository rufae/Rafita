"""Comando /demo: recorrido guiado por las capacidades de Rafita.

Pensado para presentaciones en vivo: ejecuta secciones REALES del sistema
(herramientas incluidas) con protección individual —una sección que falla no
tumba el resto— y termina con los pasos para probar chat y voz. El texto se
construye con `build_demo_text` para poder servirlo también desde el chat web.
"""

from collections.abc import Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes

from src.config import settings
from src.logger import logger


async def build_demo_text(chat_id: int) -> str:
    """Ensambla el recorrido de demo (todas las secciones protegidas)."""
    from src.handlers.chat import _execute_tool

    secciones: list[str] = []

    async def seccion(titulo: str, fn: Callable[[], Awaitable[str]]) -> None:
        try:
            texto = await fn()
        except Exception as e:
            logger.warning("demo: sección '%s' falló: %s", titulo, e)
            texto = "⚠️ no disponible en este momento"
        secciones.append("%s\n%s" % (titulo, texto))

    async def _ia() -> str:
        from src.ollama_client import llm

        health = await llm.check_health()
        estado = health.get("status", "?")
        detalle = health.get("detail") or ("modelo %s" % settings.ollama_model)
        return "Modelo *%s* — %s (%s)" % (settings.ollama_model, estado, detalle)

    async def _agenda() -> str:
        from src.database import db

        eventos = await db.get_upcoming_events(chat_id)
        if not eventos:
            return "Sin eventos próximos. Prueba: `/evento 2026-12-25 18:00 Cena navideña`"
        lineas = []
        for ev in eventos[:3]:
            lineas.append("• %s — %s" % (ev.get("title", "?"), ev.get("event_datetime", "?")))
        return "\n".join(lineas)

    async def _finanzas() -> str:
        result = await _execute_tool(chat_id, "get_finance_summary", {})
        return str(result.get("message", ""))[:400]

    async def _tiempo() -> str:
        result = await _execute_tool(chat_id, "get_weather", {})
        return str(result.get("message", ""))[:400]

    async def _segundo_cerebro() -> str:
        from src.utils.vector_manager import vector_db

        stats = await vector_db.get_stats()
        busqueda = await _execute_tool(
            chat_id, "search_second_brain", {"query": "proyectos y personas", "top_k": 2}
        )
        resumen = str(busqueda.get("message", ""))[:500]
        return "*%s* notas / *%s* fragmentos indexados.\nBúsqueda real con citas [S1]...:\n%s" % (
            stats.get("total_documents", 0),
            stats.get("total_chunks", 0),
            resumen,
        )

    async def _voz() -> str:
        return (
            "En la web, pestaña *Llamada*: habla conmigo en tiempo real "
            "(barge-in, transcripción y respuesta por voz). También puedes "
            "enviarme notas de voz por Telegram y dictar correos: "
            "'ejemplo punto ejemplo arroba gmail punto com'."
        )

    await seccion("🧠 *Inteligencia*", _ia)
    await seccion("📅 *Agenda*", _agenda)
    await seccion("💰 *Finanzas del mes*", _finanzas)
    await seccion("🌤 *Tiempo*", _tiempo)
    await seccion("📚 *Segundo cerebro (RAG con citas)*", _segundo_cerebro)
    await seccion("🎙 *Voz y dictado*", _voz)

    texto = "🎬 *Demo de Rafita* — capacidades reales, sin humo\n\n" + "\n\n".join(secciones)
    texto += (
        "\n\n✅ *Todo esto funciona ahora mismo.* Prueba a escribirme en el "
        "chat libre: 'busca en mis notas qué sabes de Ana', 'redacta un correo "
        "para...', o 'manda un mensaje a mamá'."
    )
    return texto


async def demo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    from src.utils.telegram_fmt import reply_md

    chat_id = update.effective_user.id if update.effective_user else 0
    texto = await build_demo_text(chat_id)
    await reply_md(message, texto[:4000])
