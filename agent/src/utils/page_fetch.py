"""Lectura de paginas web (fase 3, 2026-10-07): Obscura, con httpx estatico
como fallback.

Obscura es un motor headless en Rust (no un fork de Chromium): renderiza JS,
habla CDP y pesa ~30 MB por instancia. Solo NAVEGA y renderiza: esta herramienta
no rellena formularios ni pulsa botones (modo solo-lectura decidido con el
usuario). Si el binario no esta instalado o falla, se cae al fetch estatico
httpx y se dice el metodo usado: honestidad ante todo.
"""

import asyncio
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from src.config import settings
from src.logger import logger
from src.utils.web_search import fetch_page_text

MAX_FETCH_CHARS = 6000
# Margen sobre el --timeout interno de obscura para matar el proceso huerfano.
OBSURA_EXTRA_TIMEOUT = 15


def obscura_binario() -> str | None:
    """Ruta al ejecutable de Obscura, o None si no esta instalado."""
    candidato = settings.obscura_bin.strip()
    if not candidato:
        return None
    if "/" in candidato:
        ruta = Path(candidato)
        return str(ruta) if ruta.is_file() else None
    return shutil.which(candidato)


def url_valida(url: str) -> bool:
    """Solo http(s) con host: descarta file://, javascript:, datos sueltos."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


async def _render_obscura(url: str) -> str:
    binario = obscura_binario()
    if not binario:
        raise RuntimeError("binario de Obscura no encontrado")
    timeout = settings.obscura_timeout
    proc = await asyncio.create_subprocess_exec(
        binario,
        "fetch",
        url,
        "--dump",
        "markdown",
        "--timeout",
        str(timeout),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout + OBSURA_EXTRA_TIMEOUT
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"Obscura no respondio en {timeout + OBSURA_EXTRA_TIMEOUT}s") from None
    salida = stdout.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0 or not salida:
        detail = stderr.decode("utf-8", errors="replace").strip().splitlines()
        ultimo = detail[-1] if detail else f"exit {proc.returncode}"
        raise RuntimeError(f"Obscura fallo: {ultimo[:200]}")
    return salida


async def fetch_page(url: str, max_chars: int = MAX_FETCH_CHARS) -> dict[str, Any]:
    """Devuelve {"success", "method": obscura|estatica|ninguno, "text"|"message"}."""
    url = (url or "").strip()
    if not url_valida(url):
        return {
            "success": False,
            "message": "URL no valida: usa http:// o https:// con host.",
        }

    texto = ""
    metodo = "ninguno"
    if obscura_binario():
        try:
            texto = await _render_obscura(url)
            metodo = "obscura"
        except Exception as e:
            logger.warning("Obscura fetch fallo (%s); se intenta modo estatico", e)

    if not texto:
        texto = await fetch_page_text(url, max_chars=max_chars)
        metodo = "estatica" if texto else "ninguno"

    if not texto:
        return {
            "success": False,
            "message": "No pude leer la pagina (Obscura y el modo estatico fallaron).",
        }
    return {"success": True, "method": metodo, "text": texto[:max_chars]}
