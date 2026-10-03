import asyncio
import io
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from src.config import settings
from src.logger import logger

BACKUP_INCLUDE_DIRS = ["excels", "exports"]


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


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
