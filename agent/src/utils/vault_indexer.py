import asyncio
import json
import re
import threading
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from src.config import settings
from src.logger import logger
from src.vault_config import get_taxonomy

VAULT_PATH = Path(settings.obsidian_vault_dir)
VAULT_NAME = settings.obsidian_vault_name
DEBOUNCE_SECONDS = 2.0

# Documentos no-md que el watcher procesa (tarea 10: resumen+etiquetado
# automaticos al caer en la boveda). Extensiones con extractor de texto.
INTAKE_EXTENSIONS = {".pdf", ".docx", ".txt", ".csv"}
INTAKE_MAX_BYTES = 10 * 1024 * 1024


def _ignored_dirs() -> set[str]:
    return set(get_taxonomy().ignored_dirs)


def _norm_text(text: str) -> str:
    """Normaliza para comparar entidades del LLM con títulos del baúl."""
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", sin_acentos).strip().casefold()


def _vault_titles() -> dict[str, str]:
    """Mapa normalizado -> stem real de cada nota .md del baúl.

    Solo se usa para convertir entidades del LLM en `[[wikilinks]]`
    válidos: una entidad sin nota equivalente queda como texto plano,
    nunca como enlace fantasma.
    """
    if not VAULT_PATH.exists():
        return {}
    ignored = _ignored_dirs()
    titulos: dict[str, str] = {}
    for md in VAULT_PATH.rglob("*.md"):
        parts = md.relative_to(VAULT_PATH).parts
        if any(d in ignored or d.startswith(".") for d in parts):
            continue
        titulos.setdefault(_norm_text(md.stem), md.stem)
    return titulos


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
    body = content[second_delim + 3 :].strip()
    try:
        metadata = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    return metadata, body


def chunk_by_headings(
    body: str, max_tokens: int = 500, overlap_tokens: int = 50
) -> list[dict[str, Any]]:
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
                sections.append(
                    {
                        "heading": current_heading,
                        "heading_path": current_heading_path,
                        "text": "\n".join(current_lines).strip(),
                    }
                )
            level = len(match.group(1))
            heading_text = match.group(2).strip()
            current_heading = "#" * level + " " + heading_text
            if level == 1:
                current_heading_path = heading_text
            else:
                prefix = (
                    " > ".join(current_heading_path.split(" > ")[:-1])
                    if " > " in current_heading_path
                    else current_heading_path.split(" > ")[0]
                    if current_heading_path
                    else ""
                )
                current_heading_path = (prefix + " > " + heading_text) if prefix else heading_text
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections.append(
            {
                "heading": current_heading,
                "heading_path": current_heading_path or "",
                "text": "\n".join(current_lines).strip(),
            }
        )

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
            chunks.append(
                {
                    "heading": section["heading"],
                    "heading_path": section["heading_path"],
                    "text": section_text,
                }
            )
        else:
            sub_chunks = _split_long_section(
                section["heading"],
                section["heading_path"],
                section_text,
                max_tokens,
                overlap_tokens,
            )
            chunks.extend(sub_chunks)

    return chunks


