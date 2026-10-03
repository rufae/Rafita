"""Tests de las alertas de infraestructura (mejora 2)."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from src.config import settings
from src.utils import infra_monitor


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    return tmp_path


def test_check_disk_ok_cuando_hay_espacio(data_dir, monkeypatch):
    uso = type("U", (), {"total": 100, "used": 50, "free": 50})()
    monkeypatch.setattr(infra_monitor.shutil, "disk_usage", lambda p: uso)
    check = infra_monitor.check_disk()
    assert check["ok"] is True
    assert "50.0" in check["detail"]


def test_check_disk_alerta_por_porcentaje(data_dir, monkeypatch):
    uso = type("U", (), {"total": 100, "used": 86, "free": 14})()
    monkeypatch.setattr(infra_monitor.shutil, "disk_usage", lambda p: uso)
    check = infra_monitor.check_disk()
    assert check["ok"] is False
    assert check["severity"] == "warning"

    uso = type("U", (), {"total": 100, "used": 95, "free": 5})()
    monkeypatch.setattr(infra_monitor.shutil, "disk_usage", lambda p: uso)
    check = infra_monitor.check_disk()
    assert check["severity"] == "critical"


def test_check_backup_sin_datos(data_dir):
    check = infra_monitor.check_backup()
    assert check["ok"] is False
    assert "sin datos" in check["detail"]


def test_check_backup_reciente_ok(data_dir):
    stamp = datetime.now(UTC).isoformat()
    (data_dir / "backup-status.json").write_text(json.dumps({"timestamp": stamp}))
    check = infra_monitor.check_backup()
    assert check["ok"] is True


def test_check_backup_caducado(data_dir):
    stamp = (datetime.now(UTC) - timedelta(hours=30)).isoformat()
    (data_dir / "backup-status.json").write_text(json.dumps({"timestamp": stamp}))
    check = infra_monitor.check_backup()
    assert check["ok"] is False
    assert check["severity"] == "critical"


def test_check_restore_drill(data_dir):
    assert infra_monitor.check_restore_drill()["ok"] is True
    (data_dir / "restore-drill-status.json").write_text(json.dumps({"integrity": "ok"}))
    assert infra_monitor.check_restore_drill()["ok"] is True
    (data_dir / "restore-drill-status.json").write_text(json.dumps({"integrity": "failed"}))
    check = infra_monitor.check_restore_drill()
    assert check["ok"] is False
    assert "failed" in check["detail"]


async def test_notify_issues_envia_y_respeta_cooldown(data_dir, monkeypatch):
    sent = []
    store: dict[str, str] = {}

    async def fake_get(key):
        return store.get(key)

    async def fake_set(key, value, expires_at=None):
        store[key] = value

    monkeypatch.setattr("src.database.db.kv_get", fake_get)
    monkeypatch.setattr("src.database.db.kv_set", fake_set)

    async def send(text):
        sent.append(text)

    checks = [{"name": "disco", "ok": False, "severity": "critical", "detail": "disco al 95%"}]
    avisos = await infra_monitor.notify_issues(checks, send=send)
    assert len(avisos) == 1
    assert sent and "disco" in sent[0]

    # Segundo intento dentro del cooldown: no repite el aviso.
    avisos = await infra_monitor.notify_issues(checks, send=send)
    assert avisos == []
    assert len(sent) == 1


async def test_notify_issues_ignora_checks_ok(data_dir):
    sent = []

    async def send(text):
        sent.append(text)

    checks = [{"name": "ia", "ok": True, "severity": "info", "detail": "IA ok"}]
    avisos = await infra_monitor.notify_issues(checks, send=send)
    assert avisos == []
    assert sent == []


async def test_run_infra_checks_devuelve_todos_los_nombres(data_dir, monkeypatch):
    uso = type("U", (), {"total": 100, "used": 10, "free": 90})()
    monkeypatch.setattr(infra_monitor.shutil, "disk_usage", lambda p: uso)

    async def fake_llm():
        return {"name": "ia", "ok": True, "severity": "info", "detail": "IA ok"}

    async def fake_rag():
        return {"name": "rag", "ok": True, "severity": "info", "detail": "10 chunks"}

    async def fake_stt():
        return {"name": "stt", "ok": True, "severity": "info", "detail": "STT local"}

    monkeypatch.setattr(infra_monitor, "check_llm", fake_llm)
    monkeypatch.setattr(infra_monitor, "check_vector_db", fake_rag)
    monkeypatch.setattr(infra_monitor, "check_whisper_remote", fake_stt)
    checks = await infra_monitor.run_infra_checks()
    nombres = [c["name"] for c in checks]
    assert nombres == ["disco", "backup", "restore-drill", "ia", "rag", "stt"]


async def test_check_whisper_remote_caido_no_es_alertable(data_dir, monkeypatch):
    monkeypatch.setattr(settings, "whisper_remote_url", "http://torre:9001")

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            raise RuntimeError("conexion rechazada")

    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)
    check = await infra_monitor.check_whisper_remote()
    assert check["ok"] is False
    assert check["alertable"] is False

    sent = []

    async def send(text):
        sent.append(text)

    avisos = await infra_monitor.notify_issues([check], send=send)
    assert avisos == []
    assert sent == []
