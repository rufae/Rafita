"""Alertas proactivas de infraestructura (mejora 2).

Vigila las piezas críticas (disco, IA, base vectorial, backup, restore-drill,
STT remoto, contenedores del nodo, conectividad y certificados) cada
`INFRA_CHECK_MINUTES` minutos y avisa al administrador por Telegram solo
cuando algo se degrada, con un enfriamiento por incidencia
(`INFRA_ALERT_COOLDOWN_HOURS`) para no repetir el mismo aviso. Además
registra latencia y tokens en las métricas (ver `utils.telemetry` y
`/metrics`).
"""

import asyncio
import shutil
import time
from collections.abc import Awaitable, Callable
from datetime import UTC
from typing import Any

from src.config import settings
from src.logger import logger
from src.utils.automation_status import read_status_file
from src.utils.telemetry import metrics

DISK_WARN_PCT = 85.0
DISK_CRIT_PCT = 90.0
BACKUP_MAX_AGE_H = 26.0
CERT_WARN_DAYS = 30
CERT_CRIT_DAYS = 7
DOCKER_STATUS_MAX_AGE_H = 30.0
RESTART_LOOP_MIN = 10


def check_disk() -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(settings.data_dir)
    except Exception as e:
        return {
            "name": "disco",
            "ok": False,
            "severity": "warning",
            "detail": "sin datos de disco (%s)" % str(e)[:80],
        }
    pct = usage.used * 100.0 / max(usage.total, 1)
    detail = "disco al %.1f%% (%.1f GB libres)" % (pct, usage.free / 1e9)
    if pct >= DISK_CRIT_PCT:
        return {"name": "disco", "ok": False, "severity": "critical", "detail": detail}
    if pct >= DISK_WARN_PCT:
        return {"name": "disco", "ok": False, "severity": "warning", "detail": detail}
    return {"name": "disco", "ok": True, "severity": "info", "detail": detail}


def check_backup() -> dict[str, Any]:
    from datetime import datetime

    backup = read_status_file("backup-status.json")
    stamp = str(backup.get("timestamp") or "")
    if not stamp:
        return {
            "name": "backup",
            "ok": False,
            "severity": "warning",
            "detail": "sin datos de backup (¿ha corrido alguna vez?)",
        }
    age_h: float | None = None
    try:
        when = datetime.fromisoformat(stamp)
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        age_h = (datetime.now(UTC).timestamp() - when.timestamp()) / 3600.0
    except Exception:
        age_h = None
    if age_h is not None and age_h > BACKUP_MAX_AGE_H:
        return {
            "name": "backup",
            "ok": False,
            "severity": "critical",
            "detail": "último backup hace %.0f h (máximo %d h)" % (age_h, int(BACKUP_MAX_AGE_H)),
        }
    return {
        "name": "backup",
        "ok": True,
        "severity": "info",
        "detail": "último backup %s" % stamp[:16],
    }


def check_restore_drill() -> dict[str, Any]:
    drill = read_status_file("restore-drill-status.json")
    integrity = drill.get("integrity")
    if integrity in (None, "", "ok"):
        return {
            "name": "restore-drill",
            "ok": True,
            "severity": "info",
            "detail": "integridad %s" % (integrity or "sin datos"),
        }
    return {
        "name": "restore-drill",
        "ok": False,
        "severity": "critical",
        "detail": "integridad '%s'" % integrity,
    }


async def check_llm() -> dict[str, Any]:
    try:
        from src.ollama_client import llm

        health = await llm.check_health()
    except Exception as e:
        return {
            "name": "ia",
            "ok": False,
            "severity": "critical",
            "detail": "IA inaccesible (%s)" % str(e)[:120],
        }
    status = str(health.get("status") or "unhealthy")
    if status == "unhealthy":
        return {
            "name": "ia",
            "ok": False,
            "severity": "critical",
            "detail": str(health.get("detail") or "modelo no disponible"),
        }
    return {"name": "ia", "ok": True, "severity": "info", "detail": "IA %s" % status}


async def check_vector_db() -> dict[str, Any]:
    try:
        from src.utils.vector_manager import vector_db

        health = await vector_db.health()
    except Exception as e:
        return {
            "name": "rag",
            "ok": False,
            "severity": "warning",
            "detail": "RAG inaccesible (%s)" % str(e)[:120],
        }
    if health.get("status") != "ok":
        return {
            "name": "rag",
            "ok": False,
            "severity": "warning",
            "detail": str(health.get("detail") or "base vectorial en error"),
        }
    return {
        "name": "rag",
        "ok": True,
        "severity": "info",
        "detail": "%s chunks" % health.get("chunks", 0),
    }


