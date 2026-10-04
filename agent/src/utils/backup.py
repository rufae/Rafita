import asyncio
import io
import json
import os
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from src.config import settings
from src.logger import logger

BACKUP_INCLUDE_DIRS = ["excels", "exports"]

# Disparo de la copia completa del servidor (2026-10-03): el agente solo
# escribe un fichero de peticion en /data (montado del host); la unidad
# systemd rafita-backup-now.path (PathExists) lanza rafita-backup.service
# y esta borra el trigger al empezar (ExecStartPre). Sin docker.sock ni SSH:
# el contenedor nunca toca el host salvo por este fichero.
TRIGGER_NAME = "backup.trigger"
_TRIGGER_DEDUP_S = 300  # doble disparo: si hace <5 min, no se reenvia
_TRIGGER_STALE_S = 600  # si el trigger lleva >10 min, la unidad no corre


def _snapshot_db(db_path: Path) -> Path:
    """Copia consistente de la BD con la API backup() de SQLite.

    Antes se copiaba rafita.db en caliente: con WAL activado, el ZIP podia
    perder las transacciones recientes o quedar inconsistente. backup() lee
    un snapshot coherente (incluye el WAL) sin bloquear al agente.
    """
    fd, tmp_name = tempfile.mkstemp(prefix="rafita_backup_", suffix=".db")
    os.close(fd)
    tmp_path = Path(tmp_name)
    src = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        dst = sqlite3.connect(str(tmp_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return tmp_path


async def create_backup(chat_id: int) -> bytes | None:
    data_path = settings.data_path
    db_path = settings.db_path_obj

    if not db_path.exists():
        logger.error("Database not found at %s", db_path)
        return None

    buffer = io.BytesIO()
    snapshot: Path | None = None

    try:
        snapshot = await asyncio.to_thread(_snapshot_db, db_path)

        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(str(snapshot), "db/rafita.db")
            logger.debug("Added db/rafita.db (snapshot consistente) to backup")

            for dir_name in BACKUP_INCLUDE_DIRS:
                dir_path = data_path / dir_name
                if dir_path.exists() and dir_path.is_dir():
                    for file_path in dir_path.rglob("*"):
                        if file_path.is_file():
                            arcname = str(file_path.relative_to(data_path))
                            zf.write(str(file_path), arcname)
                            logger.debug("Added %s to backup", arcname)

        buffer.seek(0)
        size_bytes = buffer.getbuffer().nbytes
        logger.info(
            "Backup created for chat %d: %d bytes",
            chat_id,
            size_bytes,
        )
        return buffer.getvalue()

    except Exception as e:
        logger.exception("Backup creation failed for chat %d: %s", chat_id, e)
        return None
    finally:
        if snapshot is not None:
            try:
                snapshot.unlink()
            except OSError:
                pass


def trigger_system_backup(source: str) -> dict[str, Any]:
    """Pide al host lanzar la copia completa (restic al USB) ahora.

    Deja data/backup.trigger; el host (systemd path unit) lo consume y
    ejecuta backup.sh, que avisa por Telegram al terminar o fallar.
    """
    trigger = settings.data_path / TRIGGER_NAME
    now = time.time()
    try:
        if trigger.exists() and now - trigger.stat().st_mtime < _TRIGGER_DEDUP_S:
            return {
                "success": True,
                "state": "pending",
                "message": (
                    "Backup solicitado hace poco y aun en cola; no duplico la "
                    "peticion. Estado con '¿qué estado tiene el backup?'."
                ),
            }
        payload = {
            "requested_at": datetime.now().isoformat(timespec="seconds"),
            "source": source,
        }
        trigger.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as e:
        logger.error("No pude escribir el trigger de backup: %s", e)
        return {
            "success": False,
            "state": "error",
            "message": "No pude solicitar el backup (no escribo en /data).",
        }
    logger.info("Backup del servidor solicitado (source=%s)", source)
    return {
        "success": True,
        "state": "requested",
        "message": (
            "Backup completo del servidor solicitado (restic al USB de "
            "backup). Te aviso por Telegram al terminar; si el USB no esta "
            "conectado tambien te aviso."
        ),
    }


def system_backup_status() -> dict[str, Any]:
    """Estado de la ultima copia completa + peticion pendiente, si la hay."""
    from src.utils.automation_status import read_status_file

    status = read_status_file("backup-status.json")
    trigger = settings.data_path / TRIGGER_NAME
    ts = str(status.get("timestamp") or "")

    pending = False
    try:
        if trigger.exists() and time.time() - trigger.stat().st_mtime < _TRIGGER_STALE_S:
            pending = True
    except OSError:
        pass

    if not ts:
        return {
            "success": True,
            "has_run": False,
            "pending": pending,
            "message": (
                "Todavia no hay ninguna copia completa registrada. Si acabo "
                "de pedirla, tarda unos minutos."
            ),
        }

    age_h: float | None = None
    try:
        stamp = datetime.fromisoformat(ts)
        # date -Is lleva zona horaria; datetime.now() no: mezclarlas reventaba.
        now = datetime.now(stamp.tzinfo) if stamp.tzinfo else datetime.now()
        age_h = (now - stamp).total_seconds() / 3600.0
    except ValueError:
        pass

    parts = ["Último backup: %s" % ts.replace("T", " ")]
    if age_h is not None:
        parts.append("(hace %.1f h)" % age_h)
    if status.get("snapshot"):
        parts.append("· snapshot %s" % status["snapshot"])
    if status.get("size"):
        parts.append("· %s" % status["size"])
    if status.get("usb_free"):
        parts.append("· USB libre %s" % status["usb_free"])
    if status.get("rclone"):
        parts.append("· %s" % status["rclone"])
    if status.get("skipped"):
        parts.append("· ⚠️ omitidos: %s" % status["skipped"])
    message = " ".join(parts) + "."

    if pending:
        message += (
            " ⚠️ Hay una petición de backup reciente sin ejecutar: ¿está "
            "instalada la unidad rafita-backup-now.path en el host?"
        )
    return {
        "success": True,
        "has_run": True,
        "pending": pending,
        "timestamp": ts,
        "message": message,
    }


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    r"""\/backup: pide la copia COMPLETA del servidor ahora (disparo host)."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    result = trigger_system_backup("telegram")
    prefix = "✅ " if result.get("success") else "❌ "
    await message.reply_text(prefix + str(result.get("message", "")))


async def backup_zip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    r"""\/backup_zip: respaldo ZIP de datos (BD + excels + exports) al chat."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    await message.reply_text("🔄 Generando respaldo... Esto puede tomar unos segundos.")
    await message.reply_chat_action("upload_document")

    backup_data = await create_backup(user.id)

    if backup_data is None:
        await message.reply_text("❌ Error al generar el respaldo. Verifica los logs.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"rafita_backup_{user.id}_{timestamp}.zip"

    try:
        await message.reply_document(
            document=io.BytesIO(backup_data),
            filename=filename,
            caption=f"📦 Respaldo Rafita - {timestamp}\n"
            f"• Base de datos: rafita.db\n"
            f"• Archivos: excels, exports\n"
            f"• Tamaño: {len(backup_data) / 1024:.1f} KB",
        )
        logger.info(
            "Backup sent to user %d: %s (%d bytes)",
            user.id,
            filename,
            len(backup_data),
        )
    except Exception as e:
        logger.exception("Failed to send backup to user %d: %s", user.id, e)
        await message.reply_text(
            "❌ Error al enviar el respaldo. "
            "El archivo se guardó localmente pero no pudo enviarse por Telegram."
        )
