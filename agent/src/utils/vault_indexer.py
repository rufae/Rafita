import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from src.config import settings
from src.logger import logger

VAULT_PATH = Path(settings.obsidian_vault_dir)
VAULT_NAME = settings.obsidian_vault_name
IGNORED_DIRS = {".obsidian", ".git", ".trash", "Documentos_Indexados", "templates"}
DEBOUNCE_SECONDS = 2.0

TOKENS_PER_WORD_ES = 1.4


def estimate_tokens(text: str) -> int:
    words = len(text.split())
    return int(words * TOKENS_PER_WORD_ES)


def parse_frontmatter(content: str) -> tuple:
    if not content.startswith("---"):
        return {}, content
    second_delim = content.find("---", 3)
    if second_delim == -1:
        return {}, content
    fm_text = content[3:second_delim].strip()
    body = content[second_delim + 3:].strip()
    try:
        metadata = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    return metadata, body


def chunk_by_headings(body: str, max_tokens: int = 500, overlap_tokens: int = 50) -> list[dict[str, Any]]:
    if not body or not body.strip():
        return []

    lines = body.split("\n")
    sections = []
    current_heading = ""
    current_heading_path = ""
    current_lines = []

    heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.UNICODE)

    for line in lines:
        match = heading_pattern.match(line)
        if match:
            if current_lines:
                sections.append({
                    "heading": current_heading,
                    "heading_path": current_heading_path,
                    "text": "\n".join(current_lines).strip(),
                })
            level = len(match.group(1))
            heading_text = match.group(2).strip()
            current_heading = "#" * level + " " + heading_text
            if level == 1:
                current_heading_path = heading_text
            else:
                prefix = " > ".join(current_heading_path.split(" > ")[:-1]) if " > " in current_heading_path else current_heading_path.split(" > ")[0] if current_heading_path else ""
                current_heading_path = (prefix + " > " + heading_text) if prefix else heading_text
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections.append({
            "heading": current_heading,
            "heading_path": current_heading_path or "",
            "text": "\n".join(current_lines).strip(),
        })

    if not sections:
        text = body.strip()
        if estimate_tokens(text) <= max_tokens:
            return [{"heading": "", "heading_path": "", "text": text}]
        return _split_long_section("", "", text, max_tokens, overlap_tokens)

    chunks = []
    for section in sections:
        section_text = section["text"]
        if not section_text:
            continue
        tokens = estimate_tokens(section_text)
        if tokens <= max_tokens:
            chunks.append({
                "heading": section["heading"],
                "heading_path": section["heading_path"],
                "text": section_text,
            })
        else:
            sub_chunks = _split_long_section(
                section["heading"], section["heading_path"],
                section_text, max_tokens, overlap_tokens,
            )
            chunks.extend(sub_chunks)

    return chunks


def _split_long_section(heading: str, heading_path: str, text: str, max_tokens: int, overlap_tokens: int) -> list[dict[str, Any]]:
    words = text.split()
    max_words = int(max_tokens / TOKENS_PER_WORD_ES)
    overlap_words = int(overlap_tokens / TOKENS_PER_WORD_ES)
    overlap_words = max(overlap_words, 1)
    max_words = max(max_words, overlap_words + 1)

    if len(words) <= max_words:
        return [{"heading": heading, "heading_path": heading_path, "text": text}]

    chunks = []
    start = 0
    while start < len(words):
        end = min(start + max_words, len(words))
        chunk_words = words[start:end]
        chunks.append({
            "heading": heading,
            "heading_path": heading_path,
            "text": " ".join(chunk_words),
        })
        if end >= len(words):
            break
        start = end - overlap_words

    return chunks


def build_obsidian_uri(note_path: Path) -> str:
    try:
        rel = note_path.relative_to(VAULT_PATH)
        encoded = str(rel).replace("\\", "/").replace(" ", "%20")
        return "obsidian://open?vault=%s&file=%s" % (VAULT_NAME, encoded)
    except ValueError:
        return ""