async def check_whisper_remote() -> dict[str, Any]:
    remote = (settings.whisper_remote_url or "").strip().rstrip("/")
    if not remote:
        return {
            "name": "stt",
            "ok": True,
            "severity": "info",
            "detail": "STT local (sin servicio remoto)",
        }
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("%s/health" % remote)
            resp.raise_for_status()
    except Exception as e:
        return {
            "name": "stt",
            "ok": False,
            "severity": "info",
            # No es alertable: la torre puede estar dormida a propósito y el
            # fallback local (Whisper `small`) cubre el servicio.
            "alertable": False,
            "detail": "STT remoto no disponible (%s); se usa el local" % str(e)[:80],
        }
    return {"name": "stt", "ok": True, "severity": "info", "detail": "STT remoto OK"}


def check_docker_services() -> dict[str, Any]:
    """Contenedores del nodo en restart-loop (mejora Fase 3, ítems 11-12).

    Lee `data/docker-status.json`, que escribe el script del host
    `deploy/hp/infra/docker_status.sh` (cron de usuario o hook de
    auto_update.sh): el agente no tiene docker.sock. Sin fichero (o con
    datos viejos) lo dice y no alerta.
    """
    from datetime import datetime

    data = read_status_file("docker-status.json")
    conts = data.get("containers")
    if not isinstance(conts, list) or not conts:
        return {
            "name": "docker",
            "ok": True,
            "severity": "info",
            "detail": "sin datos de contenedores (docker_status.sh sin ejecutar)",
        }
    age_h: float | None = None
    try:
        when = datetime.fromisoformat(str(data.get("generated_at") or ""))
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        age_h = (datetime.now(UTC).timestamp() - when.timestamp()) / 3600.0
    except Exception:
        age_h = None
    stale = age_h is not None and age_h > DOCKER_STATUS_MAX_AGE_H
    loops: list[str] = []
    for c in conts:
        try:
            count = int(c.get("restart_count") or 0)
        except Exception:
            count = 0
        if str(c.get("state") or "") == "restarting" or count >= RESTART_LOOP_MIN:
            loops.append("%s (reinicios: %d)" % (c.get("name", "?"), count))
    if loops:
        detail = "restart-loop: " + ", ".join(loops)[:140]
        if stale:
            detail += " [datos de hace %.0f h]" % (age_h or 0)
        return {"name": "docker", "ok": False, "severity": "warning", "detail": detail}
    detail = "%d contenedores, ninguno en restart-loop" % len(conts)
    if stale:
        detail += " (datos de hace %.0f h)" % (age_h or 0)
    return {"name": "docker", "ok": True, "severity": "info", "detail": detail}


async def check_connectivity() -> dict[str, Any]:
    """Salida a red desde el contenedor (Telegram, GitHub, URLs de CONFIG)."""
    urls = [u.strip() for u in (settings.connectivity_urls or "").split(",") if u.strip()]
    if not urls:
        return {
            "name": "conectividad",
            "ok": True,
            "severity": "info",
            "detail": "sin URLs configuradas (CONNECTIVITY_URLS)",
        }
    fallos: list[str] = []
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            for url in urls:
                host = url.split("//", 1)[-1].split("/", 1)[0]
                try:
                    resp = await client.get(url)
                    if resp.status_code >= 500:
                        fallos.append("%s (HTTP %d)" % (host, resp.status_code))
                except Exception as e:
                    fallos.append("%s (%s)" % (host, str(e)[:40]))
    except Exception as e:
        return {
            "name": "conectividad",
            "ok": False,
            "severity": "warning",
            "detail": "comprobación no disponible (%s)" % str(e)[:80],
        }
    if fallos:
        return {
            "name": "conectividad",
            "ok": False,
            "severity": "warning",
            "detail": "sin respuesta: " + ", ".join(fallos)[:150],
        }
    return {
        "name": "conectividad",
        "ok": True,
        "severity": "info",
        "detail": "%d destinos accesibles" % len(urls),
    }


