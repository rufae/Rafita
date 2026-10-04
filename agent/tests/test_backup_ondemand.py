"""Backup bajo demanda (2026-10-03): trigger data/backup.trigger, estado y tools.

El agente no toca el host: escribe el trigger y la unidad systemd
rafita-backup-now.path (fuera de este test) lo consume.
"""

import json
from types import SimpleNamespace

from src.config import settings
from src.handlers.chat import _execute_tool
from src.utils import backup


class _FakeMessage:
    def __init__(self):
        self.texts = []

    async def reply_text(self, text, **kwargs):
        self.texts.append(text)


def _update(message, user_id=42):
    return SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=user_id),
    )


def _prepare(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    return data_dir


def test_trigger_escribe_fichero_de_peticion(tmp_path, monkeypatch):
    data_dir = _prepare(tmp_path, monkeypatch)
    result = backup.trigger_system_backup("web")
    assert result["success"] is True
    assert result["state"] == "requested"
    trigger = data_dir / backup.TRIGGER_NAME
    payload = json.loads(trigger.read_text(encoding="utf-8"))
    assert payload["source"] == "web"
    assert payload["requested_at"]


def test_trigger_no_duplica_peticion_reciente(tmp_path, monkeypatch):
    data_dir = _prepare(tmp_path, monkeypatch)
    first = backup.trigger_system_backup("telegram")
    trigger = data_dir / backup.TRIGGER_NAME
    mtime = trigger.stat().st_mtime
    second = backup.trigger_system_backup("telegram")
    assert first["state"] == "requested"
    assert second["state"] == "pending"
    assert trigger.stat().st_mtime == mtime


def test_trigger_error_si_no_puede_escribir(tmp_path, monkeypatch):
    # data_dir apunta a un fichero: el path del trigger no es escribible.
    ficherito = tmp_path / "no_dir"
    ficherito.write_text("x", encoding="utf-8")
    monkeypatch.setattr(settings, "data_dir", str(ficherito))
    result = backup.trigger_system_backup("tool")
    assert result["success"] is False
    assert result["state"] == "error"


def test_status_sin_ejecuciones(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    result = backup.system_backup_status()
    assert result["success"] is True
    assert result["has_run"] is False
    assert "ninguna copia" in result["message"]


def test_status_lee_backup_status_json(tmp_path, monkeypatch):
    data_dir = _prepare(tmp_path, monkeypatch)
    (data_dir / "backup-status.json").write_text(
        json.dumps(
            {
                "timestamp": "2026-10-02T03:35:02+02:00",
                "snapshot": "8570dae6",
                "size": "1.029 GiB",
                "usb_free": "120G",
                "rclone": "",
                "skipped": "ruta /etc/docker/daemon.json",
            }
        ),
        encoding="utf-8",
    )
    result = backup.system_backup_status()
    assert result["has_run"] is True
    assert "8570dae6" in result["message"]
    assert "1.029 GiB" in result["message"]
    assert "daemon.json" in result["message"]


def test_status_pendiente_senala_unidad_sin_instalar(tmp_path, monkeypatch):
    data_dir = _prepare(tmp_path, monkeypatch)
    (data_dir / "backup-status.json").write_text(
        json.dumps({"timestamp": "2026-10-02T03:35:02+02:00"}),
        encoding="utf-8",
    )
    backup.trigger_system_backup("tool")
    result = backup.system_backup_status()
    assert result["pending"] is True
    assert "rafita-backup-now.path" in result["message"]


async def test_tool_run_backup_y_status(monkeypatch, tmp_path):
    data_dir = _prepare(tmp_path, monkeypatch)
    result = await _execute_tool(1, "run_backup", {})
    assert result["success"] is True
    assert (data_dir / backup.TRIGGER_NAME).exists()

    status = await _execute_tool(1, "get_backup_status", {})
    assert status["success"] is True
    assert status["has_run"] is False
    assert status["pending"] is True


async def test_comando_telegram_backup_dispara_sistema():
    message = _FakeMessage()
    await backup.backup_command(_update(message), None)
    assert message.texts
    assert "Backup completo" in message.texts[0]
    assert (settings.data_path / backup.TRIGGER_NAME).exists()
    # limpieza para no arrastrar estado entre tests
    (settings.data_path / backup.TRIGGER_NAME).unlink()
