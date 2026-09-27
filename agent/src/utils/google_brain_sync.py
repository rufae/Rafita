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
    """Vuelca contactos, proximos eventos y Drive al vault (carpeta Google/)."""
    await google_services.initialize()
    if not google_services.is_ready:
        return {
            "success": False,
            "message": "Google no esta conectado. Usa /setup_google primero.",
        }

    contacts = (await google_services.list_all_contacts()).get("contacts", [])
    events = (await google_services.list_calendar_events(days=90)).get("events", [])
    drive = (await google_services.list_drive(kind="all", max_results=50)).get("files", [])

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

    notes = [
        ("Contactos Google", "\n".join(contact_lines)),
        ("Calendario Google", "\n".join(event_lines)),
        ("Drive Google", "\n".join(drive_lines)),
    ]
    results = []
    for title, content in notes:
        results.append(await overwrite_note(title, content, folder=FOLDER))

    ok = all(r.get("success") for r in results)
    logger.info(
        "Sync Google->vault: %d contactos, %d eventos, %d elementos de Drive",
        len(contacts),
        len(events),
        len(drive),
    )
    return {
        "success": ok,
        "message": (
            "Copia local creada en el segundo cerebro: %d contactos, %d eventos "
            "y %d elementos de Drive (carpeta Google/). Ya puedes preguntarme "
            "por ellos sin depender de la API." % (len(contacts), len(events), len(drive))
        ),
        "files": [r.get("filepath") for r in results],
    }
