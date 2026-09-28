"""Cobertura de src/models/schemas.py: modelos pydantic y registro de comandos."""

from datetime import datetime

import pytest

from src.config import settings
from src.models.schemas import (
    COMMANDS_REGISTRY,
    Alert,
    BotCommand,
    ChatMessage,
    ConversationContext,
    Event,
    ExportRequest,
    FinanceCategory,
    FinanceRecord,
    FinanceSummary,
    MessageRole,
)


def test_message_role_values():
    assert MessageRole.user.value == "user"
    assert MessageRole.assistant.value == "assistant"
    assert MessageRole.system.value == "system"


def test_chat_message_from_attributes():
    msg = ChatMessage(chat_id=1, role=MessageRole.user, content="hola")
    assert msg.id is None
    assert msg.created_at is None
    assert msg.model_config["from_attributes"] is True


def test_conversation_add_message_and_trim():
    ctx = ConversationContext(chat_id=7, max_history=3)
    for i in range(5):
        ctx.add_message(MessageRole.user, "m%d" % i)
    assert len(ctx.messages) == 3
    assert [m.content for m in ctx.messages] == ["m2", "m3", "m4"]
    assert all(m.chat_id == 7 for m in ctx.messages)
    assert all(isinstance(m.created_at, datetime) for m in ctx.messages)


def test_conversation_message_conversion_starts_with_system():
    ctx = ConversationContext(chat_id=7, system_prompt="sys prompt")
    ctx.add_message(MessageRole.user, "hola")
    ctx.add_message(MessageRole.assistant, "que tal")
    result = ctx.to_ollama_messages()
    assert result[0] == {"role": "system", "content": "sys prompt"}
    assert result[1] == {"role": "user", "content": "hola"}
    assert result[2] == {"role": "assistant", "content": "que tal"}


def test_conversation_system_prompt_mentions_assistant_name():
    ctx = ConversationContext(chat_id=1)
    assert settings.assistant_name in ctx.system_prompt
    assert ctx.max_history == 50


def test_event_model_defaults():
    ev = Event(chat_id=1, title="Reunion", event_datetime=datetime(2030, 1, 1, 10, 0))
    assert ev.is_active is True
    assert ev.description is None
    assert ev.id is None


def test_alert_model_defaults():
    alert = Alert(chat_id=1, message="aviso")
    assert alert.alert_type == "info"
    assert alert.is_read is False
    assert alert.expires_at is None


def test_finance_record_currency_default_and_categories():
    rec = FinanceRecord(chat_id=1, amount=10.5, category=FinanceCategory.expense)
    assert rec.currency == settings.default_currency
    assert FinanceCategory.income.value == "income"
    assert FinanceCategory.transfer.value == "transfer"
    assert FinanceCategory.investment.value == "investment"


def test_finance_record_accepts_optional_fields():
    rec = FinanceRecord(
        chat_id=1,
        amount=1.0,
        category=FinanceCategory.income,
        subcategory="salario",
        description="nómina",
        currency="EUR",
    )
    assert rec.subcategory == "salario"
    assert rec.currency == "EUR"


def test_finance_summary_defaults():
    summary = FinanceSummary()
    assert summary.total_income == 0.0
    assert summary.total_expenses == 0.0
    assert summary.balance == 0.0
    assert summary.expense_by_category == {}
    assert summary.income_by_category == {}
    assert summary.transaction_count == 0
    assert summary.period_start is None
    assert summary.period_end is None


def test_export_request_defaults():
    req = ExportRequest(chat_id=1, export_type="excel")
    assert req.status == "pending"
    assert req.file_path is None
    assert req.error_message is None


def test_bot_command_admin_only_default():
    cmd = BotCommand(command="x", description="y")
    assert cmd.admin_only is False


def test_commands_registry_contains_core_commands():
    names = [c.command for c in COMMANDS_REGISTRY]
    for expected in ("start", "chat", "evento", "gasto", "backup", "status"):
        assert expected in names
    admin = {c.command for c in COMMANDS_REGISTRY if c.admin_only}
    assert {"logs", "calendario"} <= admin
    assert "start" not in admin
    assert all(isinstance(c, BotCommand) for c in COMMANDS_REGISTRY)


def test_finance_category_rejects_unknown_value():
    with pytest.raises(ValueError):
        FinanceRecord(chat_id=1, amount=1.0, category="invented")
