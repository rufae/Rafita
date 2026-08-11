import asyncio

from src.utils.web_search import format_search_results, search_duckduckgo


async def test_web_search():
    print("=== TEST: Web Search (DuckDuckGo) ===")
    results = await search_duckduckgo("inteligencia artificial 2026", max_results=3)
    print("  RESULTADOS CRUDOS: %d resultados" % len(results))
    for r in results:
        print("    TITLE: %s" % r.get("title", "")[:60])
        print("    URL: %s" % r.get("url", "")[:60])
        print("    SNIPPET: %s..." % r.get("snippet", "")[:60])
    formatted = format_search_results(results)
    print("  FORMATEADO:\n%s" % formatted)
    print()


asyncio.run(test_web_search())
