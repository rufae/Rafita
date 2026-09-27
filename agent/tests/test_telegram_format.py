"""Formato de respuestas para Telegram: Markdown -> HTML (2026-09-27)."""

from src.handlers.chat import format_telegram_response


def test_plain_text_without_markdown_unchanged():
    text = "Hola, esto es una respuesta normal sin formato."
    formatted, mode = format_telegram_response(text)
    assert formatted == text
    assert mode is None


def test_bold_and_headings_become_html():
    text = "# Resumen\n\nEl ultimo correo es de **Google Calendar**."
    formatted, mode = format_telegram_response(text)
    assert mode == "HTML"
    assert "<b>Resumen</b>" in formatted
    assert "<b>Google Calendar</b>" in formatted
    assert "**" not in formatted
    assert "#" not in formatted


def test_bullets_code_and_links():
    text = "- primer punto\n- segundo punto\nUsa `comando` y [web](https://x.com)"
    formatted, mode = format_telegram_response(text)
    assert mode == "HTML"
    assert "• primer punto" in formatted
    assert "<code>comando</code>" in formatted
    assert '<a href="https://x.com">web</a>' in formatted


def test_markdown_table_wrapped_in_pre():
    text = "Tus datos:\n\n| Dia | Pasos |\n|---|---|\n| Lunes | 8200 |\n\nFin."
    formatted, mode = format_telegram_response(text)
    assert mode == "HTML"
    assert "<pre>" in formatted and "</pre>" in formatted
    assert "| Dia | Pasos |" in formatted
    assert "Fin." in formatted


def test_html_characters_are_escaped():
    text = "**a<b>** y 1&2"
    formatted, mode = format_telegram_response(text)
    assert mode == "HTML"
    assert "<b>a&lt;b&gt;</b>" in formatted
    assert "1&amp;2" in formatted
