r"""Import WhatsApp self-chat into Obsidian vault with AI classification.

Optimized approach:
1. Parse all messages
2. Pre-group by URL domain / keyword patterns
3. Use Qwen to generate a summary for each group
4. Qwen generates final organized note

Usage (inside container):
    docker exec rafita-agent-core python -m src.tools.import_whatsapp
"""

import asyncio
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from src.logger import logger
from src.utils.obsidian_manager import create_or_append_note

CHAT_FILE = Path("/workspace/whatsapp_chat.txt")

DOMAIN_CATEGORIES = {
    "youtube": "YouTube_Videos",
    "youtu.be": "YouTube_Videos",
    "twitter": "Twitter_X_Enlaces",
    "x.com": "Twitter_X_Enlaces",
    "github": "GitHub_Recursos",
    "amazon": "Compras_Productos",
    "instagram": "Instagram_Enlaces",
    "maps.google": "Ubicaciones",
    "whatsapp": "Grupos_WhatsApp",
    "pampling": "Compras_Productos",
    "000webhost": "Proyectos_Web",
}


def parse_whatsapp(filename: Path) -> list:
    raw = filename.read_text(encoding="utf-8")
    pattern = re.compile(
        r"^(\d{1,2}/\d{1,2}/\d{2,4}),\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*(.+?):\s*(.*)$", re.M
    )
    entries = []
    current_date = None
    current_text = ""
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = pattern.match(line)
        if m:
            if current_date and current_text.strip():
                entries.append({"date": current_date, "text": current_text.strip()})
            d, t, a, text = m.groups()
            current_date = f"{d} {t}"
            current_text = text.strip()
        else:
            if current_text:
                current_text += " " + line
    if current_date and current_text.strip():
        entries.append({"date": current_date, "text": current_text.strip()})
    return entries


def _extract_urls(text: str) -> list:
    return re.findall(r"https?://[^\s\)\]>]+", text)


def classify_entry(entry: dict) -> str:
    urls = _extract_urls(entry["text"])
    for url in urls:
        for domain, cat in DOMAIN_CATEGORIES.items():
            if domain in url.lower():
                return cat
    if urls:
        return "Enlaces_Web"
    return "Notas_Texto"


def summarize_group_for_llm(group_name: str, entries: list, max_items: int = 30) -> str:
    sample = entries[:max_items]
    items_text = "\n".join(
        f"- [{e['date']}] {e['text'][:150]}" for e in sample
    )
    head = f"Tema: {group_name} ({len(entries)} mensajes totales, mostrando {len(sample)})\n\n{items_text}"
    return head


async def ai_summarize_group(group_name: str, entries: list, llm_instance) -> str:
    if len(entries) == 0:
        return ""

    header = summarize_group_for_llm(group_name, entries)

    prompt = (
        "Eres un organizador de informacion personal. Genera un resumen en Markdown de estos mensajes "
        "guardados en un chat de WhatsApp. Agrupa por sub-temas. Extrae los enlaces. "
        "Se conciso pero no pierdas informacion valiosa. No inventes nada.\n\n"
        f"{header}\n\n"
        "Genera SOLO el contenido markdown. Sin introducciones ni despedidas. En espanol."
    )

    try:
        response = await asyncio.wait_for(
            llm_instance.chat(
                messages=[
                    {"role": "system", "content": "Eres un organizador de informacion. Creas resumenes Markdown para Obsidian. Espanol."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=512,
            ),
            timeout=120.0,
        )
        return f"## {group_name.replace('_', ' ')}\n\n{response.strip()}\n"
    except TimeoutError:
        logger.warning("AI timeout for group %s, using raw list", group_name)
    except Exception as e:
        logger.warning("AI failed for group %s: %s", group_name, e)

    lines = [f"## {group_name.replace('_', ' ')}\n"]
    for e in entries[:40]:
        urls = _extract_urls(e["text"])
        clean = e["text"][:120].replace("\n", " ")
        date = e["date"].split(" ")[0]
        if urls:
            lines.append(f"- [{date}] {clean} -> {urls[0]}")
        else:
            lines.append(f"- [{date}] {clean}")
    return "\n".join(lines) + "\n"


async def main():
    logger.info("=== WhatsApp Optimized Importer ===")
    logger.info("Loading: %s", CHAT_FILE)

    if not CHAT_FILE.exists():
        logger.error("File not found: %s", CHAT_FILE)
        raise SystemExit(1)

    entries = parse_whatsapp(CHAT_FILE)
    logger.info("Parsed %d entries", len(entries))

    filtered = [
        e for e in entries
        if e.get("text")
        and "<Multimedia omitido>" not in e["text"]
        and "cifrados de extremo" not in e["text"]
        and "Los mensajes que envias" not in e["text"]
    ]
    logger.info("Useful: %d messages", len(filtered))

    groups = defaultdict(list)
    for e in filtered:
        cat = classify_entry(e)
        groups[cat].append(e)

    logger.info("%d groups found:", len(groups))
    for cat, items in sorted(groups.items(), key=lambda x: -len(x[1])):
        logger.info("  %s: %d msgs", cat, len(items))

    logger.info("Initializing Ollama for AI summaries...")
    from src.ollama_client import OllamaClient
    local_llm = OllamaClient()
    await local_llm.initialize()
    logger.info("Ollama ready: %s", local_llm.model)

    top_groups = sorted(groups.items(), key=lambda x: -len(x[1]))
    note_sections = [
        "# WhatsApp Chat Importado (Self-Chat)\n",
        f"Fecha importacion: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n",
        f"Total mensajes: {len(filtered)} utiles de {len(entries)} lineas\n",
        "---\n",
    ]

    for group_name, items in top_groups:
        logger.info("Summarizing %s (%d msgs) via AI...", group_name, len(items))
        section = await ai_summarize_group(group_name, items, local_llm)
        note_sections.append(section)
        note_sections.append("")

    final_text = "\n".join(note_sections)
    result = await create_or_append_note(
        title="WhatsApp_Chat_Importado",
        content=final_text[:20000],
        folder="04-Archivo",
    )
    if result.get("success"):
        logger.info("Main note created: %s", result.get("filepath", ""))

    url_index = ["# Indice de Enlaces\n", f"Enlaces extraidos: {datetime.now().strftime('%Y-%m-%d')}\n\n"]
    all_urls = []
    for e in filtered:
        for u in _extract_urls(e["text"]):
            all_urls.append((u, e["date"], e["text"][:80]))
    url_index.append(f"Total enlaces encontrados: {len(all_urls)}\n\n")

    current_cat = None
    for url, date, text in sorted(all_urls):
        cat = classify_entry({"text": url, "date": date})
        if cat != current_cat:
            current_cat = cat
            url_index.append(f"## {cat.replace('_', ' ')}\n")
        url_index.append(f"- [{date}] [{text}]({url})\n")

    url_result = await create_or_append_note(
        title="WhatsApp_Enlaces_Index",
        content="\n".join(url_index)[:15000],
        folder="03-Recursos",
    )
    if url_result.get("success"):
        logger.info("URL index: %s", url_result.get("filepath", ""))

    logger.info("=== Import complete ===")


if __name__ == "__main__":
    asyncio.run(main())
