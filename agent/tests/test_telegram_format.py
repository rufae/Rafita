"""Formato de respuestas para Telegram: tablas Markdown en <pre> (2026-09-27)."""

from src.handlers.chat import format_telegram_response


def test_plain_text_without_table_unchanged():
    text = "Hola, esto es una respuesta normal con *negritas* y _cursivas_."
    formatted, mode = format_telegram_response(text)
    assert formatted == text
    assert mode is None


def test_markdown_table_wrapped_in_pre():
    text = "Tus datos:\n\n| Dia | Pasos |\n|---|---|\n| Lunes | 8200 |\n\nFin."
    formatted, mode = format_telegram_response(text)
    assert mode == "HTML"
    assert "<pre>" in formatted and "</pre>" in formatted
    assert "| Dia | Pasos |" in formatted
    assert "Fin." in formatted


def test_html_characters_are_escaped_with_table():
    text = "| Campo | Valor |\n|---|---|\n| a<b | 1&2 |"
    formatted, mode = format_telegram_response(text)
    assert mode == "HTML"
    assert "a&lt;b" in formatted
    assert "1&amp;2" in formatted
