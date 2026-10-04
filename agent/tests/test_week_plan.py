"""2.b.2 — «Prepara mi semana» (get_week_plan).

Una sola tool junta agenda (Google o local), tareas (Google Tasks o locales)
y clientes abiertos del CRM; el modelo compone el resumen y el horario.
"""

import json
from datetime import datetime, timedelta

import src.services.crm_service as crm_mod
from src.handlers import chat as chat_mod
from src.handlers.chat_tools import TOOLS_DEFINITIONS


def _definicion():
    for tool in TOOLS_DEFINITIONS:
        if tool["function"]["name"] == "get_week_plan":
            return tool["function"]
    raise AssertionError("get_week_plan no esta en TOOLS_DEFINITIONS")


def test_definicion_clara():
    fn = _definicion()
    assert "prepara mi semana" in fn["description"]
    assert "agenda" in fn["description"]
    assert "CRM" in fn["description"]
    assert "manage_google_calendar" in fn["description"]
    assert fn["parameters"]["required"] == []


class _FakeGoogle:
    def __init__(self, ready=True, events=None, tasks=None, events_exc=None):
        self.is_ready = ready
        self._events = events or []
        self._tasks = tasks or []
        self._exc = events_exc

    async def list_events(self, max_results=10, time_min=None):
        if self._exc:
            raise RuntimeError(self._exc)
        return self._events

    async def list_tasks(self):
        return {"tasks": self._tasks}


def _crm_con(entradas):
    async def _listar(_estado=None):
        return entradas

    return _listar


async def test_modo_google_filtra_por_ventana(monkeypatch):
    hoy = datetime.now()
    dentro = (hoy + timedelta(days=2)).strftime("%Y-%m-%dT09:00:00+02:00")
    fuera = (hoy + timedelta(days=30)).strftime("%Y-%m-%dT09:00:00+02:00")
    monkeypatch.setattr(
        chat_mod,
        "google_services",
        _FakeGoogle(
            events=[
                {"summary": "Reunion", "start": {"dateTime": dentro}, "end": {}},
                {"summary": "Lejos", "start": {"dateTime": fuera}, "end": {}},
            ],
            tasks=[
                {
                    "title": "Tarea de la semana",
                    "due": (hoy + timedelta(days=3)).strftime("%Y-%m-%dT00:00:00Z"),
                },
                {
                    "title": "Tarea lejana",
                    "due": (hoy + timedelta(days=60)).strftime("%Y-%m-%dT00:00:00Z"),
                },
                {"title": "Sin fecha", "due": None},
            ],
        ),
    )
    monkeypatch.setattr(
        crm_mod,
        "listar",
        _crm_con(
            [
                {
                    "nombre": "Ana",
                    "estado": "propuesta",
                    "proximo_paso": "enviar presupuesto",
                    "proximo_seguimiento": "2026-10-08",
                },
                {"nombre": "Cerrado", "estado": "cerrado", "proximo_paso": "nada"},
            ]
        ),
    )

    result = await chat_mod._execute_tool(1, "get_week_plan", {})
    assert result["success"] is True
    assert [e["titulo"] for e in result["agenda"]] == ["Reunion"]
    assert [t["titulo"] for t in result["tareas"]] == [
        "Tarea de la semana",
        "Sin fecha",
    ]
    assert [c["nombre"] for c in result["crm"]] == ["Ana"]
    assert result["crm"][0]["proximo_paso"] == "enviar presupuesto"
    assert result["avisos"] == []
    assert json.loads(json.dumps(result, ensure_ascii=False))["agenda"][0]["titulo"] == "Reunion"
    assert "cliente(s) abiertos" in result["message"]


async def test_modo_local_sin_google(monkeypatch):
    hoy = datetime.now()
    dentro = (hoy + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    fuera = (hoy + timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setattr(chat_mod, "google_services", _FakeGoogle(ready=False))

    async def _events(chat_id, limit=10):
        return [
            {"title": "Cita local", "event_datetime": dentro},
            {"title": "Muy lejos", "event_datetime": fuera},
        ]

    async def _tasks(chat_id):
        return [{"title": "Comprar pilas", "due": None}]

    monkeypatch.setattr(chat_mod.db, "get_upcoming_events", _events)
    monkeypatch.setattr(chat_mod.db, "list_tasks", _tasks)
    monkeypatch.setattr(crm_mod, "listar", _crm_con([]))

    result = await chat_mod._execute_tool(1, "get_week_plan", {})
    assert result["success"] is True
    assert [e["titulo"] for e in result["agenda"]] == ["Cita local"]
    assert [t["titulo"] for t in result["tareas"]] == ["Comprar pilas"]
    assert result["crm"] == []
    assert result["avisos"] == []


async def test_honestidad_si_falla_la_agenda(monkeypatch):
    monkeypatch.setattr(chat_mod, "google_services", _FakeGoogle(events_exc="sin credenciales"))
    monkeypatch.setattr(crm_mod, "listar", _crm_con([]))

    result = await chat_mod._execute_tool(1, "get_week_plan", {})
    assert result["success"] is True
    assert any("agenda no disponible" in a for a in result["avisos"])
    assert "sin credenciales" in " ".join(result["avisos"])
    assert result["tareas"] == []


async def test_days_limites_y_valores_raros(monkeypatch):
    monkeypatch.setattr(chat_mod, "google_services", _FakeGoogle(ready=False))
    monkeypatch.setattr(crm_mod, "listar", _crm_con([]))
    monkeypatch.setattr(chat_mod.db, "get_upcoming_events", _fake_events_vacios)
    monkeypatch.setattr(chat_mod.db, "list_tasks", _fake_tasks_vacios)

    excesivo = await chat_mod._execute_tool(1, "get_week_plan", {"days": 99})
    desde = datetime.strptime(excesivo["desde"], "%Y-%m-%d %H:%M")
    hasta = datetime.strptime(excesivo["hasta"], "%Y-%m-%d %H:%M")
    assert (hasta - desde).days <= 14

    raro = await chat_mod._execute_tool(1, "get_week_plan", {"days": "abc"})
    desde = datetime.strptime(raro["desde"], "%Y-%m-%d %H:%M")
    hasta = datetime.strptime(raro["hasta"], "%Y-%m-%d %H:%M")
    assert (hasta - desde).days == 7


async def _fake_events_vacios(chat_id, limit=10):
    return []


async def _fake_tasks_vacios(chat_id):
    return []
