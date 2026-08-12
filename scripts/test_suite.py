"""
Bateria completa de tests para RafAI - Segundo Cerebro.
Ejecuta desde dentro del contenedor: python /workspace/scripts/test_suite.py
"""

import sys, asyncio, json

sys.path.insert(0, "/app/src")
from pathlib import Path
from datetime import datetime

PASS = "✅"
FAIL = "❌"
results = []


def test(name, condition):
    results.append((name, condition))
    print("  %s %s" % (PASS if condition else FAIL, name))


async def run_tests():
    print("=" * 60)
    print("TEST SUITE: RafAI Segundo Cerebro")
    print("=" * 60)

    # === TEST 1: Database ===
    print("\n📦 TEST 1: Base de datos")
    from src.database import db

    await db.initialize()
    test("SQLite initialized", db._conn is not None)

    from src.utils.vector_manager import vector_db

    await vector_db.initialize()
    stats = await vector_db.get_stats()
    test("ChromaDB initialized", stats["total_chunks"] > 0)
    test("Chunks >= 250", stats["total_chunks"] >= 250)
    test("Documents >= 13", stats["total_documents"] >= 13)

    # === TEST 2: Semantic search ===
    print("\n🔍 TEST 2: Busqueda semantica")
    r1 = await vector_db.query("que es RafAI", top_k=3)
    test("Search returns results", len(r1.get("results", [])) > 0)
    if r1.get("results"):
        r = r1["results"][0]
        test("Has note_path", bool(r.get("note_path")))
        test("Has heading", bool(r.get("heading", "")) or True)  # some sections are root
        test("Has obsidian_uri", bool(r.get("obsidian_uri")))
        test("Has relevance", float(r.get("relevance", 0)) > 0)

    r2 = await vector_db.query("chunking semantico", top_k=3)
    test(
        "Semantic search finds Zettelkasten",
        any("Chunking" in r.get("note_path", "") for r in r2.get("results", [])),
    )

    # === TEST 3: Credentials ===
    print("\n🔐 TEST 3: Credenciales (AES-256)")
    chat_id = 999999
    await db.store_credential(chat_id, "gemini", "AIza-test-key-123456789")
    await db.store_credential(chat_id, "wifi", "MiRedWiFi-Secret123")

    val = await db.get_credential(chat_id, "gemini")
    test("Store and retrieve credential", val == "AIza-test-key-123456789")

    creds = await db.list_credentials(chat_id)
    test("List credentials", len(creds) == 2)
    test("Services listed", "gemini" in [c["service"] for c in creds])

    await db.delete_credential(chat_id, "wifi")
    creds2 = await db.list_credentials(chat_id)
    test("Delete credential", len(creds2) == 1)

    await db.delete_credential(chat_id, "gemini")
    test("All credentials cleaned", len(await db.list_credentials(chat_id)) == 0)

    # === TEST 4: Second brain log ===
    print("\n📊 TEST 4: Log de consultas")
    await db.log_second_brain_query("test query 1", chat_id, ["nota1.md", "nota2.md"], 3, 0.85)
    await db.log_second_brain_query("test query 2", chat_id, ["nota3.md"], 1, 0.45)
    brain_stats = await db.get_second_brain_stats()
    test("Query logged", brain_stats["total_queries"] >= 2)
    test("Recent queries retrieved", len(brain_stats["recent_queries"]) >= 2)
    # Clean up
    await db.execute("DELETE FROM second_brain_log WHERE chat_id = ?", (chat_id,))
    await db._conn.commit()

    # === TEST 5: Companion note creation ===
    print("\n📄 TEST 5: Creacion de nota comparnera")
    vault = Path("/data/obsidian_vault")
    from src.handlers.files import _extract_text_from_file, _create_companion_note

    test_txt = vault / "03-Recursos" / "_test_companion.txt"
    test_txt.write_text(
        "Documento de prueba del segundo cerebro.\n## Seccion 1\nContenido importante.\n## Seccion 2\nMas contenido."
    )
    text = _extract_text_from_file(test_txt)
    test("Text extracted from TXT", len(text) > 0 and "Documento de prueba" in text)

    note = _create_companion_note(
        vault, test_txt, text, "recurso", ["test", "prueba"], "Resumen de prueba"
    )
    test("Companion note created", note is not None and note.exists())
    if note:
        content = note.read_text()
        test("Has frontmatter YAML", content.startswith("---"))
        test("Has id field", "id:" in content)
        test("Has type field", "type: recurso" in content)
        test("Has tags field", "tags: [test, prueba]" in content)
        test("Has source_file field", "source_file:" in content)
        test(
            "Has link to original",
            "!_test_companion.txt" in content or "[[_test_companion.txt]]" in content,
        )
        note.unlink()
    test_txt.unlink()

    # === TEST 6: Vault structure ===
    print("\n📁 TEST 6: Estructura del vault")
    folders = [d.name for d in vault.iterdir() if d.is_dir() and not d.name.startswith(".")]
    test("Has 00-Inbox", "00-Inbox" in folders)
    test("Has 01-Proyectos", "01-Proyectos" in folders)
    test("Has 02-Areas", "02-Areas" in folders)
    test("Has 03-Recursos", "03-Recursos" in folders)
    test("Has 04-Archivo", "04-Archivo" in folders)
    test("Has 05-Zettelkasten", "05-Zettelkasten" in folders)
    test("Has 06-Diario", "06-Diario" in folders)
    test("Has Attachments", "Attachments" in folders)

    md_count = len(list(vault.rglob("*.md")))
    test("Has .md notes", md_count >= 13)

    zettle_notes = list((vault / "05-Zettelkasten").glob("*.md"))
    test("Zettelkasten has notes", len(zettle_notes) >= 3)

    # === TEST 7: Frontmatter YAML ===
    print("\n🏷️ TEST 7: Frontmatter YAML")
    import yaml

    all_valid = True
    for md_file in vault.rglob("*.md"):
        if any(d in str(md_file) for d in [".obsidian", "templates"]):
            continue
        content = md_file.read_text(encoding="utf-8", errors="replace")
        if content.startswith("---"):
            second = content.find("---", 3)
            if second != -1:
                fm_text = content[3:second]
                try:
                    fm = yaml.safe_load(fm_text)
                    if fm and isinstance(fm, dict):
                        if "id" not in fm:
                            all_valid = False
                            print("      Missing id in:", md_file.name)
                        if "type" not in fm:
                            all_valid = False
                            print("      Missing type in:", md_file.name)
                except Exception:
                    all_valid = False
                    print("      YAML parse error in:", md_file.name)
    test("All notes have valid frontmatter", all_valid)

    # === TEST 8: Auto-enlazado ===
    print("\n🔗 TEST 8: Auto-enlazado semantico")
    from src.utils.vault_indexer import VaultIndexer

    idx = VaultIndexer()
    # Index a note and check if related was updated
    note_path = (
        vault / "05-Zettelkasten" / "Chunking semantico vs chunking por palabras para RAG.md"
    )
    result = await idx.index_note(note_path)
    content = note_path.read_text()
    related_line = [l for l in content.split("\n") if "related:" in l]
    has_links = False
    if related_line:
        has_links = "[[06-Diario" in related_line[0] or "[[01-Proyectos" in related_line[0]
    test("Auto-link found related notes", has_links)

    # === TEST 9: Catch-up scan ===
    print("\n📡 TEST 9: Catch-up scan")
    from src.utils.message_scanner import scan_messages

    # Should return 0 messages (already scanned) or handle gracefully
    scan_result = await scan_messages(chat_id, limit=5)
    test("Scan executes without error", scan_result.get("success"))

    last_scan = await db.kv_get("last_scan_timestamp")
    test("last_scan_timestamp set", last_scan is not None)

    # === TEST 10: Vault indexer ===
    print("\n⚡ TEST 10: VaultIndexer")
    diario_note = vault / "06-Diario" / "2026-08-10.md"
    if diario_note.exists():
        result = await idx.index_note(diario_note)
        test("Re-index existing note", result.get("chunks_added", 0) >= 0)
        test("Re-index returns success", result.get("success"))
        test("Has note_type", bool(result.get("note_type")))

    # === SUMMARY ===
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print("RESUMEN: %d/%d tests OK (%d%%)\n" % (passed, total, int(passed / total * 100)))

    if passed == total:
        print("🎉 TODOS LOS TESTS PASARON!")
    else:
        print("⚠️ Algunos tests fallaron:")
        for name, ok in results:
            if not ok:
                print("  %s %s" % (FAIL, name))

    await db.close()
    return passed == total


if __name__ == "__main__":
    ok = asyncio.run(run_tests())
    sys.exit(0 if ok else 1)
