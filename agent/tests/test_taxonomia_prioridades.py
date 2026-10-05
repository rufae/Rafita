"""Tareas.md línea 69: taxonomía única INFO/WARNING/ERROR/CRITICAL.

Antes convivían 4 vocabularios paralelos: severity de infra_monitor (sin
error), alert_type con urgent, automation_runs.status (ok/error) y avisos
con la prioridad solo por emoji. Ahora todo pasa por src/utils/severity.
"""

import hashlib
import hmac
import json
from pathlib import Path

import pytest_asyncio
from fastapi.testclient import TestClient

from src.handlers import chat as chat_mod
from src.utils import severity, webhook_server

SECRET = "s3cret-sev"
RAIZ = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture
async def db(tmp_path):
    from src.database import DatabaseManager

    manager = DatabaseManager(db_path=tmp_path / "sev.db")
    await manager.initialize()
    yield manager
    await manager.close()


@pytest_asyncio.fixture
async def data_dir(tmp_path, monkeypatch):
    from src.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    yield tmp_path


# ---------- la taxonomía ----------


def test_niveles_en_orden_y_emoji_por_nivel():
    assert severity.LEVELS == ("info", "warning", "error", "critical")
    assert severity.RANK[severity.INFO] < severity.RANK[severity.WARNING]
    assert severity.RANK[severity.WARNING] < severity.RANK[severity.ERROR]
    assert severity.RANK[severity.ERROR] < severity.RANK[severity.CRITICAL]
    assert severity.emoji("info") == "ℹ️"
    assert severity.emoji("warning") == "⚠️"
    assert severity.emoji("error") == "❌"
    assert severity.emoji("critical") == "🚨"


def test_normalize_alias_de_vocabularios_antiguos():
    # alerts.alert_type "urgent" legacy
    assert severity.normalize("urgent") == "critical"
    # automation_runs.status "ok"/"error"
    assert severity.normalize("ok") == "info"
    assert severity.normalize("error") == "error"
    # CAP de AEMET
    assert severity.normalize("Moderate") == "warning"
    assert severity.normalize("Severe") == "error"
    assert severity.normalize("Extreme") == "critical"
    # desconocidos -> default (no inventa niveles)
    assert severity.normalize("otro") == "info"
    assert severity.normalize("otro", default=severity.WARNING) == "warning"
    assert severity.normalize(None) == "info"


def test_worst_y_tag():
    assert severity.worst("info", "warning", "critical") == "critical"
    assert severity.worst() == "info"
    assert severity.worst("urgent", "severe") == "critical"
    assert severity.tag("urgent") == "CRITICAL"
    assert severity.tag("ok") == "INFO"


def test_niveles_infra_monitor_pertenecen_a_la_taxonomia():
    from src.utils import infra_monitor

    fuente = Path(infra_monitor.__file__).read_text(encoding="utf-8")
    import re

    literales = set(re.findall(r'"severity": "([a-z]+)"', fuente))
    assert literales <= set(severity.LEVELS), literales


async def test_infra_monitor_avisa_con_nivel_textual(data_dir):  # noqa: F811
    from src.utils import infra_monitor

    enviados = []

    async def send(text):
        enviados.append(text)

    checks = [{"name": "disco", "ok": False, "severity": "error", "detail": "disco al 95%"}]
    await infra_monitor.notify_issues(checks, send=send)
    assert enviados
    assert "[ERROR]" in enviados[0]
    assert "❌" in enviados[0]


# ---------- alertas (alert_type) ----------


async def test_create_alert_normaliza_urgent_a_critical(monkeypatch):
    guardadas = []

    async def fake_add_alert(chat_id, message, alert_type="info", expires_at=None):
        guardadas.append(alert_type)
        return 9

    monkeypatch.setattr(chat_mod.db, "add_alert", fake_add_alert)
    for tipo in ("urgent", "critical", "warning", "info", "error"):
        await chat_mod._execute_tool(1, "create_alert", {"message": "x", "alert_type": tipo})

    assert guardadas == ["critical", "critical", "warning", "info", "error"]


def test_admin_tipos_son_la_taxonomia():
    fuente = (RAIZ / "agent" / "src" / "handlers" / "admin.py").read_text(encoding="utf-8")
    assert '"Tipos: info (default), warning, error, critical' in fuente
    # sin diccionarios de emoji paralelos: todo va por severity
    assert "type_emoji" not in fuente
    assert "severity.emoji" in fuente


# ---------- automation_runs (status ok/error + severity) ----------


async def test_record_derivado_status_y_listado_con_severity(db):
    await db.record_automation_run("e1", "Briefing", "ok")
    await db.record_automation_run("e2", "Radar", "error", "boom")
    await db.record_automation_run("e3", "CRM", "error", None, None, severity="critical")

    filas = {r["execution_id"]: r for r in await db.list_automation_runs(days=7)}
    assert filas["e1"]["severity"] == "info"
    assert filas["e2"]["severity"] == "error"
    assert filas["e3"]["severity"] == "critical"


async def test_endpoint_n8n_run_acepta_y_deriva_severity(tmp_path, monkeypatch):
    from src.database import DatabaseManager

    manager = DatabaseManager(db_path=tmp_path / "sev-runs.db")
    await manager.initialize()
    try:
        monkeypatch.setattr(webhook_server, "_webhook_secret", SECRET)
        monkeypatch.setattr(webhook_server, "db", manager)
        client = TestClient(webhook_server.app)

        def firmar(payload):
            body = json.dumps(payload).encode("utf-8")
            sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
            return client.post(
                "/api/n8n/run",
                content=body,
                headers={"Content-Type": "application/json", "X-Webhook-Signature": sig},
            )

        # con severity explicito (lo manda el nodo Firmar informe)
        resp = firmar(
            {
                "execution_id": "d1",
                "workflow": "W",
                "status": "error",
                "severity": "critical",
                "error": "x",
                "finished_at": "2026-10-05",
            }
        )
        assert resp.status_code == 200
        # sin severity: se deriva de status
        resp = firmar(
            {"execution_id": "d2", "workflow": "W", "status": "ok", "finished_at": "2026-10-05"}
        )
        assert resp.status_code == 200

        filas = {r["execution_id"]: r for r in await manager.list_automation_runs(days=7)}
        assert filas["d1"]["severity"] == "critical"
        assert filas["d2"]["severity"] == "info"
    finally:
        await manager.close()


def test_migracion_backfill_severity_en_status():
    import asyncio
    import pathlib
    import tempfile

    from src.database import DatabaseManager

    async def crear():
        ruta = pathlib.Path(tempfile.mkdtemp()) / "backfill.db"
        # schema viejo: automation_runs sin severity
        import sqlite3

        con = sqlite3.connect(ruta)
        con.execute(
            "CREATE TABLE automation_runs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "execution_id TEXT NOT NULL UNIQUE,"
            "workflow TEXT NOT NULL,"
            "status TEXT NOT NULL CHECK(status IN ('ok','error')),"
            "error TEXT,"
            "finished_at TEXT,"
            "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        con.execute(
            "INSERT INTO automation_runs (execution_id, workflow, status) VALUES ('v1','A','error')"
        )
        con.execute(
            "INSERT INTO automation_runs (execution_id, workflow, status) VALUES ('v2','B','ok')"
        )
        con.commit()
        con.close()

        manager = DatabaseManager(db_path=ruta)
        await manager.initialize()
        filas = {r["execution_id"]: r for r in await manager.list_automation_runs(days=7)}
        await manager.close()
        return filas

    filas = asyncio.run(crear())
    assert filas["v1"]["severity"] == "error"
    assert filas["v2"]["severity"] == "info"
