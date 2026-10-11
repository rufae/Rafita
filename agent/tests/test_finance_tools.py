"""Tools financieras nuevas: save_income, list, delete y summary por mes."""

import asyncio

from src.handlers import chat as chat_mod
from src.handlers.chat import _execute_tool


def _run(coro):
    return asyncio.run(coro)


def _afn(valor):
    async def _f(*a, **k):
        return valor

    return _f()


async def test_save_income_registra(monkeypatch):
    from src.services import finance_service

    capturas = {}

    async def _reg(**kw):
        capturas.update(kw)
        return 55

    monkeypatch.setattr(finance_service, "registrar_movimiento", _reg)
    res = await _execute_tool(1, "save_income", {"amount": 1500, "fuente": "nomina"})
    assert res["success"] is True
    assert "1500" in res["message"] or "1,500" in res["message"]
    assert capturas["category"] == "income"
    assert capturas["subcategory"] == "nomina"


async def test_save_income_importe_invalido():
    res = await _execute_tool(1, "save_income", {"amount": -3, "fuente": "x"})
    assert res["success"] is False


async def test_delete_finance_record_pide_confirm(monkeypatch):
    llamado = {}

    async def _del(chat_id, record_id):
        llamado["id"] = record_id
        return 1

    monkeypatch.setattr(chat_mod.db, "delete_finance_record", _del)
    res = await _execute_tool(1, "delete_finance_record", {"record_id": 7, "confirm": False})
    assert res["success"] is False
    assert "confirm=true" in res["message"]
    assert llamado == {}

    res2 = await _execute_tool(1, "delete_finance_record", {"record_id": 7, "confirm": True})
    assert res2["success"] is True
    assert llamado["id"] == 7


async def test_list_finance_records_tabla(monkeypatch):
    registros = [
        {
            "id": 3,
            "recorded_at": "2026-10-01 09:00:00",
            "description": "Mercadona",
            "category": "expense",
            "amount": 45.2,
        },
        {
            "id": 4,
            "recorded_at": "2026-10-02 10:00:00",
            "description": None,
            "subcategory": "nomina",
            "category": "income",
            "amount": 1500.0,
        },
    ]
    capturas = {}

    async def _records(chat_id, start_date=None, end_date=None, category=None, limit=100):
        capturas.update(start=start_date, end=end_date, category=category, limit=limit)
        return registros

    monkeypatch.setattr(chat_mod.db, "get_finance_records", _records)
    res = await _execute_tool(
        1, "list_finance_records", {"mes": "2026-10", "tipo": "todos", "limite": 5}
    )
    assert res["success"] is True
    assert "Mercadona" in res["message"]
    assert capturas["start"].startswith("2026-10-01")
    assert capturas["end"].startswith("2026-10-31")
    assert capturas["limit"] == 5


async def test_list_finance_records_mes_invalido():
    res = await _execute_tool(1, "list_finance_records", {"mes": "septiembre"})
    assert res["success"] is False


async def test_finance_summary_este_mes_y_mes_pasado(monkeypatch):
    capturas = {}

    async def _summary(chat_id, start_date=None, end_date=None):
        capturas["start"] = start_date
        capturas["end"] = end_date
        return {
            "transaction_count": 1,
            "total_income": 100.0,
            "total_expenses": 40.0,
            "balance": 60.0,
            "expense_by_category": {"alimentacion": 40.0},
            "income_by_category": {},
        }

    monkeypatch.setattr(chat_mod.db, "get_finance_summary", _summary)
    res = await _execute_tool(1, "get_finance_summary", {})
    assert res["success"] is True
    assert capturas["start"].endswith("-01 00:00:00")

    res2 = await _execute_tool(1, "get_finance_summary", {"mes": "2026-08"})
    assert res2["success"] is True
    assert capturas["start"].startswith("2026-08-01")
    assert capturas["end"].startswith("2026-08-31")

    res3 = await _execute_tool(1, "get_finance_summary", {"mes": "13-08"})
    assert res3["success"] is False


async def test_save_expense_usa_servicio(monkeypatch):
    from src.services import finance_service

    capturas = {}

    async def _reg(**kw):
        capturas.update(kw)
        return 11

    monkeypatch.setattr(finance_service, "registrar_movimiento", _reg)
    res = await _execute_tool(1, "save_expense", {"amount": "12.5", "category": "transporte"})
    assert res["success"] is True
    assert capturas["amount"] == 12.5
    assert capturas["category"] == "expense"
