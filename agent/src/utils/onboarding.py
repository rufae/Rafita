"""Onboarding guiado (mejora 3): checklist real de configuración en /start.

En lugar de un texto estático, `/start` comprueba el estado real de la
instalación (ubicación, IA, Google, backups, voz, administradores, bóveda) y
muestra qué está listo y cuál es el siguiente paso concreto. Todo es
consultable en local; no se llama a servicios externos salvo la propia IA.
"""

from typing import Any

from src.config import settings


def render_onboarding(items: list[dict[str, Any]]) -> str:
    """Formatea el checklist (función pura, testeable)."""
    lines = ["*Estado de tu instalación:*\n"]
    for item in items:
        icon = {"ok": "✅", "warn": "⚠️", "fail": "❌"}.get(item.get("status", ""), "•")
        lines.append("%s *%s*: %s" % (icon, item["title"], item["detail"]))
    pendientes = [i for i in items if i.get("status") != "ok" and i.get("next")]
    if not pendientes:
        lines.append("\nTodo listo. Usa /ayuda para ver los comandos disponibles.")
        return "\n".join(lines)
    lines.append("\n*Siguiente paso:*")
    for item in pendientes:
        lines.append("- %s: `%s`" % (item["title"], item["next"]))
    lines.append(
        "\nPuedes escribirme cualquier mensaje cuando quieras; esto solo es "
        "la configuración inicial."
    )
    return "\n".join(lines)


async def collect_onboarding_status() -> list[dict[str, Any]]:
    """Recoge el estado real de cada pieza de la instalación."""
    items: list[dict[str, Any]] = []

    # 1. Ubicación (tiempo y avisos CAP)
    configured = ""
    try:
        from src.database import db

        configured = ((await db.kv_get("briefing_municipio")) or "").strip()
    except Exception:
        configured = ""
    if not configured:
        configured = (settings.briefing_municipio or "").strip()
    items.append(
        {
            "title": "Ubicación",
            "status": "ok" if configured else "fail",
            "detail": configured or "sin configurar (hace falta para el tiempo y los avisos)",
            "next": "" if configured else "/ubicacion <tu ciudad>",
        }
    )

    # 2. IA local (modelo cargado o disponible)
    try:
        from src.ollama_client import llm

        health = await llm.check_health()
        status_llm = "ok" if health.get("status") == "ok" else "warn"
        detail_llm = "modelo %s disponible" % settings.ollama_model
        if health.get("status") == "degraded":
            detail_llm = health.get("detail") or "modelo disponible (se carga al primer uso)"
        elif health.get("status") == "unhealthy":
            status_llm = "fail"
            detail_llm = health.get("detail") or "el modelo no está disponible"
    except Exception as e:
        status_llm = "fail"
        detail_llm = "sin conexión con la IA: %s" % str(e)[:80]
    items.append(
        {
            "title": "IA local",
            "status": status_llm,
            "detail": detail_llm,
            "next": "" if status_llm == "ok" else "comprueba Ollama (`ollama list`)",
        }
    )

    # 3. Google (opcional: calendario/drive/tareas)
    try:
        from src.services.google_services_manager import google_services

        g_ready = google_services.is_ready
    except Exception:
        g_ready = False
    items.append(
        {
            "title": "Google",
            "status": "ok" if g_ready else "warn",
            "detail": "conectado"
            if g_ready
            else "no conectado (opcional; el resto funciona en local)",
            "next": "" if g_ready else "/setup_google",
        }
    )

    # 4. Backups (escritos por deploy/hp/backup/backup.sh)
    from src.utils.automation_status import read_status_file

    backup = read_status_file("backup-status.json")
    if backup.get("timestamp"):
        items.append(
            {
                "title": "Backups",
                "status": "ok",
                "detail": "último backup %s" % str(backup.get("timestamp"))[:16],
                "next": "",
            }
        )
    else:
        items.append(
            {
                "title": "Backups",
                "status": "warn",
                "detail": "sin datos de backup todavía",
                "next": "instala el backup (deploy/hp/backup/README.md)",
            }
        )

    # 5. Voz (modelo Piper/TTS presente)
    try:
        from src.utils.tts_manager import TTS_MODELS_DIR

        tts_files = list(TTS_MODELS_DIR.glob("*.onnx"))
        tts_ok = bool(tts_files)
    except Exception:
        tts_ok = False
    items.append(
        {
            "title": "Voz (TTS)",
            "status": "ok" if tts_ok else "warn",
            "detail": "modelo de voz cargado"
            if tts_ok
            else "sin modelo de voz (se descarga al usarlo)",
            "next": "",
        }
    )

    # 6. Administradores (acceso al bot)
    admin_ok = bool(settings.admin_ids)
    items.append(
        {
            "title": "Administradores",
            "status": "ok" if admin_ok else "fail",
            "detail": "%d configurado(s)" % len(settings.admin_ids)
            if admin_ok
            else "ADMIN_IDS vacío: nadie podrá usar el bot",
            "next": "" if admin_ok else "define ADMIN_IDS en el .env",
        }
    )

    # 7. Bóveda (segundo cerebro)
    try:
        from pathlib import Path

        vault_dir = Path(settings.obsidian_vault_dir)
        vault_ok = vault_dir.is_dir() and any(vault_dir.rglob("*.md"))
    except Exception:
        vault_ok = False
    items.append(
        {
            "title": "Bóveda (segundo cerebro)",
            "status": "ok" if vault_ok else "warn",
            "detail": "notas indexables presentes" if vault_ok else "carpeta de bóveda vacía",
            "next": "" if vault_ok else "añade notas en %s" % settings.obsidian_vault_dir,
        }
    )

    return items
