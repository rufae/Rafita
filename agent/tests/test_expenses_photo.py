"""Mejora 3: gastos por foto con confirmacion por botones (2026-09-28)."""

import pathlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_files_coverage import (
    FakeMessage,
    install_db,
    install_vision_deps,
    make_update,
)

from src.handlers import files


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Desvia /data/obsidian_vault y los temporales hacia tmp_path."""
    root = tmp_path / "obsidian_vault"
    root.mkdir()
    real_path = pathlib.Path

    def remapped(*args, **kwargs):
        if args and str(args[0]) == "/data/obsidian_vault":
            return root
        return real_path(*args, **kwargs)

    monkeypatch.setattr(files, "Path", remapped)
    monkeypatch.setattr(files, "VAULT_ROOT", root)
    monkeypatch.setattr(files, "TEMP_DIR", tmp_path / "uploads")
    monkeypatch.setattr(files, "CREDENTIALS_DIR", tmp_path / "credentials")
    return root


# ---------- parser de la linea GASTO ----------


def test_parse_expense_valid_line():
    text = 'Ticket de Mercadona\nGASTO: {"importe": 8.88, "comercio": "MERCADONA", "fecha": "28/09/2026", "categoria": "alimentacion"}'
    expense = files._parse_expense_from_text(text)
    assert expense == {
        "amount": 8.88,
        "comercio": "MERCADONA",
        "fecha": "28/09/2026",
        "categoria": "alimentacion",
    }


def test_parse_expense_comma_and_euro():
    text = 'GASTO: {"importe": "8,88 €", "comercio": "Bar", "fecha": "", "categoria": ""}'
    expense = files._parse_expense_from_text(text)
    assert expense["amount"] == 8.88
    assert expense["categoria"] == "otros"
    assert expense["comercio"] == "Bar"


def test_parse_expense_without_line_returns_none():
    assert files._parse_expense_from_text("Solo una foto de un gato") is None
    assert files._parse_expense_from_text(None) is None


def test_parse_expense_bad_json_returns_none():
    assert files._parse_expense_from_text("GASTO: {importe: roto}") is None


def test_parse_expense_missing_or_zero_amount_returns_none():
    assert files._parse_expense_from_text('GASTO: {"comercio": "X"}') is None
    assert files._parse_expense_from_text('GASTO: {"importe": 0}') is None


# ---------- botones de confirmacion ----------


def _callback_update(data: str):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock()
    query.message.chat_id = 12345
    update = MagicMock()
    update.callback_query = query
    return update, query


async def test_expense_callback_registers(monkeypatch):
    executed = []

    async def fake_execute_tool(chat_id, name, args):
        executed.append((chat_id, name, args))
        return {"success": True, "message": "ok"}

    monkeypatch.setattr("src.handlers.chat._execute_tool", fake_execute_tool)
    update, query = _callback_update("gasto_ok")
    context = MagicMock()
    context.user_data = {
        "pending_expense": {
            "amount": 8.88,
            "comercio": "MERCADONA",
            "fecha": "28/09/2026",
            "categoria": "alimentacion",
        }
    }

    await files.expense_callback(update, context)

    assert executed and executed[0][1] == "save_expense"
    assert executed[0][2]["amount"] == 8.88
    assert "Gasto registrado" in query.edit_message_text.call_args[0][0]
    assert "pending_expense" not in context.user_data


async def test_expense_callback_discards(monkeypatch):
    update, query = _callback_update("gasto_no")
    context = MagicMock()
    context.user_data = {
        "pending_expense": {"amount": 5, "comercio": "X", "fecha": "", "categoria": "otros"}
    }

    await files.expense_callback(update, context)

    assert "descartado" in query.edit_message_text.call_args[0][0].lower()
    assert "pending_expense" not in context.user_data


async def test_expense_callback_without_pending():
    update, query = _callback_update("gasto_ok")
    context = MagicMock()
    context.user_data = {}

    await files.expense_callback(update, context)

    assert "ya no está pendiente" in query.edit_message_text.call_args[0][0]


# ---------- flujo de vision con ticket ----------


async def test_vision_ticket_asks_confirmation(vault, monkeypatch, tmp_path):
    fake_llm = install_vision_deps(monkeypatch)
    fake_llm.vision_result = (
        "MERCADONA\nTOTAL: 8,88 EUR\n"
        'GASTO: {"importe": 8.88, "comercio": "MERCADONA", "fecha": "28/09/2026", "categoria": "alimentacion"}'
    )
    install_db(monkeypatch)
    image = tmp_path / "ticket.png"
    image.write_bytes(b"png")
    message = FakeMessage()
    context = MagicMock()
    context.user_data = {}

    await files._process_vision_image(
        make_update(message), 12345, str(image), "apunta este ticket", context
    )

    assert any("He detectado un gasto" in r for r in message.replies)
    assert context.user_data["pending_expense"]["amount"] == 8.88
    # No pasa por el procesado generico (evita doble registro)
    assert not any("Procesando con Qwen" in r for r in message.replies)


async def test_vision_same_model_not_unloaded(vault, monkeypatch, tmp_path):
    """Con gemma4 como vision y chat, no se descarga el modelo tras la imagen."""
    fake_llm = install_vision_deps(monkeypatch)
    fake_llm.model = "gemma4:12b"
    fake_llm.vision_model = "gemma4:12b"
    fake_llm.vision_result = "Una foto de un gato"
    install_db(monkeypatch)

    async def fake_execute_tool(chat_id, name, args):
        return {"message": "ok"}

    monkeypatch.setattr("src.handlers.chat._execute_tool", fake_execute_tool)
    image = tmp_path / "gato.png"
    image.write_bytes(b"png")
    message = FakeMessage()
    context = MagicMock()
    context.user_data = {}

    await files._process_vision_image(make_update(message), 12345, str(image), "mira", context)

    assert fake_llm.unloaded == []
    assert any("Analizando imagen con gemma4:12b" in r for r in message.replies)
