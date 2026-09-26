"""Guardia: ChromaDB solo en modo embebido (avisos PYSEC-2026-3813/3814/3815).

Los tres avisos aceptados en docs/SECURITY.md -> "Dependencias con avisos aceptados
(ChromaDB)" solo son explotables con el **servidor HTTP** de Chroma (auth +
multi-tenant) o registrando modelos remotos con `trust_remote_code=True`.
Rafita usa `chromadb.PersistentClient` embebido, mono-usuario y sin API HTTP.

Este test falla si alguien introduce modo servidor o `trust_remote_code` en el
código: en ese caso hay que volver a revisar los avisos antes de continuar
(no hay version corregida publicada, 1.5.9 sigue afectada a 2026-09-26).
"""

from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"

FORBIDDEN = (
    "chromadb.HttpClient",
    "chromadb.AsyncHttpClient",
    "chromadb.Client(",
    "trust_remote_code",
)


def _python_sources() -> dict[str, str]:
    return {str(p): p.read_text(encoding="utf-8") for p in SRC_DIR.rglob("*.py")}


def test_chromadb_embedded_client_is_used():
    sources = _python_sources()
    assert any("chromadb.PersistentClient" in text for text in sources.values()), (
        "VectorManager debe usar chromadb.PersistentClient (embebido)"
    )


def test_no_chromadb_server_or_remote_code_in_sources():
    for path, text in _python_sources().items():
        for forbidden in FORBIDDEN:
            assert forbidden not in text, (
                "%s encontrado en %s: revisar docs/SECURITY.md (ChromaDB solo "
                "embebido; los avisos PYSEC-2026-3813/3814/3815 aplican al "
                "servidor HTTP)" % (forbidden, path)
            )
