"""Cobertura de handlers/finance.py (gasto, ingreso, finanzas, exportar)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Bot, Chat, Message, Update, User

from src.config import settings
from src.handlers import finance


class FakeDB:
    """Reemplaza src.database.db (la BD no esta inicializada en tests)."""

    def __init__(self, records=None, summary=None):
        self.records = records or []
        self.summary = summary or {
            "transaction_count": 0,
            "total_income": 0.0,
            "total_expenses": 0.0,
            "balance": 0.0,
            "expense_by_category": {},
            "income_by_category": {},
        }
        self.added = []
        self.exports_created = []
        self.exports_updated = []
        self.summary_calls = []
        self.records_calls = []

    async def add_finance_record(self, **kwargs):
        self.added.append(kwargs)
        return 42

    async def get_finance_summary(self, chat_id, start_date=None, end_date=None):
        self.summary_calls.append((chat_id, start_date, end_date))
        return self.summary

    async def get_finance_records(self, chat_id, start_date=None, end_date=None):
        self.records_calls.append((chat_id, start_date, end_date))
        return self.records

    async def create_export(self, chat_id, export_type):
        self.exports_created.append((chat_id, export_type))
        return 7

    async def update_export(self, export_id, status, file_path=None, error_message=None):
        self.exports_updated.append((export_id, status, file_path, error_message))


def make_update(text, user_id=12345):
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()
    bot.send_chat_action = AsyncMock()
    user = User(id=user_id, first_name="Test", is_bot=False, username="testuser")
    chat = Chat(id=user_id, type="private")
    message = Message(message_id=1, date=None, chat=chat, from_user=user, text=text)
    message._bot = bot
    return Update(update_id=1, message=message), bot


def make_context(args=None):
    context = MagicMock()
    context.args = args or []
    return context


def install_db(monkeypatch, **kwargs):
    fake_db = FakeDB(**kwargs)
    monkeypatch.setattr(finance, "db", fake_db)
    return fake_db


def sent_text(bot):
    return bot.send_message.call_args.kwargs["text"]


# ---------------------------------------------------------------------------
# gasto_command
# ---------------------------------------------------------------------------


async def test_gasto_without_message_is_noop(monkeypatch):
    fake_db = install_db(monkeypatch)
    update = MagicMock()
    update.effective_message = None
    update.effective_user = User(id=1, first_name="T", is_bot=False)

    await finance.gasto_command(update, make_context(["10", "comida"]))

    assert fake_db.added == []


async def test_gasto_without_args_shows_usage(monkeypatch):
    fake_db = install_db(monkeypatch)
    update, bot = make_update("/gasto")

    await finance.gasto_command(update, make_context([]))

    assert fake_db.added == []
    assert "Usa: /gasto" in sent_text(bot)


async def test_gasto_single_arg_shows_usage(monkeypatch):
    fake_db = install_db(monkeypatch)
    update, bot = make_update("/gasto 15")

    await finance.gasto_command(update, make_context(["15"]))

    assert fake_db.added == []
    assert "Usa: /gasto" in sent_text(bot)


@pytest.mark.parametrize("bad", ["abc", "0", "-5", "1.2.3"])
async def test_gasto_invalid_amount_rejected(monkeypatch, bad):
    fake_db = install_db(monkeypatch)
    update, bot = make_update("/gasto %s comida" % bad)

    await finance.gasto_command(update, make_context([bad, "comida"]))

    assert fake_db.added == []
    assert "número positivo" in sent_text(bot)


async def test_gasto_registers_expense_with_description(monkeypatch):
    fake_db = install_db(monkeypatch)
    monkeypatch.setattr(settings, "default_currency", "EUR")
    update, bot = make_update("/gasto 150.50 despensa Compras del supermercado")

    await finance.gasto_command(
        update, make_context(["150.50", "despensa", "Compras", "del", "supermercado"])
    )

    assert fake_db.added == [
        {
            "chat_id": 12345,
            "amount": 150.5,
            "category": "expense",
            "subcategory": "despensa",
            "description": "Compras del supermercado",
            "currency": "EUR",
        }
    ]
    text = sent_text(bot)
    assert "Gasto registrado" in text
    assert "despensa" in text
    assert "Compras del supermercado" in text
    assert "ID: 42" in text


async def test_gasto_accepts_comma_decimal_and_no_description(monkeypatch):
    fake_db = install_db(monkeypatch)
    update, bot = make_update("/gasto 12,75 transporte")

    await finance.gasto_command(update, make_context(["12,75", "transporte"]))

    assert fake_db.added[0]["amount"] == 12.75
    assert fake_db.added[0]["description"] is None
    assert "Descripción" not in sent_text(bot)


# ---------------------------------------------------------------------------
# ingreso_command
# ---------------------------------------------------------------------------


async def test_ingreso_without_args_shows_usage(monkeypatch):
    fake_db = install_db(monkeypatch)
    update, bot = make_update("/ingreso")

    await finance.ingreso_command(update, make_context([]))

    assert fake_db.added == []
    assert "Usa: /ingreso" in sent_text(bot)


@pytest.mark.parametrize("bad", ["xyz", "0", "-1"])
async def test_ingreso_invalid_amount_rejected(monkeypatch, bad):
    fake_db = install_db(monkeypatch)
    update, bot = make_update("/ingreso %s nomina" % bad)

    await finance.ingreso_command(update, make_context([bad, "nomina"]))

    assert fake_db.added == []
    assert "número positivo" in sent_text(bot)


async def test_ingreso_registers_income_with_description(monkeypatch):
    fake_db = install_db(monkeypatch)
    update, bot = make_update("/ingreso 15000 nomina Sueldo mensual")

    await finance.ingreso_command(update, make_context(["15000", "nomina", "Sueldo", "mensual"]))

    assert fake_db.added == [
        {
            "chat_id": 12345,
            "amount": 15000.0,
            "category": "income",
            "subcategory": "nomina",
            "description": "Sueldo mensual",
            "currency": settings.default_currency,
        }
    ]
    text = sent_text(bot)
    assert "Ingreso registrado" in text
    assert "nomina" in text
    assert "ID: 42" in text


async def test_ingreso_without_message_is_noop(monkeypatch):
    fake_db = install_db(monkeypatch)
    update = MagicMock()
    update.effective_message = None
    update.effective_user = User(id=1, first_name="T", is_bot=False)

    await finance.ingreso_command(update, make_context(["1", "nomina"]))

    assert fake_db.added == []


# ---------------------------------------------------------------------------
# finanzas_command
# ---------------------------------------------------------------------------


async def test_finanzas_empty_month(monkeypatch):
    install_db(monkeypatch)
    update, bot = make_update("/finanzas")

    await finance.finanzas_command(update, make_context([]))

    text = sent_text(bot)
    assert "No hay registros financieros" in text


async def test_finanzas_summary_with_categories(monkeypatch):
    install_db(
        monkeypatch,
        summary={
            "transaction_count": 3,
            "total_income": 1500.0,
            "total_expenses": 300.5,
            "balance": 1199.5,
            "expense_by_category": {"comida": 200.0, "transporte": 100.5},
            "income_by_category": {"nomina": 1500.0},
        },
    )
    update, bot = make_update("/finanzas")

    await finance.finanzas_command(update, make_context([]))

    text = sent_text(bot)
    assert "Resumen Financiero" in text
    assert "1,500.00" in text
    assert "300.50" in text
    assert "1,199.50" in text
    assert "comida" in text
    assert "transporte" in text
    assert "nomina" in text
    assert "Total de transacciones: 3" in text
    assert bot.send_message.call_args.kwargs["parse_mode"] == "Markdown"
    # Las categorias van ordenadas de mayor a menor gasto
    assert text.index("comida") < text.index("transporte")


async def test_finanzas_without_user_is_noop():
    update = MagicMock()
    update.effective_message = MagicMock()
    update.effective_user = None

    await finance.finanzas_command(update, make_context([]))

    update.effective_message.reply_text.assert_not_called()


# ---------------------------------------------------------------------------
# exportar_command / _export_finances
# ---------------------------------------------------------------------------


async def test_exportar_unknown_type_shows_help(monkeypatch):
    install_db(monkeypatch)
    update, bot = make_update("/exportar pdf")

    await finance.exportar_command(update, make_context(["pdf"]))

    assert "Tipos de exportación" in sent_text(bot)


async def test_exportar_without_message_is_noop():
    update = MagicMock()
    update.effective_message = None
    update.effective_user = User(id=1, first_name="T", is_bot=False)

    await finance.exportar_command(update, make_context([]))
    # El guard "sin mensaje" sale sin tocar la API de Telegram


async def test_exportar_dispatches_full_history(monkeypatch):
    install_db(monkeypatch)
    captured = {}

    async def fake_export(update, chat_id, full_history=False):
        captured["chat_id"] = chat_id
        captured["full_history"] = full_history

    monkeypatch.setattr(finance, "_export_finances", fake_export)
    update, _bot = make_update("/exportar finanzas_completo")

    await finance.exportar_command(update, make_context(["finanzas_completo"]))

    assert captured == {"chat_id": 12345, "full_history": True}


async def test_exportar_default_is_monthly(monkeypatch):
    install_db(monkeypatch)
    captured = {}

    async def fake_export(update, chat_id, full_history=False):
        captured["full_history"] = full_history

    monkeypatch.setattr(finance, "_export_finances", fake_export)
    update, _bot = make_update("/exportar")

    await finance.exportar_command(update, make_context([]))

    assert captured["full_history"] is False


def _finance_records():
    return [
        {
            "id": 1,
            "amount": 10.5,
            "category": "expense",
            "subcategory": "comida",
            "description": "bocadillo",
            "currency": "EUR",
            "recorded_at": "2026-09-01 10:00:00",
        },
        {
            "id": 2,
            "amount": 1000.0,
            "category": "income",
            "subcategory": "nomina",
            "description": "sueldo",
            "currency": "EUR",
            "recorded_at": "2026-09-02 10:00:00",
        },
    ]


async def test_export_finances_writes_excel(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "obsidian_vault_dir", str(tmp_path))
    fake_db = install_db(
        monkeypatch,
        records=_finance_records(),
        summary={
            "transaction_count": 2,
            "total_income": 1000.0,
            "total_expenses": 10.5,
            "balance": 989.5,
            "expense_by_category": {"comida": 10.5},
            "income_by_category": {"nomina": 1000.0},
        },
    )
    update, bot = make_update("/exportar finanzas")

    await finance._export_finances(update, 12345, full_history=False)

    assert fake_db.exports_created == [(12345, "finanzas")]
    status = fake_db.exports_updated[-1]
    assert status[1] == "completed"
    exported = status[2]
    assert exported.endswith(".xlsx")
    text = sent_text(bot)
    assert "Exportación completada" in text
    assert "Registros: 2" in text
    # Registros del mes: rango con primer dia del mes
    chat_id, start, end = fake_db.records_calls[0]
    assert chat_id == 12345
    assert start.endswith("00:00:00")
    assert end.endswith("23:59:59")


async def test_export_finances_full_history_omits_range(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "obsidian_vault_dir", str(tmp_path))
    fake_db = install_db(monkeypatch, records=_finance_records())
    update, _bot = make_update("/exportar finanzas_completo")

    await finance._export_finances(update, 12345, full_history=True)

    assert fake_db.records_calls == [(12345, None, None)]
    assert fake_db.exports_updated[-1][1] == "completed"


async def test_export_finances_without_records_fails_export(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "obsidian_vault_dir", str(tmp_path))
    fake_db = install_db(monkeypatch, records=[])
    update, bot = make_update("/exportar finanzas")

    await finance._export_finances(update, 12345)

    assert fake_db.exports_updated[-1][1] == "failed"
    assert "No hay registros" in sent_text(bot)


async def test_export_finances_reports_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "obsidian_vault_dir", str(tmp_path))
    fake_db = install_db(monkeypatch, records=_finance_records())

    async def boom_summary(chat_id, start_date=None, end_date=None):
        raise RuntimeError("resumen roto")

    monkeypatch.setattr(fake_db, "get_finance_summary", boom_summary)
    update, bot = make_update("/exportar finanzas")

    await finance._export_finances(update, 12345)

    assert fake_db.exports_updated[-1][1] == "failed"
    assert "Error al exportar" in sent_text(bot)


async def test_export_finances_without_message_is_noop(monkeypatch):
    fake_db = install_db(monkeypatch)
    update = MagicMock()
    update.effective_message = None

    await finance._export_finances(update, 12345)

    assert fake_db.exports_created == []
