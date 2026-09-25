"""Integration test: editing a note replaces its chunks (task 1.1).

Requires a live Ollama with the configured embedding model; skipped in CI,
where Ollama is not available.
"""

import os

import httpx
import pytest

from src.config import settings


def _ollama_reachable() -> bool:
    if os.environ.get("CI"):
        return False
    try:
        resp = httpx.get("%s/api/tags" % settings.ollama_host.rstrip("/"), timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _ollama_reachable(), reason="Live Ollama required for reindex integration test"
)


async def test_reindex_replaces_old_chunks(tmp_path, monkeypatch):
    from src.utils import vault_indexer as vi
    from src.utils import vector_manager as vml

    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "Nota.md"
    note.write_text("---\ntype: nota\n---\n## Seccion\nCONTENIDO_VIEJO_ALFA\n", encoding="utf-8")

    monkeypatch.setattr(settings, "vector_db_dir", str(tmp_path / "vdb"))
    manager = vml.VectorManager()
    try:
        await manager.initialize()
        monkeypatch.setattr(vml, "vector_db", manager)
        monkeypatch.setattr(vi, "VAULT_PATH", vault)
        monkeypatch.setattr(vi, "VAULT_NAME", "testvault")
        indexer = vi.VaultIndexer()
        rel = "Nota.md"

        await indexer.index_note(note)
        first = manager._collection.get(where={"note_path": rel}, include=["documents"])
        assert len(first["ids"]) > 0
        assert any("VIEJO" in (doc or "") for doc in first["documents"])

        note.write_text(
            "---\ntype: nota\n---\n## Seccion\nCONTENIDO_NUEVO_BETA\n", encoding="utf-8"
        )
        await indexer.index_note(note)
        second = manager._collection.get(where={"note_path": rel}, include=["documents"])
        docs = second["documents"]

        assert not any("VIEJO" in (doc or "") for doc in docs), "old chunks were not deleted"
        assert any("NUEVO" in (doc or "") for doc in docs)
        assert len(second["ids"]) == len(first["ids"]), "chunks duplicated instead of replaced"

        rel_path = "Nota.md"
        deleted = await indexer.delete_note_chunks(note)
        assert deleted == len(second["ids"])
        assert manager._collection.get(where={"note_path": rel_path})["ids"] == []
    finally:
        await manager.close()
