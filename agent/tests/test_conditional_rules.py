"""Reglas condicionales proactivas (2026-10-06): metric/condiciones del clima."""

from datetime import datetime

from src.services import automation_service
from src.utils import conditional_rules as cr


class _FakeBot:
    def __init__(self):
        self.messages = []

    async def send_proactive_message(self, chat_id, text):
        self.messages.append((chat_id, text))
        return 1


def test_normalize_y_metricas():
    assert cr.normalize("Lluvia") == "lluvia"
    m = cr.system_metrics()
    assert {"ram_pct", "disk_pct", "swap_pct"} <= set(m)
    assert all(isinstance(v, float) for v in m.values())


def test_metric_matches(monkeypatch):
    monkeypatch.setattr(cr, "system_metrics", lambda: {"ram_pct": 91.0})
    assert cr._metric_matches({"metric": "ram_pct", "op": ">", "value": 90})
    assert not cr._metric_matches({"metric": "ram_pct", "op": "<", "value": 90})
    assert not cr._metric_matches({"metric": "otra", "op": ">", "value": 1})
    assert not cr._metric_matches({"metric": "ram_pct", "op": ">", "value": "x"})


def test_in_window():
    now = datetime(2026, 10, 6, 7, 30)
    assert cr._in_window({}, now)
    assert cr._in_window({"hour": "09:00"}, now)
    assert not cr._in_window({"hour": "09:00"}, datetime(2026, 10, 6, 10, 0))
    assert cr._in_window({"hour": "05:00"}, datetime(2026, 10, 6, 3, 0))
    assert cr._in_window({"hour": "no-es-hora"}, now)


async def test_check_condition_weather(monkeypatch):
    async def fake_weather(ciudad="", dia=""):
        return {"resumen": "Lluvias intensas por la tarde"}

    monkeypatch.setattr(automation_service, "weather_report", fake_weather)
    assert await cr.check_condition(
        {"type": "weather", "keywords": ["lluvia"]}, datetime(2026, 10, 6, 8, 0)
    )
    assert not await cr.check_condition(
        {"type": "weather", "keywords": ["nieve"]}, datetime(2026, 10, 6, 8, 0)
    )
    assert not await cr.check_condition(
        {"type": "weather", "keywords": ["lluvia"], "hour": "07:00"},
        datetime(2026, 10, 6, 8, 0),
    )
    assert not await cr.check_condition({"type": "otra"}, datetime(2026, 10, 6, 8, 0))


async def test_evaluate_and_fire_one_shot(monkeypatch):
    rows = [
        {
            "id": 3,
            "chat_id": 11,
            "rule_json": '{"type": "metric", "metric": "ram_pct", "op": ">", "value": 50}',
            "message": "RAM alta",
            "repeats": 0,
            "fired_at": "",
        }
    ]
    disparadas = []

    async def fake_list():
        return rows

    async def fake_fire(rid, repeats=False):
        disparadas.append((rid, repeats))

    monkeypatch.setattr(cr.db, "list_active_conditional_rules", fake_list)
    monkeypatch.setattr(cr.db, "fire_conditional_rule", fake_fire)
    monkeypatch.setattr(cr, "system_metrics", lambda: {"ram_pct": 91.0})
    bot = _FakeBot()
    assert await cr.evaluate_and_fire(bot, datetime(2026, 10, 6, 8, 0)) == 1
    assert bot.messages == [(11, "RAM alta")]
    assert disparadas == [(3, False)]


async def test_evaluate_and_fire_repeats_uno_al_dia(monkeypatch):
    rows = [
        {
            "id": 4,
            "chat_id": 11,
            "rule_json": '{"type": "metric", "metric": "disk_pct", "op": ">", "value": 50}',
            "message": "Disco lleno",
            "repeats": 1,
            "fired_at": "2026-10-06 07:00:00",
        }
    ]

    async def fake_list():
        return rows

    async def fake_fire(rid, repeats=False):
        return None

    monkeypatch.setattr(cr.db, "list_active_conditional_rules", fake_list)
    monkeypatch.setattr(cr.db, "fire_conditional_rule", fake_fire)
    monkeypatch.setattr(cr, "system_metrics", lambda: {"disk_pct": 91.0})
    bot = _FakeBot()
    # ya disparada hoy: silencio
    assert await cr.evaluate_and_fire(bot, datetime(2026, 10, 6, 8, 0)) == 0
    # al dia siguiente vuelve a avisar
    assert await cr.evaluate_and_fire(bot, datetime(2026, 10, 7, 8, 0)) == 1
