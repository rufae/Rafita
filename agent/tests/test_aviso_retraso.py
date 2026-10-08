"""Aviso de mensajes retrasados (Rafita offline)."""

from datetime import UTC, datetime, timedelta

from src.handlers.chat import _aviso_retraso

NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)


def test_mensaje_reciente_no_avisa():
    assert _aviso_retraso(NOW - timedelta(seconds=90), ahora=NOW) == ""


def test_none_no_avisa():
    assert _aviso_retraso(None, ahora=NOW) == ""


def test_fecha_sin_tz_se_trata_como_utc():
    naive = datetime(2026, 10, 8, 11, 0, 0)
    assert _aviso_retraso(naive, ahora=NOW) != ""


def test_retraso_minutos():
    aviso = _aviso_retraso(NOW - timedelta(minutes=45), ahora=NOW)
    assert aviso.startswith("AVISO_MENSAJE_RETRASADO")
    assert "45m" in aviso
    assert "2026-10-08 11:15" in aviso


def test_retraso_horas():
    aviso = _aviso_retraso(NOW - timedelta(hours=3, minutes=5), ahora=NOW)
    assert "3h 05m" in aviso


def test_retraso_en_cola_larga_telegram():
    aviso = _aviso_retraso(NOW - timedelta(hours=20), ahora=NOW)
    assert "20h" in aviso
    assert "encolo" in aviso
