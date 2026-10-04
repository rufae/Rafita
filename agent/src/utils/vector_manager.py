import asyncio
import os
import re
import time
import unicodedata
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

os.environ["CHROMA_TELEMETRY_ENABLED"] = "false"

import chromadb
from chromadb.config import Settings as ChromaSettings

from src.config import settings
from src.logger import logger
from src.utils.rag_rerank import score_candidates
from src.utils.telemetry import metrics


class OllamaEmbeddingFunction:
    """Chroma embedding function delegating to the configured AI provider.

    Named for backwards compatibility; with AI_PROVIDER=openai the batch
    embeddings go through the OpenAI-compatible adapter instead.
    """

    def __init__(self):
        # LRU acotada: select_tools_semantic embebe cada mensaje del usuario;
        # sin tope la cache crecia indefinidamente durante la vida del proceso.
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._cache_max = 1024

    def _cache_get(self, text: str) -> list[float] | None:
        emb = self._cache.get(text)
        if emb is not None:
            self._cache.move_to_end(text)
        return emb

    def _cache_put(self, text: str, emb: list[float]) -> None:
        self._cache[text] = emb
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)

    def __call__(self, input: list[str]) -> list[list[float]]:
        results: list[list[float] | None] = []
        uncached_texts: list[str] = []
        uncached_indices: list[int] = []
        for i, text in enumerate(input):
            cached = self._cache_get(text)
            if cached is not None:
                results.append(cached)
            else:
                results.append(None)
                uncached_texts.append(text)
                uncached_indices.append(i)
        if uncached_texts:
            from src.ollama_client import llm

            embeddings = llm.embed_texts(uncached_texts)
            if len(embeddings) != len(uncached_texts):
                raise RuntimeError(
                    "AI provider returned %d embeddings for %d input texts"
                    % (len(embeddings), len(uncached_texts))
                )
            for idx, emb, text in zip(uncached_indices, embeddings, uncached_texts):
                results[idx] = emb
                self._cache_put(text, emb)
        return [result for result in results if result is not None]


def _parse_tags(meta: dict[str, Any]) -> list[str]:
    """Safely extract tags list from ChromaDB metadata (mypy-safe)."""
    raw = meta.get("tags_str", "")
    if isinstance(raw, str) and raw.strip():
        return [t.strip() for t in raw.split(",") if t.strip()]
    return []


TAG_FLAG_PREFIX = "tag__"
MAX_TAG_FLAGS = 20


def normalize_tag(tag: str) -> str:
    """Normalize a tag to a stable metadata-key suffix (ascii, lowercase)."""
    if not tag:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(tag).lower())
    ascii_tag = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "_", ascii_tag).strip("_")[:40]


def build_tag_where(tags: list[str] | None) -> dict[str, Any] | None:
    """Chroma `where` expression for tag filtering (OR semantics).

    Chroma metadata values must be scalars and there is no `$contains` for
    metadata, so each tag is stored at index time as a `tag__<normalized>`
    flag. One tag needs a plain equality; several need `$or` (Chroma requires
    at least two sub-expressions). Missing keys simply do not match.
    """
    normalized: list[str] = []
    for tag in tags or []:
        norm = normalize_tag(tag)
        if norm and norm not in normalized:
            normalized.append(norm)
        if len(normalized) >= MAX_TAG_FLAGS:
            break
    if not normalized:
        return None
    if len(normalized) == 1:
        return {TAG_FLAG_PREFIX + normalized[0]: 1}
    return {"$or": [{TAG_FLAG_PREFIX + n: 1} for n in normalized]}


