"""Cobertura de src/utils/backup.py: respaldo ZIP de datos."""

import io
import sqlite3
import zipfile
from types import SimpleNamespace

from src.config import settings
from src.utils import backup


class _FakeMessage:
    def __init__(self, document_error=None):
        self.texts = []
        self.documents = []
        self.chat_actions = []
        self._document_error = document_error

    async def reply_text(self, text, **kwargs):
        self.texts.append(text)

    async def reply_chat_action(self, action):
        self.chat_actions.append(action)

    async def reply_document(self, document=None, filename=None, caption=None):
        if self._document_error:
            raise self._document_error
        self.documents.append((document, filename, caption))


def _update(message, user_id=42):
    return SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=user_id) if user_id is not None else None,
    )


def _prepare_data_dir(tmp_path, monkeypatch, with_db=True):
    data_dir = tmp_path / "data"
    db_dir = data_dir / "db"
    db_dir.mkdir(parents=True)
    if with_db:
        # BD SQLite real: create_backup usa la API backup() (snapshot
        # consistente con WAL) y un fichero falso no es valido.
        con = sqlite3.connect(str(db_dir / "rafita.db"))
        con.execute("CREATE TABLE t (x INTEGER)")
        con.commit()
        con.close()
    excels = data_dir / "excels"
    excels.mkdir()
    (excels / "gastos.csv").write_text("a,b", encoding="utf-8")
    exports = data_dir / "exports"
    exports.mkdir()
    (exports / "informe.txt").write_text("informe", encoding="utf-8")
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "db_path", str(db_dir / "rafita.db"))
    return data_dir


async def test_create_backup_missing_db(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "db_path", str(data_dir / "db" / "rafita.db"))
    assert await backup.create_backup(42) is None


async def test_create_backup_includes_db_and_dirs(tmp_path, monkeypatch):
    _prepare_data_dir(tmp_path, monkeypatch)
    data = await backup.create_backup(42)
    assert data is not None
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        assert "db/rafita.db" in names
        assert "excels/gastos.csv" in names
        assert "exports/informe.txt" in names
        assert zf.read("db/rafita.db").startswith(b"SQLite format 3")
    assert set(names) <= {"db/rafita.db", "excels/gastos.csv", "exports/informe.txt"}


async def test_create_backup_zip_error_returns_none(tmp_path, monkeypatch):
    _prepare_data_dir(tmp_path, monkeypatch)

    class _BoomZip:
        def __init__(self, *args, **kwargs):
            raise OSError("disco lleno")

    monkeypatch.setattr(backup.zipfile, "ZipFile", _BoomZip)
    assert await backup.create_backup(42) is None


async def test_backup_command_without_message_or_user():
    message = _FakeMessage()
    await backup.backup_zip_command(
        SimpleNamespace(effective_message=None, effective_user=None), None
    )
    assert message.texts == []
    await backup.backup_zip_command(
        SimpleNamespace(effective_message=message, effective_user=None), None
    )
    assert message.texts == []


async def test_backup_command_reports_failure(tmp_path, monkeypatch):
    _prepare_data_dir(tmp_path, monkeypatch, with_db=False)

    async def fake_create(chat_id):
        return None

    monkeypatch.setattr(backup, "create_backup", fake_create)
    message = _FakeMessage()
    await backup.backup_zip_command(_update(message), None)
    assert message.chat_actions == ["upload_document"]
    assert any("Error al generar el respaldo" in t for t in message.texts)
    assert message.documents == []


async def test_backup_command_sends_document(tmp_path, monkeypatch):
    _prepare_data_dir(tmp_path, monkeypatch)

    async def fake_create(chat_id):
        return b"ZIPDATA"

    monkeypatch.setattr(backup, "create_backup", fake_create)
    message = _FakeMessage()
    await backup.backup_zip_command(_update(message, user_id=7), None)

    assert len(message.documents) == 1
    document, filename, caption = message.documents[0]
    assert filename.startswith("rafita_backup_7_")
    assert filename.endswith(".zip")
    assert "Respaldo Rafita" in caption
    assert "KB" in caption
    assert isinstance(document, io.BytesIO)


async def test_backup_command_send_error_falls_back_to_text(tmp_path, monkeypatch):
    _prepare_data_dir(tmp_path, monkeypatch)

    async def fake_create(chat_id):
        return b"ZIPDATA"

    monkeypatch.setattr(backup, "create_backup", fake_create)
    message = _FakeMessage(document_error=RuntimeError("telegram caido"))
    await backup.backup_zip_command(_update(message), None)
    assert any("no pudo enviarse" in t for t in message.texts)
