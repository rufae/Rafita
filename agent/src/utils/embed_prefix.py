"""Prefijos de tarea para EmbeddingGemma 2 (y passthrough para el resto).

Google entreno embeddinggemma-2 con estos prefijos (tarjeta del modelo): sin
ellos la precision se desploma. A/B sobre el vault real del 2026-10-11:
hit@1 de 2/12 con bge-m3, 4/12 con eg2 sin prefijos y 11/12 con eg2 +
prefijos (MRR 0.34 -> 0.92).

Chroma no distingue la consulta del documento dentro de su embedding
function, así que quien embebe aplica el prefijo aqui y pasa los vectores a
Chroma explicitamente (add/upsert/query con embeddings=).

Solo se activan si EMBEDDING_MODEL contiene "embeddinggemma"; con cualquier
otro modelo (bge-m3, nomic...) los textos pasan tal cual, sin cambios.
"""

from src.config import settings

_QUERY_PREFIX = "task: search result | query: "
_DOC_PREFIX = "title: %s | text: "


def prefixes_active() -> bool:
    return "embeddinggemma" in settings.embedding_model.lower()


def embed_query_text(text: str) -> str:
    """Texto a embeber cuando se busca (rol consulta)."""
    return _QUERY_PREFIX + text if prefixes_active() else text


def embed_document_text(text: str, title: str | None = None) -> str:
    """Texto a embeber al indexar (rol documento). `title` = nombre de la nota."""
    if not prefixes_active():
        return text
    return _DOC_PREFIX % (title or "none") + text
