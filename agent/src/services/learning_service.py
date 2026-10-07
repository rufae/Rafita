"""Learning loop propio (fase 2, 2026-10-07): destila aprendizajes en skills.

Hermes crea skills desde su experiencia automaticamente; aqui la version
honesta y contenida: una vez por semana se leen los `Aprendizajes/` nuevos
de la boveda, el LLM propone como mucho 2 skills y se guardan en
`<boveda>/skills/` (revisables con cargar_skill). Solo instrucciones en
Markdown: no se genera ni se ejecuta codigo.
"""

import asyncio
import json
import re
import time
from datetime import UTC, datetime
from typing import Any

from src.config import settings
from src.logger import logger
from src.utils.vault_indexer import VAULT_PATH

_MAX_APRENDIZAJES = 15
_MAX_CHARS = 1500


def _slug(name: str) -> str:
    limpio = re.sub(r"[^\w\s-]", "", str(name or "")).strip()
    limpio = re.sub(r"[/\\]", "-", limpio)
    return limpio[:60] or "aprendizaje"


async def _notify_admins(text: str) -> None:
    from src.bot import bot

    for admin_id in settings.admin_ids or []:
        try:
            await bot.send_proactive_message(admin_id, text)
        except Exception as e:
            logger.warning("Learning: no pude avisar a %s: %s", admin_id, str(e)[:80])


async def weekly_learning_review(force: bool = False) -> dict[str, Any]:
    """Destila los aprendizajes de la ultima semana en skills propuestas.

    Guard KV `learning:last` = semana ISO: una corrida util por semana (salvo
    `force`). Si el LLM falla NO se guarda la marca: se reintenta al dia
    siguiente, sin inventar nada.
    """
    from src.database import db

    marca = "%d-W%02d" % datetime.now(UTC).isocalendar()[:2]
    if not force and (await db.kv_get("learning:last")) == marca:
        return {"success": True, "skipped": "ya ejecutado esta semana"}

    carpeta = VAULT_PATH / "Aprendizajes"
    limite = time.time() - 7 * 86400
    recientes = []
    if carpeta.is_dir():
        for path in sorted(carpeta.glob("*.md")):
            try:
                if path.stat().st_mtime >= limite:
                    recientes.append(path)
            except OSError:
                continue
    if not recientes:
        await db.kv_set("learning:last", marca)
        return {
            "success": True,
            "sin_aprendizajes": True,
            "message": "Sin aprendizajes nuevos esta semana.",
        }

    bloques = []
    for path in recientes[:_MAX_APRENDIZAJES]:
        try:
            texto = path.read_text(encoding="utf-8", errors="replace")[:_MAX_CHARS]
        except OSError:
            continue
        bloques.append("## %s\n%s" % (path.stem, texto))
    try:
        from src.ollama_client import llm

        respuesta = await asyncio.wait_for(
            llm.chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Eres el curador de skills de un asistente personal. "
                            "Respondes SOLO con JSON."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "A partir de estos aprendizajes de la semana, propone "
                            "como maximo 2 skills (procedimientos reutilizables en "
                            "Markdown, sin codigo).\n\n"
                            "Responde SOLO con JSON:\n"
                            '{"skills": [{"name": "nombre-corto", '
                            '"description": "que hace (una frase)", '
                            '"body": "pasos numerados del procedimiento"}]}\n'
                            'Si nada es reutilizable, responde {"skills": []}.\n\n'
                            "Aprendizajes:\n\n%s" % "\n\n".join(bloques)
                        ),
                    },
                ],
                temperature=0.3,
                max_tokens=900,
            ),
            timeout=120.0,
        )
    except Exception as e:
        logger.warning("Learning: LLM no disponible (%s)", str(e)[:120])
        return {
            "success": False,
            "message": "LLM no disponible; reintentare otro dia: %s" % str(e)[:80],
        }

    datos: dict[str, Any] = {}
    match = re.search(r"\{.*\}", respuesta or "", re.DOTALL)
    if match:
        try:
            cargado = json.loads(match.group())
            if isinstance(cargado, dict):
                datos = cargado
        except (json.JSONDecodeError, ValueError):
            datos = {}
    crudas = datos.get("skills") if isinstance(datos.get("skills"), list) else []
    guardadas: list[str] = []
    if crudas:
        from src.utils.skills_manager import save_skill

        for sk in crudas[:2]:
            if not isinstance(sk, dict):
                continue
            name = _slug(str(sk.get("name") or ""))
            body = str(sk.get("body") or "").strip()
            if not body:
                continue
            try:
                save_skill(
                    name,
                    str(sk.get("description") or "Skill propuesta por el learning loop."),
                    body,
                )
                guardadas.append(name)
            except (OSError, ValueError) as e:
                logger.warning("Learning: no pude guardar skill %s: %s", name, e)

    await db.kv_set("learning:last", marca)
    if guardadas:
        await _notify_admins(
            "🧠 *Learning loop:* convertí %d aprendizaje(s) de la semana en "
            "skill(s): %s. Revísalas con `cargar_skill`." % (len(recientes), ", ".join(guardadas))
        )
        return {"success": True, "guardadas": guardadas, "aprendizajes": len(recientes)}
    return {
        "success": True,
        "sin_propuestas": True,
        "aprendizajes": len(recientes),
        "message": "Aprendizajes leidos (%d), sin skill reutilizable esta semana." % len(recientes),
    }
