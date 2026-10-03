"""Citas numeradas [S1] con enlace para el segundo cerebro (mejora 1).

Cada fragmento devuelto por `search_second_brain` recibe un identificador
único por turno de conversación (S1, S2, ...). El modelo debe terminar cada
afirmación documental con su cita; al final, `finalize_citations` valida las
citas, elimina las inválidas y añade la sección «Fuentes» con el enlace
obsidian:// de cada nota (en voz se quitan las marcas: VOICE_RULE prohíbe
URLs y tecnicismos).

El registro es por turno (se resetea en `_prepare_tool_phase`), no por chat:
así varias búsquedas en el mismo turno numeran S1, S2, ... sin colisionar.
"""

import re
from typing import Any

_CITE_RE = re.compile(r"\[S(\d+)\]")


def strip_citation_marks(text: str) -> str:
    """Quita las marcas de cita (para voz: VOICE_RULE prohíbe tecnicismos).

    También limpia el hueco que deja la marca antes de un signo de puntuación
    ("Babel [S1]." -> "Babel.").
    """
    if not text:
        return text
    out = _CITE_RE.sub("", text)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\s+([.!?,;:]+)(?=\s|$)", r"\1", out)
    return out


class CitationRegistry:
    """Fuentes numeradas del turno en curso (un registro global, reset por turno)."""

    def __init__(self) -> None:
        self._sources: list[dict[str, Any]] = []

    def reset(self) -> None:
        self._sources = []

    def add(self, note_path: str, heading: str = "", obsidian_uri: str = "") -> str:
        sid = "S%d" % (len(self._sources) + 1)
        self._sources.append(
            {
                "id": sid,
                "note_path": note_path,
                "heading": heading,
                "obsidian_uri": obsidian_uri,
            }
        )
        return sid

    def sources(self) -> list[dict[str, Any]]:
        return list(self._sources)

    def clear(self) -> None:
        self._sources = []


citations = CitationRegistry()


def _tidy(text: str) -> str:
    """Limpia huecos que dejan las marcas de cita al eliminarse."""
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([.!?,;:])", r"\1", text)
    return text.strip()


def finalize_citations(content: str, sources: list[dict[str, Any]], voice: bool = False) -> str:
    """Valida las citas [Sn] y añade la sección «Fuentes» (chat).

    - Citas que no corresponden a ninguna fuente recuperada: se eliminan.
    - Sin citas pero con fuentes recuperadas: se lista igualmente (la
      respuesta está fundamentada aunque el modelo no cite).
    - Voz: se eliminan las marcas; nunca se añaden enlaces.
    """
    if not content:
        return content
    if voice:
        return _tidy(_CITE_RE.sub("", content))

    by_id = {str(s.get("id")): s for s in sources}
    valid: list[str] = []
    for match in _CITE_RE.finditer(content):
        sid = "S%s" % match.group(1)
        if sid in by_id and sid not in valid:
            valid.append(sid)

    def _keep(match: re.Match) -> str:
        return match.group(0) if ("S%s" % match.group(1)) in by_id else ""

    cleaned = _tidy(_CITE_RE.sub(_keep, content))
    chosen = valid or [str(s.get("id")) for s in sources if s.get("id")]
    if not chosen:
        return cleaned

    lines = ["", "", "Fuentes:"]
    for sid in chosen:
        source = by_id.get(sid)
        if not source:
            continue
        uri = str(source.get("obsidian_uri") or "")
        link = " [abrir](%s)" % uri if uri else ""
        heading = str(source.get("heading") or "")
        extra = " — %s" % heading if heading else ""
        lines.append("- [%s] %s%s%s" % (sid, source.get("note_path", "?"), extra, link))
    return cleaned + "\n".join(lines)
