"""Reglas condicionales proactivas (2026-10-06): «avísame si mañana llueve
antes de las 9», «si la RAM supera el 90%».

El usuario crea reglas en lenguaje natural (tool `create_condition_rule`) y el
worker de `proactive_briefing` las evalúa cada tick; si se cumple la condición,
se avisa por Telegram y la regla se desactiva (o se re-arma cada día, máximo un
aviso/día, si `repeats`). Las métricas del sistema se leen con stdlib (sin
psutil): `/proc/meminfo` y `shutil.disk_usage`.
"""

from __future__ import annotations

import json
import shutil
import unicodedata
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from src.config import settings
from src.database import db
from src.logger import logger

TYPES = ("weather", "metric")
METRICS = ("ram_pct", "disk_pct", "swap_pct")
OPS = (">", "<", ">=", "<=")


def normalize(text: str) -> str:
    """Minusculas y sin acentos ('Lluvia' == 'lluvia')."""
    decomposed = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def system_metrics() -> dict[str, float]:
    """RAM/disco/swap usados en % (Linux, solo stdlib)."""
    metrics: dict[str, float] = {}
    try:
        uso = shutil.disk_usage("/")
        metrics["disk_pct"] = (
            round(100.0 * (uso.total - uso.free) / uso.total, 1) if uso.total else 0.0
        )
    except Exception:
        metrics["disk_pct"] = 0.0
    try:
        info: dict[str, str] = {}
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                info[key.strip()] = rest.strip()
        mem_total = float(info.get("MemTotal", "0 kB").split()[0])
        mem_disp = float(info.get("MemAvailable", "0 kB").split()[0])
        metrics["ram_pct"] = (
            round(100.0 * (mem_total - mem_disp) / mem_total, 1) if mem_total else 0.0
        )
        swap_total = float(info.get("SwapTotal", "0 kB").split()[0])
        swap_free = float(info.get("SwapFree", "0 kB").split()[0])
        metrics["swap_pct"] = (
            round(100.0 * (swap_total - swap_free) / swap_total, 1) if swap_total else 0.0
        )
    except Exception:
        metrics.setdefault("ram_pct", 0.0)
        metrics.setdefault("swap_pct", 0.0)
    return metrics


def _in_window(rule: dict[str, Any], now: datetime) -> bool:
    """True si estamos dentro del horario del aviso ('antes de las 9').

    Sin `hour` se puede avisar a cualquier hora; con `hour`, la ventana es de
    las 06:00 hasta esa hora (o desde medianoche si la hora es anterior).
    """
    hour = str(rule.get("hour") or "").strip()
    if not hour:
        return True
    try:
        limite = datetime.strptime(hour, "%H:%M").time()
    except ValueError:
        return True
    actual = now.time()
    inicio = time(6, 0) if limite > time(6, 0) else time(0, 0)
    return inicio <= actual <= limite


async def _weather_matches(rule: dict[str, Any]) -> bool:
    from src.services import automation_service

    ciudad = str(rule.get("city") or "").strip()
    dia = str(rule.get("day") or "hoy").strip() or "hoy"
    try:
        report = await automation_service.weather_report(ciudad=ciudad, dia=dia)
    except Exception as e:
        logger.warning("Reglas: el pronostico fallo (%s)", e)
        return False
    texto = normalize(json.dumps(report, ensure_ascii=False, default=str))
    keywords = [normalize(str(k)) for k in rule.get("keywords") or [] if str(k).strip()]
    return bool(keywords) and any(k in texto for k in keywords)


def _metric_matches(rule: dict[str, Any]) -> bool:
    name = str(rule.get("metric") or "")
    op = str(rule.get("op") or ">")
    raw = rule.get("value")
    if raw is None:
        return False
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return False
    actual = system_metrics().get(name)
    if actual is None:
        return False
    return {
        ">": actual > value,
        "<": actual < value,
        ">=": actual >= value,
        "<=": actual <= value,
    }.get(op, False)


async def check_condition(rule: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or datetime.now(ZoneInfo(settings.timezone))
    if not _in_window(rule, now):
        return False
    kind = str(rule.get("type") or "")
    if kind == "weather":
        return await _weather_matches(rule)
    if kind == "metric":
        return _metric_matches(rule)
    return False


async def evaluate_and_fire(bot: Any, now: datetime | None = None) -> int:
    """Evalúa las reglas activas; avisa y las desactiva (o re-arma si repeats).

    Devuelve el número de avisos enviados. Las de `repeats` se disparan como
    máximo una vez al día.
    """
    now = now or datetime.now(ZoneInfo(settings.timezone))
    enviadas = 0
    for row in await db.list_active_conditional_rules():
        try:
            rule = json.loads(row["rule_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        repeats = bool(row.get("repeats"))
        fired_at = str(row.get("fired_at") or "")
        if repeats and fired_at.startswith(now.strftime("%Y-%m-%d")):
            continue
        try:
            if not await check_condition(rule, now):
                continue
        except Exception as e:
            logger.warning("Reglas: condicion fallo id=%s: %s", row.get("id"), e)
            continue
        message = row.get("message") or "Se ha cumplido tu regla: %s" % json.dumps(
            rule, ensure_ascii=False
        )
        try:
            await bot.send_proactive_message(int(row["chat_id"]), message)
            enviadas += 1
        except Exception as e:
            logger.warning("Reglas: no se pudo avisar chat_id=%s: %s", row.get("chat_id"), e)
            continue
        await db.fire_conditional_rule(int(row["id"]), repeats=repeats)
    return enviadas
