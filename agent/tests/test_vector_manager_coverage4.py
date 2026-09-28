"""Cobertura de vector_manager.py: coleccion falsa y Chroma temporal sin red."""

import pytest

import src.utils.vector_manager as vm
from src.utils.vector_manager import (
    MAX_TAG_FLAGS,
    TAG_FLAG_PREFIX,
    VectorManager,
    build_tag_where,
    chunk_text,
    normalize_tag,
)


def _fake_call(self, input):
    return [[float((len(t) + i) % 7)] * 4 for i, t in enumerate(input)]


class FakeCollection:
    def __init__(self, count=0, existing=None, query_result=None):
        self._count = count
        self._existing = existing or {"ids": []}
        self._query_result = query_result or {
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }
        self.added = []
        self.deleted = []
        self.get_calls = []
        self.query_calls = []
        self.add_errors = []
        self.query_error = None
        self.get_error = None
        self.count_error = None

    def count(self):
        if self.count_error:
            raise self.count_error
        return self._count

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        if self.get_error:
            raise self.get_error
        return self._existing

    def add(self, ids=None, documents=None, metadatas=None):
        if self.add_errors:
            raise self.add_errors.pop(0)
        self.added.append((ids, documents, metadatas))

    def delete(self, ids=None):
        self.deleted.append(ids)

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        if self.query_error:
            raise self.query_error
        return self._query_result


def _manager(collection=None, initialized=True) -> VectorManager:
    manager = VectorManager()
    manager._initialized = initialized
    manager._collection = collection
    return manager


# ---------------------------------------------------------------------------
# Funcion de embeddings (sin Ollama real)
# ---------------------------------------------------------------------------


def test_embedding_function_caches_and_requires_matching(monkeypatch):
    calls = []

    class _LLM:
        @staticmethod
        def embed_texts(texts):
            calls.append(list(texts))
            return [[1.0] for _ in texts]

    monkeypatch.setattr("src.ollama_client.llm", _LLM)
    fn = vm.OllamaEmbeddingFunction()
    assert fn([]) == []
    result = fn(["hola", "mundo"])
    assert result == [[1.0], [1.0]]
    assert calls == [["hola", "mundo"]]
    assert fn(["hola"]) == [[1.0]]
    assert len(calls) == 1

    class _BadLLM:
        @staticmethod
        def embed_texts(texts):
            return [[1.0]]

    monkeypatch.setattr("src.ollama_client.llm", _BadLLM)
    with pytest.raises(RuntimeError, match="embeddings"):
        fn(["dos", "textos"])


# ---------------------------------------------------------------------------
# add_document / index_chunks / delete_by_note_path
# ---------------------------------------------------------------------------


async def test_add_document_chunks_and_skips_existing(monkeypatch):
    monkeypatch.setattr(vm.settings, "chunk_size", 3)
    monkeypatch.setattr(vm.settings, "chunk_overlap", 1)
    collection = FakeCollection(existing={"ids": ["doc.md::chunk_0"]})
    manager = _manager(collection)
    result = await manager.add_document("doc.md", "uno dos tres cuatro", {"tipo": "x"})
    assert result["success"] is True
    assert result["chunks_added"] == 1
    assert result["chunks_total"] == 2
    ids, docs, metas = collection.added[0]
    assert ids == ["doc.md::chunk_1"]
    assert metas[0]["tipo"] == "x"
    assert metas[0]["chunk_index"] == 1


async def test_add_document_edge_cases():
    manager = _manager(initialized=False)
    result = await manager.add_document("doc.md", "hola")
    assert result["success"] is False

    manager = _manager(FakeCollection())
    result = await manager.add_document("doc.md", "   ")
    assert result["success"] is False
    assert "fragmentos" in result["message"]


