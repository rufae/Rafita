"""Backend Kokoro-82M (ONNX) para una voz mas natural (2026-09-28).

Kokoro es un modelo de 82M parametros (Apache 2.0) que corre en CPU con
onnxruntime (el mismo motor que ya usa Piper), sin PyTorch. Se usa para notas
de voz, briefing y respuestas de chat (calidad); la llamada en tiempo real
sigue con Piper por latencia (ver TTS_ENGINE_CALL).

Modelos: release `model-files-v1.1` de kokoro-onnx
(kokoro-v1.0.int8.onnx ~114 MB + voices-v1.0.bin ~28 MB). Voces en espanol:
ef_dora, em_alex, em_santa.
"""

import asyncio
import threading
from pathlib import Path
from typing import Any

from src.config import settings
from src.logger import logger

KOKORO_DIR = Path("/app/tts_models/kokoro")
# Alternativa si el volumen de modelos no es escribible (bind mount de root):
# /data es el volumen persistente estandar y suele ser escribible.
FALLBACK_DIR = Path("/data/tts_models/kokoro")
MODEL_URL_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"
MODEL_FILE = "kokoro-v1.0.int8.onnx"
VOICES_FILE = "voices-v1.0.bin"
DEFAULT_VOICE = "ef_dora"
SPANISH_VOICES = ("ef_dora", "em_alex", "em_santa")

_kokoro: Any = None
_kokoro_lock = threading.Lock()


def resolve_voice(name: str | None) -> str:
    """Devuelve una voz Kokoro valida.

    TTS_VOICE tambien se usa para Piper (formato `es_ES-davefx-medium`); si el
    valor tiene pinta de Piper (contiene guion) se ignora y se usa la voz
    Kokoro por defecto.
    """
    candidate = (name or "").strip()
    if candidate and "-" not in candidate:
        return candidate
    return DEFAULT_VOICE


def speed_from_length_scale(scale: float) -> float:
    """TTS_SPEED es tipo length_scale (>1 = mas lento); Kokoro usa `speed`."""
    if not scale or scale <= 0:
        return 1.0
    return max(0.5, min(2.0, 1.0 / scale))


def _work_dir() -> Path:
    """Directorio escribible para los modelos (con alternativa automatica)."""
    for candidate in (KOKORO_DIR, FALLBACK_DIR):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError as e:
            logger.warning("Kokoro dir %s no utilizable: %s", candidate, e)
    return KOKORO_DIR


async def ensure_kokoro_model() -> tuple[Path, Path] | None:
    """Descarga (una sola vez) el modelo y las voces; devuelve sus rutas."""
    work_dir = _work_dir()
    model_path = work_dir / MODEL_FILE
    voices_path = work_dir / VOICES_FILE
    if model_path.exists() and voices_path.exists():
        return model_path, voices_path

    try:
        import httpx

        async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
            for url, path in (
                (f"{MODEL_URL_BASE}/{MODEL_FILE}", model_path),
                (f"{MODEL_URL_BASE}/{VOICES_FILE}", voices_path),
            ):
                if path.exists():
                    continue
                logger.info("Downloading Kokoro file %s ...", path.name)
                tmp = path.with_suffix(path.suffix + ".part")
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with open(tmp, "wb") as fh:
                        async for chunk in resp.aiter_bytes(1024 * 256):
                            fh.write(chunk)
                tmp.replace(path)
                logger.info("Kokoro %s saved (%d bytes)", path.name, path.stat().st_size)
    except Exception as e:
        logger.warning("No se pudo descargar Kokoro: %s", e)
        return None
    return model_path, voices_path


def _load_kokoro(model_path: Path, voices_path: Path):
    """Carga (una sola vez) el motor Kokoro y lo deja cacheado."""
    global _kokoro
    with _kokoro_lock:
        if _kokoro is not None:
            return _kokoro

        import onnxruntime as rt
        from kokoro_onnx import Kokoro

        so = rt.SessionOptions()
        threads = int(getattr(settings, "kokoro_threads", 0) or 0)
        if threads > 0:
            so.intra_op_num_threads = threads
            so.inter_op_num_threads = 1
        session = rt.InferenceSession(str(model_path), so, providers=["CPUExecutionProvider"])
        _kokoro = Kokoro.from_session(session, str(voices_path))
        logger.info("Kokoro engine loaded (%s)", model_path.name)
        return _kokoro


def _synthesize_blocking(
    model_path: Path, voices_path: Path, text: str, voice: str, speed: float
) -> tuple[Any, int]:
    engine = _load_kokoro(model_path, voices_path)
    samples, sample_rate = engine.create(text, voice=voice, lang="es", speed=speed)
    return samples, sample_rate


async def synthesize_kokoro(
    text: str, voice: str | None = None, speed: float = 1.0
) -> tuple[Any, int] | None:
    """Sintetiza texto en espanol; None si el backend no esta disponible."""
    paths = await ensure_kokoro_model()
    if paths is None:
        return None
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(
            None,
            _synthesize_blocking,
            paths[0],
            paths[1],
            text,
            resolve_voice(voice),
            speed,
        )
    except Exception as e:
        logger.warning("Kokoro synthesis failed: %s", e)
        return None


async def prewarm_kokoro() -> bool:
    """Precalienta el motor al arrancar (la primera sintesis no paga la carga)."""
    paths = await ensure_kokoro_model()
    if paths is None:
        return False
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _load_kokoro, paths[0], paths[1])
        return True
    except Exception as e:
        logger.warning("Kokoro preload failed: %s", e)
        return False
