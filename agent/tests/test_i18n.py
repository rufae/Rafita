"""Tests for language/timezone/currency helpers (task 2.2)."""

from src import i18n
from src.config import settings


def test_language_rule_spanish_default(monkeypatch):
    monkeypatch.setattr(settings, "language", "es")
    assert "español" in i18n.language_rule()
    assert i18n.language_name() == "español"
    assert i18n.reply_instruction() == "Responde siempre en español."


def test_language_rule_english(monkeypatch):
    monkeypatch.setattr(settings, "language", "en")
    assert "EXCLUSIVELY English" in i18n.language_rule()
    assert i18n.reply_instruction() == "Always answer in English."
    assert "English" in i18n.stt_prompt()


def test_currency_symbol(monkeypatch):
    monkeypatch.setattr(settings, "default_currency", "EUR")
    assert i18n.currency_symbol() == "€"
    monkeypatch.setattr(settings, "default_currency", "MXN")
    assert i18n.currency_symbol() == "$"
    assert i18n.currency_symbol("GBP") == "£"
    assert i18n.currency_symbol("XYZ") == "XYZ"


def test_timezone_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "timezone", "Europe/Madrid")
    assert str(i18n.timezone()) == "Europe/Madrid"
