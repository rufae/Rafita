"""2.b.4 — Actas de reunión → seguimiento (create_meeting_tasks).

Una sola llamada crea N tareas con responsable (antepuesto al título, ya que
Google Tasks no tiene assignee) y fecha relativa/absoluta resuelta.
"""

import json

import pytest

from src.handlers import chat as chat_mod
from src.handlers.chat_tools import TOOLS_DEFINITIONS


def _definicion():
    for tool in TOOLS_DEFINITIONS:
        if tool["function"]["name"] == "create_meeting_tasks":
            return tool["function"]
    raise AssertionError("create_meeting_tasks no esta en TOOLS_DEFINITIONS")


def test_definicion_acta_y_reporte_honesto():
    fn = _definicion()
    desc = fn["description"]
    assert "acta" in desc
    assert "responsable" in desc
    assert "dilo" in desc
    assert "tasks" in fn["parameters"]["properties"]
    assert fn["parameters"]["required"] == ["tasks"]


class _FakeGoogle:
    def __init__(self, ready=True, falla=False):
        self.is_ready = ready
        self.falla = falla
        self.creadas = []

    async def create_task(self, title, tasklist="@default", due=None):
        if self.falla:
            return {"success": False, "message": "cuota excedida"}
        self.creadas.append({"title": title, "due": due})
        return {"success": True, "id": "id%d" % len(self.creadas), "title": title}


async def test_crea_varias_con_responsable_y_fecha(monkeypatch):
    fake = _FakeGoogle()
    monkeypatch.setattr(chat_mod, "google_services", fake)
    tareas = [
        {"title": "Enviar presupuesto", "responsable": "Ana", "due": "2026-10-10"},
        {"title": "Llamar al proveedor", "due": "manana"},
        {"title": "Sin fecha ni responsable"},
    ]
    result = await chat_mod._execute_tool(1, "create_meeting_tasks", {"tasks": tareas})
    assert result["success"] is True
    assert len(result["creadas"]) == 3
    assert result["fallidas"] == []
    assert result["creadas"][0]["titulo"] == "[Ana] Enviar presupuesto"
    assert result["creadas"][0]["due"] == "2026-10-10"
    assert result["creadas"][1]["titulo"] == "Llamar al proveedor"
    from datetime import datetime, timedelta

    manana = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    assert result["creadas"][1]["due"] == manana  # 'manana'
    assert result["creadas"][2]["due"] == "sin fecha"
    assert fake.creadas[0]["title"] == "[Ana] Enviar presupuesto"
    assert fake.creadas[0]["due"] == "2026-10-10"
    assert "creadas 3 de 3" in result["message"]


async def test_modo_local_sin_google(monkeypatch):
    fake = _FakeGoogle(ready=False)
    monkeypatch.setattr(chat_mod, "google_services", fake)
    guardadas = []

    async def _add(chat_id, title, due=None):
        guardadas.append({"chat_id": chat_id, "title": title, "due": due})
        return len(guardadas)

    monkeypatch.setattr(chat_mod.db, "add_task", _add)
    result = await chat_mod._execute_tool(
        1, "create_meeting_tasks", {"tasks": [{"title": "Comprar pilas"}]}
    )
    assert result["success"] is True
    assert guardadas[0]["title"] == "Comprar pilas"
    assert guardadas[0]["due"] is None


async def test_falla_total_es_honesta(monkeypatch):
    fake = _FakeGoogle(falla=True)
    monkeypatch.setattr(chat_mod, "google_services", fake)
    result = await chat_mod._execute_tool(
        1,
        "create_meeting_tasks",
        {"tasks": [{"title": "A"}, {"title": "B"}]},
    )
    assert result["success"] is False
    assert result["creadas"] == []
    assert len(result["fallidas"]) == 2
    assert "cuota excedida" in result["message"]
    assert "creadas 0 de 2" in result["message"]


async def test_falla_parcial_reporta_ambas(monkeypatch):
    class _Parcial(_FakeGoogle):
        async def create_task(self, title, tasklist="@default", due=None):
            if title.startswith("[Ana]"):
                return {"success": False, "message": "error google"}
            return await super().create_task(title, tasklist=tasklist, due=due)

    monkeypatch.setattr(chat_mod, "google_services", _Parcial())
    result = await chat_mod._execute_tool(
        1,
        "create_meeting_tasks",
        {
            "tasks": [
                {"title": "Con Ana", "responsable": "Ana"},
                {"title": "Sin Ana"},
            ]
        },
    )
    assert result["success"] is True
    assert len(result["creadas"]) == 1
    assert len(result["fallidas"]) == 1
    assert "fallidas" in result["message"]


async def test_acepta_tasks_como_cadena_json(monkeypatch):
    monkeypatch.setattr(chat_mod, "google_services", _FakeGoogle())
    cadena = json.dumps([{"title": "Resumir acta", "responsable": "Luis"}])
    result = await chat_mod._execute_tool(1, "create_meeting_tasks", {"tasks": cadena})
    assert result["success"] is True
    assert result["creadas"][0]["titulo"] == "[Luis] Resumir acta"


async def test_lista_vacia_rechazada():
    result = await chat_mod._execute_tool(1, "create_meeting_tasks", {"tasks": []})
    assert result["success"] is False
    assert "lista" in result["message"]


async def test_limita_a_diez(monkeypatch):
    fake = _FakeGoogle()
    monkeypatch.setattr(chat_mod, "google_services", fake)
    tareas = [{"title": "T%d" % i} for i in range(15)]
    result = await chat_mod._execute_tool(1, "create_meeting_tasks", {"tasks": tareas})
    assert len(result["creadas"]) == 10
    assert len(fake.creadas) == 10


@pytest.mark.parametrize("payload", [{}, {"tasks": "no-json"}, {"tasks": 42}])
async def test_payload_inutil_es_honesto(payload):
    result = await chat_mod._execute_tool(1, "create_meeting_tasks", payload)
    assert result["success"] is False
