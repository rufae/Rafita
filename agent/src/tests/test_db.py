import asyncio

from src.database import DatabaseManager


async def test_db():
    print("=== TEST: Personal Knowledge DB ===)")
    d = DatabaseManager()
    await d.initialize()

    cid = 9999

    await d.store_personal_knowledge(cid, "nombre_completo", "Rafael Test")
    print("  STORE: nombre_completo = Rafael Test")

    await d.store_personal_knowledge(cid, "ciudad", "CDMX")
    print("  STORE: ciudad = CDMX")

    rows = await d.search_personal_knowledge(cid, "Rafael")
    print("  SEARCH 'Rafael': %d resultados" % len(rows))
    for r in rows:
        print("    %s = %s (%s)" % (r["key"], r["value"], r["category"]))

    all_rows = await d.get_all_personal_knowledge(cid)
    print("  ALL: %d facts" % len(all_rows))

    count = await d.count_personal_knowledge(cid)
    print("  COUNT: %d" % count)

    deleted = await d.delete_personal_knowledge(cid, "ciudad")
    print("  DELETE ciudad: %s" % ("OK" if deleted else "FAIL"))

    count2 = await d.count_personal_knowledge(cid)
    print("  COUNT after delete: %d" % count2)

    await d.delete_personal_knowledge(cid, "nombre_completo")
    print()


asyncio.run(test_db())
