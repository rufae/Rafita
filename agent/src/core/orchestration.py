"""Capa explicita de orquestacion (tareas.md 22 / item 13).

    Usuario -> Rafita -> interpretacion -> PERMISOS -> SELECCION
    (catalogo AUTOMATIONS) -> n8n -> resultado -> Rafita

En lugar de que el modelo suelte `trigger_n8n` con cualquier nombre o URL,
las automatizaciones viven en un catalogo con:

- **seleccion**: clave estable, descripcion, horario y webhook `manual-*`
  (los 10 flujos de `n8n/workflows/` tienen ademas de su trigger de
  programacion un webhook manual para lanzarlos a demanda desde chat);
- **permisos**: modo `read` (solo informa) / `write` (modifica datos
  internos) / `execute` (acciones externas: correos a terceros, comandos
  del sistema) — `execute` exige `confirm=true` tras pedirselo al usuario.

La honestidad se mantiene: si n8n no responde o el flujo esta inactivo, el
resultado lo dice con el detalle real en vez de afirmar que se lanzo.
"""

from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote

from src.config import settings
from src.logger import logger

Mode = Literal["read", "write", "execute"]


@dataclass(frozen=True)
class Automation:
    key: str
    name: str
    flow: str
    description: str
    mode: Mode
    schedule: str
    webhook: str

    @property
    def url(self) -> str:
        return "%s/webhook/%s" % (settings.n8n_base_url.rstrip("/"), self.webhook)


# Catalogo de los 10 flujos de n8n/workflows/. La clave es estable (la usa
# el modelo); `webhook` es el nodo "Manual (chat)" anadido a cada flujo.
AUTOMATIONS: dict[str, Automation] = {
    spec.key: spec
    for spec in (
        Automation(
            key="briefing",
            name="Briefing contextual",
            flow="Rafita · 1 Briefing · Contextual (08:00)",
            description="Informe de la manana: agenda, tareas, correo, tiempo y servidor.",
            mode="read",
            schedule="diario 08:00",
            webhook="manual-briefing",
        ),
        Automation(
            key="inbox-zero",
            name="Correo Inbox Zero",
            flow="Rafita · 2 Correo · Inbox Zero (cada 30 min)",
            description="Clasifica y ordena el correo no leido de la bandeja principal.",
            mode="write",
            schedule="cada 30 min",
            webhook="manual-inbox-zero",
        ),
        Automation(
            key="captura-vault",
            name="Captura a la boveda",
            flow="Rafita · 3 Boveda · Captura (webhook)",
            description="Crea una nota .md en la boveda Obsidian con frontmatter y tags.",
            mode="write",
            schedule="bajo demanda (webhook)",
            webhook="manual-captura-vault",
        ),
        Automation(
            key="sync-google-vault",
            name="Sync Google <-> boveda",
            flow="Rafita · 4 Google · Sync boveda (cada hora)",
            description="Sincroniza eventos de Calendar con notas de la boveda.",
            mode="write",
            schedule="cada hora",
            webhook="manual-sync-google-vault",
        ),
        Automation(
            key="informe-semanal",
            name="Informe semanal",
            flow="Rafita · 5 Infra · Informe semanal (domingo 23:00)",
            description="Resumen de infraestructura, backups y actividad de la semana.",
            mode="read",
            schedule="domingo 23:00",
            webhook="manual-informe-semanal",
        ),
        Automation(
            key="radar-ia",
            name="Radar de IA",
            flow="Rafita · 6 Radar · IA (09:00)",
            description="Lectura de feeds RSS de inteligencia artificial y novedades.",
            mode="read",
            schedule="diario 09:00",
            webhook="manual-radar-ia",
        ),
        Automation(
            key="ejecutable",
            name="Ejecutable desde chat/voz",
            flow="Rafita · 7 Automatizacion · Ejecutable desde chat/voz",
            description="Ejecuta una accion definida en n8n (comandos/acciones externas).",
            mode="execute",
            schedule="bajo demanda (webhook)",
            webhook="manual-ejecutable",
        ),
        Automation(
            key="plantilla-aviso",
            name="Aviso programado (plantilla)",
            flow="Rafita · 8 Plantillas · Aviso programado (ejemplo)",
            description="Plantilla de ejemplo para avisos programados (normalmente inactiva).",
            mode="write",
            schedule="plantilla (ejemplo)",
            webhook="manual-plantilla-aviso",
        ),
        Automation(
            key="crm-seguimiento",
            name="Seguimiento CRM",
            flow="Rafita · 9 Clientes · Seguimiento CRM (lunes 09:00)",
            description="Recordatorios de seguimiento de clientes del CRM.",
            mode="write",
            schedule="lunes 09:00",
            webhook="manual-crm-seguimiento",
        ),
        Automation(
            key="secuencias-email",
            name="Secuencias de email",
            flow="Rafita · 10 Secuencias · Email seguimiento (09:30)",
            description="Envia la siguiente replica de las secuencias de correo a clientes.",
            mode="execute",
            schedule="diario 09:30",
            webhook="manual-secuencias-email",
        ),
    )
}

