#!/usr/bin/env python3
"""Batería de frases variadas contra todas las APIs (auditoría 2026-09-27).

Cada caso usa una forma distinta de pedir lo mismo (sin palabras clave
compartidas) para comprobar que el router semántico + el modelo eligen bien.
Uso (dentro del contenedor):
    python -u /workspace/agent/scripts/tool_phrasing_battery.py
"""

import asyncio
import sys

from src.core.orchestrator import SYSTEM_PROMPT_VOICE
from src.handlers.chat_tools import select_tools_semantic
from src.ollama_client import llm

CASES = [
    ("me he dejado 20 pavos en el almuerzo", {"save_expense"}),
    ("apunta que pagué 15 euros de parking", {"save_expense"}),
    ("el jueves a las 18:30 tengo dentista, apúntalo", {"create_event"}),
    ("agéndame una comida el 3 de octubre a las 14:00", {"create_event"}),
    ("no se me olvide renovar el carnet, es urgente", {"create_alert"}),
    ("¿cómo voy de pasta este mes?", {"get_finance_summary"}),
    ("mi talla de zapato es la 43", {"remember_fact"}),
    (
        "dime lo que tengo guardado sobre mi alergia",
        {
            "search_knowledge",
            "ask_deep_knowledge_base",
            "search_second_brain",
            "search_obsidian_vault",
        },
    ),
    ("quién ganó el partido del Madrid ayer", {"search_web"}),
    (
        "guárdame una nota con las ideas de la reunión: mejorar la web y buscar proveedor",
        {"manage_obsidian_note"},
    ),
    (
        "búscame en mis notas dónde hablo del huerto",
        {"search_obsidian_vault", "search_second_brain", "ask_deep_knowledge_base"},
    ),
    ("enséñame los archivos del proyecto", {"inspect_project_files"}),
    ("cómo va el servidor, hay errores?", {"analyze_system_logs"}),
    ("renombra la nota de ideas a ideas-verano", {"move_or_rename_file", "manage_obsidian_note"}),
    (
        "qué tengo apuntado sobre el seguro del coche",
        {"ask_deep_knowledge_base", "search_second_brain", "search_knowledge"},
    ),
    (
        "qué tengo en Google Calendar esta semana",
        {"manage_google_calendar", "get_google_calendar_events"},
    ),
    ("recuérdame cada lunes llamar a mi madre", {"set_recurring_reminder"}),
    (
        "pon en google calendar una cita el martes a las 9",
        {"create_google_calendar_event", "manage_google_calendar"},
    ),
    ("he subido un PDF de la declaración, guárdalo en el segundo cerebro", {"ingest_file"}),
    ("tengo un archivo en la nube de google sobre el proyecto, búscalo", {"search_google_drive"}),
    ("¿me ha escrito alguien hoy? mira mi buzón", {"search_gmail"}),
    ("quiero apuntar en google tasks comprar pilas", {"manage_google_tasks"}),
    ("¿cuál es el número de mamá?", {"find_contact"}),
    ("cómo llevo la actividad de hoy", {"fitness_daily_steps"}),
    ("cuéntame un chiste", set()),
]


async def main() -> int:
    await llm.initialize()
    failures = 0
    for prompt, expected in CASES:
        tools = await select_tools_semantic(prompt, k=10)
        offered = [t["function"]["name"] for t in tools]
        content, tool_calls = await llm.chat_with_tools(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_VOICE},
                {"role": "user", "content": prompt},
            ],
            tools=tools,
            max_tokens=256,
        )
        chosen = tool_calls[0]["function"]["name"] if tool_calls else "(sin tool)"
        ok = (chosen in expected) if expected else (chosen == "(sin tool)")
        if not ok:
            failures += 1
        mark = "[OK]  " if ok else "[FALLA]"
        print("%s %-55s -> %s" % (mark, prompt[:55], chosen))
        if not ok:
            print(
                "        esperado: %s | ofrecidas: %s" % (sorted(expected) or "(ninguna)", offered)
            )
    print("\nRESUMEN: %d/%d correctas" % (len(CASES) - failures, len(CASES)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
