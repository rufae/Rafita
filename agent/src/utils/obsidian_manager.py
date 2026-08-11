import re
from datetime import datetime
from pathlib import Path
from typing import Any

from src.logger import logger

OBSIDIAN_VAULT = Path("/data/obsidian_vault")


def _resolve_path(title: str, folder: str = "") -> Path:
    folder_part = folder.strip().strip("/\\") if folder else ""
    safe_title = _safe_filename(title)
    if folder_part:
        base = OBSIDIAN_VAULT / folder_part
    else:
        base = OBSIDIAN_VAULT
    return (base / safe_title).with_suffix(".md")


def _safe_filename(name: str) -> str:
    name = name.strip()
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", " ", name)
    return name[:200]


def _ensure_folder(folder: str) -> Path:
    folder_part = folder.strip().strip("/\\") if folder else ""
    if folder_part:
        target = OBSIDIAN_VAULT / folder_part
    else:
        target = OBSIDIAN_VAULT
    target.mkdir(parents=True, exist_ok=True)
    return target


async def create_or_append_note(title: str, content: str, folder: str = "") -> dict[str, Any]:
    filepath = _resolve_path(title, folder)
    _ensure_folder(folder)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    existed = filepath.exists()
    if existed:
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(f"\n\n---\n*Actualizado el {now_str}*\n\n{content}")
    else:
        header = f"---\ntitle: {title}\ncreated: {now_str}\n---\n\n"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(header + content)
    logger.info("Obsidian note %s: %s", "appended to" if existed else "created", filepath)
    return {
        "success": True,
        "action": "appended" if existed else "created",
        "filepath": str(filepath),
        "message": f"Nota '{title}' {'actualizada' if existed else 'creada'} en Obsidian.",
    }


async def read_note(title: str, folder: str = "") -> dict[str, Any]:
    filepath = _resolve_path(title, folder)
    if not filepath.exists():
        return {
            "success": False,
            "message": f"No encontré la nota '{title}' en Obsidian.",
        }
    content = filepath.read_text(encoding="utf-8")
    lines = content.split("\n")
    preview = "\n".join(lines[:50])
    if len(lines) > 50:
        preview += f"\n\n... y {len(lines) - 50} líneas más."
    logger.info("Obsidian note read: %s (%d lines)", filepath, len(lines))
    return {
        "success": True,
        "title": title,
        "content": preview,
        "full_content": content,
        "message": f"Nota '{title}' leída ({len(lines)} líneas).",
    }