async def test_index_chunks_retries_and_skips(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(vm.asyncio, "sleep", no_sleep)
    collection = FakeCollection()
    collection.add_errors = [RuntimeError("1"), RuntimeError("2")]
    manager = _manager(collection)
    result = await manager.index_chunks(
        [{"text": "texto", "metadata": {"note_path": "n.md", "heading": "H"}}]
    )
    assert result["success"] is True
    assert result["chunks_added"] == 1
    assert result["chunks_skipped"] == 0

    collection = FakeCollection()
    collection.add_errors = [RuntimeError("1"), RuntimeError("2"), RuntimeError("3")]
    manager = _manager(collection)
    result = await manager.index_chunks([{"text": "texto", "metadata": {"note_path": "n.md"}}])
    assert result["chunks_added"] == 0
    assert result["chunks_skipped"] == 1
    assert "omitidos" in result["message"]


async def test_index_chunks_rejects_empty():
    manager = _manager(initialized=False)
    assert (await manager.index_chunks([{"text": "x", "metadata": {}}]))["success"] is False
    manager = _manager(FakeCollection())
    assert (await manager.index_chunks([]))["success"] is False


async def test_delete_by_note_path_unions_keys_and_tolerates_errors():
    collection = FakeCollection()

    def get(**kwargs):
        collection.get_calls.append(kwargs)
        key = list(kwargs.get("where", {}))[0]
        if key == "note_path":
            raise RuntimeError("clave ausente en filas legacy")
        return {"ids": ["b", "a"]}

    collection.get = get
    manager = _manager(collection)
    deleted = await manager.delete_by_note_path("n.md")
    assert deleted == 2
    assert collection.deleted == [["a", "b"]]

    manager = _manager(FakeCollection(existing={"ids": []}))
    assert await manager.delete_by_note_path("n.md") == 0

    manager = _manager(None, initialized=True)
    manager._collection = None
    assert await manager.delete_by_note_path("n.md") == 0


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------


def _query_result(*entries):
    docs, metas, dists = [], [], []
    for doc, meta, dist in entries:
        docs.append(doc)
        metas.append(meta)
        dists.append(dist)
    return {"documents": [docs], "metadatas": [metas], "distances": [dists]}


async def test_query_applies_threshold_and_tags():
    collection = FakeCollection(
        count=3,
        query_result=_query_result(
            (
                "cerca",
                {"note_path": "a.md", "filename": "a.md", "tags_str": "finanzas", "heading": "H"},
                0.1,
            ),
            (
                "lejos",
                {"note_path": "b.md", "filename": "b.md", "tags_str": "otro", "heading": "H"},
                1.99,
            ),
        ),
    )
    manager = _manager(collection)
    result = await manager.query("consulta", top_k=5)
    assert result["success"] is True
    assert [r["note_path"] for r in result["results"]] == ["a.md"]
    assert result["notes_found"] == ["a.md"]
    assert float(result["results"][0]["relevance"]) > 0.49

    result = await manager.query("consulta", apply_threshold=False, filter_tags=["finanzas"])
    assert [r["note_path"] for r in result["results"]] == ["a.md"]
    assert collection.query_calls[-1]["where"] == {TAG_FLAG_PREFIX + "finanzas": 1}

    result = await manager.query("consulta", apply_threshold=False, filter_tags=["sin-tag"])
    assert result["results"] == []
    assert result["notes_found"] == []


async def test_query_empty_uninitialized_and_errors():
    manager = _manager(initialized=False)
    result = await manager.query("x")
    assert result["success"] is False

    manager = _manager(FakeCollection(count=0))
    result = await manager.query("x")
    assert result["success"] is True
    assert "vacia" in result["message"]

    collection = FakeCollection(count=1)
    collection.query_error = RuntimeError("chroma caido")
    manager = _manager(collection)
    result = await manager.query("x")
    assert result["success"] is False
    assert "Error en busqueda" in result["message"]


async def test_query_falls_back_to_python_tag_filter():
    empty_then_full = [
        _query_result(),
        _query_result(
            ("doc", {"note_path": "legacy.md", "source": "legacy.md", "tags_str": "casa"}, 0.1)
        ),
    ]

    class _FallbackCollection(FakeCollection):
        def query(self, **kwargs):
            self.query_calls.append(kwargs)
            return empty_then_full.pop(0)

    manager = _manager(_FallbackCollection(count=1))
    result = await manager.query("casa", filter_tags=["casa"])
    assert result["success"] is True
    assert [r["source"] for r in result["results"]] == ["legacy.md"]
    assert manager._collection.query_calls[-1]["where"] is None

    class _BrokenFallback(FakeCollection):
        def query(self, **kwargs):
            self.query_calls.append(kwargs)
            if kwargs.get("where") is None:
                raise RuntimeError("fallback caido")
            return _query_result()

    manager = _manager(_BrokenFallback(count=1))
    result = await manager.query("casa", filter_tags=["casa"])
    assert result["success"] is False


async def test_query_without_results_reports_empty():
    manager = _manager(FakeCollection(count=2, query_result=_query_result()))
    result = await manager.query("x")
    assert result["success"] is True
    assert "Sin resultados" in result["message"]


# ---------------------------------------------------------------------------
# document_exists / stats / health / close
# ---------------------------------------------------------------------------


async def test_document_exists_variants():
    manager = _manager(FakeCollection(existing={"ids": ["1"]}))
    assert await manager.document_exists("a.md") is True

    collection = FakeCollection(existing={"ids": []})
    calls = []

    def get(**kwargs):
        calls.append(kwargs)
        if list(kwargs["where"])[0] == "note_path":
            raise RuntimeError("legacy")
        return {"ids": ["2"]}

    collection.get = get
    manager = _manager(collection)
    assert await manager.document_exists("a.md") is True

    manager = _manager(FakeCollection(existing={"ids": []}))
    assert await manager.document_exists("a.md") is False

    manager = _manager(None)
    manager._collection = None
    assert await manager.document_exists("a.md") is False


async def test_get_stats_counts_documents():
    manager = _manager(None)
    manager._collection = None
    assert await manager.get_stats() == {"total_chunks": 0, "total_documents": 0}

    collection = FakeCollection(
        count=3,
        existing={"ids": ["1", "2"], "metadatas": [{"filename": "a.md"}, {"source": "b.md"}, {}]},
    )
    manager = _manager(collection)
    stats = await manager.get_stats()
    assert stats == {"total_chunks": 3, "total_documents": 2}

    collection.get_error = RuntimeError("metadata ilegible")
    stats = await manager.get_stats()
    assert stats == {"total_chunks": 3, "total_documents": 0}


async def test_health_variants():
    manager = _manager(None)
    manager._collection = None
    assert (await manager.health())["status"] == "error"

    collection = FakeCollection(count=5)
    manager = _manager(collection)
    assert await manager.health() == {"status": "ok", "chunks": 5}

    collection.count_error = RuntimeError("bloqueada")
    result = await manager.health()
    assert result["status"] == "error"
    assert "query failed" in result["detail"]


async def test_close_marks_uninitialized():
    manager = _manager(FakeCollection())
    await manager.close()
    assert manager._initialized is False


# ---------------------------------------------------------------------------
# Chroma temporal (sin embeddings reales)
# ---------------------------------------------------------------------------


async def test_initialize_add_query_delete_with_temp_chroma(tmp_path, monkeypatch):
    monkeypatch.setattr(vm.OllamaEmbeddingFunction, "__call__", _fake_call)
    monkeypatch.setattr(vm.settings, "vector_db_dir", str(tmp_path))
    monkeypatch.setattr(vm.settings, "relevance_threshold", 0.0)
    manager = VectorManager()
    await manager.initialize()
    assert manager._initialized
    assert manager._collection.count() == 0

    result = await manager.add_document("notas/gastos.md", "Gasolina ochenta euros al mes")
    assert result["success"] is True
    assert result["chunks_added"] == 1
    assert await manager.document_exists("notas/gastos.md") is True

    stats = await manager.get_stats()
    assert stats["total_chunks"] == 1
    assert stats["total_documents"] == 1

    result = await manager.query("gasolina euros", top_k=3, apply_threshold=False)
    assert result["results"]
    assert result["results"][0]["note_path"] == "notas/gastos.md"

    deleted = await manager.delete_by_note_path("notas/gastos.md")
    assert deleted == 1
    assert manager._collection.count() == 0
    assert (await manager.health())["status"] == "ok"
    await manager.close()


# ---------------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------------


def test_normalize_and_build_tag_where():
    assert normalize_tag("Año Nuevo!") == "ano_nuevo"
    assert normalize_tag("") == ""
    assert build_tag_where([]) is None
    assert build_tag_where(None) is None
    assert build_tag_where(["casa"]) == {TAG_FLAG_PREFIX + "casa": 1}
    assert build_tag_where(["casa", "casa"]) == {TAG_FLAG_PREFIX + "casa": 1}
    expression = build_tag_where(["a", "b"])
    assert expression == {"$or": [{TAG_FLAG_PREFIX + "a": 1}, {TAG_FLAG_PREFIX + "b": 1}]}
    many = build_tag_where(["tag%d" % i for i in range(MAX_TAG_FLAGS + 5)])
    assert len(many["$or"]) == MAX_TAG_FLAGS


def test_parse_tags_and_chunk_text():
    assert vm._parse_tags({}) == []
    assert vm._parse_tags({"tags_str": " a , b "}) == ["a", "b"]
    assert vm._parse_tags({"tags_str": 5}) == []
    assert chunk_text("") == []
    assert chunk_text("uno dos") == ["uno dos"]
    chunks = chunk_text("uno dos tres cuatro cinco", chunk_size=2, chunk_overlap=1)
    assert chunks == ["uno dos", "dos tres", "tres cuatro", "cuatro cinco", "cinco"]


async def test_query_uses_limit_and_metrics(monkeypatch):
    observed = []

    monkeypatch.setattr(vm.metrics, "observe", lambda name, value: observed.append(name))
    collection = FakeCollection(
        count=1,
        query_result=_query_result(("doc", {"note_path": "a.md", "tags_str": ""}, 0.0)),
    )
    manager = _manager(collection)
    result = await manager.query("x", top_k=9)
    assert collection.query_calls[-1]["n_results"] == 1
    assert observed == ["embedding_query_latency"]
    assert result["results"][0]["tags"] == []
    assert result["results"][0]["heading"] == ""
