"""Cobertura de _execute_tool (handlers/chat.py): todas las ramas de herramientas."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config import settings
from src.handlers import chat as chat_mod
from src.services.google_services_manager import GoogleServicesManager


@pytest.fixture(autouse=True)
def _google_ready(monkeypatch):
    """Estos tests ejercitan la ruta Google: forzamos is_ready=True."""
    monkeypatch.setattr(GoogleServicesManager, "is_ready", property(lambda self: True))


def _fn(result=None, exc=None, calls=None):
    async def _inner(*args, **kwargs):
        if calls is not None:
            calls.append((args, kwargs))
        if exc is not None:
            raise exc
        return result

    return _inner


def _patch_db(monkeypatch, **results):
    defaults = {
        "add_finance_record": 7,
        "add_event": 3,
        "add_alert": 4,
        "get_finance_summary": {
            "transaction_count": 0,
            "total_income": 0.0,
            "total_expenses": 0.0,
            "balance": 0.0,
        },
        "store_personal_knowledge": None,
        "count_personal_knowledge": 1,
        "search_personal_knowledge": [],
        "get_all_personal_knowledge": [],
        "add_recurring_alert": 9,
        "get_active_alerts": [],
    }
    for name, result in defaults.items():
        monkeypatch.setattr(chat_mod.db, name, _fn(results.get(name, result)))


@pytest.fixture(autouse=True)
def _writes_enabled(monkeypatch):
    monkeypatch.setattr(settings, "persist_to_brain", True)


# ---------- save_expense / finanzas ----------


async def test_save_expense_syncs_to_obsidian(monkeypatch):
    _patch_db(monkeypatch)
    notes = []
    monkeypatch.setattr(
        "src.utils.obsidian_manager.create_or_append_note", _fn({"success": True}, calls=notes)
    )
    result = await chat_mod._execute_tool(1, "save_expense", {"amount": 12.5, "category": "casa"})
    assert result["success"]
    assert "Gasto registrado" in result["message"]
    assert notes


async def test_save_expense_survives_obsidian_failure(monkeypatch):
    _patch_db(monkeypatch)
    monkeypatch.setattr(
        "src.utils.obsidian_manager.create_or_append_note", _fn(exc=RuntimeError("vault roto"))
    )
    result = await chat_mod._execute_tool(1, "save_expense", {"amount": 3, "category": "comida"})
    assert result["success"]


async def test_save_expense_accepts_string_amount(monkeypatch):
    # El modelo manda a veces el importe como string; el f-string final
    # reventaba con ValueError y el gasto se rechazaba.
    _patch_db(monkeypatch)
    monkeypatch.setattr("src.utils.obsidian_manager.create_or_append_note", _fn({"success": True}))
    result = await chat_mod._execute_tool(
        1, "save_expense", {"amount": "150.50", "category": "casa"}
    )
    assert result["success"]
    assert "150.50" in result["message"]


async def test_get_finance_summary_empty_and_populated(monkeypatch):
    _patch_db(monkeypatch)
    empty = await chat_mod._execute_tool(1, "get_finance_summary", {})
    assert "No hay registros" in empty["message"]

    _patch_db(
        monkeypatch,
        get_finance_summary={
            "transaction_count": 3,
            "total_income": 100.0,
            "total_expenses": 40.0,
            "balance": 60.0,
        },
    )
    full = await chat_mod._execute_tool(1, "get_finance_summary", {})
    assert "Ingresos" in full["message"]
    assert "Balance" in full["message"]


# ---------- eventos y alertas ----------


async def test_create_event_requires_datetime(monkeypatch):
    _patch_db(monkeypatch)
    result = await chat_mod._execute_tool(1, "create_event", {"title": "Cena"})
    assert not result["success"]


async def test_create_event_uses_google_when_ready(monkeypatch):
    calls = []
    google = SimpleNamespace(
        is_ready=True, create_event=_fn({"success": True, "message": "ok google"}, calls=calls)
    )
    monkeypatch.setattr(chat_mod, "google_services", google)
    result = await chat_mod._execute_tool(
        1, "create_event", {"title": "Cena", "event_datetime": "2026-10-01 20:00"}
    )
    assert result["message"] == "ok google"
    assert calls


async def test_create_event_parses_relative_datetime_for_google(monkeypatch):
    # Antes "el viernes a las 8" llegaba en crudo a Google y fallaba; ahora se
    # parsea con parse_relative_datetime (mismo parser que la tool de Google).
    calls = []
    google = SimpleNamespace(
        is_ready=True, create_event=_fn({"success": True, "message": "ok"}, calls=calls)
    )
    monkeypatch.setattr(chat_mod, "google_services", google)
    result = await chat_mod._execute_tool(
        1, "create_event", {"title": "Cena", "event_datetime": "el viernes a las 8"}
    )
    assert result["success"]
    assert "T08:00:00" in calls[0][0][1]


async def test_create_event_acepta_alias_when(monkeypatch):
    # Bug 2026-09-30: el modelo pasaba 'when' (parametro de otras tools) y
    # create_event respondia "no se proporciono una fecha valida" aunque la
    # fecha era perfectamente valida.
    calls = []
    google = SimpleNamespace(
        is_ready=True, create_event=_fn({"success": True, "message": "ok"}, calls=calls)
    )
    monkeypatch.setattr(chat_mod, "google_services", google)
    result = await chat_mod._execute_tool(
        1, "create_event", {"title": "Cena", "when": "mañana a las 10"}
    )
    assert result["success"]
    assert "T10:00:00" in calls[0][0][1]


async def test_create_event_falls_back_to_local(monkeypatch):
    _patch_db(monkeypatch)
    google = SimpleNamespace(
        is_ready=True, create_event=_fn({"success": False, "message": "sin credenciales"})
    )
    monkeypatch.setattr(chat_mod, "google_services", google)
    result = await chat_mod._execute_tool(
        1, "create_event", {"title": "Cena", "event_datetime": "2026-10-01 20:00"}
    )
    assert result["success"]
    assert "Evento creado" in result["message"]


async def test_create_event_local_when_google_not_ready(monkeypatch):
    _patch_db(monkeypatch)
    monkeypatch.setattr(chat_mod, "google_services", SimpleNamespace(is_ready=False))
    result = await chat_mod._execute_tool(
        1, "create_event", {"title": "Cena", "event_datetime": "2026-10-01 20:00"}
    )
    assert result["success"]
    assert "01/10/2026 20:00" in result["message"]


async def test_get_finance_summary_devuelve_tabla_markdown(monkeypatch):
    _patch_db(
        monkeypatch,
        get_finance_summary={
            "transaction_count": 3,
            "total_income": 100.0,
            "total_expenses": 40.0,
            "balance": 60.0,
            "expense_by_category": {"comida": 25.0, "transporte": 15.0},
        },
    )
    cur = settings.default_currency
    result = await chat_mod._execute_tool(1, "get_finance_summary", {})
    assert "| Concepto | Importe |" in result["message"]
    assert f"| comida | 25.00 {cur} |" in result["message"]
    assert f"| **Balance** | **60.00 {cur}** |" in result["message"]


async def test_get_alerts_lista_pendientes_y_vacio(monkeypatch):
    _patch_db(monkeypatch, get_active_alerts=[])
    empty = await chat_mod._execute_tool(1, "get_alerts", {})
    assert "No tienes alertas" in empty["message"]

    _patch_db(
        monkeypatch,
        get_active_alerts=[
            {"id": 5, "message": "pagar la luz", "alert_type": "urgent", "expires_at": None},
            {
                "id": 6,
                "message": "renovar dni",
                "alert_type": "info",
                "expires_at": "2026-10-02 00:00:00",
            },
        ],
    )
    full = await chat_mod._execute_tool(1, "get_alerts", {})
    assert "| ID | Alerta | Tipo | Vence |" in full["message"]
    # alert_type legacy "urgent" se muestra con la taxonomia unica (D12)
    assert "| 5 | pagar la luz | critical | - |" in full["message"]
    assert "| 6 | renovar dni | info | 2026-10-02 00:00:00 |" in full["message"]


async def test_get_weather_delega_en_weather_report(monkeypatch):
    calls = []

    async def fake_report(ciudad="", dia="hoy"):
        calls.append((ciudad, dia))
        return {"success": True, "message": "🌤 En Sevilla hoy: 18-28 °C"}

    monkeypatch.setattr("src.services.automation_service.weather_report", fake_report)
    result = await chat_mod._execute_tool(1, "get_weather", {"ciudad": "Sevilla", "dia": "mañana"})
    assert result["success"]
    assert calls == [("Sevilla", "mañana")]

    result = await chat_mod._execute_tool(1, "get_weather", {})
    assert calls[-1] == ("", "hoy")


async def test_create_alert_expires_variants(monkeypatch):
    _patch_db(monkeypatch)
    valid = await chat_mod._execute_tool(
        1, "create_alert", {"message": "pagar", "expires_at": "2026-12-01"}
    )
    assert valid["success"]
    invalid = await chat_mod._execute_tool(
        1, "create_alert", {"message": "pagar", "expires_at": "no-es-fecha"}
    )
    assert invalid["success"]
    plain = await chat_mod._execute_tool(1, "create_alert", {"message": "pagar"})
    assert plain["success"]


# ---------- remember_fact / search_knowledge ----------


async def test_remember_fact_requires_key_and_value(monkeypatch):
    _patch_db(monkeypatch)
    result = await chat_mod._execute_tool(1, "remember_fact", {"key": "", "value": ""})
    assert not result["success"]


async def test_remember_fact_counts_and_pluralizes(monkeypatch):
    _patch_db(monkeypatch, count_personal_knowledge=1)
    one = await chat_mod._execute_tool(
        1, "remember_fact", {"key": "madre", "value": "Aa Mama", "category": "familia"}
    )
    assert "1 hecho" in one["message"]
    _patch_db(monkeypatch, count_personal_knowledge=3)
    many = await chat_mod._execute_tool(1, "remember_fact", {"key": "a", "value": "b"})
    assert "3 hechos" in many["message"]


async def test_search_knowledge_all_and_search(monkeypatch):
    _patch_db(
        monkeypatch,
        get_all_personal_knowledge=[{"key": "madre", "value": "Aa", "category": "familia"}],
        search_personal_knowledge=[{"key": "coche", "value": "Audi", "category": "general"}],
    )
    all_rows = await chat_mod._execute_tool(1, "search_knowledge", {})
    assert "Aa" in all_rows["message"]
    filtered = await chat_mod._execute_tool(1, "search_knowledge", {"query": "coche"})
    assert "Audi" in filtered["message"]


async def test_search_knowledge_no_results(monkeypatch):
    _patch_db(monkeypatch)
    result = await chat_mod._execute_tool(1, "search_knowledge", {"query": "zzz"})
    assert "No tengo información" in result["message"]


# ---------- search_web ----------


async def test_search_web_requires_query(monkeypatch):
    result = await chat_mod._execute_tool(1, "search_web", {"query": ""})
    assert not result["success"]


async def test_search_web_without_results(monkeypatch):
    monkeypatch.setattr(chat_mod, "search_duckduckgo", _fn([]))
    result = await chat_mod._execute_tool(1, "search_web", {"query": "clima"})
    assert not result["success"]


async def test_search_web_formats_results(monkeypatch):
    monkeypatch.setattr(chat_mod, "search_duckduckgo", _fn([{"title": "a"}]))
    monkeypatch.setattr(chat_mod, "format_search_results", lambda rows: "titulo a")
    result = await chat_mod._execute_tool(1, "search_web", {"query": "clima"})
    assert result["success"]
    assert "titulo a" in result["message"]


# ---------- manage_obsidian_note ----------


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({"action": "create", "title": ""}, "título"),
        ({"action": "create", "title": "X", "content": ""}, "contenido"),
        ({"action": "append", "title": "X", "content": ""}, "contenido"),
        ({"action": "overwrite", "title": "X", "content": ""}, "contenido"),
        ({"action": "explode", "title": "X"}, "desconocida"),
    ],
)
async def test_manage_obsidian_note_validation_errors(monkeypatch, args, expected):
    result = await chat_mod._execute_tool(1, "manage_obsidian_note", args)
    assert not result["success"]
    assert expected in result["message"]


async def test_manage_obsidian_note_write_and_read_actions(monkeypatch):
    calls = []
    ob = SimpleNamespace(
        create_or_append_note=_fn({"success": True, "action": "created"}, calls=calls),
        overwrite_note=_fn({"success": True, "action": "overwritten"}),
        read_note=_fn({"success": True, "content": "hola"}),
        delete_note=_fn({"success": True, "message": "borrada"}),
    )
    monkeypatch.setattr(chat_mod, "ob", ob)
    created = await chat_mod._execute_tool(
        1, "manage_obsidian_note", {"action": "create", "title": "X", "content": "y"}
    )
    assert created["action"] == "created"
    appended = await chat_mod._execute_tool(
        1, "manage_obsidian_note", {"action": "append", "title": "X", "content": "y"}
    )
    assert appended["action"] == "created"
    overwritten = await chat_mod._execute_tool(
        1, "manage_obsidian_note", {"action": "overwrite", "title": "X", "content": "y"}
    )
    assert overwritten["action"] == "overwritten"
    read = await chat_mod._execute_tool(1, "manage_obsidian_note", {"action": "read", "title": "X"})
    assert read["content"] == "hola"
    deleted = await chat_mod._execute_tool(
        1, "manage_obsidian_note", {"action": "delete", "title": "X"}
    )
    assert deleted["message"] == "borrada"
    assert calls


# ---------- search_obsidian_vault ----------


async def test_search_obsidian_vault_requires_query():
    result = await chat_mod._execute_tool(1, "search_obsidian_vault", {})
    assert not result["success"]


async def test_search_obsidian_vault_formats_matches(monkeypatch):
    rows = [
        {
            "title": "nota %d" % i,
            "folder": "diario",
            "match_count": 1 if i else 2,
            "snippets": ["s"],
        }
        for i in range(12)
    ]
    monkeypatch.setattr(chat_mod.ob, "search_notes_content", _fn({"results": rows, "message": "x"}))
    result = await chat_mod._execute_tool(1, "search_obsidian_vault", {"query": "coche"})
    assert "nota(s) más" in result["message"]
    assert "coincidencia" in result["message"]


async def test_search_obsidian_vault_without_matches(monkeypatch):
    monkeypatch.setattr(
        chat_mod.ob, "search_notes_content", _fn({"results": [], "message": "nada"})
    )
    result = await chat_mod._execute_tool(1, "search_obsidian_vault", {"query": "coche"})
    assert result["message"] == "nada"


# ---------- inspect_project_files / analyze_system_logs ----------


async def test_inspect_project_files_variants(monkeypatch):
    monkeypatch.setattr(
        chat_mod.wm, "list_workspace_files", _fn({"success": False, "message": "prohibido"})
    )
    failed = await chat_mod._execute_tool(1, "inspect_project_files", {"path": "/etc"})
    assert failed["message"] == "prohibido"

    monkeypatch.setattr(
        chat_mod.wm,
        "list_workspace_files",
        _fn({"success": True, "items": [], "path": "/w", "relative": ".", "summary": "vacio"}),
    )
    empty = await chat_mod._execute_tool(1, "inspect_project_files", {"path": ""})
    assert "vacío" in empty["message"]

    monkeypatch.setattr(
        chat_mod.wm,
        "list_workspace_files",
        _fn(
            {
                "success": True,
                "items": [
                    {"type": "dir", "name": "docs", "children": 3},
                    {"type": "file", "name": "a.md", "size": "1 KB"},
                ],
                "path": "/w",
                "relative": ".",
                "summary": "2 elementos",
            }
        ),
    )
    listed = await chat_mod._execute_tool(1, "inspect_project_files", {"path": ""})
    assert "docs" in listed["message"]
    assert "a.md" in listed["message"]


async def test_analyze_system_logs_variants(monkeypatch):
    monkeypatch.setattr(
        chat_mod.wm,
        "get_system_health",
        _fn(
            {
                "database": {"size": "1 MB"},
                "disk": {"total": "10 G", "used": "5 G", "percent_used": 50, "free": "5 G"},
                "logs": {"recent_errors": ["boom", "bam"]},
                "chats": {"active_chats": 2},
                "timestamp": "2026-09-28",
                "health": "healthy",
            }
        ),
    )
    healthy = await chat_mod._execute_tool(1, "analyze_system_logs", {})
    assert "Saludable" in healthy["message"]
    assert "boom" in healthy["message"]

    monkeypatch.setattr(
        chat_mod.wm,
        "get_system_health",
        _fn(
            {
                "database": {},
                "disk": {"error": "sin permisos"},
                "logs": {"status": "sin logs"},
                "chats": {},
                "timestamp": "2026-09-28",
                "health": "degraded",
            }
        ),
    )
    degraded = await chat_mod._execute_tool(1, "analyze_system_logs", {})
    assert "Degradado" in degraded["message"]
    assert "sin permisos" in degraded["message"]


async def test_analyze_system_logs_without_recent_errors(monkeypatch):
    monkeypatch.setattr(
        chat_mod.wm,
        "get_system_health",
        _fn(
            {
                "database": {},
                "disk": {"total": "1 G", "used": "1 G", "percent_used": 99, "free": "0"},
                "logs": {"recent_errors": []},
                "chats": {"active_chats": 0},
                "timestamp": "t",
                "health": "healthy",
            }
        ),
    )
    result = await chat_mod._execute_tool(1, "analyze_system_logs", {})
    assert "Sin errores recientes" in result["message"]


# ---------- move_or_rename_file ----------


async def test_move_or_rename_file_validation(monkeypatch):
    missing_src = await chat_mod._execute_tool(1, "move_or_rename_file", {"source_path": ""})
    assert not missing_src["success"]
    missing_dest = await chat_mod._execute_tool(
        1, "move_or_rename_file", {"source_path": "a.md", "dest_folder": ""}
    )
    assert not missing_dest["success"]


async def test_move_or_rename_file_dispatch(monkeypatch):
    calls = []
    monkeypatch.setattr(chat_mod, "obsidian_move_rename", _fn({"success": True}, calls=calls))
    result = await chat_mod._execute_tool(
        1, "move_or_rename_file", {"source_path": "/diario/a.md", "dest_folder": "proyectos"}
    )
    assert result["success"]
    assert calls[0][0][0] == "/data/obsidian_vault/diario/a.md"


# ---------- ask_deep_knowledge_base / search_second_brain ----------


async def test_ask_deep_knowledge_base_validation_and_empty(monkeypatch):
    missing = await chat_mod._execute_tool(1, "ask_deep_knowledge_base", {"query": ""})
    assert not missing["success"]
    monkeypatch.setattr(chat_mod.vector_db, "query", _fn({"success": True, "results": []}))
    empty = await chat_mod._execute_tool(1, "ask_deep_knowledge_base", {"query": "coche"})
    assert "NO_ENCONTRADO" in empty["message"]
    monkeypatch.setattr(chat_mod.vector_db, "query", _fn({"success": False, "message": "roto"}))
    broken = await chat_mod._execute_tool(1, "ask_deep_knowledge_base", {"query": "coche"})
    assert broken["message"] == "roto"


async def test_ask_deep_knowledge_base_formats_hits(monkeypatch):
    monkeypatch.setattr(
        chat_mod.vector_db,
        "query",
        _fn(
            {
                "success": True,
                "results": [
                    {
                        "relevance": 0.8,
                        "note_path": "coches.md",
                        "heading": "Motor",
                        "obsidian_uri": "obsidian://x",
                        "content": "  el coche es azul  ",
                    }
                ],
                "notes_found": ["coches.md"],
            }
        ),
    )
    result = await chat_mod._execute_tool(1, "ask_deep_knowledge_base", {"query": "coche"})
    assert "coches.md" in result["message"]
    assert "Motor" in result["message"]
    assert "Notas encontradas" in result["message"]


async def test_search_second_brain_variants(monkeypatch):
    missing = await chat_mod._execute_tool(1, "search_second_brain", {"query": ""})
    assert not missing["success"]

    seen = {}

    async def fake_query(query, top_k=6, filter_tags=None):
        seen["filter_tags"] = filter_tags
        return {"success": True, "results": []}

    monkeypatch.setattr(chat_mod.vector_db, "query", fake_query)
    empty = await chat_mod._execute_tool(
        1, "search_second_brain", {"query": "coche", "tags": ["a"]}
    )
    assert "NO_ENCONTRADO" in empty["message"]
    assert seen["filter_tags"] == ["a"]

    monkeypatch.setattr(chat_mod.vector_db, "query", _fn({"success": False, "message": "roto"}))
    broken = await chat_mod._execute_tool(1, "search_second_brain", {"query": "coche"})
    assert broken["message"] == "roto"


async def test_search_second_brain_formats_hits(monkeypatch):
    monkeypatch.setattr(
        chat_mod.vector_db,
        "query",
        _fn(
            {
                "success": True,
                "results": [
                    {
                        "relevance": 0.5,
                        "note_path": "viaje.md",
                        "heading": "Italia",
                        "obsidian_uri": "obsidian://y",
                        "content": "roma",
                    }
                ],
                "notes_found": ["viaje.md"],
            }
        ),
    )
    result = await chat_mod._execute_tool(1, "search_second_brain", {"query": "italia"})
    assert "Italia" in result["message"]
    assert "obsidian://y" in result["message"]
    assert "Notas de origen" in result["message"]


# ---------- manage_google_calendar ----------


async def test_manage_google_calendar_create_variants(monkeypatch):
    calls = []
    monkeypatch.setattr(
        chat_mod, "gcal", SimpleNamespace(add_event=_fn({"success": True}, calls=calls))
    )
    ok = await chat_mod._execute_tool(
        1,
        "manage_google_calendar",
        {"action": "create", "title": "Reunion", "datetime_str": "2026-10-01T10:00"},
    )
    assert ok["success"]

    missing = await chat_mod._execute_tool(1, "manage_google_calendar", {"action": "create"})
    assert not missing["success"]

    monkeypatch.setattr(
        "src.services.google_services_manager.parse_relative_datetime",
        lambda when: None,
    )
    no_when = await chat_mod._execute_tool(
        1, "manage_google_calendar", {"action": "create", "title": "X", "when": "mañana"}
    )
    assert not no_when["success"]


async def test_manage_google_calendar_create_from_when(monkeypatch):
    calls = []
    monkeypatch.setattr(
        chat_mod, "gcal", SimpleNamespace(add_event=_fn({"success": True}, calls=calls))
    )
    monkeypatch.setattr(
        "src.services.google_services_manager.parse_relative_datetime",
        lambda when: SimpleNamespace(isoformat=lambda: "2026-10-02T09:00:00"),
    )
    result = await chat_mod._execute_tool(
        1, "manage_google_calendar", {"action": "create", "title": "X", "when": "mañana"}
    )
    assert result["success"]
    assert calls[0][0][1] == "2026-10-02T09:00:00"


async def test_manage_google_calendar_list_variants(monkeypatch):
    monkeypatch.setattr(chat_mod, "gcal", SimpleNamespace(list_upcoming_events=_fn([])))
    empty = await chat_mod._execute_tool(1, "manage_google_calendar", {"action": "list"})
    assert "No hay eventos" in empty["message"]

    monkeypatch.setattr(
        chat_mod,
        "gcal",
        SimpleNamespace(list_upcoming_events=_fn([{"title": "Cena", "start": "hoy"}])),
    )
    listed = await chat_mod._execute_tool(1, "manage_google_calendar", {"action": "list"})
    assert "Cena" in listed["message"]


async def test_manage_google_calendar_delete_by_id_and_title(monkeypatch):
    deleted = []
    monkeypatch.setattr(
        chat_mod,
        "gcal",
        SimpleNamespace(
            list_upcoming_events=_fn([{"id": "e1", "title": "Cena con Ana"}]),
            delete_event=_fn({"success": True}, calls=deleted),
        ),
    )
    by_id = await chat_mod._execute_tool(
        1, "manage_google_calendar", {"action": "delete", "event_id": "e1"}
    )
    assert by_id["success"]
    by_title = await chat_mod._execute_tool(
        1, "manage_google_calendar", {"action": "delete", "title": "cena"}
    )
    assert by_title["success"]
    assert deleted[1][0][0] == "e1"

    missing = await chat_mod._execute_tool(1, "manage_google_calendar", {"action": "delete"})
    assert not missing["success"]

    unknown = await chat_mod._execute_tool(
        1, "manage_google_calendar", {"action": "delete", "title": "fiesta"}
    )
    assert not unknown["success"]
    assert "No encontre" in unknown["message"]

    invalid = await chat_mod._execute_tool(1, "manage_google_calendar", {"action": "otra"})
    assert not invalid["success"]


# ---------- set_recurring_reminder ----------


async def test_set_recurring_reminder_validation(monkeypatch):
    _patch_db(monkeypatch)
    missing = await chat_mod._execute_tool(
        1, "set_recurring_reminder", {"pattern": "", "message": ""}
    )
    assert not missing["success"]
    invalid = await chat_mod._execute_tool(
        1, "set_recurring_reminder", {"pattern": "cada bisiesto", "message": "x"}
    )
    assert not invalid["success"]


async def test_set_recurring_reminder_rolls_to_next_day(monkeypatch):
    calls = []
    monkeypatch.setattr(chat_mod.db, "add_recurring_alert", _fn(9, calls=calls))
    result = await chat_mod._execute_tool(
        1,
        "set_recurring_reminder",
        {"pattern": "daily", "message": "pastilla", "time_str": "00:00"},
    )
    assert result["success"]
    assert calls


async def test_set_recurring_reminder_patterns_and_time(monkeypatch):
    _patch_db(monkeypatch)
    every = await chat_mod._execute_tool(
        1, "set_recurring_reminder", {"pattern": "every_2_hours", "message": "bebe agua"}
    )
    assert every["success"]
    with_time = await chat_mod._execute_tool(
        1, "set_recurring_reminder", {"pattern": "daily", "message": "x", "time_str": "23:58"}
    )
    assert with_time["success"]
    bad_time = await chat_mod._execute_tool(
        1, "set_recurring_reminder", {"pattern": "daily", "message": "x", "time_str": "no-hora"}
    )
    assert bad_time["success"]


# ---------- Google auth / calendario / drive / gmail ----------


async def test_generate_google_auth_link_variants(monkeypatch):
    monkeypatch.setattr(
        chat_mod.google_service,
        "generate_auth_url",
        _fn({"success": True, "auth_url": "https://accounts.google/o"}),
    )
    ok = await chat_mod._execute_tool(1, "generate_google_auth_link", {})
    assert "https://accounts.google/o" in ok["message"]

    monkeypatch.setattr(
        chat_mod.google_service,
        "generate_auth_url",
        _fn({"success": False, "message": "sin oauth"}),
    )
    failed = await chat_mod._execute_tool(1, "generate_google_auth_link", {})
    assert failed["message"] == "sin oauth"


async def test_save_google_verification_code_variants(monkeypatch):
    missing = await chat_mod._execute_tool(1, "save_google_verification_code", {"auth_code": ""})
    assert not missing["success"]
    calls = []
    monkeypatch.setattr(
        chat_mod.google_service, "exchange_code", _fn({"success": True}, calls=calls)
    )
    ok = await chat_mod._execute_tool(1, "save_google_verification_code", {"auth_code": "4/abc"})
    assert ok["success"]
    assert calls[0][0][0] == "4/abc"


async def test_get_google_calendar_events_variants(monkeypatch):
    # Bug 2026-10-03: antes se llamaba al servicio legacy (google_service,
    # sin autenticar) y respondia "usa /setup" aunque Google estaba conectado.
    monkeypatch.setattr(chat_mod.google_services, "list_events", _fn([]))
    monkeypatch.setattr(chat_mod.db, "get_upcoming_events", _fn([]))
    empty = await chat_mod._execute_tool(1, "get_google_calendar_events", {})
    assert "No hay eventos" in empty["message"]

    monkeypatch.setattr(
        chat_mod.google_services,
        "list_events",
        _fn([{"summary": "Dentista", "start": {"dateTime": "2099-01-01T10:00:00"}}]),
    )
    monkeypatch.setattr(
        "src.utils.obsidian_manager.sync_calendar_to_obsidian",
        _fn({"success": True, "message": "sincronizado"}),
    )
    synced = await chat_mod._execute_tool(1, "get_google_calendar_events", {})
    assert "Dentista" in synced["message"]
    assert "sincronizado" in synced["message"]


async def test_create_google_calendar_event_variants(monkeypatch):
    missing = await chat_mod._execute_tool(1, "create_google_calendar_event", {"title": ""})
    assert not missing["success"]

    monkeypatch.setattr(
        chat_mod.google_services,
        "create_event",
        _fn({"success": True, "message": "creado", "html_link": "https://cal"}),
    )
    monkeypatch.setattr(
        "src.utils.obsidian_manager.sync_calendar_to_obsidian",
        _fn({"success": True, "message": "guardado en obsidian"}),
    )
    monkeypatch.setattr(
        "src.services.google_services_manager.parse_relative_datetime",
        lambda when: SimpleNamespace(isoformat=lambda: "2026-10-03T11:00:00"),
    )
    created = await chat_mod._execute_tool(
        1,
        "create_google_calendar_event",
        {
            "title": "Revision",
            "when": "pasado mañana",
            "end_datetime": "2026-10-03T12:00:00",
            "description": "anual",
        },
    )
    assert created["success"]
    assert "guardado en obsidian" in created["message"]


async def test_search_google_drive_dispatch(monkeypatch):
    monkeypatch.setattr(
        chat_mod,
        "google_services",
        SimpleNamespace(search_files=_fn([{"name": "informe.pdf", "id": "abc"}]), is_ready=True),
    )
    searched = await chat_mod._execute_tool(1, "search_google_drive", {"query": "informe"})
    assert searched["success"]
    assert "informe.pdf" in searched["message"]

    monkeypatch.setattr(
        chat_mod,
        "google_services",
        SimpleNamespace(search_files=_fn([]), is_ready=True),
    )
    empty = await chat_mod._execute_tool(1, "search_google_drive", {"query": "nada"})
    assert "No encontré nada" in empty["message"]


async def test_list_google_drive_variants(monkeypatch):
    monkeypatch.setattr(
        chat_mod.google_services, "list_drive", _fn({"success": False, "message": "x"})
    )
    failed = await chat_mod._execute_tool(1, "list_google_drive", {})
    assert failed["message"] == "x"

    monkeypatch.setattr(chat_mod.google_services, "list_drive", _fn({"success": True, "files": []}))
    empty = await chat_mod._execute_tool(1, "list_google_drive", {})
    assert "No hay elementos" in empty["message"]

    monkeypatch.setattr(
        chat_mod.google_services,
        "list_drive",
        _fn(
            {
                "success": True,
                "files": [
                    {"name": "docs", "id": "f1", "mimeType": "application/vnd.google-apps.folder"},
                    {"name": "a.pdf", "id": "f2", "mimeType": "application/pdf"},
                ],
            }
        ),
    )
    listed = await chat_mod._execute_tool(1, "list_google_drive", {"kind": "all"})
    assert "docs" in listed["message"]
    assert listed["files"]


async def test_read_google_drive_file_variants(monkeypatch):
    monkeypatch.setattr(
        chat_mod.google_services, "read_file", _fn({"success": False, "message": "404"})
    )
    failed = await chat_mod._execute_tool(1, "read_google_drive_file", {"file_id": "x"})
    assert failed["message"] == "404"

    monkeypatch.setattr(
        chat_mod.google_services,
        "read_file",
        _fn({"success": True, "name": "a.pdf", "text": "contenido", "truncated": True}),
    )
    ok = await chat_mod._execute_tool(1, "read_google_drive_file", {"file_id": "x"})
    assert "recortado" in ok["message"]
    assert "contenido" in ok["message"]


async def test_search_gmail_variants(monkeypatch):
    monkeypatch.setattr(
        chat_mod.google_services, "search_gmail", _fn({"success": False, "message": "x"})
    )
    failed = await chat_mod._execute_tool(1, "search_gmail", {"query": "factura"})
    assert failed["message"] == "x"

    monkeypatch.setattr(
        chat_mod.google_services, "search_gmail", _fn({"success": True, "messages": []})
    )
    empty = await chat_mod._execute_tool(1, "search_gmail", {"query": "factura"})
    assert "No encontré correos" in empty["message"]

    monkeypatch.setattr(
        chat_mod.google_services,
        "search_gmail",
        _fn(
            {
                "success": True,
                "messages": [
                    {"subject": "Factura", "from": "banco", "date": "hoy", "snippet": "pago"}
                ],
            }
        ),
    )
    listed = await chat_mod._execute_tool(1, "search_gmail", {"query": "factura"})
    assert "Factura" in listed["message"]


async def test_trigger_n8n_dispatch(monkeypatch):
    async def fake_trigger(args):
        return {"success": True, "message": "ejecutada"}

    monkeypatch.setattr(chat_mod, "_trigger_n8n", fake_trigger)
    result = await chat_mod._execute_tool(1, "trigger_n8n", {"workflow": "facturas"})
    assert result["message"] == "ejecutada"


async def test_send_gmail_variants(monkeypatch):
    missing = await chat_mod._execute_tool(1, "send_gmail", {"to": "", "body": ""})
    assert not missing["success"]
    monkeypatch.setattr(chat_mod.google_services, "send_email", _fn({"success": True}))
    ok = await chat_mod._execute_tool(
        1, "send_gmail", {"to": "a@b.es", "subject": "hola", "body": "texto"}
    )
    assert ok["success"]


async def test_manage_google_tasks_variants(monkeypatch):
    monkeypatch.setattr(chat_mod.google_services, "list_tasks", _fn({"success": True, "tasks": []}))
    empty = await chat_mod._execute_tool(1, "manage_google_tasks", {"action": "list"})
    assert "No tienes tareas" in empty["message"]

    monkeypatch.setattr(
        chat_mod.google_services,
        "list_tasks",
        _fn({"success": True, "tasks": [{"title": "Comprar", "id": "t1"}]}),
    )
    listed = await chat_mod._execute_tool(1, "manage_google_tasks", {"action": "list"})
    assert "Comprar" in listed["message"]

    missing_title = await chat_mod._execute_tool(1, "manage_google_tasks", {"action": "create"})
    assert not missing_title["success"]
    monkeypatch.setattr(chat_mod.google_services, "create_task", _fn({"success": True}))
    created = await chat_mod._execute_tool(
        1, "manage_google_tasks", {"action": "create", "title": "Comprar"}
    )
    assert created["success"]

    # Con fecha relativa: se parsea y se pasa como due (regresion 2026-09-29).
    calls: list = []
    monkeypatch.setattr(
        chat_mod.google_services, "create_task", _fn({"success": True}, calls=calls)
    )
    con_fecha = await chat_mod._execute_tool(
        1, "manage_google_tasks", {"action": "create", "title": "Pagar", "due": "mañana"}
    )
    assert con_fecha["success"]
    assert calls and calls[-1][1].get("due")

    missing_id = await chat_mod._execute_tool(1, "manage_google_tasks", {"action": "complete"})
    assert not missing_id["success"]
    monkeypatch.setattr(chat_mod.google_services, "complete_task", _fn({"success": True}))
    monkeypatch.setattr(chat_mod.google_services, "delete_task", _fn({"success": True}))
    completed = await chat_mod._execute_tool(
        1, "manage_google_tasks", {"action": "complete", "task_id": "t1"}
    )
    assert completed["success"]
    deleted = await chat_mod._execute_tool(
        1, "manage_google_tasks", {"action": "delete", "task_id": "t1"}
    )
    assert deleted["success"]

    invalid = await chat_mod._execute_tool(1, "manage_google_tasks", {"action": "archivar"})
    assert not invalid["success"]


# ---------- find_contact / fitness / ingest_file / desconocida ----------


async def test_find_contact_multiple_hits_and_alias_retry(monkeypatch):
    monkeypatch.setattr(
        chat_mod.google_services,
        "find_contact",
        _fn(
            {
                "success": True,
                "contacts": [
                    {"name": "Ana", "email": "a@b.es", "phone": "600"},
                    {"name": "Analia", "email": None, "phone": None},
                ],
            }
        ),
    )
    multi = await chat_mod._execute_tool(1, "find_contact", {"query": "ana"})
    assert "varios coinciden" in multi["message"]

    calls = []

    async def fake_find(query):
        calls.append(query)
        return (
            {"success": True, "contacts": []}
            if len(calls) == 1
            else {
                "success": True,
                "contacts": [{"name": "Aa Mama", "email": "", "phone": "611"}],
            }
        )

    monkeypatch.setattr(chat_mod.google_services, "find_contact", fake_find)
    monkeypatch.setattr(chat_mod, "_resolve_contact_alias", _fn("Aa Mama"))
    retried = await chat_mod._execute_tool(1, "find_contact", {"query": "mama raulito"})
    assert "Aa Mama" in retried["message"]
    assert calls == ["mama raulito", "Aa Mama"]


async def test_fitness_daily_steps(monkeypatch):
    monkeypatch.setattr(chat_mod.google_services, "fitness_daily_steps", _fn({"steps": 4321}))
    result = await chat_mod._execute_tool(1, "fitness_daily_steps", {})
    assert "4321 pasos" in result["message"]


async def test_ingest_file_variants(monkeypatch):
    missing = await chat_mod._execute_tool(1, "ingest_file", {"filename": ""})
    assert not missing["success"]

    notes = []
    monkeypatch.setattr(
        "src.utils.obsidian_manager.create_or_append_note", _fn({"success": True}, calls=notes)
    )
    ok = await chat_mod._execute_tool(
        1,
        "ingest_file",
        {
            "filename": "contrato.pdf",
            "folder": "recursos",
            "note_type": "recurso",
            "tags": ["legal"],
            "summary": "contrato de alquiler",
            "content": "clausulas",
        },
    )
    assert ok["success"]
    assert "contrato.pdf" in ok["message"]
    assert notes and "contrato de alquiler" in notes[0][1]["content"]

    monkeypatch.setattr(
        "src.utils.obsidian_manager.create_or_append_note",
        _fn({"success": False, "message": "roto"}),
    )
    failed = await chat_mod._execute_tool(1, "ingest_file", {"filename": "x.txt"})
    assert failed["message"] == "roto"


async def test_unknown_function_reports_error():
    result = await chat_mod._execute_tool(1, "funcion_inventada", {})
    assert not result["success"]
    assert "desconocida" in result["message"]


async def test_tool_exception_is_captured(monkeypatch):
    monkeypatch.setattr(chat_mod.db, "add_finance_record", _fn(exc=RuntimeError("db caida")))
    result = await chat_mod._execute_tool(1, "save_expense", {"amount": 1})
    assert not result["success"]
    assert "db caida" in result["message"]


async def test_manage_google_tasks_delete_por_titulo(monkeypatch):
    llamadas = []
    google = SimpleNamespace(
        is_ready=True,
        list_tasks=_fn({"success": True, "tasks": [{"id": "abc", "title": "Comprar pilas"}]}),
        delete_task=_fn({"success": True, "message": "Tarea borrada"}, calls=llamadas),
    )
    monkeypatch.setattr(chat_mod, "google_services", google)
    result = await chat_mod._execute_tool(
        1, "manage_google_tasks", {"action": "delete", "task_title": "comprar pilas"}
    )
    assert result["success"]
    assert llamadas and llamadas[0][0][0] == "abc"


async def test_manage_google_tasks_delete_titulo_no_encontrado(monkeypatch):
    google = SimpleNamespace(is_ready=True, list_tasks=_fn({"success": True, "tasks": []}))
    monkeypatch.setattr(chat_mod, "google_services", google)
    result = await chat_mod._execute_tool(
        1, "manage_google_tasks", {"action": "delete", "task_title": "no existe"}
    )
    assert not result["success"]
    assert "No encontre" in result["message"]


def test_limpiar_consulta_contacto_quita_relleno():
    from src.handlers import chat as chat_mod

    assert (
        chat_mod._limpiar_consulta_contacto(
            "busca un contacto con el nombre Ana que tiene puesto su correo"
        )
        == "Ana"
    )
    assert chat_mod._limpiar_consulta_contacto("encuentra a Ana en mis contactos") == "a Ana"
    assert chat_mod._limpiar_consulta_contacto("mi madre") == "mi madre"
    assert chat_mod._limpiar_consulta_contacto("Aa Mama") == "Aa Mama"
    assert chat_mod._limpiar_consulta_contacto("") == ""


def test_limpiar_titulo_tarea_quita_instruccion():
    from src.handlers import chat as chat_mod

    assert chat_mod._limpiar_titulo_tarea("Marcala como realizada") == ""
    assert (
        chat_mod._limpiar_titulo_tarea("marca como hecha la tarea comprar pilas") == "comprar pilas"
    )
    assert chat_mod._limpiar_titulo_tarea("Programa Buena Tierra") == "programa buena tierra"


async def test_manage_google_tasks_complete_titulo_en_task_id(monkeypatch):
    llamadas = []
    google = SimpleNamespace(
        is_ready=True,
        list_tasks=_fn(
            {"success": True, "tasks": [{"id": "abc", "title": "Programa Buena Tierra"}]}
        ),
        complete_task=_fn({"success": True, "message": "Tarea completada"}, calls=llamadas),
    )
    monkeypatch.setattr(chat_mod, "google_services", google)
    result = await chat_mod._execute_tool(
        1, "manage_google_tasks", {"action": "complete", "task_id": "Programa Buena Tierra"}
    )
    assert result["success"]
    assert llamadas and llamadas[0][0][0] == "abc"


async def test_manage_google_tasks_complete_instruccion_como_titulo(monkeypatch):
    llamadas = []
    google = SimpleNamespace(
        is_ready=True,
        list_tasks=_fn({"success": True, "tasks": [{"id": "abc", "title": "Comprar pilas"}]}),
        complete_task=_fn({"success": True, "message": "ok"}, calls=llamadas),
    )
    monkeypatch.setattr(chat_mod, "google_services", google)
    result = await chat_mod._execute_tool(
        1,
        "manage_google_tasks",
        {"action": "complete", "task_title": "marca como realizada la tarea comprar pilas"},
    )
    assert result["success"]
    assert llamadas and llamadas[0][0][0] == "abc"


async def test_manage_google_tasks_no_encontrada_lista_pendientes(monkeypatch):
    google = SimpleNamespace(
        is_ready=True,
        list_tasks=_fn({"success": True, "tasks": [{"id": "abc", "title": "Comprar pilas"}]}),
    )
    monkeypatch.setattr(chat_mod, "google_services", google)
    result = await chat_mod._execute_tool(
        1, "manage_google_tasks", {"action": "complete", "task_title": "pasear al perro"}
    )
    assert not result["success"]
    assert "Tareas pendientes" in result["message"]
    assert "Comprar pilas" in result["message"]


async def test_manage_google_tasks_id_invalido_reintenta_por_titulo(monkeypatch):
    intentos = []

    async def complete(task_id, *a, **k):
        intentos.append(task_id)
        if task_id == "Programa Buena Tierra":
            raise RuntimeError("invalid task id")
        return {"success": True, "message": "Tarea completada"}

    google = SimpleNamespace(
        is_ready=True,
        list_tasks=_fn(
            {"success": True, "tasks": [{"id": "abc", "title": "Programa Buena Tierra"}]}
        ),
        complete_task=complete,
    )
    monkeypatch.setattr(chat_mod, "google_services", google)
    result = await chat_mod._execute_tool(
        1, "manage_google_tasks", {"action": "complete", "task_id": "Programa Buena Tierra"}
    )
    assert result["success"]
    # task_id con espacios -> se trata como titulo directamente
    assert intentos == ["abc"]


async def test_send_gmail_normaliza_correo_dictado(monkeypatch):
    llamadas = []
    google = SimpleNamespace(
        is_ready=True,
        send_email=_fn({"success": True, "message": "enviado"}, calls=llamadas),
    )
    monkeypatch.setattr(chat_mod, "google_services", google)
    result = await chat_mod._execute_tool(
        1,
        "send_gmail",
        {"to": "anabel arroba gmail punto com", "subject": "Hola", "body": "test"},
    )
    assert result["success"]
    assert llamadas[0][1]["to"] == "anabel@gmail.com"


async def test_manage_google_tasks_acepta_alias_de_accion_de_gemma(monkeypatch):
    """gemma4:12b manda action='complete_task' y task_name (2026-09-30)."""
    llamadas = []
    google = SimpleNamespace(
        is_ready=True,
        list_tasks=_fn({"success": True, "tasks": [{"id": "abc", "title": "Comprar pilas"}]}),
        complete_task=_fn({"success": True, "message": "ok"}, calls=llamadas),
    )
    monkeypatch.setattr(chat_mod, "google_services", google)
    result = await chat_mod._execute_tool(
        1,
        "manage_google_tasks",
        {"action": "complete_task", "task_name": "comprar pilas"},
    )
    assert result["success"]
    assert llamadas and llamadas[0][0][0] == "abc"


async def test_manage_google_tasks_accion_vacia_mensaje_util(monkeypatch):
    google = SimpleNamespace(is_ready=True)
    monkeypatch.setattr(chat_mod, "google_services", google)
    result = await chat_mod._execute_tool(1, "manage_google_tasks", {"action": ""})
    assert not result["success"]
    assert "list, create, complete" in result["message"]


async def test_manage_google_calendar_acepta_alias_de_accion(monkeypatch):
    llamadas = []
    google = SimpleNamespace(is_ready=True)
    monkeypatch.setattr(chat_mod, "google_services", google)
    monkeypatch.setattr(
        chat_mod,
        "gcal",
        SimpleNamespace(add_event=_fn({"success": True, "message": "ok"}, calls=llamadas)),
    )
    result = await chat_mod._execute_tool(
        1,
        "manage_google_calendar",
        {"action": "add", "title": "Cena", "when": "mañana a las 21"},
    )
    assert result["success"]
    assert llamadas


# ---------- citas [S#] en search_second_brain (mejora 1) ----------


async def test_search_second_brain_con_citas(monkeypatch):
    _patch_db(monkeypatch)
    resultado_busqueda = {
        "success": True,
        "results": [
            {
                "content": "Ana trabaja en el proyecto Babel",
                "relevance": "0.900",
                "note_path": "personas/ana.md",
                "heading": "Trabajo",
                "obsidian_uri": "obsidian://open?file=ana",
                "tags": [],
            }
        ],
        "notes_found": ["personas/ana.md"],
        "message": "Encontrados 1 fragmentos relevantes en 1 nota.",
    }
    monkeypatch.setattr(chat_mod.vector_db, "query", _fn(resultado_busqueda))
    from src.utils.citations import citations

    citations.reset()
    result = await chat_mod._execute_tool(1, "search_second_brain", {"query": "Ana"})
    citations.clear()
    assert result["success"]
    assert "[S1]" in result["message"]
    assert "personas/ana.md" in result["message"]
    assert result["sources"][0]["id"] == "S1"
    assert result["sources"][0]["obsidian_uri"] == "obsidian://open?file=ana"


# ---------- grafo de conocimiento (mejora 8) ----------


async def test_add_relation_y_search_relations(monkeypatch):
    _patch_db(monkeypatch)
    monkeypatch.setattr(
        chat_mod.db,
        "add_relation",
        _fn(
            {
                "success": True,
                "relation": {
                    "id": 1,
                    "subject": "Ana",
                    "predicate": "trabaja_en",
                    "object": "Babel",
                },
            }
        ),
    )
    monkeypatch.setattr(
        chat_mod.db,
        "search_relations",
        _fn([{"id": 1, "subject": "Ana", "predicate": "trabaja_en", "object": "Babel"}]),
    )
    result = await chat_mod._execute_tool(
        1, "add_relation", {"subject": "Ana", "predicate": "Trabaja_En", "object": "Babel"}
    )
    assert result["success"]
    assert "Ana" in result["message"]

    result = await chat_mod._execute_tool(1, "search_relations", {"query": "Ana"})
    assert result["success"]
    assert "[1]" in result["message"]
    assert "trabaja_en" in result["message"]


async def test_add_relation_invalida_y_search_vacia(monkeypatch):
    _patch_db(monkeypatch)
    monkeypatch.setattr(
        chat_mod.db,
        "add_relation",
        _fn({"success": False, "message": "Sujeto, predicado y objeto son obligatorios."}),
    )
    monkeypatch.setattr(chat_mod.db, "search_relations", _fn([]))
    result = await chat_mod._execute_tool(1, "add_relation", {"subject": "Ana"})
    assert not result["success"]

    result = await chat_mod._execute_tool(1, "search_relations", {})
    assert result["success"]
    assert "NO_ENCONTRADO" in result["message"]


async def test_delete_relation(monkeypatch):
    _patch_db(monkeypatch)
    monkeypatch.setattr(chat_mod.db, "delete_relation", _fn(True))
    result = await chat_mod._execute_tool(1, "delete_relation", {"relation_id": "3"})
    assert result["success"]
    assert "3" in result["message"]

    result = await chat_mod._execute_tool(1, "delete_relation", {"relation_id": "x"})
    assert not result["success"]


# ---------- RGPD (mejora 7) ----------


async def test_export_my_data(monkeypatch, tmp_path):
    _patch_db(monkeypatch)
    monkeypatch.setattr(
        chat_mod.db,
        "export_user_data",
        _fn({"chat_id": 1, "chat_history": [{"id": 1}], "events": [{"id": 2}]}),
    )
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    result = await chat_mod._execute_tool(1, "export_my_data", {})
    assert result["success"]
    assert result["counts"]["chat_history"] == 1
    assert Path(result["path"]).exists()


async def test_delete_my_data_pide_confirmacion(monkeypatch):
    _patch_db(monkeypatch)
    monkeypatch.setattr(chat_mod.db, "delete_user_data", _fn({"chat_history": 2}))

    result = await chat_mod._execute_tool(1, "delete_my_data", {})
    assert not result["success"]
    assert "confirm" in result["message"].lower() or "CONFIRMA" in result["message"]

    result = await chat_mod._execute_tool(1, "delete_my_data", {"confirm": True})
    assert result["success"]
    assert "chat_history" in result["message"]


# ---------- métricas por herramienta (mejora 2) ----------


async def test_execute_tool_measured_registra_metricas(monkeypatch):
    _patch_db(monkeypatch)
    from src.utils.telemetry import metrics

    metrics.reset()
    result = await chat_mod.execute_tool_measured(1, "get_finance_summary", {})
    assert result["success"] is True
    counters = metrics.get_counters()
    assert counters.get("tool_calls") == 1
    assert counters.get("tool_calls.get_finance_summary") == 1
    assert metrics.get_histogram_stats("tool_latency_ms")["count"] == 1
    assert metrics.get_histogram_stats("tool_latency_ms.get_finance_summary")["count"] == 1
    metrics.reset()


# ---------- correos: redactar (draft_gmail) y dictado (mejora demo) ----------


async def test_draft_gmail_requiere_destinatario_y_cuerpo(monkeypatch):
    _patch_db(monkeypatch)
    result = await chat_mod._execute_tool(1, "draft_gmail", {"to": "x@y.z"})
    assert not result["success"]
    result = await chat_mod._execute_tool(1, "draft_gmail", {"body": "solo cuerpo"})
    assert not result["success"]


async def test_draft_gmail_llama_create_draft_con_correo_normalizado(monkeypatch):
    _patch_db(monkeypatch)
    llamadas = []

    async def fake_draft(to, subject, body):
        llamadas.append((to, subject, body))
        return {"success": True, "message": "Borrador creado para %s" % to}

    monkeypatch.setattr(chat_mod.google_services, "create_draft", fake_draft)
    result = await chat_mod._execute_tool(
        1,
        "draft_gmail",
        {
            "to": "ejemplo punto ejemplo arroba gmail punto com",
            "subject": "Prueba",
            "body": "Hola, esto es una prueba.",
        },
    )
    assert result["success"]
    assert llamadas == [("ejemplo.ejemplo@gmail.com", "Prueba", "Hola, esto es una prueba.")]


async def test_find_contact_normaliza_correo_dictado(monkeypatch):
    _patch_db(monkeypatch)
    vistas = []

    async def fake_find(query, max_results=5):
        vistas.append(query)
        return {
            "contacts": [
                {"name": "Ana", "email": "ejemplo.ejemplo@gmail.com", "phone": "600111222"}
            ]
        }

    monkeypatch.setattr(chat_mod.google_services, "find_contact", fake_find)
    result = await chat_mod._execute_tool(
        1, "find_contact", {"query": "ejemplo punto ejemplo arroba gmail punto com"}
    )
    assert result["success"]
    assert vistas and "@" in vistas[0]
    assert "ejemplo.ejemplo@gmail.com" in result["message"]


# ---------- calendario: usar el manager autenticado (bug 2026-10-03) ----------


async def test_get_google_calendar_events_usa_google_conectado(monkeypatch):
    _patch_db(monkeypatch)

    async def fake_list(max_results=10, time_min=None):
        return [
            {"summary": "Reunión", "start": {"dateTime": "2099-01-01T10:00:00"}},
        ]

    monkeypatch.setattr(chat_mod.google_services, "list_events", fake_list)
    monkeypatch.setattr(
        "src.utils.obsidian_manager.sync_calendar_to_obsidian", _fn({"message": ""})
    )
    result = await chat_mod._execute_tool(1, "get_google_calendar_events", {})
    assert result["success"]
    assert "Reunión" in result["message"]


async def test_get_google_calendar_events_falla_a_agenda_local(monkeypatch):
    _patch_db(monkeypatch)

    async def broken(max_results=10, time_min=None):
        raise RuntimeError("google caido")

    monkeypatch.setattr(chat_mod.google_services, "list_events", broken)
    monkeypatch.setattr(
        chat_mod.db,
        "get_upcoming_events",
        _fn([{"title": "Local", "event_datetime": "2099-01-01 10:00:00"}]),
    )
    monkeypatch.setattr(
        "src.utils.obsidian_manager.sync_calendar_to_obsidian", _fn({"message": ""})
    )
    result = await chat_mod._execute_tool(1, "get_google_calendar_events", {})
    assert result["success"]
    assert "Local" in result["message"]
    assert "/setup" not in result["message"]


async def test_get_google_calendar_events_filtra_por_dias(monkeypatch):
    _patch_db(monkeypatch)

    async def fake_list(max_results=10, time_min=None):
        return [
            {"summary": "Cerca", "start": {"dateTime": "2000-01-02T10:00:00"}},
            {"summary": "Lejos", "start": {"dateTime": "2099-01-01T10:00:00"}},
        ]

    monkeypatch.setattr(chat_mod.google_services, "list_events", fake_list)
    monkeypatch.setattr(
        "src.utils.obsidian_manager.sync_calendar_to_obsidian", _fn({"message": ""})
    )
    result = await chat_mod._execute_tool(1, "get_google_calendar_events", {"days": 7})
    assert result["success"]
    assert "Cerca" in result["message"]
    assert "Lejos" not in result["message"]


# ---------- manage_google_tasks local: rowcount real (bug 2026-10-06) ----------


async def test_manage_google_tasks_local_no_afirma_si_no_hay_fila(monkeypatch):
    monkeypatch.setattr(GoogleServicesManager, "is_ready", property(lambda self: False))
    monkeypatch.setattr(chat_mod.db, "list_tasks", _fn([]))
    monkeypatch.setattr(chat_mod.db, "complete_task", _fn(0))
    monkeypatch.setattr(chat_mod.db, "delete_task", _fn(0))
    res = await chat_mod._execute_tool(
        1, "manage_google_tasks", {"action": "complete", "task_id": "42"}
    )
    assert res["success"] is False
    res = await chat_mod._execute_tool(
        1, "manage_google_tasks", {"action": "delete", "task_id": "42"}
    )
    assert res["success"] is False
    res = await chat_mod._execute_tool(
        1, "manage_google_tasks", {"action": "complete", "task_id": "no-es-numero"}
    )
    assert res["success"] is False
    monkeypatch.setattr(chat_mod.db, "complete_task", _fn(1))
    res = await chat_mod._execute_tool(
        1, "manage_google_tasks", {"action": "complete", "task_id": "42"}
    )
    assert res["success"] is True


# ---------- create_condition_rule (2026-10-06) ----------


async def test_create_condition_rule_crud(monkeypatch):
    guardados = []

    async def add_rule(chat_id, rule_json, message, repeats=False):
        guardados.append((chat_id, rule_json, message, repeats))
        return 5

    async def list_rules(chat_id):
        return [{"id": 5, "message": "aviso", "is_active": 1}]

    async def del_rule(chat_id, rule_id):
        return 1 if rule_id == 5 else 0

    monkeypatch.setattr(chat_mod.db, "add_conditional_rule", add_rule)
    monkeypatch.setattr(chat_mod.db, "list_conditional_rules", list_rules)
    monkeypatch.setattr(chat_mod.db, "delete_conditional_rule", del_rule)

    res = await chat_mod._execute_tool(
        1,
        "create_condition_rule",
        {
            "action": "create",
            "type": "metric",
            "metric": "ram_pct",
            "op": ">",
            "value": 90,
            "message": "RAM alta",
            "repeats": True,
        },
    )
    assert res["success"] and guardados and guardados[0][3] is True
    res = await chat_mod._execute_tool(
        1, "create_condition_rule", {"action": "create", "type": "weather", "keywords": ["lluvia"]}
    )
    assert res["success"] and len(guardados) == 2
    res = await chat_mod._execute_tool(
        1, "create_condition_rule", {"action": "create", "type": "metric"}
    )
    assert res["success"] is False
    res = await chat_mod._execute_tool(1, "create_condition_rule", {"action": "list"})
    assert res["success"] and "Reglas" in res["message"]
    res = await chat_mod._execute_tool(
        1, "create_condition_rule", {"action": "delete", "rule_id": 5}
    )
    assert res["success"]
    res = await chat_mod._execute_tool(
        1, "create_condition_rule", {"action": "delete", "rule_id": 99}
    )
    assert res["success"] is False
