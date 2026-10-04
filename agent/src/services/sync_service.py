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
from datetime import datetime
from typing import Any

from src.logger import logger

TASKS_TITLE = "Tareas"
CAL_TITLE = "Calendario"
INBOX_FOLDER = "00-Inbox"

_TASK_RE = re.compile(r"^\s*-\s*\[( |x|X)\]\s+(.+?)\s*$")
_EVENT_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})\s*\|\s*(.+?)\s*$")

# Maximo de altas que se anaden en una pasada desde Google (evita inundar la
# nota si hay decenas de tareas/eventos pendientes de anadir).
MAX_ALTA_GOOGLE = 20


def _parse_iso(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _event_line_from_iso(iso: str) -> str | None:
    dt = _parse_iso(iso)
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M")


def _note_mtime(title: str) -> float:
    """mtime de la nota en la boveda (para el choque de conflictos)."""
    try:
        from src.utils.obsidian_manager import OBSIDIAN_VAULT

        return (OBSIDIAN_VAULT / INBOX_FOLDER / ("%s.md" % title)).stat().st_mtime
    except OSError:
        return 0.0


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
    """Boveda <-> Google Tasks.

    - Boveda -> Google: crea las `[ ]` que no estan y completa `[x]`.
    - Google -> boveda: completa lo completado en Google y **anade** las
      tareas pendientes que solo existen en Google (cierre del circulo).
    - Politica: completar gana siempre.
    """
    stats = {
        "created": 0,
        "completed_google": 0,
        "completed_vault": 0,
        "added_from_google": 0,
    }
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
    note_keys: set[str] = set()
    for line in lines:
        match = _TASK_RE.match(line)
        if not match:
            out.append(line)
            continue
        done = match.group(1).lower() == "x"
        title = match.group(2).strip()
        key = _normalize(title)
        note_keys.add(key)
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

    # Google -> boveda: pendientes de Google ausentes de la nota.
    added = 0
    for key, task in pending.items():
        if added >= MAX_ALTA_GOOGLE:
            break
        if key in note_keys:
            continue
        out.append("- [ ] %s" % str(task.get("title", "")).strip())
        note_keys.add(key)
        stats["added_from_google"] += 1
        added += 1
        changed = True

    if changed or stats["created"] or stats["completed_google"]:
        await _write_lines(TASKS_TITLE, INBOX_FOLDER, out)
    return stats


async def _sync_events() -> dict[str, int]:
    """Boveda <-> Google Calendar (crea, mueve y resuelve conflictos).

    - Boveda -> Google: crea los que faltan y mueve los que cambian de hora.
    - Google -> boveda: anade los eventos que solo existen en Google.
    - **Conflicto** (mismo titulo, horas distintas): gana el lado que cambio
      despues de la ultima modificacion de la otra parte — Google si su
      `updated` es posterior al mtime de la nota; si no, la nota (se mueve
      el evento). Nunca se duplica.
    """
    stats = {"created": 0, "moved": 0, "google_wins": 0, "added_from_google": 0}
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

    note_mtime = _note_mtime(CAL_TITLE)
    changed = False
    out: list[str] = []
    matched: set[str] = set()
    for line in lines:
        match = _EVENT_RE.match(line)
        if not match:
            out.append(line)
            continue
        date, hm, title = match.groups()
        start_iso = "%sT%s:00" % (date, hm)
        key = _normalize(title)
        matched.add(key)
        existing = by_title.get(key)
        if existing:
            current = str(existing.get("start", ""))
            if current[:16] != start_iso[:16]:
                google_updated = _parse_iso(existing.get("updated", ""))
                if google_updated is not None and google_updated.timestamp() > note_mtime:
                    # Conflicto: el evento se movio en Google DESPUES de la
                    # ultima edicion de la nota -> gana Google y se reescribe
                    # la linea (no se revierte el movimiento).
                    fecha = _event_line_from_iso(current)
                    if fecha:
                        out.append("%s | %s" % (fecha, title))
                        stats["google_wins"] += 1
                        changed = True
                        continue
                result = await gcal.move_event(existing.get("id", ""), start_iso)
                if result.get("success"):
                    stats["moved"] += 1
        else:
            result = await google_services.create_event(title, start_iso)
            if result.get("success"):
                stats["created"] += 1
        out.append(line)

    # Google -> boveda: eventos de Google ausentes de la nota.
    added = 0
    for event in events:
        if added >= MAX_ALTA_GOOGLE:
            break
        key = _normalize(event.get("title", ""))
        if not key or key in matched:
            continue
        fecha = _event_line_from_iso(str(event.get("start", "")))
        if fecha is None:
            continue
        out.append("%s | %s" % (fecha, str(event.get("title", "")).strip()))
        matched.add(key)
        stats["added_from_google"] += 1
        added += 1
        changed = True

    if changed:
        await _write_lines(CAL_TITLE, INBOX_FOLDER, out)
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
        result["tasks"] = {
            "created": 0,
            "completed_google": 0,
            "completed_vault": 0,
            "added_from_google": 0,
        }
        result["events"] = {"created": 0, "moved": 0, "google_wins": 0, "added_from_google": 0}

    tasks = result["tasks"]
    events = result["events"]
    changes = sum(tasks.values()) + sum(events.values())
    result["changes"] = changes
    result["message"] = (
        "Sync %s: %d tareas creadas, %d completadas (Google->nota: %d), "
        "%d anadidas desde Google; %d eventos creados, %d movidos, "
        "%d ajustados a Google y %d anadidos desde Google."
        % (
            "completa" if google_ok else "sin Google (solo lectura local)",
            tasks.get("created", 0),
            tasks.get("completed_google", 0),
            tasks.get("completed_vault", 0),
            tasks.get("added_from_google", 0),
            events.get("created", 0),
            events.get("moved", 0),
            events.get("google_wins", 0),
            events.get("added_from_google", 0),
        )
    )
    logger.info("Sync Google<->vault: %s", result["message"])
    return result
