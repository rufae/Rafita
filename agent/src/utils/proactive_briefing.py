"""Briefing matutino y recordatorios proactivos (mejoras 2 y 6, 2026-09-28).

- Briefing: cada dia a `BRIEFING_TIME` resume agenda de hoy, correos sin leer
  del ultimo dia y tiempo (open-meteo, gratis sin API key si hay coordenadas).
- Recordatorios: avisa UNA vez por evento de Google de las proximas 24-48 h y
  por tareas de Google vencidas hoy, sin repetirse (kv_store).
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.config import settings
from src.database import db
from src.logger import logger
from src.utils.holidays import proximo_festivo

# Nombres de dia y mes en espanol fijos en el codigo: `strftime("%A"/"%B")`
# depende del locale del contenedor (en CI sale "Tuesday"), y el briefing
# debe leerse igual en todas las maquinas.
_DIAS_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_MESES_ES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


async def _weather_summary() -> str:
    lat, lon = settings.briefing_lat, settings.briefing_lon
    if not lat and not lon:
        return ""
    try:
        import httpx

        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
            "&timezone=auto&forecast_days=1"
        )
        async with httpx.AsyncClient(timeout=10.0) as client:
            data = (await client.get(url)).json()
        daily = data.get("daily", {}) or {}
        tmax = (daily.get("temperature_2m_max") or [None])[0]
        tmin = (daily.get("temperature_2m_min") or [None])[0]
        rain = (daily.get("precipitation_probability_max") or [None])[0]
        if tmax is None:
            return ""
        text = "🌡 %.0f-%.0f °C" % (tmin if tmin is not None else tmax, tmax)
        if rain is not None:
            text += ", %.0f%% de lluvia" % rain
        return text
    except Exception as e:
        logger.warning("Briefing: tiempo no disponible: %s", e)
        return ""


async def _agenda_lines(days: int) -> list[str]:
    try:
        from src.services.google_services_manager import google_services

        if not await google_services.initialize() or not google_services.is_ready:
            return []
        result = await google_services.list_calendar_events(days=days, max_results=10)
        lines = []
        for ev in result.get("events", []):
            start = ev.get("start", "")
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                when = dt.strftime("%d/%m %H:%M")
            except Exception:
                when = start
            lines.append("  • %s — %s" % (when, ev.get("title") or "sin título"))
        return lines
    except Exception as e:
        logger.warning("Briefing: agenda no disponible: %s", e)
        return []


async def _mail_lines() -> list[str]:
    try:
        from src.services.google_services_manager import google_services

        if not google_services.is_ready:
            return []
        result = await google_services.search_gmail(query="is:unread newer_than:1d", max_results=5)
        return [
            "  • %s (de %s)" % (m.get("subject") or "sin asunto", m.get("from") or "?")
            for m in result.get("messages", [])
        ]
    except Exception as e:
        logger.warning("Briefing: correo no disponible: %s", e)
        return []


async def _holiday_line() -> str | None:
    """Linea «Próximo festivo: lunes 12 de octubre (Fiesta Nacional ...)».

    Usa `utils.holidays.proximo_festivo` (Nager.Date). Devuelve None si no
    queda ningun festivo en el ano o si la API falla: el briefing se envia
    igual, sin la linea.
    """
    try:
        proximo = await proximo_festivo()
    except Exception as e:
        logger.debug("Briefing: festivos no disponibles: %s", e)
        return None
    if not proximo:
        return None
    fecha, nombre = proximo
    dia = _DIAS_ES[fecha.weekday()]
    mes = _MESES_ES[fecha.month - 1]
    return "Próximo festivo: %s %d de %s (%s)" % (dia, fecha.day, mes, nombre)


async def send_briefing(bot: Any) -> int:
    """Envia el briefing del dia a los administradores. Devuelve cuantos envios."""
    if not settings.briefing_enabled:
        return 0
    agenda = await _agenda_lines(days=1)
    mail = await _mail_lines()
    weather = await _weather_summary()
    try:
        festivo = await _holiday_line()
    except Exception as e:
        # Defensa extra: el briefing nunca debe caerse por la linea de festivos.
        logger.debug("Briefing: linea de festivo omitida: %s", e)
        festivo = None

    lines = ["☀️ *Buenos días — briefing de hoy*", ""]
    if weather:
        lines.append("*Tiempo:* %s" % weather)
    if festivo:
        # _holiday_line ya trae «Próximo festivo: ...»; se compone igual que
        # el resto de etiquetas: *🎉 Próximo festivo:* lunes 12 de octubre (...)
        lines.append("*🎉 Próximo festivo:* %s" % festivo.removeprefix("Próximo festivo: "))
    lines.append("*📅 Agenda de hoy:*")
    lines.extend(agenda or ["  • Nada previsto"])
    if mail:
        lines.append("")
        lines.append("*📧 Correos sin leer (último día):*")
        lines.extend(mail)
    text = "\n".join(lines)

    sent = 0
    for admin_id in settings.admin_ids or []:
        try:
            await bot.send_proactive_message(admin_id, text)
            sent += 1
        except Exception as e:
            logger.error("Briefing: fallo enviando a %s: %s", admin_id, e)
    logger.info("Briefing enviado a %d destinatario(s)", sent)
    return sent


async def send_proactive_reminders(bot: Any) -> int:
    """Avisa una sola vez por evento proximo (24-48h) y tarea vencida hoy."""
    try:
        from src.services.google_services_manager import google_services

        if not await google_services.initialize() or not google_services.is_ready:
            return 0
        result = await google_services.list_calendar_events(days=2, max_results=25)
        events = result.get("events", [])
        tasks_result = await google_services.list_tasks()
        tasks = tasks_result.get("tasks", [])
    except Exception as e:
        logger.warning("Recordatorios proactivos: Google no disponible: %s", e)
        return 0

    now = datetime.now(ZoneInfo(settings.timezone))
    sent = 0
    for ev in events:
        try:
            start = datetime.fromisoformat(str(ev.get("start", "")).replace("Z", "+00:00"))
        except Exception:
            continue
        hours = (start - now).total_seconds() / 3600.0
        if not (0 <= hours <= 24):
            continue
        key = "proactive:event:%s:%s" % (ev.get("title", ""), ev.get("start", ""))
        if await db.kv_get(key):
            continue
        when = start.strftime("%A %d/%m a las %H:%M")
        day_label = "hoy" if hours < 12 else "mañana"
        for admin_id in settings.admin_ids or []:
            try:
                await bot.send_proactive_message(
                    admin_id,
                    "⏰ *Recordatorio:* %s tienes *%s* (%s)" % (day_label, ev.get("title"), when),
                )
                sent += 1
            except Exception as e:
                logger.error("Recordatorio: fallo enviando a %s: %s", admin_id, e)
        await db.kv_set(key, "1")
        logger.info("Recordatorio enviado para '%s' (%s)", ev.get("title"), when)

    for task in tasks:
        key = "proactive:task:%s" % task.get("id", task.get("title", ""))
        if await db.kv_get(key):
            continue
        for admin_id in settings.admin_ids or []:
            try:
                await bot.send_proactive_message(
                    admin_id,
                    "✅ *Tarea pendiente hoy:* %s" % task.get("title"),
                )
                sent += 1
            except Exception as e:
                logger.error("Recordatorio de tarea: fallo a %s: %s", admin_id, e)
        await db.kv_set(key, "1")
    return sent


# ---------- Eval RAG semanal (2026-10-06): avisa SOLO si el recall cae ----------

_RAG_EVAL_RECALL_MIN = 0.5
_RAG_EVAL_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "rag_eval.py"


async def _run_rag_eval() -> dict[str, Any]:
    """Ejecuta agent/scripts/rag_eval.py como subproceso y devuelve su reporte."""
    import os
    import sys
    import tempfile

    out = Path(tempfile.mkdtemp()) / "rag_eval.json"
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(_RAG_EVAL_SCRIPT),
        "--json",
        str(out),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        env=dict(os.environ),
    )
    _, err = await asyncio.wait_for(proc.communicate(), timeout=900.0)
    if proc.returncode != 0 or not out.exists():
        detalle = (err or b"").decode("utf-8", "replace")[:200]
        raise RuntimeError("rag_eval fallo (rc=%s): %s" % (proc.returncode, detalle))
    report: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    return report


async def rag_eval_weekly(bot: Any, state: dict[str, str], now: datetime | None = None) -> bool:
    """Cada domingo corre la eval del gold set RAG; avisa si recall@5 < minimo."""
    now = now or datetime.now(ZoneInfo(settings.timezone))
    if now.weekday() != 6:
        return False
    week = now.strftime("%G-W%V")
    if state.get("last_week") == week:
        return False
    state["last_week"] = week
    try:
        report = await _run_rag_eval()
    except Exception as e:
        logger.warning("Eval RAG semanal fallo: %s", e)
        return False
    positives = report.get("positives") or {}
    top_k = report.get("top_k", 5)
    recall = float(positives.get("recall@%s" % top_k, 0.0) or 0.0)
    mrr = float(positives.get("mrr@%s" % top_k, 0.0) or 0.0)
    logger.info("Eval RAG semanal: recall@%s=%.2f mrr=%.2f", top_k, recall, mrr)
    if recall >= _RAG_EVAL_RECALL_MIN:
        return True
    texto = (
        "⚠️ *Eval RAG semanal:* el recall@%s ha bajado a *%.2f* (mínimo %.2f; "
        "MRR %.2f). Revisa el índice o el modelo de embeddings."
        % (top_k, recall, _RAG_EVAL_RECALL_MIN, mrr)
    )
    for admin_id in settings.admin_ids or []:
        try:
            await bot.send_proactive_message(admin_id, texto)
        except Exception as e:
            logger.warning("Eval RAG: no se pudo avisar a %s: %s", admin_id, e)
    return True


class BriefingWorker:
    """Tick cada 30 min: recordatorios proactivos + briefing a la hora fijada."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._last_briefing_day = ""
        self._rag_eval_state: dict[str, str] = {}
        self._failures = 0

    async def start(self, shutdown_event: asyncio.Event) -> None:
        self._shutdown_event = shutdown_event
        self._task = asyncio.create_task(self._run_loop())
        logger.info("BriefingWorker started (briefing a las %s)", settings.briefing_time)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("BriefingWorker stopped")

    async def _run_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(1800)
                if self._shutdown_event and self._shutdown_event.is_set():
                    break
                try:
                    await self._tick()
                    self._failures = 0
                except Exception as e:
                    self._failures += 1
                    backoff = min(self._failures * 60, 600)
                    logger.exception("BriefingWorker tick failed (%d): %s", self._failures, e)
                    await asyncio.sleep(backoff)
        except asyncio.CancelledError:
            logger.info("BriefingWorker loop cancelled")

    async def _tick(self) -> None:
        from src.bot import bot

        await send_proactive_reminders(bot)
        # Reglas condicionales ("avisa si llueve/la RAM supera X") cada tick.
        try:
            from src.utils.conditional_rules import evaluate_and_fire

            await evaluate_and_fire(bot)
        except Exception as e:
            logger.warning("Reglas condicionales: %s", e)
        # Eval RAG semanal (domingos) con aviso si el recall cae.
        try:
            await rag_eval_weekly(bot, self._rag_eval_state)
        except Exception as e:
            logger.warning("Eval RAG semanal: %s", e)
        now = datetime.now(ZoneInfo(settings.timezone))
        try:
            hour = int(settings.briefing_time.split(":")[0])
        except Exception:
            hour = 8
        if now.hour == hour and now.minute < 30:
            day = now.strftime("%Y-%m-%d")
            if self._last_briefing_day != day:
                self._last_briefing_day = day
                await send_briefing(bot)
