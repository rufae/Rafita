"""Heartbeat de fiabilidad (2026-10-07): vigila el RESULTADO, no el pipeline.

El 5-7/10 el `errorWorkflow` de n8n marco de verde ejecuciones con nodos
rotos (el briefing del 6/10 no llego y nadie se entero). Este worker
comprueba que la nota del dia existe en la boveda; si falta, la regenera
UNA vez en el propio proceso (funciona aunque n8n este caido) y avisa por
Telegram de forma honesta (exito, fallo o regeneracion).
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.config import settings
from src.logger import logger
from src.utils.vault_indexer import VAULT_PATH


def _find_today_note(name_pattern: str) -> Any:
    """Busca la nota de hoy (fecha local del usuario) pase donde pase."""
    hoy = datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d")
    for path in VAULT_PATH.rglob(name_pattern % hoy):
        return path
    return None


async def _notify_admins(text: str) -> None:
    from src.bot import bot

    for admin_id in settings.admin_ids or []:
        try:
            await bot.send_proactive_message(admin_id, text)
        except Exception as e:
            logger.warning("Heartbeat: no pude avisar a %s: %s", admin_id, str(e)[:80])


async def check_artifact(
    label: str,
    name_pattern: str,
    regenerate: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Garantiza la nota del dia: si falta, regenera una vez y avisa siempre."""
    if _find_today_note(name_pattern) is not None:
        logger.info("Heartbeat %s: la nota de hoy ya existe", label)
        return {"success": True, "presente": True}
    logger.warning("Heartbeat %s: falta la nota del dia; regenero", label)
    await _notify_admins(
        "❤️ *Heartbeat:* no encontré el %s de hoy. Lo regenero ahora mismo." % label
    )
    try:
        res = await regenerate()
    except Exception as e:
        res = {"success": False, "message": str(e)[:160]}
    if res.get("success"):
        texto = str(res.get("text") or res.get("message") or "").strip()
        await _notify_admins(
            "♻️ *Heartbeat:* %s regenerado:\n\n%s" % (label, texto[:3500] or "(sin cuerpo)")
        )
        return {"success": True, "presente": False, "regenerado": True}
    detalle = str(res.get("message") or res.get("error") or "sin detalle")
    await _notify_admins(
        "❌ *Heartbeat:* no pude regenerar el %s (%s). Revisa n8n y el agente; "
        "no lo doy por hecho." % (label, detalle)
    )
    return {
        "success": False,
        "presente": False,
        "regenerado": False,
        "message": detalle,
    }


async def _regen_briefing() -> dict[str, Any]:
    from src.services.automation_service import build_briefing

    return await build_briefing()


async def _regen_radar() -> dict[str, Any]:
    from src.services.automation_service import radar

    return await radar()


# kind, etiqueta para el usuario, patron de nota, hora de comprobacion, regen
_CHECKS: list[tuple[str, str, str, str, Callable[[], Awaitable[dict[str, Any]]]]] = [
    ("briefing", "briefing", "Briefing %s.md", "briefing", _regen_briefing),
    ("radar", "radar de IA", "Radar IA %s.md", "radar", _regen_radar),
]


class HeartbeatWorker:
    """Dos comprobaciones al dia (post-briefing 08:50, post-radar 09:40)."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._done: dict[str, str] = {}

    async def start(self, shutdown_event: asyncio.Event) -> None:
        self._shutdown_event = shutdown_event
        self._task = asyncio.create_task(self._run_loop())
        logger.info("HeartbeatWorker started")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("HeartbeatWorker stopped")

    def _time_for(self, kind: str) -> tuple[int, int]:
        raw = (
            settings.heartbeat_briefing_time
            if kind == "briefing"
            else settings.heartbeat_radar_time
        )
        try:
            hour, minute = map(int, str(raw).split(":"))
            return hour, minute
        except ValueError:
            return (8, 50) if kind == "briefing" else (9, 40)

    def _next_wait(self) -> float:
        """Segundos hasta la siguiente comprobacion pendiente."""
        now = datetime.now(ZoneInfo(settings.timezone))
        espera = 3600.0
        for kind, _label, _patron, _hora, _regen in _CHECKS:
            if self._done.get(kind) == now.strftime("%Y-%m-%d"):
                continue
            hour, minute = self._time_for(kind)
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                continue
            espera = min(espera, (target - now).total_seconds())
        return min(espera, 3600.0)

    async def _run_loop(self) -> None:
        try:
            while True:
                if self._shutdown_event and self._shutdown_event.is_set():
                    break
                now = datetime.now(ZoneInfo(settings.timezone))
                marca = now.strftime("%Y-%m-%d")
                for kind, label, patron, _hora, regen in _CHECKS:
                    if self._done.get(kind) == marca:
                        continue
                    hour, minute = self._time_for(kind)
                    if now < now.replace(hour=hour, minute=minute, second=0, microsecond=0):
                        continue
                    self._done[kind] = marca
                    try:
                        await check_artifact(label, patron, regen)
                    except Exception as e:
                        logger.warning("Heartbeat %s fallo: %s", label, str(e)[:120])
                wait = max(self._next_wait(), 30.0)
                await asyncio.sleep(min(wait, 60.0))
        except asyncio.CancelledError:
            logger.info("HeartbeatWorker loop cancelled")
        except Exception as e:
            logger.exception("HeartbeatWorker error: %s", e)