async def search_notes_content(query: str) -> dict[str, Any]:
    if not OBSIDIAN_VAULT.exists():
        return {"success": True, "results": [], "message": "La bóveda Obsidian aún no existe."}
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    results = []
    for md_file in sorted(OBSIDIAN_VAULT.rglob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue
        matches = list(pattern.finditer(content))
        if matches:
            relative = md_file.relative_to(OBSIDIAN_VAULT)
            snippets = []
            for m in matches[:3]:
                start = max(0, m.start() - 60)
                end = min(len(content), m.end() + 60)
                snippet = content[start:end].replace("\n", " ").strip()
                if start > 0:
                    snippet = "..." + snippet
                if end < len(content):
                    snippet = snippet + "..."
                snippets.append(snippet)
            results.append({
                "file": str(relative),
                "title": md_file.stem,
                "folder": str(relative.parent) if relative.parent != Path(".") else "",
                "match_count": len(matches),
                "snippets": snippets,
            })
    results.sort(key=lambda r: r["match_count"], reverse=True)
    logger.info("Obsidian search '%s': %d notes found", query, len(results))
    return {
        "success": True,
        "results": results,
        "message": f"Encontré {len(results)} nota{'s' if len(results) != 1 else ''} con '{query}'.",
    }


VAULT_STRUCTURE = [
    "00-Inbox",
    "01-Proyectos",
    "02-Areas/Finanzas",
    "02-Areas/Salud",
    "02-Areas/Casa",
    "02-Areas/Trabajo",
    "03-Recursos",
    "04-Archivo",
    "05-Zettelkasten",
    "06-Diario",
    "Attachments",
]

ATTACHMENTS_DIR = OBSIDIAN_VAULT / "Attachments"


def save_attachment(src_path: Path, clean_name: str) -> dict[str, Any]:
    if not src_path.exists():
        return {"success": False, "message": "Archivo origen no existe: %s" % src_path}
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    ext = src_path.suffix.lower()
    safe_base = _safe_filename(clean_name)
    dest_path = ATTACHMENTS_DIR / ("%s%s" % (safe_base, ext))
    counter = 1
    while dest_path.exists():
        dest_path = ATTACHMENTS_DIR / ("%s_%d%s" % (safe_base, counter, ext))
        counter += 1
    import shutil as _shutil
    _shutil.copy2(str(src_path), str(dest_path))
    if not dest_path.exists():
        return {"success": False, "message": "No se pudo escribir en la carpeta Attachments de Obsidian."}
    logger.info("Attachment saved: %s -> %s (%d bytes)", src_path.name, dest_path.name, dest_path.stat().st_size)
    return {
        "success": True,
        "filename": dest_path.name,
        "filepath": str(dest_path),
        "obsidian_link": "![[%s]]" % dest_path.name,
        "message": "Imagen guardada en Attachments/%s" % dest_path.name,
    }


def create_note_with_image(
    title: str,
    body_text: str,
    image_filename: str,
    folder: str = "00-Inbox",
) -> dict[str, Any]:
    _ensure_folder(folder)
    filepath = _resolve_path(title, folder)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    content = (
        "---\ntitle: %s\ncreated: %s\n---\n\n"
        "## Descripcion de la imagen\n\n%s\n\n"
        "## Imagen\n\n![[%s]]\n"
        % (title, now_str, body_text.strip(), image_filename)
    )
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    if not filepath.exists():
        return {"success": False, "message": "No se pudo crear la nota en Obsidian."}
    logger.info("Obsidian note with image created: %s", filepath)
    return {
        "success": True,
        "filepath": str(filepath),
        "message": "Nota '%s.md' creada con imagen vinculada en %s." % (title, folder),
    }


async def initialize_vault_structure() -> None:
    for folder_path in VAULT_STRUCTURE:
        target = OBSIDIAN_VAULT / folder_path
        target.mkdir(parents=True, exist_ok=True)
    logger.info("Obsidian vault structure initialized (%d folders)", len(VAULT_STRUCTURE))


async def move_or_rename_file(source_path: str, dest_folder: str, new_name: str) -> dict[str, Any]:
    src = Path(source_path)
    if not src.exists():
        return {"success": False, "message": f"No existe el archivo: {source_path}"}
    if not str(src).startswith(str(OBSIDIAN_VAULT)):
        return {"success": False, "message": "Solo puedo mover archivos dentro de la boveda Obsidian."}
    folder_part = dest_folder.strip().strip("/\\") if dest_folder else ""
    dest_dir = (OBSIDIAN_VAULT / folder_part) if folder_part else src.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_filename(new_name)
    dest_path = dest_dir / (safe_name + src.suffix)
    if dest_path.exists():
        base = dest_path.stem
        counter = 1
        while dest_path.exists():
            dest_path = dest_dir / ("%s_%d%s" % (base, counter, src.suffix))
            counter += 1
    src.rename(dest_path)
    logger.info("File moved: %s -> %s", source_path, dest_path)
    return {
        "success": True,
        "source": str(src),
        "destination": str(dest_path),
        "message": f"Archivo movido a {str(dest_path.relative_to(OBSIDIAN_VAULT))}",
    }


async def delete_note(title: str, folder: str = "") -> dict[str, Any]:
    filepath = _resolve_path(title, folder)
    if not filepath.exists():
        return {
            "success": False,
            "message": f"No existe la nota '{title}' en Obsidian.",
        }
    filepath.unlink()
    logger.info("Obsidian note deleted: %s", filepath)
    return {
        "success": True,
        "message": f"Nota '{title}' eliminada de Obsidian.",
    }


async def sync_calendar_to_obsidian(events: list, note_title: str = "Calendario Semanal") -> dict[str, Any]:
    if not events:
        return {"success": True, "message": "No hay eventos para sincronizar."}

    folder = "02-Areas/Agenda"
    filepath = _resolve_path(note_title, folder)
    _ensure_folder(folder)

    lines = ["## Calendar Sincronizado de Google Calendar\n"]
    for ev in events:
        start_raw = ev.get("start", "")
        end_raw = ev.get("end", "")
        title = ev.get("title", "Sin titulo")

        start_fmt = start_raw
        end_fmt = end_raw
        try:
            from datetime import datetime as _dt
            if "T" in start_raw:
                dt_start = _dt.fromisoformat(start_raw.replace("Z", "+00:00"))
                start_fmt = dt_start.strftime("%H:%M")
            if "T" in end_raw:
                dt_end = _dt.fromisoformat(end_raw.replace("Z", "+00:00"))
                end_fmt = dt_end.strftime("%H:%M")
        except Exception:
            pass

        link = ev.get("html_link", "")
        link_md = " [link](%s)" % link if link else ""
        lines.append("- [ ] %s - %s | %s%s" % (start_fmt, end_fmt, title, link_md))

    content = "\n".join(lines) + "\n"

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    existed = filepath.exists()
    if existed:
        with open(filepath, "a", encoding="utf-8") as f:
            f.write("\n\n---\n*Sincronizado el %s*\n\n%s" % (now_str, content))
    else:
        header = "---\ntitle: %s\ncreated: %s\n---\n\n" % (note_title, now_str)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(header + content)

    logger.info("Obsidian hyper-sync: %d eventos sincronizados en %s", len(events), filepath.name)
    return {
        "success": True,
        "message": "Calendario sincronizado en Obsidian: %s (%d eventos)" % (filepath.name, len(events)),
        "filepath": str(filepath),
    }
