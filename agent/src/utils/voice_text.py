"""Limpieza de texto para la conversacion de voz (2026-09-27).

El TTS no debe leer Markdown, tablas, emojis, URLs ni IDs, y hay que evitar
repeticiones degeneradas del modelo ("re re re", "si si si si") que suenan
como un tartamudeo.
"""

import re

_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_TABLE_RE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_MD_SEP_RE = re.compile(r"^\s*\|?[\s:|-]{3,}\|?\s*$", re.MULTILINE)
_EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001faff"
    "\U00002600-\U000027bf"
    "\U0001f000-\U0001f2ff"
    "\u2190-\u21ff\u2b00-\u2bff\ufe0f\u200d"
    "]+"
)
_BULLET_RE = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s+", re.MULTILINE)
_HEADING_RE = re.compile(r"^\s*#{1,6}\s*", re.MULTILINE)
_CODE_RE = re.compile(r"`{1,3}([^`]*)`{1,3}")
_MD_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1")
_MULTISPACE_RE = re.compile(r"[ \t]{2,}")
_MULTINEWLINE_RE = re.compile(r"\n{2,}")
# Repeticion degenerada del modelo: misma palabra 3+ veces seguidas.
_REPEAT_WORD_RE = re.compile(
    r"\b([\w\u00e1\u00e9\u00ed\u00f3\u00fa\u00fc\u00f1]+)(?:\s+\1\b){2,}", re.IGNORECASE
)
# Silaba corta pegada y repetida: "rerere", "lalala".
_REPEAT_SUBSTR_RE = re.compile(r"\b(\w{1,3})(?:\1){2,}\b", re.IGNORECASE)
# IDs/tecnicismos largos que no se deben leer.
_LONG_ID_RE = re.compile(r"\b[a-zA-Z0-9_-]{24,}\b")


def sanitize_for_tts(text: str) -> str:
    """Devuelve texto listo para sintetizar en voz."""
    if not text:
        return ""
    out = text
    out = _MD_LINK_RE.sub(r"\1", out)
    out = _URL_RE.sub("", out)
    out = _MD_TABLE_RE.sub("", out)
    out = _MD_SEP_RE.sub("", out)
    out = _CODE_RE.sub(r"\1", out)
    out = _HEADING_RE.sub("", out)
    out = _BULLET_RE.sub("", out)
    out = _EMOJI_RE.sub("", out)
    out = _MD_EMPHASIS_RE.sub(r"\2", out)
    out = _REPEAT_WORD_RE.sub(r"\1", out)
    out = _REPEAT_SUBSTR_RE.sub(r"\1", out)
    out = _LONG_ID_RE.sub("", out)
    out = _MULTISPACE_RE.sub(" ", out)
    out = _MULTINEWLINE_RE.sub(" ", out)
    return out.strip()
