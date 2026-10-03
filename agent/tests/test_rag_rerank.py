"""Tests del reranking híbrido del segundo cerebro (mejora 1)."""

from src.utils.rag_rerank import (
    is_exhaustive_query,
    normalize_text,
    proper_terms,
    query_terms,
    score_candidates,
    tokenize,
)


def test_normalize_text_quita_acentos_y_mayusculas():
    assert normalize_text("Ana Pérez") == "ana perez"
    assert normalize_text(None) == ""


def test_tokenize_filtra_stopwords_y_cortos():
    tokens = tokenize("La reunión de Ana con el proyecto")
    assert "ana" in tokens
    assert "proyecto" in tokens
    assert "la" not in tokens
    assert "de" not in tokens


def test_is_exhaustive_query():
    assert is_exhaustive_query("dime todo lo que diga de Ana")
    assert is_exhaustive_query("todas las menciones de Rafita")
    assert not is_exhaustive_query("que dice Ana de Rafita")


def test_proper_terms_detecta_nombres_propios():
    terms = proper_terms("¿Qué dice Ana sobre Proyecto Babel?")
    assert "ana" in terms
    assert "babel" in terms
    assert "proyecto" in terms
    assert proper_terms("todo en minusculas") == []


def test_query_terms_combina_lexico_y_propios():
    terms = query_terms("agenda de Ana")
    assert "agenda" in terms
    assert "ana" in terms


def test_score_candidates_prioriza_entidad_y_fichero():
    candidates = [
        {
            "text": "El presupuesto anual es de mil euros y no hay mas datos.",
            "semantic_score": 0.9,
            "note_path": "finanzas/presupuesto.md",
        },
        {
            "text": "Ana trabaja en el proyecto Babel desde enero.",
            "semantic_score": 0.5,
            "note_path": "personas/ana.md",
        },
    ]
    scored = score_candidates("que dice Ana", candidates)
    # La entidad exacta (Ana) debe ganar al candidato semanticamente mejor.
    assert scored[0]["note_path"] == "personas/ana.md"
    assert "ana" in scored[0]["entity_matches"]
    assert scored[0]["rerank_score"] > scored[1]["rerank_score"]


def test_score_candidates_boost_exhaustivo():
    candidates = [
        {"text": "Ana va al gimnasio", "semantic_score": 0.4, "note_path": "a.md"},
        {"text": "otra cosa sin relacion", "semantic_score": 0.9, "note_path": "b.md"},
    ]
    scored = score_candidates("todo lo que diga de Ana", candidates)
    assert scored[0]["note_path"] == "a.md"
    assert scored[0]["exhaustive"] is True
    assert scored[0]["rerank_score"] >= 0.4 + 6.0 + 10.0


def test_score_candidates_coincidencia_de_fichero():
    candidates = [
        {"text": "texto generico", "semantic_score": 0.8, "note_path": "proyectos/rafita.md"},
        {"text": "texto generico tambien", "semantic_score": 0.8, "note_path": "otros/otro.md"},
    ]
    scored = score_candidates("rafita", candidates)
    assert scored[0]["note_path"] == "proyectos/rafita.md"
    assert scored[0]["file_matches"] == ["rafita"]


def test_score_candidates_desempate_estable():
    candidates = [
        {"text": "mismo texto", "semantic_score": 0.5, "note_path": "x.md"},
        {"text": "mismo texto", "semantic_score": 0.5, "note_path": "y.md"},
    ]
    scored = score_candidates("consulta sin relacion", candidates)
    assert [c["note_path"] for c in scored] == ["x.md", "y.md"]
