"""Cobertura de vault_indexer.py: indexado de notas, auto-enlaces y watcher."""

import asyncio
from pathlib import Path

import pytest

import src.utils.vault_indexer as vault_indexer
from src.utils.vault_indexer import (
    VaultIndexer,
    _VaultEventHandler,
    build_obsidian_uri,
    estimate_tokens,
    parse_frontmatter,
)


class FakeVectorDB:
    def __init__(self, results=None):
        self.deleted = []
        self.indexed = []
        self.queried = []
        self.results = results or {"results": []}
        self.query_error = None
        self.index_error = None
        self._initialized = True

    async def delete_by_note_path(self, note_path):
        self.deleted.append(note_path)
        return 2

    async def index_chunks(self, chunks):
        if self.index_error:
            raise self.index_error
        self.indexed.append(chunks)
        return {"success": True, "chunks_added": len(chunks)}

    async def query(self, text, top_k=6, apply_threshold=True):
        self.queried.append((text, top_k, apply_threshold))
        if self.query_error:
            raise self.query_error
        return self.results


NOTE_WITH_FM = """---
type: nota
tags: [finanzas, "Año nuevo"]
updated: 2026-01-01
---
# Presupuesto

Los gastos de este mes.
"""

NOTE_NO_FM = """# Sin frontmatter

Solo texto.
"""


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(vault_indexer, "VAULT_PATH", tmp_path)
    monkeypatch.setattr(vault_indexer, "_ignored_dirs", lambda: {"archivos"})
    return tmp_path


# ---------------------------------------------------------------------------
# index_note
# ---------------------------------------------------------------------------


async def test_index_note_stores_tag_flags(vault, monkeypatch):
    fake = FakeVectorDB()
    monkeypatch.setattr("src.utils.vector_manager.vector_db", fake)
    note = vault / "presupuesto.md"
    note.write_text(NOTE_WITH_FM, encoding="utf-8")

    result = await VaultIndexer().index_note(note)

    assert result["success"] is True
    assert result["note_type"] == "nota"
    assert result["chunks_added"] == 1
    assert fake.deleted == ["presupuesto.md"]
    chunk = fake.indexed[0][0]
    meta = chunk["metadata"]
    assert meta["note_path"] == "presupuesto.md"
    assert meta["filename"] == "presupuesto.md"
    assert meta["heading"] == "Presupuesto"
    assert meta["tags_str"] == "finanzas,Año nuevo"
    assert meta["tag__finanzas"] == 1
    assert meta["tag__ano_nuevo"] == 1
    assert meta["obsidian_uri"].startswith("obsidian://open")


async def test_index_note_without_chunks(vault, monkeypatch):
    fake = FakeVectorDB()
    monkeypatch.setattr("src.utils.vector_manager.vector_db", fake)
    note = vault / "vacia.md"
    note.write_text("---\ntype: nota\n---\n\n", encoding="utf-8")

    result = await VaultIndexer().index_note(note)
    assert result["success"] is True
    assert result["chunks_added"] == 0
    assert fake.indexed == []
    assert fake.deleted == []


async def test_index_note_unreadable_reports_failure(vault, monkeypatch):
    monkeypatch.setattr("src.utils.vector_manager.vector_db", FakeVectorDB())
    result = await VaultIndexer().index_note(vault / "no-existe.md")
    assert result["success"] is False


async def test_index_note_autolinks_related_notes(vault, monkeypatch):
    fake = FakeVectorDB(
        results={"results": [{"note_path": "otra.md"}, {"note_path": "presupuesto.md"}]}
    )
    monkeypatch.setattr("src.utils.vector_manager.vector_db", fake)
    note = vault / "presupuesto.md"
    note.write_text(NOTE_WITH_FM, encoding="utf-8")

    result = await VaultIndexer().index_note(note)

    assert result["chunks_added"] == 1
    content = note.read_text(encoding="utf-8")
    assert "[[otra.md]]" in content
    assert content.startswith("---")
    assert "related:" in content


async def test_index_note_autolink_skips_existing_and_bad_fm(vault, monkeypatch):
    fake = FakeVectorDB(results={"results": [{"note_path": "otra.md"}]})
    monkeypatch.setattr("src.utils.vector_manager.vector_db", fake)
    note = vault / "presupuesto.md"
    note.write_text("---\nrelated: ['[[otra.md]]']\n---\ntexto", encoding="utf-8")

    result = await VaultIndexer().index_note(note)
    assert result["chunks_added"] == 1
    content = note.read_text(encoding="utf-8")
    assert content.count("[[otra.md]]") == 1

    other = vault / "sin-fm.md"
    other.write_text(NOTE_NO_FM, encoding="utf-8")
    result = await VaultIndexer().index_note(other)
    assert result["chunks_added"] == 1
    assert "[[" not in other.read_text(encoding="utf-8")