class VectorManager:
    def __init__(self):
        self._client: chromadb.Client | None = None
        self._collection: chromadb.Collection | None = None
        self._embed_fn = OllamaEmbeddingFunction()
        self._initialized = False

    async def initialize(self) -> None:
        # Chroma crea su cliente posthog aunque anonymized_telemetry=False y la
        # version instalada falla al enviar (ruido en los logs). Se silencia
        # ANTES de crear el cliente: el primer evento (ClientStartEvent) se
        # dispara dentro de PersistentClient().
        import logging as _logging

        _logging.getLogger("chromadb.telemetry.product.posthog").setLevel(_logging.CRITICAL)
        # chroma 0.5.0 avisa "Add of existing embedding ID" en CADA upsert de
        # una nota ya indexada (por diseño el reindex es idempotente): en el
        # arranque eso son ~660 lineas de WARNING inutiles. El mensaje de
        # indice ya queda en los logs de Vector DB.
        _logging.getLogger("chromadb.segment.impl.vector.local_persistent_hnsw").setLevel(
            _logging.ERROR
        )
        db_path = settings.vector_db_path
        db_path.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_running_loop()
        self._client = await loop.run_in_executor(
            None,
            lambda: chromadb.PersistentClient(
                path=str(db_path),
                settings=ChromaSettings(anonymized_telemetry=False),
            ),
        )
        # Chroma defaults to L2 (collection metadata is empty). Task 1.4
        # verified that bge-m3 through Ollama returns unit-norm vectors for
        # both documents and queries (norm ~1.0) and that L2 and cosine rank
        # identically (Pearson ~1.0), so the default space is equivalent to
        # cosine here; do not change it without re-measuring and reindexing.
        self._collection = self._client.get_or_create_collection(
            name="rafita_rag",
            embedding_function=self._embed_fn,
        )
        self._initialized = True
        count = self._collection.count()
        logger.info("Vector DB initialized at %s (%d chunks indexed)", db_path, count)

    async def add_document(
        self, file_path: str, content: str, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self._initialized:
            return {"success": False, "message": "Vector DB not initialized."}
        chunks = chunk_text(content, settings.chunk_size, settings.chunk_overlap)
        if not chunks:
            return {"success": False, "message": "No se pudo dividir el contenido en fragmentos."}
        base_meta = {
            "note_path": file_path,
            "filename": Path(file_path).name,
            "indexed_at": datetime.now().isoformat(),
        }
        if metadata:
            base_meta.update(metadata)
        ids = []
        documents = []
        metadatas = []
        for i, chunk in enumerate(chunks):
            chunk_id = "%s::chunk_%d" % (file_path, i)
            ids.append(chunk_id)
            documents.append(chunk)
            meta = dict(base_meta)
            meta["chunk_index"] = i
            meta["chunk_total"] = len(chunks)
            metadatas.append(meta)
        loop = asyncio.get_running_loop()
        existing_ids = set()
        try:
            existing = await loop.run_in_executor(
                None, lambda: self._collection.get(ids=ids, include=[])
            )
            existing_ids = set(existing["ids"] if existing and "ids" in existing else [])
        except Exception:
            pass
        new_ids = []
        new_docs = []
        new_metas = []
        for i in range(len(ids)):
            if ids[i] not in existing_ids:
                new_ids.append(ids[i])
                new_docs.append(documents[i])
                new_metas.append(metadatas[i])
        if new_ids:
            await loop.run_in_executor(
                None,
                lambda: self._collection.add(ids=new_ids, documents=new_docs, metadatas=new_metas),
            )
        logger.info(
            "Vector DB: added %d chunks from %s (%d already existed)",
            len(new_ids),
            file_path,
            len(ids) - len(new_ids),
        )
        return {
            "success": True,
            "chunks_added": len(new_ids),
            "chunks_total": len(ids),
            "message": "Indexados %d fragmentos de %s." % (len(new_ids), Path(file_path).name),
        }

    async def delete_by_note_path(self, note_path: str) -> int:
        """Delete every chunk of a note using the `note_path` metadata key
        written by `index_chunks`/`add_document`.

        Legacy rows may carry the old `source` key instead. Chroma 0.5 raises
        KeyError when filtering by a key that is absent in some rows, so each
        filter is queried independently and the results are unioned.
        """
        if not self._collection:
            return 0
        loop = asyncio.get_running_loop()
        ids_to_delete: set[str] = set()
        for key in ("note_path", "source"):
            try:
                results = await loop.run_in_executor(
                    None, lambda k=key: self._collection.get(where={k: note_path}, include=[])
                )
                ids_to_delete.update(results["ids"] if results and results.get("ids") else [])
            except Exception as e:
                logger.debug("delete_by_note_path filter %s error: %s", key, e)
        if not ids_to_delete:
            return 0
        await loop.run_in_executor(
            None,
            lambda: self._collection.delete(ids=sorted(ids_to_delete)),
        )
        return len(ids_to_delete)

    async def index_chunks(self, chunks: list[dict[str, Any]]) -> dict[str, Any]:
        if not self._initialized:
            return {"success": False, "message": "Vector DB not initialized."}
        if not chunks:
            return {"success": False, "message": "No chunks to index."}
        loop = asyncio.get_running_loop()
        added = 0
        skipped = 0
        note_path = chunks[0]["metadata"].get("note_path", "unknown")
        for i, chunk in enumerate(chunks):
            chunk_id = "%s::h_%s::chunk_%d" % (
                chunk["metadata"]["note_path"],
                chunk["metadata"].get("heading", "root").replace("/", "_").replace(" ", "_")[:40],
                i,
            )
            for attempt in range(3):
                try:
                    # upsert (no add): reindexar la misma nota es idempotente y
                    # chroma deja de avisar "Add of existing embedding ID".
                    await loop.run_in_executor(
                        None,
                        lambda c=chunk, cid=chunk_id: self._collection.upsert(
                            ids=[cid],
                            documents=[c["text"]],
                            metadatas=[c["metadata"]],
                        ),
                    )
                    added += 1
                    break
                except Exception as e:
                    if attempt < 2:
                        wait = 2**attempt
                        logger.warning(
                            "Embedding chunk %d/%d of '%s' failed (attempt %d/3): %s. Retrying in %ds...",
                            i + 1,
                            len(chunks),
                            note_path,
                            attempt + 1,
                            e,
                            wait,
                        )
                        await asyncio.sleep(wait)
                    else:
                        logger.error(
                            "Chunk %d/%d of '%s' FAILED after 3 attempts: %s. Skipping.",
                            i + 1,
                            len(chunks),
                            note_path,
                            e,
                        )
                        skipped += 1
        if added > 0:
            logger.info(
                "Vector DB: indexed %d chunks for %s (%d skipped)",
                added,
                note_path,
                skipped,
            )
        return {
            "success": True,
            "chunks_added": added,
            "chunks_skipped": skipped,
            "message": "Indexados %d fragmentos (%d omitidos)." % (added, skipped)
            if skipped
            else "Indexados %d fragmentos." % added,
        }

    async def query(
        self,
        query_text: str,
        top_k: int = 5,
        filter_tags: list[str] | None = None,
        apply_threshold: bool = True,
    ) -> dict[str, Any]:
        if not self._initialized:
            return {"success": False, "results": [], "message": "Vector DB no inicializada."}
        if self._collection.count() == 0:
            return {"success": True, "results": [], "message": "La base vectorial esta vacia."}
        loop = asyncio.get_running_loop()

        _t0 = time.perf_counter()

        where_filter = build_tag_where(filter_tags)

        try:
            # Sobrrecuperación (mejora 1): el reranking híbrido necesita más
            # candidatos que top_k para rescatar coincidencias exactas de
            # nombres propios que el coseno ordena por detrás.
            results = await loop.run_in_executor(
                None,
                lambda: self._collection.query(
                    query_texts=[query_text],
                    n_results=min(max(top_k * 4, 20), self._collection.count()),
                    where=where_filter,
                ),
            )
        except Exception as e:
            logger.error("Vector query failed: %s", e)
            return {"success": False, "results": [], "message": "Error en busqueda: %s" % e}

        empty = not results or not results.get("documents") or not results["documents"][0]
        if empty and where_filter is not None:
            # Legacy collection without tag flags: over-fetch and filter in
            # Python so un-reindexed installs keep the previous behaviour.
            wider = min(self._collection.count(), max(top_k * 10, 100))
            try:
                results = await loop.run_in_executor(
                    None,
                    lambda: self._collection.query(
                        query_texts=[query_text],
                        n_results=wider,
                        where=None,
                    ),
                )
            except Exception as e:
                logger.error("Vector query (tag fallback) failed: %s", e)
                return {"success": False, "results": [], "message": "Error en busqueda: %s" % e}

        if not results or not results.get("documents") or not results["documents"][0]:
            return {"success": True, "results": [], "message": "Sin resultados relevantes."}

        candidates = []
        for i in range(len(results["documents"][0])):
            doc = results["documents"][0][i]
            meta = (results["metadatas"][0][i]) if results.get("metadatas") else {}
            distance = (results["distances"][0][i]) if results.get("distances") else 0.0
            note_path = meta.get("note_path", meta.get("source", "desconocido"))
            candidates.append(
                {
                    "text": doc,
                    "semantic_score": max(0.0, 1.0 - float(distance) / 2.0),
                    "meta": meta,
                    "note_path": note_path,
                }
            )

        # Reranking híbrido (mejora 1): señal léxica/entidad/fichero sobre la
        # similitud vectorial para nombres propios y consultas exhaustivas.
        scored = score_candidates(query_text, candidates)

        threshold = settings.relevance_threshold
        kept = []
        for cand in scored:
            if apply_threshold:
                rescate = bool(cand["entity_matches"] or cand["file_matches"])
                if cand["semantic_score"] < threshold and not rescate:
                    continue
            kept.append(cand)

        if apply_threshold and not kept:
            return {
                "success": True,
                "results": [],
                "notes_found": [],
                "message": (
                    "NO_ENCONTRADO: ningun fragmento supera el umbral de relevancia "
                    "(%.2f)." % threshold
                ),
            }

        if filter_tags:
            wanted = {normalize_tag(t) for t in filter_tags if normalize_tag(t)}
            kept = [c for c in kept if wanted & {normalize_tag(t) for t in _parse_tags(c["meta"])}]

        kept = kept[:top_k]

        formatted = []
        for cand in kept:
            meta = cand["meta"]
            note_path = cand["note_path"]
            formatted.append(
                {
                    "content": cand["text"][:800],
                    "source": meta.get("filename", note_path),
                    "note_path": note_path,
                    "heading": meta.get("heading", ""),
                    "obsidian_uri": meta.get("obsidian_uri", ""),
                    "tags": _parse_tags(meta),
                    "relevance": "%.3f" % cand["semantic_score"],
                    "matched_terms": cand["matched_terms"],
                }
            )
        metrics.observe("embedding_query_latency", time.perf_counter() - _t0)

        if not formatted:
            return {
                "success": True,
                "results": [],
                "notes_found": [],
                "message": "Sin resultados relevantes.",
            }

        seen_notes = {r["note_path"] for r in formatted}
        return {
            "success": True,
            "results": formatted,
            "notes_found": list(seen_notes),
            "message": "Encontrados %d fragmentos relevantes en %d nota%s."
            % (len(formatted), len(seen_notes), "s" if len(seen_notes) != 1 else ""),
        }

    async def document_exists(self, file_path: str) -> bool:
        if not self._collection:
            return False
        loop = asyncio.get_running_loop()
        for key in ("note_path", "source"):
            try:
                results = await loop.run_in_executor(
                    None,
                    lambda k=key: self._collection.get(
                        where={k: file_path},
                        limit=1,
                        include=[],
                    ),
                )
                if results and results.get("ids"):
                    return True
            except Exception:
                continue
        return False

    async def get_stats(self) -> dict[str, Any]:
        if not self._collection:
            return {"total_chunks": 0, "total_documents": 0}
        loop = asyncio.get_running_loop()
        count = await loop.run_in_executor(None, self._collection.count)
        try:
            all_meta = await loop.run_in_executor(
                None, lambda: self._collection.get(include=["metadatas"])
            )
            sources = set()
            for m in all_meta["metadatas"]:
                src = m.get("filename", m.get("source", ""))
                if src:
                    sources.add(src)
            return {"total_chunks": count, "total_documents": len(sources)}
        except Exception:
            return {"total_chunks": count, "total_documents": 0}

    async def health(self) -> dict[str, Any]:
        """Readiness probe for the vector store (used by the gateway /ready)."""
        if not self._initialized or self._collection is None:
            return {"status": "error", "detail": "not initialized"}
        try:
            loop = asyncio.get_running_loop()
            count = await loop.run_in_executor(None, self._collection.count)
        except Exception as e:
            return {"status": "error", "detail": "query failed: %s" % str(e)[:150]}
        return {"status": "ok", "chunks": count}

    async def close(self) -> None:
        self._initialized = False
        logger.info("Vector DB closed")


def chunk_text(text: str, chunk_size: int = 512, chunk_overlap: int = 64) -> list[str]:
    if not text or not text.strip():
        return []
    if chunk_overlap >= chunk_size:
        # Sin esto el paso era 0 o negativo y el bucle no avanzaba nunca.
        chunk_overlap = 0
    text = re.sub(r"\s+", " ", text).strip()
    words = text.split()
    if len(words) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunk = words[start:end]
        chunks.append(" ".join(chunk))
        start += chunk_size - chunk_overlap
    return chunks


vector_db = VectorManager()
