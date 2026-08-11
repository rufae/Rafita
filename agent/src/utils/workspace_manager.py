import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import settings

WORKSPACE_ROOT = Path("/workspace")
MAX_READ_SIZE = 100 * 1024
MAX_LIST_DEPTH = 3
IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea", ".vscode"}
TEXT_EXTENSIONS = {
    ".py", ".md", ".txt", ".yml", ".yaml", ".json", ".jsonc",
    ".env", ".cfg", ".ini", ".conf", ".toml", ".xml", ".html",
    ".css", ".js", ".ts", ".sh", ".bat", ".ps1", ".sql", ".csv",
    ".log", ".dockerfile", ".gitignore",
}


def _safe_path(relative_path: str) -> Path:
    relative_path = relative_path.strip().strip("/\\") if relative_path else ""
    if not relative_path:
        return WORKSPACE_ROOT
    resolved = (WORKSPACE_ROOT / relative_path).resolve()
    if not str(resolved).startswith(str(WORKSPACE_ROOT.resolve())):
        raise ValueError("Path traversal detected: %s" % relative_path)
    if not resolved.exists():
        raise FileNotFoundError("Path not found: %s" % relative_path)
    return resolved


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return "%d B" % size_bytes
    elif size_bytes < 1024 * 1024:
        return "%.1f KB" % (size_bytes / 1024)
    elif size_bytes < 1024 * 1024 * 1024:
        return "%.1f MB" % (size_bytes / (1024 * 1024))
    return "%.1f GB" % (size_bytes / (1024 * 1024 * 1024))


def _is_text_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_EXTENSIONS or path.suffix == ""


async def list_workspace_files(relative_path: str = "") -> dict[str, Any]:
    try:
        target = _safe_path(relative_path)
    except (ValueError, FileNotFoundError) as e:
        return {"success": False, "message": str(e)}
    items = []
    total_size = 0
    file_count = 0
    dir_count = 0
    try:
        for entry in sorted(target.iterdir()):
            if entry.name.startswith(".") and entry.name not in (".env", ".gitignore"):
                continue
            if entry.is_dir():
                if entry.name in IGNORE_DIRS:
                    continue
                dir_count += 1
                try:
                    sub_count = len([f for f in entry.rglob("*") if f.is_file() and not any(p.startswith(".") for p in f.parts)])
                except PermissionError:
                    sub_count = -1
                items.append({
                    "name": entry.name,
                    "type": "dir",
                    "size": "",
                    "children": sub_count if sub_count >= 0 else "?",
                })
            elif entry.is_file():
                file_count += 1
                sz = entry.stat().st_size
                total_size += sz
                items.append({
                    "name": entry.name,
                    "type": "file",
                    "size": _format_size(sz),
                    "ext": entry.suffix,
                })
    except PermissionError as e:
        return {"success": False, "message": "Permission denied: %s" % e}
    return {
        "success": True,
        "path": str(target),
        "relative": relative_path or ".",
        "items": items,
        "summary": "%d directorios, %d archivos (%s)" % (dir_count, file_count, _format_size(total_size)),
    }


async def read_workspace_file(file_path: str) -> dict[str, Any]:
    try:
        target = _safe_path(file_path)
    except (ValueError, FileNotFoundError) as e:
        return {"success": False, "message": str(e)}
    if not target.is_file():
        return {"success": False, "message": "'%s' no es un archivo." % file_path}
    if not _is_text_file(target):
        return {"success": False, "message": "Solo puedo leer archivos de texto. '%s' tiene extensión binaria." % target.suffix}
    try:
        size = target.stat().st_size
        if size > MAX_READ_SIZE:
            return {"success": False, "message": "Archivo demasiado grande (%s). Máximo: 100 KB." % _format_size(size)}
        content = target.read_text(encoding="utf-8")
        lines = content.split("\n")
        return {
            "success": True,
            "path": str(target),
            "size": _format_size(size),
            "lines": len(lines),
            "content": content,
            "preview": "\n".join(lines[:30]) + ("\n..." if len(lines) > 30 else ""),
        }
    except UnicodeDecodeError:
        return {"success": False, "message": "No puedo decodificar el archivo como UTF-8."}
    except Exception as e:
        return {"success": False, "message": "Error al leer: %s" % e}


async def get_system_health() -> dict[str, Any]:
    report = {}
    report["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        db_path = Path(settings.db_path)
        if db_path.exists():
            db_size = db_path.stat().st_size
            report["database"] = {
                "path": str(db_path),
                "size": _format_size(db_size),
                "size_bytes": db_size,
            }
        else:
            report["database"] = {"path": str(db_path), "status": "not_found"}
    except Exception as e:
        report["database"] = {"error": str(e)}

    try:
        usage = shutil.disk_usage(str(WORKSPACE_ROOT))
        report["disk"] = {
            "total": _format_size(usage.total),
            "used": _format_size(usage.used),
            "free": _format_size(usage.free),
            "percent_used": round(usage.used / usage.total * 100, 1),
        }
    except Exception as e:
        report["disk"] = {"error": str(e)}

    try:
        log_path = settings.log_path
        if log_path.exists():
            log_files = sorted(log_path.glob("*.log"), key=os.path.getmtime, reverse=True)
            recent_errors = []
            total_logs = 0
            for lf in log_files[:3]:
                try:
                    content = lf.read_text(encoding="utf-8")
                    total_logs += len(content.split("\n"))
                    for line in content.split("\n")[-50:]:
                        if "ERROR" in line or "exception" in line.lower() or "traceback" in line.lower():
                            recent_errors.append(line.strip())
                except Exception:
                    pass
            report["logs"] = {
                "files_checked": len(log_files[:3]),
                "last_lines": total_logs,
                "recent_errors": recent_errors[-10:],
                "error_count": len(recent_errors),
            }
        else:
            report["logs"] = {"status": "no_logs_found"}
    except Exception as e:
        report["logs"] = {"error": str(e)}

    try:
        from src.database import db as dbm
        chat_count = len(await dbm.get_all_chat_ids())
        report["chats"] = {"active_chats": chat_count}
    except Exception as e:
        report["chats"] = {"error": str(e)}

    report["health"] = "healthy" if (
        report.get("database", {}).get("status") != "not_found"
        and report.get("disk", {}).get("free")
    ) else "degraded"

    return report
