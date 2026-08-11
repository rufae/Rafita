import asyncio
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

os.environ["CHROMA_TELEMETRY_ENABLED"] = "false"

import chromadb
from chromadb.config import Settings as ChromaSettings

from src.config import settings
from src.logger import logger
from src.utils.telemetry import metrics


class OllamaEmbeddingFunction:
    def __init__(self):
        self._cache = {}

    def __call__(self, input: list[str]) -> list[list[float]]:
        results = []
        uncached_texts = []
        uncached_indices = []
        for i, text in enumerate(input):
            cached = self._cache.get(text)
            if cached is not None:
                results.append(cached)
            else:
                results.append(None)
                uncached_texts.append(text)
                uncached_indices.append(i)
        if uncached_texts:
            payload = {
                "model": settings.embedding_model,
                "input": uncached_texts,
            }
            resp = httpx.post(
                "%s/api/embed" % settings.ollama_host.rstrip("/"),
                json=payload,
                timeout=600.0,
            )
            resp.raise_for_status()
            data = resp.json()
            embeddings = data.get("embeddings", [])
            if len(embeddings) != len(uncached_texts):
                raise RuntimeError(
                    "Ollama returned %d embeddings for %d input texts"
                    % (len(embeddings), len(uncached_texts))
                )
            for idx, emb in zip(uncached_indices, embeddings):
                results[idx] = emb
                self._cache[uncached_texts[uncached_indices.index(idx)]] = emb
        return [r for r in results if r is not None]


class VectorManager:
    def __init__(self):
        self._client: chromadb.Client | None = None
        self._collection: chromadb.Collection | None = None
        self._embed_fn = OllamaEmbeddingFunction()
        self._initialized = False

    async def initialize(self) -> None:
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
            "source": file_path,
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
            existing = self._collection.get(ids=ids, include=[])
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
                lambda: self._collection.add(
                    ids=new_ids, documents=new_docs, metadatas=new_metas
                ),
            )
        logger.info(
            "Vector DB: added %d chunks from %s (%d already existed)",
            len(new_ids), file_path, len(ids) - len(new_ids),
        )
        return {
            "success": True,
            "chunks_added": len(new_ids),
            "chunks_total": len(ids),
            "message": "Indexados %d fragmentos de %s." % (len(new_ids), Path(file_path).name),
        }

    async def delete_by_source(self, note_path: str) -> int:
        if not self._collection:
            return 0
        try:
            loop = asyncio.get_running_loop()
            results = self._collection.get(
                where={"source": note_path},
                include=[],
            )
            ids_to_delete = results["ids"] if results else []
            if ids_to_delete:
                await loop.run_in_executor(
                    None,
                    lambda: self._collection.delete(ids=ids_to_delete),
                )
            return len(ids_to_delete)
        except Exception as e:
            logger.debug("delete_by_source error for %s: %s", note_path, e)
            return 0

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
                    await loop.run_in_executor(
                        None,
                        lambda c=chunk, cid=chunk_id: self._collection.add(
                            ids=[cid],
                            documents=[c["text"]],
                            metadatas=[c["metadata"]],
                        ),
                    )
                    added += 1
                    break
                except Exception as e:
                    if attempt < 2:
                        wait = 2 ** attempt
                        logger.warning(
                            "Embedding chunk %d/%d of '%s' failed (attempt %d/3): %s. Retrying in %ds...",
                            i + 1, len(chunks), note_path, attempt + 1, e, wait,
                        )
                        await asyncio.sleep(wait)
                    else:
                        logger.error(
                            "Chunk %d/%d of '%s' FAILED after 3 attempts: %s. Skipping.",
                            i + 1, len(chunks), note_path, e,
                        )
                        skipped += 1
        if added > 0:
            logger.info(
                "Vector DB: indexed %d chunks for %s (%d skipped)",
                added, note_path, skipped,
            )
        return {
            "success": True,
            "chunks_added": added,
            "chunks_skipped": skipped,
            "message": "Indexados %d fragmentos (%d omitidos)." % (added, skipped) if skipped else "Indexados %d fragmentos." % added,
        }

    async def query(self, query_text: str, top_k: int = 5, filter_tags: list[str] | None = None) -> dict[str, Any]:
        if not self._initialized:
            return {"success": False, "results": [], "message": "Vector DB no inicializada."}
        if self._collection.count() == 0:
            return {"success": True, "results": [], "message": "La base vectorial esta vacia."}
        loop = asyncio.get_running_loop()

        _t0 = time.perf_counter()

        where_filter = None
        if filter_tags and isinstance(filter_tags, list) and len(filter_tags) > 0:
            tag = filter_tags[0]
            where_filter = {"tags_str": tag}
            # Nota: solo filtra por el primer tag (exact match).
            # ChromaDB 0.5.0 no soporta $contains ni $in para strings.
            # Para filtro multi-tag se necesitaria ChromaDB >=0.6.0 o un campo de lista.

        try:
            results = await loop.run_in_executor(
                None,
                lambda: self._collection.query(
                    query_texts=[query_text],
                    n_results=min(top_k, self._collection.count()),
                    where=where_filter,
                ),
            )
        except Exception as e:
            logger.error("Vector query failed: %s", e)
            return {"success": False, "results": [], "message": "Error en busqueda: %s" % e}

        if not results or not results.get("documents") or not results["documents"][0]:
            return {"success": True, "results": [], "message": "Sin resultados relevantes."}

        formatted = []
        seen_notes = set()
        for i in range(len(results["documents"][0])):
            doc = results["documents"][0][i]
            meta = (results["metadatas"][0][i]) if results.get("metadatas") else {}
            distance = (results["distances"][0][i]) if results.get("distances") else 0.0
            note_path = meta.get("note_path", meta.get("source", "desconocido"))
            seen_notes.add(note_path)
            formatted.append({
                "content": doc[:800],
                "source": meta.get("filename", note_path),
                "note_path": note_path,
                "heading": meta.get("heading", ""),
                "obsidian_uri": meta.get("obsidian_uri", ""),
                "tags": meta.get("tags_str", "").split(",") if meta.get("tags_str") else [],
                "relevance": "%.3f" % max(0.0, 1.0 - distance / 2.0),
            })
        metrics.observe("embedding_query_latency", time.perf_counter() - _t0)
        return {
            "success": True,
            "results": formatted,
            "notes_found": list(seen_notes),
            "message": "Encontrados %d fragmentos relevantes en %d nota%s." % (
                len(formatted), len(seen_notes), "s" if len(seen_notes) != 1 else ""
            ),
        }

    async def document_exists(self, file_path: str) -> bool:
        if not self._collection:
            return False
        try:
            results = self._collection.get(
                where={"source": file_path},
                limit=1,
                include=[],
            )
            return len(results["ids"]) > 0 if results else False
        except Exception:
            return False

    async def get_stats(self) -> dict[str, Any]:
        if not self._collection:
            return {"total_chunks": 0, "total_documents": 0}
        count = self._collection.count()
        try:
            all_meta = self._collection.get(include=["metadatas"])
            sources = set()
            for m in all_meta["metadatas"]:
                src = m.get("filename", m.get("source", ""))
                if src:
                    sources.add(src)
            return {"total_chunks": count, "total_documents": len(sources)}
        except Exception:
            return {"total_chunks": count, "total_documents": 0}

    async def close(self) -> None:
        self._initialized = False
        logger.info("Vector DB closed")


def chunk_text(text: str, chunk_size: int = 512, chunk_overlap: int = 64) -> list[str]:
    if not text or not text.strip():
        return []
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
