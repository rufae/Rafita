"""Language / timezone / currency helpers (task 2.2).

Single source of truth: `settings.language`, `settings.timezone`,
`settings.default_currency` and `settings.assistant_name`. Prompts and
formatting must use these helpers instead of hardcoded Spanish/EUR strings.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from src.config import settings

CURRENCY_SYMBOLS: dict[str, str] = {
    "EUR": "€",
    "MXN": "$",
    "USD": "$",
    "GBP": "£",
    "ARS": "$",
    "COP": "$",
    "CLP": "$",
    "PEN": "S/",
}

_LANGUAGE_NAMES: dict[str, str] = {"es": "español", "en": "English"}

_LANGUAGE_RULES: dict[str, str] = {
    "es": (
        "STRICT_LANGUAGE_RULE: Tu idioma es EXCLUSIVAMENTE el español. "
        "Queda prohibido el uso de caracteres chinos, japoneses o inglés.\n"
    ),
    "en": (
        "STRICT_LANGUAGE_RULE: Your language is EXCLUSIVELY English. "
        "Do not answer in any other language.\n"
    ),
}


def language_name() -> str:
    return _LANGUAGE_NAMES.get(settings.language, settings.language)


def language_rule() -> str:
    """Prompt rule that forces the reply language."""
    return _LANGUAGE_RULES.get(settings.language, _LANGUAGE_RULES["es"])


def reply_instruction() -> str:
    if settings.language == "en":
        return f"Always answer in {language_name()}."
    return f"Responde siempre en {language_name()}."


def currency_symbol(code: str | None = None) -> str:
    currency = (code or settings.default_currency).upper()
    return CURRENCY_SYMBOLS.get(currency, currency)


_STT_PROMPTS: dict[str, str] = {
    "es": "A continuacion, una conversacion en espanol.",
    "en": "The following is a conversation in English.",
}


def stt_prompt() -> str:
    return _STT_PROMPTS.get(settings.language, _STT_PROMPTS["es"])


def timezone() -> ZoneInfo:
    return ZoneInfo(settings.timezone)
