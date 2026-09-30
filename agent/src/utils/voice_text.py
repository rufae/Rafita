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

# Unidades: que no se lea "veinticuatro h" ni "por ciento" mal.
_UNIT_REPLACEMENTS = [
    (re.compile(r"(\d[\d.,]*)\s*°\s*C\b"), r"\1 grados"),
    (re.compile(r"(\d[\d.,]*)\s*%"), r"\1 por ciento"),
    (re.compile(r"(\d[\d.,]*)\s*€"), r"\1 euros"),
    (re.compile(r"(\d[\d.,]*)\s*km\b", re.IGNORECASE), r"\1 kilómetros"),
    (re.compile(r"(\d[\d.,]*)\s*h\b"), r"\1 horas"),
    (re.compile(r"(\d[\d.,]*)\s*min\b", re.IGNORECASE), r"\1 minutos"),
    (re.compile(r"(\d[\d.,]*)\s*GB\b"), r"\1 gigabytes"),
    (re.compile(r"(\d[\d.,]*)\s*MB\b"), r"\1 megabytes"),
]
# Restos de Markdown que quedan sueltos (asterisco/almohadilla...).
_STRAY_MD_RE = re.compile(r"[*_#`~|]+")
# Simbolos al inicio (emojis sobrantes, comillas, guiones): causan balbuceo.
# Se conservan los signos de apertura espanoles (¿ ¡).
_LEADING_SYMBOLS_RE = re.compile(r"^[^\wÁÉÍÓÚÜÑáéíóúüñ¿¡]+")
# Palabras inglesas que Piper pronuncia raro en espanol: se sustituyen al hablar.
_LOANWORD_REPLACEMENTS = [
    (re.compile(r"\bbriefings\b", re.IGNORECASE), "resúmenes"),
    (re.compile(r"\bbriefing\b", re.IGNORECASE), "resumen"),
]


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
    for pattern, replacement in _UNIT_REPLACEMENTS:
        out = pattern.sub(replacement, out)
    for pattern, replacement in _LOANWORD_REPLACEMENTS:
        out = pattern.sub(replacement, out)
    out = _STRAY_MD_RE.sub(" ", out)
    out = _MULTISPACE_RE.sub(" ", out)
    out = _MULTINEWLINE_RE.sub(" ", out)
    out = out.strip()
    out = _LEADING_SYMBOLS_RE.sub("", out)
    return out.strip()


_WORD_RE = re.compile(r"[\w\u00e1\u00e9\u00ed\u00f3\u00fa\u00fc\u00f1]+")


def _collapse_word_loop(words: list[str]) -> list[str]:
    """Colapsa repeticiones consecutivas de 1-4 palabras ('la vida, la vida')."""
    out: list[str] = []
    i = 0
    while i < len(words):
        matched = False
        for n in (1, 2, 3, 4):
            if i + 2 * n <= len(words) and words[i : i + n] == words[i + n : i + 2 * n]:
                j = i + n
                while j + n <= len(words) and words[j : j + n] == words[i : i + n]:
                    j += n
                out.extend(words[i : i + n])
                i = j
                matched = True
                break
        if not matched:
            out.append(words[i])
            i += 1
    return out


_DICTADO_ARROBA_RE = re.compile(r"\s+(?:arroba|at)\s+", re.IGNORECASE)
_DICTADO_PUNTO_RE = re.compile(r"\s+(?:punto|dot)\s+", re.IGNORECASE)
_DICTADO_GUION_BAJO_RE = re.compile(r"\s+guion\s+bajo\s+", re.IGNORECASE)
_DICTADO_GUION_RE = re.compile(r"\s+guion\s+", re.IGNORECASE)


def normalize_dictated_email(text: str | None) -> str | None:
    """Convierte un correo dictado a su forma real (bug 2026-09-30).

    El STT transcribia 'anabel arroba gmail punto com' tal cual (o perdia la
    arroba: 'anabel.84.amg.gmail.com') y send_gmail fallaba. Solo se aplica
    cuando aparece 'arroba' (senal clara de dictado), para no tocar texto
    normal.
    """
    if not text:
        return text
    bajo = " %s " % text.lower()
    if " arroba " not in bajo and " at " not in bajo:
        return text
    resultado = " %s " % text
    resultado = _DICTADO_ARROBA_RE.sub("@", resultado)
    resultado = _DICTADO_GUION_BAJO_RE.sub("_", resultado)
    resultado = _DICTADO_GUION_RE.sub("-", resultado)
    resultado = _DICTADO_PUNTO_RE.sub(".", resultado)
    # Espacios alrededor de @ y . SOLO entre caracteres de palabra (para no
    # tocar la puntuacion normal de la frase).
    resultado = re.sub(r"(\w)\s*@\s*(\w)", r"\1@\2", resultado)
    resultado = re.sub(r"(\w)\s*\.\s*(\w)", r"\1.\2", resultado)
    return " ".join(resultado.split()).strip()


def clean_stt_transcript(text: str | None) -> str | None:
    """Limpia la transcripcion del STT y rechaza alucinaciones por bucle.

    Whisper a veces repite una frase en bucle sobre ruido de fondo
    ('la vida, la vida, la vida...'); eso no es habla del usuario.
    Devuelve None si el texto es una alucinacion degenerada.
    """
    if not text or not text.strip():
        return None
    words = _WORD_RE.findall(text.lower())
    if not words:
        return None
    collapsed = _collapse_word_loop(words)
    # Alucinacion: el bucle se ha comido casi todo el texto original.
    if len(text) > 40 and len(collapsed) < len(words) * 0.5:
        return None
    if len(collapsed) == len(words):
        return text.strip()
    return " ".join(collapsed)
