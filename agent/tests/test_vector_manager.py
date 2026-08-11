"""Tests for vector_manager.py — embedding function, indexing, querying."""
import asyncio
import tempfile
import shutil

import pytest

from src.utils.vector_manager import (
    OllamaEmbeddingFunction,
    VectorManager,
    chunk_text,
)


class TestOllamaEmbeddingFunction:
    def test_produces_non_zero_embeddings(self):
        ef = OllamaEmbeddingFunction()
        texts = ["test text one", "different text two", "third unique text"]
        result = ef(texts)
        assert len(result) == 3
        for i, emb in enumerate(result):
            assert len(emb) > 0, f"Embedding {i} is empty"
            is_zero = all(abs(v) < 1e-10 for v in emb)
            assert not is_zero, f"Embedding {i} is all zeros"

    def test_different_texts_produce_different_embeddings(self):
        ef = OllamaEmbeddingFunction()
        texts = ["gasto en gasolina mensual", "DNI 29560575D", "Audi A3 comprado"]
        result = ef(texts)
        for i in range(len(result)):
            for j in range(i + 1, len(result)):
                l2 = sum((a - b) ** 2 for a, b in zip(result[i], result[j])) ** 0.5
                assert l2 > 0.01, f"Embeddings {i} and {j} are identical"

    def test_empty_input_returns_empty(self):
        ef = OllamaEmbeddingFunction()
        result = ef([])
        assert result == []

    def test_caches_identical_inputs(self):
        ef = OllamaEmbeddingFunction()
        texts = ["repeated text"]
        r1 = ef(texts)
        r2 = ef(texts)
        assert r1[0] == r2[0]


class TestVectorManager:
    @pytest.fixture
    async def vector_db(self):
        tmp_dir = tempfile.mkdtemp()
        from src.config import settings

        old_path = settings.vector_db_dir
        settings.vector_db_dir = tmp_dir

        vm = VectorManager()
        await vm.initialize()
        yield vm
        await vm.close()
        settings.vector_db_dir = old_path
        shutil.rmtree(tmp_dir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_initialize_creates_collection(self, vector_db):
        assert vector_db._initialized
        assert vector_db._collection is not None
        assert vector_db._collection.count() == 0

    @pytest.mark.asyncio
    async def test_index_chunks_no_skipped(self, vector_db):
        chunks = [
            {"text": "Gasolina: 80 euros al mes", "metadata": {"note_path": "test/finanzas.md", "heading": "Transporte"}},
            {"text": "Alquiler: 400 euros al mes", "metadata": {"note_path": "test/finanzas.md", "heading": "Vivienda"}},
        ]
        result = await vector_db.index_chunks(chunks)
        assert result["success"]
        assert result["chunks_added"] == 2
        assert result.get("chunks_skipped", 0) == 0

    @pytest.mark.asyncio
    async def test_index_chunks_empty(self, vector_db):
        result = await vector_db.index_chunks([])
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_query_returns_results(self, vector_db):
        chunks = [
            {"text": "Salario mensual: 1313 euros netos", "metadata": {"note_path": "test/finanzas.md", "heading": "Ingresos", "tags_str": "finanzas"}},
            {"text": "Gasolina: 80 euros al mes", "metadata": {"note_path": "test/finanzas.md", "heading": "Transporte", "tags_str": "finanzas,transporte"}},
        ]
        await vector_db.index_chunks(chunks)

        result = await vector_db.query("cuanto gano al mes")
        assert result["success"]
        assert len(result["results"]) > 0
        assert result["results"][0]["note_path"] == "test/finanzas.md"

    @pytest.mark.asyncio
    async def test_query_empty_db(self, vector_db):
        result = await vector_db.query("anything")
        assert result["success"]
        assert result["results"] == []
        assert "vacia" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_get_stats(self, vector_db):
        stats = await vector_db.get_stats()
        assert stats["total_chunks"] == 0
        assert stats["total_documents"] == 0

        await vector_db.index_chunks([
            {"text": "Test content", "metadata": {"note_path": "test/a.md", "filename": "a.md", "source": "test/a.md", "heading": "root"}},
        ])
        stats = await vector_db.get_stats()
        assert stats["total_chunks"] == 1
        assert stats["total_documents"] == 1

    @pytest.mark.asyncio
    async def test_delete_by_source(self, vector_db):
        await vector_db.index_chunks([
            {"text": "Chunk 1", "metadata": {"note_path": "test/x.md", "source": "test/x.md", "heading": "root"}},
            {"text": "Chunk 2", "metadata": {"note_path": "test/x.md", "source": "test/x.md", "heading": "root2"}},
        ])
        assert vector_db._collection.count() == 2
        deleted = await vector_db.delete_by_source("test/x.md")
        assert deleted == 2
        assert vector_db._collection.count() == 0


class TestChunkText:
    def test_empty_text(self):
        assert chunk_text("") == []
        assert chunk_text("   ") == []

    def test_small_text_single_chunk(self):
        chunks = chunk_text("Short text.", chunk_size=512)
        assert len(chunks) == 1
        assert chunks[0] == "Short text."

    def test_large_text_splits_with_overlap(self):
        words = ["word"] * 1000
        text = " ".join(words)
        chunks = chunk_text(text, chunk_size=200, chunk_overlap=50)
        assert len(chunks) > 3
        # Check overlap between consecutive chunks
        for i in range(len(chunks) - 1):
            words1 = set(chunks[i].split())
            words2 = set(chunks[i + 1].split())
            overlap = words1 & words2
            assert len(overlap) > 0, f"No overlap between chunk {i} and {i+1}"
