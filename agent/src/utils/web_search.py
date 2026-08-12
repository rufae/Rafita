import httpx
from bs4 import BeautifulSoup

from src.logger import logger

WEB_SEARCH_TIMEOUT = 15
MAX_CONTENT_CHARS = 3000


async def search_duckduckgo(query: str, max_results: int = 5) -> list[dict[str, str]]:
    try:
        from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=max_results))
        results = []
        for r in raw:
            results.append(
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                }
            )
        return results
    except Exception as e:
        logger.warning("DuckDuckGo search failed: %s", e)
        return []


async def fetch_page_text(url: str, max_chars: int = MAX_CONTENT_CHARS) -> str:
    try:
        async with httpx.AsyncClient(timeout=WEB_SEARCH_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)
            return text[:max_chars]  # type: ignore[no-any-return]
    except Exception as e:
        logger.debug("Failed to fetch %s: %s", url, e)
        return ""


def format_search_results(results: list[dict[str, str]]) -> str:
    if not results:
        return "No se encontraron resultados."
    lines = []
    for i, r in enumerate(results, 1):
        title = (r.get("title") or "").strip()
        snippet = (r.get("snippet") or "").strip()
        url = (r.get("url") or "").strip()
        lines.append(f"{i}. {title}")
        if snippet:
            lines.append(f"   {snippet}")
        if url:
            lines.append(f"   Fuente: {url}")
    return "\n".join(lines)
