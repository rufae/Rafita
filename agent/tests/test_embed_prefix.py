"""Prefijos de rol para embeddings (embed_prefix)."""

from src.config import settings
from src.utils.embed_prefix import embed_document_text, embed_query_text, prefixes_active


def test_prefixes_off_for_other_models(monkeypatch):
    monkeypatch.setattr(settings, "embedding_model", "bge-m3")
    assert not prefixes_active()
    assert embed_query_text("hola") == "hola"
    assert embed_document_text("texto", "nota") == "texto"


def test_prefixes_on_for_embeddinggemma(monkeypatch):
    monkeypatch.setattr(settings, "embedding_model", "embeddinggemma-2:740m-bf16")
    assert prefixes_active()
    assert (
        embed_query_text("¿dónde están mis llaves?")
        == "task: search result | query: ¿dónde están mis llaves?"
    )
    assert embed_document_text("contenido", "Compras") == "title: Compras | text: contenido"


def test_document_prefix_without_title(monkeypatch):
    monkeypatch.setattr(settings, "embedding_model", "embeddinggemma-2")
    assert embed_document_text("contenido") == "title: none | text: contenido"