async def check_certificates() -> dict[str, Any]:
    """Caducidad de los certificados X.509 de CERT_CHECK_DIR (*.crt/*.pem).

    En el HP el override monta `~/certs/tailscale` en `/data/certs`. Aviso
    con <30 días (warning) y <7 días (critical).
    """
    from datetime import date

    from cryptography import x509

    directory = getattr(settings, "cert_check_dir", "") or ""
    files: list[Any] = []
    if directory:
        try:
            from pathlib import Path

            base = Path(directory)
            if base.is_dir():
                files = [
                    f
                    for f in sorted([*base.glob("*.crt"), *base.glob("*.pem")])
                    if "key" not in f.name.lower() and "priv" not in f.name.lower()
                ]
        except Exception:
            files = []
    if not files:
        return {
            "name": "certificados",
            "ok": True,
            "severity": "info",
            "alertable": False,
            "detail": "sin certificados en %s (CERT_CHECK_DIR)" % (directory or "(sin configurar)"),
        }
    hoy = date.today()
    detalles: list[str] = []
    malos: list[str] = []
    peor = "info"
    for f in files:
        try:
            cert = x509.load_pem_x509_certificate(f.read_bytes())
            fin = getattr(cert, "not_valid_after_utc", None)
            if fin is None:
                fin = cert.not_valid_after
            dias = (fin.date() - hoy).days
        except Exception as e:
            malos.append("%s: ilegible (%s)" % (f.stem, str(e)[:40]))
            peor = "warning"
            continue
        detalles.append("%s: %d d" % (f.stem, dias))
        if dias < CERT_CRIT_DAYS:
            malos.append("%s: caduca en %d d" % (f.stem, dias))
            peor = "critical"
        elif dias < CERT_WARN_DAYS and peor != "critical":
            malos.append("%s: caduca en %d d" % (f.stem, dias))
            peor = "warning"
    if malos:
        return {
            "name": "certificados",
            "ok": False,
            "severity": peor,
            "detail": "; ".join(malos)[:150],
        }
    return {
        "name": "certificados",
        "ok": True,
        "severity": "info",
        "detail": "; ".join(detalles)[:150],
    }


async def run_infra_checks() -> list[dict[str, Any]]:
    """Ejecuta todas las comprobaciones y devuelve sus resultados."""
    checks = [check_disk(), check_backup(), check_restore_drill(), check_docker_services()]
    for fn in (
        check_llm,
        check_vector_db,
        check_whisper_remote,
        check_connectivity,
        check_certificates,
    ):
        checks.append(await fn())
    metrics.set_gauge("infra_checks_ok", 1.0 if all(c.get("ok") for c in checks) else 0.0)
    return checks


async def notify_issues(
    checks: list[dict[str, Any]],
    send: Callable[[str], Awaitable[None]] | None = None,
) -> list[dict[str, Any]]:
    """Avisa de las incidencias con cooldown por incidencia.

    `send` permite inyectar el destino en tests; por defecto se envía a los
    administradores por Telegram.
    """
    from src.database import db

    cooldown_s = float(getattr(settings, "infra_alert_cooldown_hours", 6.0)) * 3600.0
    now = time.time()
    sent: list[dict[str, Any]] = []
    for check in checks:
        if check.get("ok"):
            continue
        if check.get("alertable") is False:
            continue
        key = "infra_alert_last:%s" % check.get("name", "?")
        try:
            last = float((await db.kv_get(key)) or 0.0)
        except Exception:
            last = 0.0
        if now - last < cooldown_s:
            continue
        try:
            await db.kv_set(key, str(now))
        except Exception:
            pass
        icon = "🚨" if check.get("severity") == "critical" else "⚠️"
        text = "%s *Infraestructura: %s*\n%s" % (
            icon,
            check.get("name", "?"),
            check.get("detail", ""),
        )
        try:
            if send is not None:
                await send(text)
            else:
                from src.bot import bot

                for chat_id in settings.admin_ids or []:
                    await bot.send_proactive_message(chat_id, text)
        except Exception as e:
            logger.warning("Infra: no se pudo enviar el aviso: %s", e)
        sent.append(check)
    return sent


class InfraWorker:
    """Bucle periódico de comprobaciones + avisos (se arranca con la app)."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    async def start(self, shutdown_event: asyncio.Event) -> None:
        self._shutdown_event = shutdown_event
        self._task = asyncio.create_task(self._run_loop())
        logger.info("InfraWorker started (cada %d min)", settings.infra_check_minutes)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("InfraWorker stopped")

    async def _run_loop(self) -> None:
        try:
            while True:
                try:
                    checks = await run_infra_checks()
                    await notify_issues(checks)
                except Exception as e:
                    logger.warning("Infra check falló: %s", e)
                await asyncio.sleep(max(int(settings.infra_check_minutes), 5) * 60)
        except asyncio.CancelledError:
            logger.info("InfraWorker cancelled")
