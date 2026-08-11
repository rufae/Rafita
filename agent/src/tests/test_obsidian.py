import asyncio

from src.utils.obsidian_manager import (
    create_or_append_note,
    delete_note,
    read_note,
    search_notes_content,
)


async def test_obsidian():
    print("=== TEST: Obsidian Vault CRUD ===")
    r = await create_or_append_note("Nota de Prueba", "Este es el contenido de prueba.")
    print("  CREATE: %s | %s" % (r["action"], r["message"]))

    r2 = await create_or_append_note("Nota de Prueba", "Segundo parrafo anadido despues.")
    print("  APPEND: %s | %s" % (r2["action"], r2["message"]))

    r3 = await read_note("Nota de Prueba")
    print("  READ: %s" % r3["message"])
    print("  CONTENT PREVIEW: %s..." % r3["content"][:80])

    r4 = await search_notes_content("contenido")
    print("  SEARCH: %s" % r4["message"])
    for res in r4["results"]:
        print("    %s (%d coincidencias)" % (res["title"], res["match_count"]))

    r5 = await delete_note("Nota de Prueba")
    print("  DELETE: %s" % r5["message"])

    r6 = await read_note("Nota de Prueba")
    print("  VERIFY: %s" % r6["message"])
    print()


asyncio.run(test_obsidian())