async def test_index_note_autolink_swallows_query_errors(vault, monkeypatch):
    fake = FakeVectorDB()
    fake.query_error = RuntimeError("vector caido")
    monkeypatch.setattr("src.utils.vector_manager.vector_db", fake)
    note = vault / "presupuesto.md"
    note.write_text(NOTE_WITH_FM, encoding="utf-8")

    result = await VaultIndexer().index_note(note)
    assert result["chunks_added"] == 1
    assert "[[" not in note.read_text(encoding="utf-8")


async def test_auto_link_replaces_related_line(vault, monkeypatch):
    fake = FakeVectorDB(results={"results": [{"note_path": "nueva.md"}]})
    monkeypatch.setattr("src.utils.vector_manager.vector_db", fake)
    note = vault / "con-rel.md"
    note.write_text("---\nrelated: algo-raro\n---\ntexto", encoding="utf-8")

    linked = await VaultIndexer()._auto_link_related(note, "con-rel.md", "texto")
    assert linked == 1
    content = note.read_text(encoding="utf-8")
    assert "[[nueva.md]]" in content
    assert "related:" in content


# ---------------------------------------------------------------------------
# index_all / delete_note_chunks
# ---------------------------------------------------------------------------


async def test_index_all_ignores_dirs_and_hidden(vault, monkeypatch):
    fake = FakeVectorDB()
    monkeypatch.setattr("src.utils.vector_manager.vector_db", fake)
    (vault / "a.md").write_text(NOTE_WITH_FM, encoding="utf-8")
    (vault / "archivos").mkdir()
    (vault / "archivos" / "b.md").write_text(NOTE_WITH_FM, encoding="utf-8")
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "c.md").write_text(NOTE_WITH_FM, encoding="utf-8")
    (vault / "notas").mkdir()
    (vault / "notas" / "d.md").write_text(NOTE_WITH_FM, encoding="utf-8")

    result = await VaultIndexer().index_all()

    assert result["success"] is True
    assert result["notes_indexed"] == 2
    assert result["total_chunks"] == 2
    assert result["failures"] == 0
    indexed_names = {c[0]["metadata"]["filename"] for c in fake.indexed}
    assert indexed_names == {"a.md", "d.md"}


async def test_index_all_counts_failures(vault, monkeypatch):
    monkeypatch.setattr("src.utils.vector_manager.vector_db", FakeVectorDB())
    (vault / "a.md").write_text(NOTE_WITH_FM, encoding="utf-8")
    (vault / "b.md").write_text(NOTE_WITH_FM, encoding="utf-8")

    indexer = VaultIndexer()
    real_index = indexer.index_note

    async def fake_index(path):
        if path.name == "b.md":
            raise RuntimeError("nota corrupta")
        return await real_index(path)

    monkeypatch.setattr(indexer, "index_note", fake_index)
    result = await indexer.index_all()
    assert result["notes_indexed"] == 1
    assert result["failures"] == 1


async def test_delete_note_chunks_uses_relative_path(vault, monkeypatch):
    fake = FakeVectorDB()
    monkeypatch.setattr("src.utils.vector_manager.vector_db", fake)
    deleted = await VaultIndexer().delete_note_chunks(vault / "carpeta" / "nota.md")
    assert deleted == 2
    assert fake.deleted == ["carpeta/nota.md"]


# ---------------------------------------------------------------------------
# Eventos del watcher
# ---------------------------------------------------------------------------


class _Event:
    def __init__(self, path, is_directory=False):
        self.src_path = str(path)
        self.is_directory = is_directory


def test_on_file_event_filters_and_queues(vault):
    indexer = VaultIndexer()
    indexer._on_file_event(_Event(vault / "sub", is_directory=True))
    indexer._on_file_event(_Event(vault / "imagen.png"))
    indexer._on_file_event(_Event(vault / "archivos" / "oculta.md"))
    indexer._on_file_event(_Event(vault / ".trash" / "oculta.md"))
    indexer._on_file_event(_Event(Path("/fuera-del-vault.md")))
    assert indexer._pending_paths == {}

    indexer._on_file_event(_Event(vault / "notas" / "viva.md"))
    assert list(indexer._pending_paths) == [str(Path("notas") / "viva.md")]


def test_event_handler_delegates(vault):
    indexer = VaultIndexer()
    handler = _VaultEventHandler(indexer)
    event = _Event(vault / "x.md")
    handler.on_created(event)
    handler.on_modified(event)
    handler.on_deleted(event)
    handler.on_moved(event)
    assert len(indexer._pending_paths) == 1


# ---------------------------------------------------------------------------
# Debounce y ciclo de vida
# ---------------------------------------------------------------------------


