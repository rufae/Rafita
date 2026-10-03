"""Reranking híbrido para la búsqueda del segundo cerebro (mejora 1).

La similitud vectorial falla con nombres propios y con consultas de extracción
amplia («todo lo que diga de Ana»). Este módulo combina la puntuación semántica
con señales léxicas: términos exactos del contenido, entidades (inicial
mayúscula), coincidencias en el nombre/ruta de la nota y un empujón para las
consultas exhaustivas. Es un módulo puro (sin I/O) para poder testearlo.

Ingeniería inversa del patrón «Select Active Evidence» del flujo n8n de
trabajo: semantic + lexical + entity + file + exhaustive boost.
"""

import re
import unicodedata

_EXHAUSTIVE_RE = re.compile(
    r"\b(todo lo que|todo cuanto|todo sobre|toda la informacion|toda la información|"
    r"revisa .* todo|revisa .* toda|que ponga sobre|qué ponga sobre|"
    r"todas las referencias|todas las menciones|todas las apariciones|"
    r"todo lo que diga|todo lo que dice)\b",
    re.IGNORECASE,
)

_PROPER_RE = re.compile(r"\b([A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{2,})\b")

_STOPWORDS = frozenset(
    {
        "a",
        "al",
        "algo",
        "algun",
        "alguna",
        "algunas",
        "alguno",
        "algunos",
        "ante",
        "bajo",
        "con",
        "contra",
        "cual",
        "cuales",
        "como",
        "de",
        "del",
        "desde",
        "donde",
        "dos",
        "el",
        "ella",
        "ellas",
        "ello",
        "ellos",
        "en",
        "entre",
        "es",
        "esa",
        "esas",
        "ese",
        "eso",
        "esos",
        "esta",
        "estas",
        "este",
        "esto",
        "estos",
        "la",
        "las",
        "le",
        "les",
        "lo",
        "los",
        "mas",
        "me",
        "mi",
        "mis",
        "muy",
        "no",
        "nos",
        "o",
        "para",
        "pero",
        "por",
        "que",
        "quien",
        "quienes",
        "se",
        "sin",
        "sobre",
        "su",
        "sus",
        "tambien",
        "te",
        "tengo",
        "todo",
        "toda",
        "todas",
        "todos",
        "tu",
        "tus",
        "un",
        "una",
        "unas",
        "uno",
        "unos",
        "y",
        "ya",
        "hay",
        "tiene",
        "tienen",
        "tienes",
        "usa",
        "poner",
        "ponga",
        "revisa",
        "revisar",
        "dime",
        "diga",
        "buscar",
        "busca",
        "explícame",
        "explicame",
    }
)


def normalize_text(value: object) -> str:
    """Minúsculas sin acentos (comparaciones robustas)."""
    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text.lower()


def tokenize(value: object) -> list[str]:
    """Tokens significativos (>=2 letras, sin stopwords)."""
    return [
        t
        for t in re.findall(r"[a-z0-9_]+", normalize_text(value))
        if len(t) >= 2 and t not in _STOPWORDS
    ]


def is_exhaustive_query(query: object) -> bool:
    """True si la pregunta pide extracción amplia («todo lo que diga de X»)."""
    return bool(_EXHAUSTIVE_RE.search(str(query or "")))


def proper_terms(query: object) -> list[str]:
    """Nombres propios (inicial mayúscula): 'Ana', 'Proyecto Babel'."""
    found = [normalize_text(m) for m in _PROPER_RE.findall(str(query or " "))]
    return [t for t in dict.fromkeys(found) if t not in _STOPWORDS]


def query_terms(query: object) -> list[str]:
    """Términos de la pregunta: léxicos + nombres propios, sin duplicados."""
    return list(dict.fromkeys(tokenize(query) + proper_terms(query)))


def score_candidates(query: str, candidates: list[dict]) -> list[dict]:
    """Puntúa y ordena candidatos por relevancia híbrida.

    Cada candidato es un dict con al menos `text` y `semantic_score` (0-1);
    opcionalmente `note_path`/`source` para la señal de nombre de fichero.
    Devuelve copias con `rerank_score`, `matched_terms`, `entity_matches`,
    `file_matches` e `index` (posición original, para desempates estables).
    """
    terms = query_terms(query)
    proper = proper_terms(query)
    exhaustive = is_exhaustive_query(query)
    scored: list[dict] = []
    for index, cand in enumerate(candidates):
        text_norm = normalize_text(cand.get("text", ""))
        name_norm = normalize_text(cand.get("note_path") or cand.get("source") or "")
        matched = [t for t in terms if re.search(r"\b%s\b" % re.escape(t), text_norm)]
        entity_hits = [t for t in proper if re.search(r"\b%s\b" % re.escape(t), text_norm)]
        file_hits = [t for t in terms if t and t in name_norm]
        lexical_score = 2.0 * len(matched)
        entity_score = 6.0 * len(entity_hits)
        file_score = 5.0 * len(file_hits)
        exhaustive_boost = 10.0 if (exhaustive and entity_hits) else 0.0
        semantic_score = float(cand.get("semantic_score", 0.0) or 0.0)
        scored.append(
            {
                **cand,
                "index": index,
                "rerank_score": semantic_score
                + lexical_score
                + entity_score
                + file_score
                + exhaustive_boost,
                "matched_terms": matched,
                "entity_matches": entity_hits,
                "file_matches": file_hits,
                "exhaustive": exhaustive,
            }
        )
    scored.sort(key=lambda c: (-c["rerank_score"], c["index"]))
    return scored
