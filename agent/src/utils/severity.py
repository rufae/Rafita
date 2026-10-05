"""Taxonomía única de prioridades (tareas.md línea 69).

Cuatro vocabularios convivían en paralelo:

- `infra_monitor.severity`: info/warning/critical (sin `error`)
- `alerts.alert_type`: info/warning/urgent
- `automation_runs.status`: ok/error
- avisos con prioridad solo por emoji (⚠️/🚨 sin nivel textual)

y los flujos n8n no enviaban nivel ninguno. Esta taxonomía es la única
fuente de verdad: niveles en orden INFO < WARNING < ERROR < CRITICAL,
alias para migrar vocabularios antiguos (`urgent`→critical, `ok`→info) y
emoji textual por nivel para que ningún aviso dependa solo del emoji.
"""

from __future__ import annotations

INFO = "info"
WARNING = "warning"
ERROR = "error"
CRITICAL = "critical"

LEVELS: tuple[str, ...] = (INFO, WARNING, ERROR, CRITICAL)
RANK: dict[str, int] = {INFO: 0, WARNING: 1, ERROR: 2, CRITICAL: 3}

EMOJI: dict[str, str] = {
    INFO: "ℹ️",
    WARNING: "⚠️",
    ERROR: "❌",
    CRITICAL: "🚨",
}

# Vocabularios antiguos (alerts urgent, infra/estado ok, CAP de AEMET...).
_ALIASES: dict[str, str] = {
    "ok": INFO,
    "info": INFO,
    "information": INFO,
    "informativo": INFO,
    "warn": WARNING,
    "warning": WARNING,
    "aviso": WARNING,
    "moderate": WARNING,
    "err": ERROR,
    "error": ERROR,
    "failed": ERROR,
    "fallido": ERROR,
    "severe": ERROR,
    "urgent": CRITICAL,
    "urgente": CRITICAL,
    "crit": CRITICAL,
    "critical": CRITICAL,
    "extreme": CRITICAL,
}


def normalize(value: str | None, default: str = INFO) -> str:
    """Devuelve el nivel canónico (alias incluidos); desconocidos → default."""
    key = str(value or "").strip().lower()
    if key in RANK:
        return key
    return _ALIASES.get(key, default)


def rank(value: str | None) -> int:
    """Posición en la escala (0=info ... 3=critical)."""
    return RANK[normalize(value)]


def worst(*values: str | None) -> str:
    """El nivel más alto entre los dados (vacíos ignorados)."""
    mejor = INFO
    for value in values:
        if not value:
            continue
        nivel = normalize(value)
        if RANK[nivel] > RANK[mejor]:
            mejor = nivel
    return mejor


def emoji(value: str | None) -> str:
    """Emoji del nivel (sustituye a los ternarios ⚠️/🚨 sueltos)."""
    return EMOJI[normalize(value)]


def tag(value: str | None) -> str:
    """Etiqueta textual en mayúsculas para que ningún aviso sea solo-emoji."""
    return normalize(value).upper()