def _split_long_section(
    heading: str, heading_path: str, text: str, max_tokens: int, overlap_tokens: int
) -> list[dict[str, Any]]:
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
        chunks.append(
            {
                "heading": heading,
                "heading_path": heading_path,
                "text": " ".join(chunk_words),
            }
        )
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
        self._pending_lock = threading.Lock()

    async def index_note(self, note_path: Path) -> dict[str, Any]:
        from src.utils.vector_manager import (
            MAX_TAG_FLAGS,
            TAG_FLAG_PREFIX,
            normalize_tag,
            vector_db,
        )

        rel_path = (
            str(note_path.relative_to(VAULT_PATH))
            if str(note_path).startswith(str(VAULT_PATH))
            else note_path.name
        )
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

        await vector_db.delete_by_note_path(rel_path)

        # Scalar flags per tag: Chroma metadata does not accept lists and has no
        # metadata $contains, so tag filtering in the query needs one flag per
        # tag (task 1.6).
        tag_flags: dict[str, Any] = {}
        raw_tags = metadata.get("tags", [])
        if isinstance(raw_tags, list):
            for tag in raw_tags[:MAX_TAG_FLAGS]:
                norm = normalize_tag(str(tag))
                if norm:
                    tag_flags[TAG_FLAG_PREFIX + norm] = 1

        chunks_to_index = []
        for chunk in chunks_data:
            chunk_meta = {
                "note_path": rel_path,
                "filename": note_path.name,
                "heading": chunk["heading_path"] or chunk["heading"],
                "tags_str": ",".join(raw_tags) if isinstance(raw_tags, list) else "",
                "note_type": str(metadata.get("type", "")),
                "status": str(metadata.get("status", "")),
                "updated_at": str(metadata.get("updated", "")),
                "obsidian_uri": build_obsidian_uri(note_path),
                "indexed_at": datetime.now().isoformat(),
            }
            chunk_meta.update(tag_flags)
            chunks_to_index.append({"text": chunk["text"], "metadata": chunk_meta})

        result = await vector_db.index_chunks(chunks_to_index)
        logger.info(
            "VaultIndexer: %s -> %d chunks (%s)",
            rel_path,
            result.get("chunks_added", 0),
            metadata.get("type", "nota"),
        )

        if result.get("chunks_added", 0) > 0:
            linked = await self._auto_link_related(
                note_path, rel_path, chunks_data[0]["text"][:500]
            )
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
            # Linking wants any semantic neighbour, not only high-relevance hits.
            results = await vector_db.query(sample_text, top_k=6, apply_threshold=False)
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
                match = re.search(r"\[\[([^]]+)\]\]", item)
                if match:
                    p = match.group(1)
                    existing_paths.add(p)
                    existing_paths.add(Path(p).name)
                    existing_paths.add(Path(p).stem)

        new_links = []
        for p in related_paths:
            name = Path(p).name
            stem = Path(p).stem
            if (
                p not in existing_paths
                and name not in existing_paths
                and stem not in existing_paths
            ):
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
        ignored = _ignored_dirs()
        for md_file in VAULT_PATH.rglob("*.md"):
            parts = md_file.relative_to(VAULT_PATH).parts
            if any(d in ignored or d.startswith(".") for d in parts):
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
            total_indexed,
            total_chunks,
            failures,
        )
        return {
            "success": True,
            "notes_indexed": total_indexed,
            "total_chunks": total_chunks,
            "failures": failures,
            "message": "Backfill: %d notas indexadas (%d chunks), %d fallos."
            % (
                total_indexed,
                total_chunks,
                failures,
            ),
        }

    async def delete_note_chunks(self, note_path: Path) -> int:
        from src.utils.vector_manager import vector_db

        rel_path = str(note_path.relative_to(VAULT_PATH))
        deleted = await vector_db.delete_by_note_path(rel_path)
        if deleted:
            logger.info("VaultIndexer: deleted %d chunks for %s", deleted, rel_path)
        return deleted

    def _on_file_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src_path = Path(event.src_path)
        if src_path.suffix != ".md" and src_path.suffix.lower() not in INTAKE_EXTENSIONS:
            return
        try:
            rel = src_path.relative_to(VAULT_PATH)
        except ValueError:
            return
        parts = rel.parts
        if any(d in _ignored_dirs() or d.startswith(".") for d in parts):
            return

        rel_str = str(rel)
        now = datetime.now().timestamp()

        with self._pending_lock:
            self._pending_paths[rel_str] = now

    async def intake_document(self, doc_path: Path) -> dict[str, Any]:
        """Crea la nota compañera de un documento no-md caído en la bóveda.

        Flujo del ítem 10: extrae el texto (PDF/DOCX/TXT/CSV), escribe
        `<nombre>.md` al lado con `## Archivo original` y `## Contenido
        extraido`; la nota nueva dispara después su indexación y el
        enriquecimiento con IA (`_enrich_note`). Best-effort e idempotente:
        si ya existe la nota, no la pisa.
        """
        from src.handlers.files import _create_companion_note, _extract_text_from_file

        note_path = doc_path.with_suffix(".md")
        if note_path.exists():
            return {"success": True, "skipped": "ya existe %s" % note_path.name}
        try:
            if doc_path.stat().st_size > INTAKE_MAX_BYTES:
                return {"success": False, "message": "archivo demasiado grande"}
        except OSError:
            return {"success": False, "message": "archivo no accesible"}
        texto = _extract_text_from_file(doc_path)
        creada = _create_companion_note(VAULT_PATH, doc_path, texto, "documento", [], "")
        if not creada:
            return {"success": False, "message": "no se pudo crear la nota"}
        logger.info(
            "VaultIndexer: documento %s -> nota %s (%d chars extraidos)",
            doc_path.name,
            creada.name,
            len(texto),
        )
        return {"success": True, "note": creada.name, "chars": len(texto)}

    async def _enrich_note(self, note_path: Path) -> bool:
        """Rellena `tags`, `## Resumen` y `entities` de notas nuevas (ítem 10).

        Solo actúa si faltan etiquetas, resumen o entidades; si el LLM no
        responde, la nota queda como está (el siguiente evento lo reintentará).
        Las entidades que coinciden con una nota existente del baúl se
        guardan como `[[wikilinks]]` (GraphRAG-lite); el resto, como texto
        plano. La escritura dispara un nuevo evento, y como ya no falta
        nada, no hay bucle.
        """
        try:
            content = note_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return False
        metadata, body = parse_frontmatter(content)
        tags = metadata.get("tags") if isinstance(metadata.get("tags"), list) else []
        tiene_resumen = bool(re.search(r"^##\s+Resumen\s*$", body, re.MULTILINE))
        tiene_entities = isinstance(metadata.get("entities"), list)
        if tags and tiene_resumen and tiene_entities:
            return False
        muestra = body[:3000].strip()
        if not muestra:
            return False
        try:
            from src.ollama_client import llm

            respuesta = await asyncio.wait_for(
                llm.chat(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Eres el catalogador de un segundo cerebro personal. "
                                "Respondes SOLO con JSON."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                "Etiqueta, resume y extrae entidades de esta nota "
                                "en espanol.\n\n"
                                "Responde SOLO con JSON:\n"
                                '{"tags": ["etiqueta1", "etiqueta2"], '
                                '"summary": "resumen de 2 frases", '
                                '"entities": ["Nombre persona", "Nombre proyecto"]}\n'
                                "entities: maximo 8 nombres propios relevantes "
                                "(personas, proyectos, lugares, organizaciones).\n\n"
                                "Nota:\n%s" % muestra
                            ),
                        },
                    ],
                    temperature=0.3,
                    max_tokens=400,
                ),
                timeout=90.0,
            )
        except Exception as e:
            logger.debug("Enrich %s: LLM no disponible (%s)", note_path.name, str(e)[:80])
            return False
        datos: dict[str, Any] = {}
        match = re.search(r"\{.*\}", respuesta or "", re.DOTALL)
        if match:
            try:
                cargado = json.loads(match.group())
                if isinstance(cargado, dict):
                    datos = cargado
            except (json.JSONDecodeError, ValueError):
                datos = {}
        nuevos_tags = (
            [str(t).strip() for t in datos.get("tags", []) if str(t).strip()]
            if isinstance(datos.get("tags"), list)
            else []
        )
        resumen = str(datos.get("summary") or "").strip()
        crudos = datos.get("entities")
        nuevos_entities = (
            [e.strip() for e in crudos if isinstance(e, str) and e.strip()]
            if isinstance(crudos, list)
            else []
        )
        if not nuevos_tags and not resumen and not nuevos_entities:
            return False
        if not nuevos_tags and tags:
            nuevos_tags = tags
        entidades_nuevas = isinstance(crudos, list) and not tiene_entities
        if not resumen and nuevos_tags == tags and not entidades_nuevas:
            # Nada que cambiar (el LLM no aporto resumen ni entidades nuevas).
            return False
        enlazadas: list[str] = []
        if nuevos_entities:
            titulos = _vault_titles()
            for entidad in nuevos_entities:
                stem = titulos.get(_norm_text(entidad))
                enlazadas.append("[[%s]]" % stem if stem else entidad)
        nuevo_body = body
        if resumen and not tiene_resumen:
            lineas = body.split("\n")
            idx = 0
            for i, linea in enumerate(lineas):
                if linea.startswith("# "):
                    idx = i + 1
                    break
            lineas[idx:idx] = ["", "## Resumen", resumen, ""]
            nuevo_body = "\n".join(lineas)
        metadata["tags"] = nuevos_tags
        if isinstance(crudos, list):
            metadata["entities"] = enlazadas
        nuevo_fm = (
            "---\n"
            + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).rstrip()
            + "\n---"
        )
        try:
            note_path.write_text(nuevo_fm + "\n" + nuevo_body, encoding="utf-8")
        except Exception as e:
            logger.warning("Enrich %s: no se pudo escribir (%s)", note_path.name, e)
            return False
        logger.info(
            "VaultIndexer: %s enriquecida (%d tags%s%s)",
            note_path.name,
            len(nuevos_tags),
            ", resumen" if resumen and not tiene_resumen else "",
            ", %d entidades" % len(enlazadas) if enlazadas else "",
        )
        return True

    async def _debounce_loop(self) -> None:
        while not self._shutdown_event.is_set():
            now = datetime.now().timestamp()
            to_process = []
            # Lock: _pending_paths lo escribe el hilo de watchdog; sin el,
            # iterar y reasignar aqui lanzaba "dictionary changed size during
            # iteration" y se perdian eventos.
            with self._pending_lock:
                for path_str, ts in list(self._pending_paths.items()):
                    if now - ts >= DEBOUNCE_SECONDS:
                        to_process.append(path_str)
                        del self._pending_paths[path_str]

            for path_str in to_process:
                note_path = VAULT_PATH / path_str
                if note_path.exists():
                    try:
                        if note_path.suffix.lower() == ".md":
                            await self.index_note(note_path)
                            await self._enrich_note(note_path)
                        else:
                            await self.intake_document(note_path)
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
            logger.warning(
                "Vector DB not initialized, vault indexer will start without persistence"
            )

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
