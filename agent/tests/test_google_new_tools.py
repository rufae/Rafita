"""Nuevas herramientas Google: Gmail, Tasks, Contactos y Fitness (2026-09-27)."""

from types import SimpleNamespace

from src.handlers.chat import TOOLS_DEFINITIONS
from src.handlers.chat_tools import get_tools_for_message
from src.services.google_services_manager import GoogleServicesManager


class _Req:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


def _manager() -> GoogleServicesManager:
    manager = GoogleServicesManager()
    manager._ready = True
    return manager


def _names(tools):
    return {t["function"]["name"] for t in tools}


def test_message_without_google_keywords_gets_core_only():
    names = _names(get_tools_for_message("Gasté 45 euros en gasolina"))
    assert "search_google_drive" not in names
    assert "search_gmail" not in names
    assert "save_expense" in names


def test_message_with_gmail_keyword_includes_gmail_only():
    names = _names(get_tools_for_message("resúmeme los correos de hoy"))
    assert "search_gmail" in names
    assert "search_google_drive" not in names
    assert "manage_google_tasks" not in names


def test_message_with_tasks_keyword_includes_tasks():
    names = _names(get_tools_for_message("añádeme una tarea: comprar pan"))
    assert "manage_google_tasks" in names
    assert "search_gmail" not in names


def test_message_with_drive_keyword_includes_drive():
    names = _names(get_tools_for_message("busca en mi Drive el informe"))
    assert "search_google_drive" in names
    assert "read_google_drive_file" in names


def test_new_google_tools_registered():
    names = {t["function"]["name"] for t in TOOLS_DEFINITIONS}
    assert {
        "search_gmail",
        "manage_google_tasks",
        "find_contact",
        "fitness_daily_steps",
    } <= names


async def test_search_gmail_returns_metadata():
    manager = _manager()

    class _Messages:
        def list(self, **_kwargs):
            return _Req({"messages": [{"id": "m1"}]})

        def get(self, **_kwargs):
            return _Req(
                {
                    "snippet": "Hola, tu factura...",
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Factura de luz"},
                            {"name": "From", "value": "Iberdrola <x@y.es>"},
                            {"name": "Date", "value": "Fri, 26 Sep 2026"},
                        ]
                    },
                }
            )

    manager._gmail = SimpleNamespace(users=lambda: SimpleNamespace(messages=lambda: _Messages()))
    result = await manager.search_gmail("from:iberdrola")
    assert result["success"] is True
    assert result["messages"][0]["subject"] == "Factura de luz"
    assert "Iberdrola" in result["messages"][0]["from"]


async def test_list_and_complete_and_delete_tasks():
    manager = _manager()
    calls = {}

    class _Tasks:
        def list(self, **_kwargs):
            return _Req({"items": [{"id": "t1", "title": "Comprar pan", "status": "needsAction"}]})

        def patch(self, **kwargs):
            calls["patch"] = kwargs
            return _Req({"id": "t1", "status": "completed"})

        def delete(self, **kwargs):
            calls["delete"] = kwargs
            return _Req({})

    manager._tasks = SimpleNamespace(tasks=lambda: _Tasks())

    listed = await manager.list_tasks()
    assert listed["tasks"][0]["title"] == "Comprar pan"

    completed = await manager.complete_task("t1")
    assert completed["success"] is True
    assert calls["patch"]["task"] == "t1"
    assert calls["patch"]["body"]["status"] == "completed"

    deleted = await manager.delete_task("t1")
    assert deleted["success"] is True
    assert calls["delete"]["task"] == "t1"


async def test_find_contact_filters_by_name():
    manager = _manager()

    class _Connections:
        def list(self, **_kwargs):
            return _Req(
                {
                    "connections": [
                        {
                            "names": [{"displayName": "Ana Pérez"}],
                            "emailAddresses": [{"value": "ana@x.com"}],
                            "phoneNumbers": [{"value": "600111222"}],
                        },
                        {
                            "names": [{"displayName": "Luis Gómez"}],
                            "emailAddresses": [{"value": "luis@x.com"}],
                        },
                    ]
                }
            )

    manager._people = SimpleNamespace(
        people=lambda: SimpleNamespace(connections=lambda: _Connections())
    )
    result = await manager.find_contact("ana")
    assert len(result["contacts"]) == 1
    assert result["contacts"][0]["phone"] == "600111222"


async def test_fitness_daily_steps_sums_points():
    manager = _manager()

    class _Dataset:
        def aggregate(self, **_kwargs):
            return _Req(
                {
                    "bucket": [
                        {
                            "dataset": [
                                {"point": [{"value": [{"intVal": 1200}]}]},
                                {"point": [{"value": [{"intVal": 300}]}]},
                            ]
                        }
                    ]
                }
            )

    manager._fitness = SimpleNamespace(users=lambda: SimpleNamespace(dataset=lambda: _Dataset()))
    result = await manager.fitness_daily_steps()
    assert result["steps"] == 1500
