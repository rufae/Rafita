"""Heartbeat de fiabilidad: detecta artefactos ausentes, regenera y avisa."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.config import settings
from src.utils import heartbeat as hb


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(hb, "VAULT_PATH", tmp_path)
    avisos = []

    async def fake_notify(text):
        avisos.append(text)

    monkeypatch.setattr(hb, "_notify_admins", fake_notify)
    return tmp_path, avisos


def _nota_hoy(patron: str) -> str:
    return patron % datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d")


async def test_presente_no_regenera_ni_avisa(vault, monkeypatch):
    tmp, avisos = vault
    (tmp / _nota_hoy("Briefing %s.md")).write_text("# ok", encoding="utf-8")

    async def no_llamar():
        raise AssertionError("no debe regenerar si la nota existe")

    res = await hb.check_artifact("briefing", "Briefing %s.md", no_llamar)
    assert res == {"success": True, "presente": True}
    assert avisos == []


async def test_falta_y_regen_ok_avisa_dos_veces(vault):
    tmp, avisos = vault

    async def regen():
        return {"success": True, "text": "Briefing regenerado de prueba."}

    res = await hb.check_artifact("briefing", "Briefing %s.md", regen)
    assert res["success"] is True
    assert res["regenerado"] is True
    assert len(avisos) == 2
    assert "regenerado" in avisos[1]
    assert "Briefing regenerado de prueba." in avisos[1]


async def test_falta_y_regen_falla_avisa_honesto(vault):
    _tmp, avisos = vault

    async def regen():
        return {"success": False, "message": "n8n caido"}

    res = await hb.check_artifact("radar de IA", "Radar IA %s.md", regen)
    assert res["success"] is False
    assert len(avisos) == 2
    assert "no pude regenerar" in avisos[1]
    assert "n8n caido" in avisos[1]
    assert "no lo doy por hecho" in avisos[1]


async def test_regen_lanza_excepcion_no_rompe(vault):
    _tmp, avisos = vault

    async def regen():
        raise RuntimeError("boom")

    res = await hb.check_artifact("briefing", "Briefing %s.md", regen)
    assert res["success"] is False
    assert "boom" in res["message"]
    assert len(avisos) == 2


def test_worker_parsea_horas_invalidas(monkeypatch, tmp_path):
    monkeypatch.setattr(hb, "VAULT_PATH", tmp_path)
    w = hb.HeartbeatWorker()
    monkeypatch.setattr(settings, "heartbeat_briefing_time", "no-es-hora", raising=False)
    assert w._time_for("briefing") == (8, 50)
    monkeypatch.setattr(settings, "heartbeat_radar_time", "xx", raising=False)
    assert w._time_for("radar") == (9, 40)


def test_next_wait_devuelve_espera_finita():
    w = hb.HeartbeatWorker()
    wait = w._next_wait()
    assert 0 < wait <= 3600.0
