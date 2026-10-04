"""Copia local de los datos de Google en el segundo cerebro.

Idea del usuario (2026-09-27): tras conectar Google, poder guardar contactos,
calendario y Drive como notas locales para consultar sin depender de la API
(el segundo cerebro pasa a tener informacion real, no solo apuntes).

Las notas se crean en la carpeta `Google/` del vault; el indexador del vault
las detecta por el watcher y las deja disponibles en la busqueda semantica.
"""

from datetime import datetime
from typing import Any

from src.logger import logger
from src.services.google_services_manager import google_services
from src.utils.obsidian_manager import overwrite_note

FOLDER = "Google"


def _fmt_dt(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return value or "-"


def _frontmatter(title: str, extra: list[str] | None = None) -> list[str]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = ["---", f"title: {title}", "tipo: copia-google", f"actualizado: {now}"]
    if extra:
        lines.extend(extra)
    lines.extend(["---", ""])
    return lines


async def sync_google_to_vault() -> dict[str, Any]:
    """Vuelca contactos, proximos eventos y Drive al vault (carpeta Google/).

    Degradacion honesta (2026-10-04): cada fuente de Google se exporta por
    separado; si una falla (p. ej. Gmail con credenciales caducadas) se
    exporta lo disponible, NO se sobreescriben las notas de las fuentes
    caidas y el mensaje dice que se omitio y por que, en vez de tumbar el
    comando con un \"error interno\".
    """
    await google_services.initialize()
    if not google_services.is_ready:
        return {
            "success": False,
            "message": "Google no esta conectado. Usa /setup_google primero.",
        }

    omitidas: list[tuple[str, str]] = []

    async def _fuente(seccion: str, coro):
        try:
            return await coro
        except Exception as e:
            motivo = str(e)[:160]
            omitidas.append((seccion, motivo))
            logger.warning("Sync google->vault: fuente '%s' omitida: %s", seccion, motivo)
            return None

    contacts = (await _fuente("contactos", google_services.list_all_contacts()) or {}).get(
        "contacts", []
    )
    events = (await _fuente("calendario", google_services.list_calendar_events(days=90)) or {}).get(
        "events", []
    )
    drive = (
        await _fuente("drive", google_services.list_drive(kind="all", max_results=50)) or {}
    ).get("files", [])
    tasks = (await _fuente("tareas", google_services.list_tasks()) or {}).get("tasks", [])
    mails = (
        await _fuente(
            "correos",
            google_services.search_gmail(query="is:unread newer_than:7d", max_results=10),
        )
        or {}
    ).get("messages", [])

    contact_lines = _frontmatter("Contactos Google")
    contact_lines += [
        "# Contactos de Google",
        "",
        "| Nombre | Correo | Telefono |",
        "|---|---|---|",
    ]
    for c in contacts:
        contact_lines.append(
            "| %s | %s | %s |"
            % (c.get("name") or "-", c.get("email") or "-", c.get("phone") or "-")
        )
    contact_lines.append("")

    event_lines = _frontmatter("Calendario Google")
    event_lines += ["# Proximos eventos (90 dias)", "", "| Fecha | Evento |", "|---|---|"]
    for e in events:
        event_lines.append("| %s | %s |" % (_fmt_dt(e.get("start", "")), e.get("title") or "-"))
    event_lines.append("")

    drive_lines = _frontmatter("Drive Google")
    drive_lines += ["# Google Drive", "", "| Nombre | Tipo | Modificado |", "|---|---|---|"]
    for f in drive:
        is_folder = f.get("mimeType") == "application/vnd.google-apps.folder"
        drive_lines.append(
            "| %s | %s | %s |"
            % (
                f.get("name") or "-",
                "carpeta" if is_folder else "archivo",
                _fmt_dt(f.get("modifiedTime", "")),
            )
        )
    drive_lines.append("")

    task_lines = _frontmatter("Tareas Google")
    task_lines += ["# Tareas pendientes (Google Tasks)", "", "| Tarea | id |", "|---|---|"]
    for t in tasks:
        task_lines.append("| %s | %s |" % (t.get("title") or "-", t.get("id") or "-"))
    task_lines.append("")

    mail_lines = _frontmatter("Correo Google")
    mail_lines += [
        "# Correo sin leer (7 dias)",
        "",
        "| Asunto | De | Fecha |",
        "|---|---|---|",
    ]
    for m in mails:
        mail_lines.append(
            "| %s | %s | %s |"
            % (m.get("subject") or "-", m.get("from") or "-", m.get("date") or "-")
        )
    mail_lines.append("")

    secciones = [
        ("contactos", "Contactos Google", "\n".join(contact_lines)),
        ("calendario", "Calendario Google", "\n".join(event_lines)),
        ("drive", "Drive Google", "\n".join(drive_lines)),
        ("tareas", "Tareas Google", "\n".join(task_lines)),
        ("correos", "Correo Google", "\n".join(mail_lines)),
    ]
    claves_omitidas = {s for s, _ in omitidas}
    # Una fuente caida NO sobreescribe su nota: se conserva la copia
    # anterior (con datos reales) en vez de dejarla vacia.
    notes = [(titulo, cuerpo) for s, titulo, cuerpo in secciones if s not in claves_omitidas]

    if not notes:
        return {
            "success": False,
            "message": (
                "No pude copiar nada de Google: %s. Vuelve a conectar la cuenta "
                "con /setup_google." % _detalle_omitidas(omitidas)
            ),
            "omitidas": [s for s, _ in omitidas],
            "files": [],
        }

    results = []
    for title, content in notes:
        results.append(await overwrite_note(title, content, folder=FOLDER))

    ok = all(r.get("success") for r in results)
    logger.info(
        "Sync Google->vault: %d contactos, %d eventos, %d elementos de Drive, "
        "%d tareas, %d correos",
        len(contacts),
        len(events),
        len(drive),
        len(tasks),
        len(mails),
    )
    mensaje = (
        "Copia local creada en el segundo cerebro: %d contactos, %d eventos, "
        "%d elementos de Drive, %d tareas y %d correos (carpeta Google/). "
        "Ya puedes preguntarme por ellos sin depender de la API."
        % (len(contacts), len(events), len(drive), len(tasks), len(mails))
    )
    if omitidas:
        mensaje += " Fuentes omitidas: %s (sus notas anteriores se conservan)." % _detalle_omitidas(
            omitidas
        )
    return {
        "success": ok,
        "message": mensaje,
        "omitidas": [s for s, _ in omitidas],
        "files": [r.get("filepath") for r in results],
    }


def _detalle_omitidas(omitidas: list[tuple[str, str]]) -> str:
    return "; ".join("%s (%s)" % (seccion, motivo) for seccion, motivo in omitidas)