async def test_debounce_loop_indexes_and_deletes(vault, monkeypatch):
    monkeypatch.setattr(vault_indexer, "DEBOUNCE_SECONDS", 0.0)
    indexer = VaultIndexer()
    shutdown = asyncio.Event()
    indexer._shutdown_event = shutdown

    existe = vault / "existe.md"
    existe.write_text(NOTE_WITH_FM, encoding="utf-8")
    indexer._pending_paths = {"existe.md": 0.0, "borrado.md": 0.0}

    indexed = []
    deleted = []

    async def fake_index(path):
        indexed.append(path)

    async def fake_delete(path):
        deleted.append(path)
        return 0

    monkeypatch.setattr(indexer, "index_note", fake_index)
    monkeypatch.setattr(indexer, "delete_note_chunks", fake_delete)

    task = asyncio.create_task(indexer._debounce_loop())
    await asyncio.sleep(0.1)
    shutdown.set()
    await asyncio.wait_for(task, timeout=3)

    assert indexed == [existe]
    assert deleted == [vault / "borrado.md"]


async def test_debounce_loop_keeps_recent_paths(vault, monkeypatch):
    monkeypatch.setattr(vault_indexer, "DEBOUNCE_SECONDS", 60.0)
    indexer = VaultIndexer()
    shutdown = asyncio.Event()
    indexer._shutdown_event = shutdown
    indexer._pending_paths = {"reciente.md": 10**12}

    task = asyncio.create_task(indexer._debounce_loop())
    await asyncio.sleep(0.1)
    assert "reciente.md" in indexer._pending_paths
    shutdown.set()
    await asyncio.wait_for(task, timeout=3)


async def test_debounce_loop_swallows_index_errors(vault, monkeypatch):
    monkeypatch.setattr(vault_indexer, "DEBOUNCE_SECONDS", 0.0)
    indexer = VaultIndexer()
    shutdown = asyncio.Event()
    indexer._shutdown_event = shutdown
    (vault / "existe.md").write_text(NOTE_WITH_FM, encoding="utf-8")
    indexer._pending_paths = {"existe.md": 0.0, "borrado.md": 0.0}

    async def fake_index(path):
        raise RuntimeError("fallo")

    async def fake_delete(path):
        raise RuntimeError("fallo")

    monkeypatch.setattr(indexer, "index_note", fake_index)
    monkeypatch.setattr(indexer, "delete_note_chunks", fake_delete)

    task = asyncio.create_task(indexer._debounce_loop())
    await asyncio.sleep(0.1)
    shutdown.set()
    await asyncio.wait_for(task, timeout=3)


class FakeObserver:
    instances = []

    def __init__(self):
        self.started = False
        self.stopped = False
        self.joined = False
        self.scheduled = []
        FakeObserver.instances.append(self)

    def schedule(self, handler, path, recursive=True):
        self.scheduled.append((handler, path, recursive))

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        self.joined = True


async def test_start_and_stop_lifecycle(vault, monkeypatch):
    monkeypatch.setattr(vault_indexer, "Observer", FakeObserver)
    monkeypatch.setattr("src.utils.vector_manager.vector_db", FakeVectorDB())
    indexer = VaultIndexer()
    shutdown = asyncio.Event()

    await indexer.start(shutdown)
    observer = FakeObserver.instances[-1]
    assert observer.started
    assert observer.scheduled and observer.scheduled[0][1] == str(vault)
    assert indexer._task is not None

    await indexer.stop()
    assert observer.stopped and observer.joined
    assert indexer._task is None
    assert indexer._observer is None


async def test_start_warns_without_vector_db(vault, monkeypatch):
    monkeypatch.setattr(vault_indexer, "Observer", FakeObserver)
    fake = FakeVectorDB()
    fake._initialized = False
    monkeypatch.setattr("src.utils.vector_manager.vector_db", fake)
    indexer = VaultIndexer()
    shutdown = asyncio.Event()
    await indexer.start(shutdown)
    await indexer.stop()


async def test_stop_without_start_is_noop():
    indexer = VaultIndexer()
    await indexer.stop()


# ---------------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------------


def test_parse_frontmatter_broken_yaml_and_non_dict():
    metadata, body = parse_frontmatter("---\n[1, 2\n---\ncuerpo")
    assert metadata == {}
    assert "cuerpo" in body

    metadata, body = parse_frontmatter("---\n- uno\n- dos\n---\ncuerpo")
    assert metadata == {}
    assert "cuerpo" in body

    metadata, body = parse_frontmatter("---\nsin cierre")
    assert metadata == {}
    assert body == "---\nsin cierre"


def test_estimate_tokens_counts_words():
    assert estimate_tokens("") == 0
    assert estimate_tokens("uno dos tres") == 4


def test_build_obsidian_uri_outside_vault(vault, monkeypatch):
    monkeypatch.setattr(vault_indexer, "VAULT_PATH", vault)
    assert build_obsidian_uri(vault / "nota con espacio.md").endswith("nota%20con%20espacio.md")
    assert build_obsidian_uri(Path("/otro/lado.md")) == ""
