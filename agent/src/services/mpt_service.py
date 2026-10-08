"""Puente SSH para ejecutar MoneyPrinterTurbo en el PC del usuario.

El bot (agente) corre en el HP y MPT vive en el PC local; el puente es SSH
con clave dedicada (clave del HP autorizada en el PC con ``restrict,from=``
y sin PTY ni forwards). Sólo se lanzan comandos cerrados de MPT: nunca se
ejecuta shell arbitrario con texto del usuario.

Variables de entorno (ver config.py): MPT_SSH_TARGET, MPT_SSH_KEY, MPT_DIR.
Si falta MPT_SSH_TARGET la tool devuelve un error estructurado y claro.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import subprocess
from typing import Any

from src.config import settings

LOG_REMOTO = "storage/hacer_videos.log"
UV_REMOTO = "~/.local/bin/uv"
_TIMEOUT_SALIDA = 20
_GUION_RE = re.compile(r"^[^/\\]{1,120}\.md$")


def _sanitizar_guion(guion: str) -> str:
    """Devuelve el nombre del .md o lanza ValueError (sin rutas arbitrarias)."""
    texto = guion.strip()
    if texto.startswith("guiones/"):
        texto = texto[len("guiones/") :].strip()
    elif "/" in texto or "\\" in texto:
        raise ValueError("ruta no permitida: escribe sólo el nombre del .md")
    if not texto or texto.startswith(".") or not _GUION_RE.match(texto):
        raise ValueError("nombre de guion no valido (esperado algo como 'uno.md')")
    return texto


def _ssh(comando: str) -> str:
    """Ejecuta un comando cerrado en el PC local por SSH (bloqueante)."""
    if not settings.mpt_ssh_target:
        raise RuntimeError("MPT_SSH_TARGET sin configurar en .env")
    destino = settings.mpt_ssh_target.split()[0]
    known_hosts = os.path.join(os.path.dirname(settings.mpt_ssh_key), "known_hosts")
    cmd = [
        "ssh",
        "-i",
        settings.mpt_ssh_key,
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "UserKnownHostsFile=" + known_hosts,
        destino,
        comando,
    ]
    try:
        resultado = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT_SALIDA,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("ssh no disponible en esta imagen") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("SSH sin respuesta en %d s" % _TIMEOUT_SALIDA) from exc
    if resultado.returncode != 0:
        detalle = (resultado.stderr or resultado.stdout or "").strip()[:300]
        raise RuntimeError("SSH fallo (codigo %d): %s" % (resultado.returncode, detalle))
    return resultado.stdout.strip()


def _cd() -> str:
    d = settings.mpt_dir
    if d == "~" or d.startswith("~/"):
        return "cd %s" % d
    return "cd %s" % shlex.quote(d)


def _construir_comando_lanzar(guion: str, fuerza: bool) -> str:
    partes = [UV_REMOTO, "run", "python", "hacer_videos.py"]
    if fuerza:
        partes.append("--fuerza")
    if guion:
        partes.append(shlex.quote(guion))
    linea = " ".join(partes)
    return "%s && mkdir -p storage && ( nohup %s > %s 2>&1 < /dev/null & echo LANZADO_PID=$! )" % (
        _cd(),
        linea,
        LOG_REMOTO,
    )


def _comando_estado() -> str:
    return (
        "%s; "
        "if pgrep -f '[h]acer_videos.py' >/dev/null 2>&1; then "
        "echo ESTADO=CORRIENDO; else echo ESTADO=PARADO; fi; "
        "if [ -f %s ]; then tail -n 25 %s; else echo '(sin log todavia)'; fi"
        % (_cd(), LOG_REMOTO, LOG_REMOTO)
    )


def _resultado_lanzar(salida: str) -> dict[str, Any]:
    pid = ""
    for trozo in salida.split():
        if trozo.startswith("LANZADO_PID="):
            pid = trozo.split("=", 1)[1]
    mensaje = "Generacion lanzada en el PC"
    if pid:
        mensaje += " (pid %s)" % pid
    mensaje += (
        ". Tarda varios minutos; pregunta 'estado de hacer_videos' cuando "
        "quieras ver el avance. Al terminar, el timer de subidas la envia a "
        "n8n para tu aprobacion en Telegram."
    )
    return {"success": True, "message": mensaje, "pid": pid}


async def ejecutar_hacer_videos(
    guion: str = "", fuerza: bool = False, accion: str = ""
) -> dict[str, Any]:
    """Entry point de la tool: 'lanzar' (defecto) o 'estado'."""
    accion = (accion or "lanzar").strip().lower()
    if accion not in ("lanzar", "estado"):
        return {
            "success": False,
            "message": "accion no reconocida: usa 'lanzar' o 'estado'.",
        }

    try:
        if accion == "estado":
            salida = await asyncio.to_thread(_ssh, _comando_estado())
            return _resultado_estado(salida)
        nombre = _sanitizar_guion(guion) if guion.strip() else ""
    except ValueError as exc:
        return {"success": False, "message": str(exc)}
    except RuntimeError as exc:
        return {"success": False, "message": str(exc)}

    try:
        salida = await asyncio.to_thread(_ssh, _construir_comando_lanzar(nombre, fuerza))
    except RuntimeError as exc:
        return {"success": False, "message": str(exc)}
    if "LANZADO_PID=" not in salida:
        return {
            "success": False,
            "message": "El PC no confirmo el arranque: %s" % (salida[:300] or "(sin salida)"),
        }
    return _resultado_lanzar(salida)


def _resultado_estado(salida: str) -> dict[str, Any]:
    corriendo = "ESTADO=CORRIENDO" in salida
    cola = "Corriendo ahora" if corriendo else "Parado (terminado o todavia no lanzado)"
    log = "\n".join(
        linea
        for linea in salida.splitlines()
        if not linea.startswith("ESTADO=") and not linea.startswith("cd ")
    ).strip()
    mensaje = "hacer_videos: %s.\n" % cola
    mensaje += log[-1800:] if log else "(sin log todavia)"
    return {"success": True, "message": mensaje, "corriendo": corriendo}