MODE_ES = {"read": "lectura", "write": "escritura", "execute": "accion externa"}


def resolve(name: str) -> Automation | None:
    """Busca una automatizacion por clave o nombre (sin acentos/mayusculas)."""
    clave = (name or "").strip().lower()
    if clave in AUTOMATIONS:
        return AUTOMATIONS[clave]
    import unicodedata

    def norm(s: str) -> str:
        return "".join(
            c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
        )

    n = norm(clave)
    if n.replace(" ", "-") in AUTOMATIONS:
        return AUTOMATIONS[n.replace(" ", "-")]
    for spec in AUTOMATIONS.values():
        if norm(spec.name).lower() == n or norm(spec.flow).lower() == n:
            return spec
    return None


def authorize(spec: Automation, confirm: bool = False) -> dict[str, Any]:
    """Capa de permisos: read/write auto; execute exige confirmacion previa."""
    if spec.mode != "execute" or confirm:
        return {"allowed": True, "mode": spec.mode, "reason": "auto"}
    return {
        "allowed": False,
        "mode": spec.mode,
        "needs_confirmation": True,
        "reason": "accion externa (correos a terceros o comandos)",
    }


def describe_automations() -> dict[str, Any]:
    """Catalogo para el modelo/usuario (tool list_automations)."""
    items = [
        {
            "key": s.key,
            "name": s.name,
            "mode": s.mode,
            "permiso": MODE_ES[s.mode],
            "schedule": s.schedule,
            "description": s.description,
        }
        for s in AUTOMATIONS.values()
    ]
    return {
        "success": True,
        "automations": items,
        "message": (
            "%d automatizaciones en el catalogo. Modos: lectura (auto), "
            "escritura (auto), accion externa (requiere confirm=true)." % len(items)
        ),
    }


async def _post(url: str, payload: dict[str, Any]) -> tuple[bool, str]:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload or {})
    except Exception as e:
        return False, "No pude contactar con n8n: %s" % str(e)[:150]
    if resp.status_code >= 400:
        return False, "n8n respondio HTTP %s: %s" % (resp.status_code, resp.text[:200])
    detalle = "HTTP %s" % resp.status_code
    try:
        cuerpo = resp.json()
        if isinstance(cuerpo, dict) and cuerpo.get("message"):
            detalle += ": %s" % str(cuerpo["message"])[:80]
    except ValueError:
        pass
    return True, detalle


async def run_automation(
    key: str, params: dict[str, Any] | None = None, confirm: bool = False
) -> dict[str, Any]:
    """Seleccion + permisos + ejecucion en n8n (tool run_automation)."""
    spec = resolve(key)
    if not spec:
        return {
            "success": False,
            "message": (
                "No conozco la automatizacion '%s'. Claves disponibles: %s"
                % (key, ", ".join(sorted(AUTOMATIONS)))
            ),
            "disponibles": sorted(AUTOMATIONS),
        }

    decision = authorize(spec, confirm)
    if not decision["allowed"]:
        return {
            "success": False,
            "needs_confirmation": True,
            "automation": spec.name,
            "mode": spec.mode,
            "message": (
                "'%s' tiene modo '%s' (%s): pide confirmacion al usuario y "
                "vuelve a llamar con confirm=true cuando acepte."
                % (spec.name, MODE_ES[spec.mode], decision["reason"])
            ),
        }

    payload = params if isinstance(params, dict) else {}
    ok, detalle = await _post(spec.url, payload)
    logger.info(
        "Orquestador: %s (%s) -> %s [%s] %s",
        spec.key,
        spec.mode,
        "ok" if ok else "error",
        spec.webhook,
        detalle,
    )
    return {
        "success": ok,
        "automation": spec.name,
        "key": spec.key,
        "mode": spec.mode,
        "message": (
            "'%s' lanzada en n8n (%s)." % (spec.name, detalle)
            if ok
            else "No se pudo lanzar '%s': %s" % (spec.name, detalle)
        ),
    }


def webhook_preview(spec: Automation) -> str:
    """URL del webhook manual (para depuracion; no se expone al modelo)."""
    return quote(spec.url, safe=":/?=&")