class VaultIndexer:
    def __init__(self):
        self._observer: Observer | None = None
        self._task: asyncio.Task | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._debounce_tasks: dict[str, asyncio.Task] = {}
        self._pending_paths: dict[str, float] = {}

    async def index_note(self, note_path: Path) -> dict[str, Any]:
        from src.utils.vector_manager import vector_db

        rel_path = str(note_path.relative_to(VAULT_PATH)) if str(note_path).startswith(str(VAULT_PATH)) else note_path.name
        try:
            content = note_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning("Cannot read %s: %s", rel_path, e)
            return {"success": False, "message": str(e)}

        metadata, body = parse_frontmatter(content)
        chunks_data = chunk_by_headings(body, max_tokens=500, overlap_tokens=50)

        if not chunks_data:
            logger.debug("No chunkable content in %s", rel_path)
            return {"success": True, "chunks_added": 0, "note_path": rel_path}

        await vector_db.delete_by_source(rel_path)

        chunks_to_index = []
        for chunk in chunks_data:
            chunk_meta = {
                "note_path": rel_path,
                "filename": note_path.name,
                "heading": chunk["heading_path"] or chunk["heading"],
                "tags_str": ",".join(metadata.get("tags", [])) if isinstance(metadata.get("tags"), list) else "",
                "note_type": str(metadata.get("type", "")),
                "status": str(metadata.get("status", "")),
                "updated_at": str(metadata.get("updated", "")),
                "obsidian_uri": build_obsidian_uri(note_path),
                "indexed_at": datetime.now().isoformat(),
            }
            chunks_to_index.append({"text": chunk["text"], "metadata": chunk_meta})

        result = await vector_db.index_chunks(chunks_to_index)
        logger.info(
            "VaultIndexer: %s -> %d chunks (%s)",
            rel_path, result.get("chunks_added", 0), metadata.get("type", "nota"),
        )

        if result.get("chunks_added", 0) > 0:
            linked = await self._auto_link_related(note_path, rel_path, chunks_data[0]["text"][:500])
            if linked > 0:
                logger.info("VaultIndexer: %s -> auto-linked to %d notes", rel_path, linked)

        return {
            "success": True,
            "chunks_added": result.get("chunks_added", 0),
            "note_path": rel_path,
            "note_type": metadata.get("type", ""),
            "message": "Indexada %s: %d fragmentos." % (rel_path, result.get("chunks_added", 0)),
        }

    async def _auto_link_related(self, note_path: Path, rel_path: str, sample_text: str) -> int:
        from src.utils.vector_manager import vector_db

        try:
            results = await vector_db.query(sample_text, top_k=6)
        except Exception:
            return 0

        related_paths = set()
        for r in results.get("results", []):
            found = r.get("note_path", "")
            if found and found != rel_path:
                related_paths.add(found)

        if not related_paths:
            return 0

        content = note_path.read_text(encoding="utf-8", errors="replace")
        metadata, body = parse_frontmatter(content)

        existing = metadata.get("related", [])
        if not isinstance(existing, list):
            existing = []
        existing_paths = set()
        for item in existing:
            if isinstance(item, str):
                match = re.search(r'\[\[([^]]+)\]\]', item)
                if match:
                    p = match.group(1)
                    existing_paths.add(p)
                    existing_paths.add(Path(p).name)
                    existing_paths.add(Path(p).stem)

        new_links = []
        for p in related_paths:
            name = Path(p).name
            stem = Path(p).stem
            if p not in existing_paths and name not in existing_paths and stem not in existing_paths:
                new_links.append("[[%s]]" % p)
                existing_paths.add(p)
                existing_paths.add(name)
                existing_paths.add(stem)

        if not new_links:
            return 0

        if existing:
            updated_related = existing + new_links
        else:
            updated_related = new_links

        fm_text = ""
        if content.startswith("---"):
            second = content.find("---", 3)
            if second != -1:
                fm_text = content[3:second]
        if not fm_text:
            return 0

        lines = fm_text.split("\n")
        new_lines = []
        replaced = False
        for line in lines:
            if line.strip().startswith("related:"):
                new_lines.append("related: %s" % str(updated_related))
                replaced = True
            else:
                new_lines.append(line)
        if not replaced:
            new_lines.append("related: %s" % str(updated_related))

        new_fm = "\n".join(new_lines)
        new_content = "---\n%s\n---\n%s" % (new_fm, body)
        note_path.write_text(new_content, encoding="utf-8")

        return len(new_links)

    async def index_all(self) -> dict[str, Any]:

        md_files = []
        for md_file in VAULT_PATH.rglob("*.md"):
            parts = md_file.relative_to(VAULT_PATH).parts
            if any(d in IGNORED_DIRS or d.startswith(".") for d in parts):
                continue
            md_files.append(md_file)

        total_indexed = 0
        total_chunks = 0
        failures = 0

        for md_file in md_files:
            try:
                result = await self.index_note(md_file)
                if result["success"]:
                    total_indexed += 1
                    total_chunks += result.get("chunks_added", 0)
                else:
                    failures += 1
            except Exception as e:
                logger.warning("Backfill error for %s: %s", md_file.name, e)
                failures += 1

        logger.info(
            "Backfill complete: %d notas, %d chunks, %d fallos",
            total_indexed, total_chunks, failures,
        )
        return {
            "success": True,
            "notes_indexed": total_indexed,
            "total_chunks": total_chunks,
            "failures": failures,
            "message": "Backfill: %d notas indexadas (%d chunks), %d fallos." % (
                total_indexed, total_chunks, failures,
            ),
        }

    async def delete_note_chunks(self, note_path: Path) -> int:
        from src.utils.vector_manager import vector_db
        rel_path = str(note_path.relative_to(VAULT_PATH))
        deleted = await vector_db.delete_by_source(rel_path)
        if deleted:
            logger.info("VaultIndexer: deleted %d chunks for %s", deleted, rel_path)
        return deleted

    def _on_file_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src_path = Path(event.src_path)
        if src_path.suffix != ".md":
            return
        try:
            rel = src_path.relative_to(VAULT_PATH)
        except ValueError:
            return
        parts = rel.parts
        if any(d in IGNORED_DIRS or d.startswith(".") for d in parts):
            return

        rel_str = str(rel)
        now = datetime.now().timestamp()

        self._pending_paths[rel_str] = now

    async def _debounce_loop(self) -> None:
        while not self._shutdown_event.is_set():
            now = datetime.now().timestamp()
            to_process = []
            still_pending = {}
            for path_str, ts in list(self._pending_paths.items()):
                if now - ts >= DEBOUNCE_SECONDS:
                    to_process.append(path_str)
                else:
                    still_pending[path_str] = ts
            self._pending_paths = still_pending

            for path_str in to_process:
                note_path = VAULT_PATH / path_str
                if note_path.exists():
                    try:
                        await self.index_note(note_path)
                    except Exception as e:
                        logger.warning("Debounced index error %s: %s", path_str, e)
                else:
                    try:
                        await self.delete_note_chunks(note_path)
                    except Exception as e:
                        logger.warning("Debounced delete error %s: %s", path_str, e)

            await asyncio.sleep(0.5)

    async def start(self, shutdown_event: asyncio.Event) -> None:
        self._shutdown_event = shutdown_event

        from src.utils.vector_manager import vector_db
        if not vector_db._initialized:
            logger.warning("Vector DB not initialized, vault indexer will start without persistence")

        observer = Observer()
        handler = _VaultEventHandler(self)
        observer.schedule(handler, str(VAULT_PATH), recursive=True)
        observer.start()
        self._observer = observer
        logger.info("VaultIndexer watcher started on %s", VAULT_PATH)

        self._task = asyncio.create_task(self._debounce_loop())
        logger.info("VaultIndexer debounce loop started (%.1fs)", DEBOUNCE_SECONDS)

    async def stop(self) -> None:
        if self._observer:
            self._observer.stop()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._observer:
            self._observer.join(timeout=5)
            self._observer = None
        logger.info("VaultIndexer stopped")


class _VaultEventHandler(FileSystemEventHandler):
    def __init__(self, indexer: VaultIndexer):
        super().__init__()
        self._indexer = indexer

    def on_created(self, event: FileSystemEvent) -> None:
        self._indexer._on_file_event(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._indexer._on_file_event(event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._indexer._on_file_event(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._indexer._on_file_event(event)
