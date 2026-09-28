"""Sync inteligente Google <-> boveda (2026-09-28, automatizacion A).

Convenciones en la boveda (carpeta `00-Inbox/`):
- **Tareas.md**: lineas `- [ ] texto` (pendiente) y `- [x] texto` (hecha).
  Se crean en Google Tasks si no existen; las hechas en Google se marcan `[x]`
  en la nota y las `[x]` de la nota se completan en Google (completar gana).
- **Calendario.md**: lineas `YYYY-MM-DD HH:MM | Titulo`. Se crean en Google
  Calendar si no existen; si el titulo ya existe y cambia la hora, se MUEVE
  (nunca se duplica).

Deduplicacion por titulo normalizado (sin acentos ni mayusculas) contra el
estado real de Google: no hace falta mantener mapeos que se queden obsoletos.
"""

import re
import unicodedata
from typing import Any

from src.logger import logger

TASKS_TITLE = "Tareas"
CAL_TITLE = "Calendario"
INBOX_FOLDER = "00-Inbox"

_TASK_RE = re.compile(r"^\s*-\s*\[( |x|X)\]\s*(.+?)\s*$")
_EVENT_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})\s*\|\s*(.+?)\s*$")


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip()


async def _read_lines(title: str, folder: str) -> list[str]:
    from src.utils.obsidian_manager import read_note

    result = await read_note(title, folder)
    if not result.get("success"):
        return []
    content = result.get("full_content") or result.get("content") or ""
    return content.splitlines()


async def _write_lines(title: str, folder: str, lines: list[str]) -> None:
    from src.utils.obsidian_manager import overwrite_note

    await overwrite_note(title, "\n".join(lines) + "\n", folder=folder)


async def _google_ready() -> bool:
    try:
        from src.services.google_services_manager import google_services

        return await google_services.initialize() and google_services.is_ready
    except Exception:
        return False


async def _sync_tasks() -> dict[str, int]:
    """Boveda -> Google Tasks (y completadas de Google -> boveda)."""
    stats = {"created": 0, "completed_google": 0, "completed_vault": 0}
    lines = await _read_lines(TASKS_TITLE, INBOX_FOLDER)
    if not lines:
        return stats
    from src.services.google_services_manager import google_services

    all_tasks = (await google_services.list_tasks(show_completed=True, max_results=50)).get(
        "tasks", []
    )
    pending: dict[str, dict[str, Any]] = {}
    completed: set[str] = set()
    for task in all_tasks:
        title = _normalize(task.get("title", ""))
        if task.get("status") == "completed":
            completed.add(title)
        else:
            pending[title] = task

    changed = False
    out: list[str] = []
    for line in lines:
        match = _TASK_RE.match(line)
        if not match:
            out.append(line)
            continue
        done = match.group(1).lower() == "x"
        title = match.group(2).strip()
        key = _normalize(title)
        if done and key in pending:
            result = await google_services.complete_task(pending[key]["id"])
            if result.get("success"):
                stats["completed_google"] += 1
        elif not done and key not in pending and key not in completed:
            result = await google_services.create_task(title)
            if result.get("success"):
                stats["created"] += 1
        if not done and key in completed:
            # Completar gana: se hizo en Google -> marcar en la nota.
            line = line.replace("[ ]", "[x]", 1)
            stats["completed_vault"] += 1
            changed = True
        out.append(line)
    if changed or stats["created"] or stats["completed_google"]:
        await _write_lines(TASKS_TITLE, INBOX_FOLDER, out)
    return stats


async def _sync_events() -> dict[str, int]:
    """Boveda -> Google Calendar (crea o mueve; nunca duplica)."""
    stats = {"created": 0, "moved": 0}
    lines = await _read_lines(CAL_TITLE, INBOX_FOLDER)
    if not lines:
        return stats
    from src.services.google_services_manager import google_services
    from src.utils.google_calendar_manager import gcal

    events = (await google_services.list_calendar_events(days=365, max_results=250)).get(
        "events", []
    )
    by_title: dict[str, dict[str, Any]] = {}
    for event in events:
        key = _normalize(event.get("title", ""))
        if key:
            by_title.setdefault(key, event)

    for line in lines:
        match = _EVENT_RE.match(line)
        if not match:
            continue
        date, hm, title = match.groups()
        start_iso = "%sT%s:00" % (date, hm)
        key = _normalize(title)
        existing = by_title.get(key)
        if existing:
            current = str(existing.get("start", ""))
            if current[:16] != start_iso[:16]:
                result = await gcal.move_event(existing.get("id", ""), start_iso)
                if result.get("success"):
                    stats["moved"] += 1
        else:
            result = await google_services.create_event(title, start_iso)
            if result.get("success"):
                stats["created"] += 1
    return stats


async def sync_google_vault() -> dict[str, Any]:
    """Sync bidireccional completo. Devuelve resumen con contadores."""
    google_ok = await _google_ready()
    result: dict[str, Any] = {"success": True, "google": google_ok}

    if google_ok:
        try:
            from src.utils.google_brain_sync import sync_google_to_vault

            export = await sync_google_to_vault()
            result["export"] = bool(export.get("success"))
        except Exception as e:
            logger.warning("Sync: export Google->vault fallo: %s", str(e)[:150])
            result["export"] = False
        result["tasks"] = await _sync_tasks()
        result["events"] = await _sync_events()
    else:
        result["export"] = False
        result["tasks"] = {"created": 0, "completed_google": 0, "completed_vault": 0}
        result["events"] = {"created": 0, "moved": 0}

    tasks = result["tasks"]
    events = result["events"]
    changes = sum(tasks.values()) + sum(events.values())
    result["changes"] = changes
    result["message"] = (
        "Sync %s: %d tareas creadas, %d completadas (Google->nota: %d), "
        "%d eventos creados, %d movidos."
        % (
            "completa" if google_ok else "sin Google (solo lectura local)",
            tasks.get("created", 0),
            tasks.get("completed_google", 0),
            tasks.get("completed_vault", 0),
            events.get("created", 0),
            events.get("moved", 0),
        )
    )
    logger.info("Sync Google<->vault: %s", result["message"])
    return result
