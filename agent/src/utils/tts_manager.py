import asyncio
import shutil
import tempfile
import threading
from pathlib import Path

from src.config import settings
from src.logger import logger

TTS_MODELS_DIR = Path("/app/tts_models")
VOICE_URL_PREFIX = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
FALLBACK_LANG = "es"
DEFAULT_VOICE = "es_ES-davefx-medium"

_tts_ready = False

# Cache del modelo Piper (2026-09-27): antes se creaba InferenceSession y
# PiperVoice en CADA fragmento, lo que anadia latencia y degradaba el audio
# (tartamudeo). Ahora se carga una sola vez y se reutiliza.
_piper_voice = None
_piper_lock = threading.Lock()


def _voice_parts(name: str) -> tuple[str, str, str, str]:
    """'es_ES-davefx-medium' -> (lang, locale, speaker, quality)."""
    parts = name.split("-")
    if len(parts) < 3 or "_" not in parts[0]:
        raise ValueError("TTS_VOICE debe tener el formato es_ES-davefx-medium")
    locale = parts[0]
    quality = parts[-1]
    speaker = "-".join(parts[1:-1])
    lang = locale.split("_")[0]
    return lang, locale, speaker, quality


async def ensure_voice_model() -> Path | None:
    """Descarga (una vez) la voz configurada en TTS_VOICE y la devuelve."""
    TTS_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    name = (settings.tts_voice or DEFAULT_VOICE).strip()
    model_path = TTS_MODELS_DIR / (name + ".onnx")
    config_path = TTS_MODELS_DIR / (name + ".onnx.json")
    global _tts_ready
    if model_path.exists() and config_path.exists():
        _tts_ready = True
        return model_path

    try:
        lang, locale, speaker, quality = _voice_parts(name)
    except ValueError as e:
        logger.warning("TTS_VOICE invalido (%s); usa es_ES-davefx-medium", e)
        return None

    model_url = f"{VOICE_URL_PREFIX}/{lang}/{locale}/{speaker}/{quality}/{name}.onnx"
    config_url = model_url + ".json"
    try:
        import httpx

        async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
            logger.info("Downloading Piper voice %s ...", name)
            resp = await client.get(model_url)
            resp.raise_for_status()
            model_path.write_bytes(resp.content)
            logger.info("Model saved (%d bytes)", len(resp.content))
            resp = await client.get(config_url)
            resp.raise_for_status()
            config_path.write_bytes(resp.content)
            logger.info("Config saved (%d bytes)", len(resp.content))
    except Exception as e:
        logger.warning("Failed to download Piper model: %s. Will use espeak fallback.", e)
        return None

    _tts_ready = True
    return model_path


async def text_to_speech(text: str) -> Path | None:
    # Limpieza para voz (2026-09-28): quita Markdown (se leia "asterisco
    # asterisco") y emojis (piper balbuceaba al inicio con ellos).
    from src.utils.voice_text import sanitize_for_tts

    text = sanitize_for_tts(text)
    if not text:
        return None
    output_dir = Path(tempfile.mkdtemp(prefix="rafita_tts_"))
    output_path = output_dir / "response.wav"

    model_path = await ensure_voice_model()

    if model_path is None:
        return await _fallback_espeak(text, output_path)

    try:
        logger.debug("TTS synthesizing %d chars via Piper", len(text))

        loop = asyncio.get_event_loop()
        audio, sample_rate = await loop.run_in_executor(
            None, _synthesize_piper, text, str(model_path), str(output_path)
        )

        if audio is None or len(audio) == 0:
            raise ValueError("Piper returned empty audio")

        logger.info("TTS generated: %s (%d samples, %d Hz)", output_path, len(audio), sample_rate)
        return output_path

    except Exception as e:
        logger.warning("Piper TTS failed: %s. Falling back to espeak.", e)
        return await _fallback_espeak(text, output_path)


def _load_piper_voice(model_path: str):
    """Carga (una sola vez) el modelo Piper y lo deja cacheado."""
    global _piper_voice
    with _piper_lock:
        if _piper_voice is not None:
            return _piper_voice

        import json

        import onnxruntime
        from piper import PiperVoice
        from piper.config import PhonemeType, PiperConfig

        model_path = str(model_path)
        config_path = model_path.rsplit(".", 1)[0] + ".onnx.json"

        with open(config_path) as f:
            cfg = json.load(f)

        length_scale = cfg.get("inference", {}).get("length_scale", 1.0)
        speed = float(getattr(settings, "tts_speed", 0.0) or 0.0)
        if speed > 0:
            # TTS_SPEED: >1 = mas lento y pausado, <1 = mas rapido.
            length_scale = speed

        config = PiperConfig(
            num_symbols=cfg["num_symbols"],
            num_speakers=cfg["num_speakers"],
            sample_rate=cfg.get("audio", {}).get("sample_rate", 22050),
            espeak_voice=cfg.get("espeak", {}).get("voice", ""),
            length_scale=length_scale,
            noise_scale=cfg.get("inference", {}).get("noise_scale", 0.667),
            noise_w=cfg.get("inference", {}).get("noise_w", 0.8),
            phoneme_id_map=cfg["phoneme_id_map"],
            phoneme_type=PhonemeType.ESPEAK,
        )

        session = onnxruntime.InferenceSession(model_path)
        _piper_voice = PiperVoice(session, config)
        logger.info("Piper voice loaded and cached: %s", Path(model_path).name)
        return _piper_voice


async def prewarm_tts() -> bool:
    """Precalienta Piper al arrancar (la primera llamada ya no paga la carga)."""
    model_path = await ensure_voice_model()
    if model_path is None:
        return False
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _load_piper_voice, str(model_path))
        return True
    except Exception as e:
        logger.warning("Piper preload failed: %s", e)
        return False


def _synthesize_piper(text: str, model_path: str, output_path: str):
    try:
        import wave

        voice = _load_piper_voice(model_path)
        with _piper_lock, wave.open(output_path, "w") as wav_file:
            voice.synthesize(text, wav_file)

        import soundfile as sf

        data, samplerate = sf.read(output_path)
        logger.info("Piper TTS: %d samples at %d Hz", len(data), samplerate)
        return data, samplerate

    except Exception as e:
        logger.error("Piper synthesis error: %s", e)
        import traceback

        traceback.print_exc()
        return None, 22050


async def _fallback_espeak(text: str, output_path: Path) -> Path | None:
    try:
        max_chars = 500
        if len(text) > max_chars:
            text = text[:max_chars] + "..."
        cmd = [
            "espeak-ng",
            "-v",
            FALLBACK_LANG,
            "-w",
            str(output_path),
            text,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
        if output_path.exists() and output_path.stat().st_size > 0:
            logger.info("TTS fallback (espeak): %s", output_path)
            return output_path
        logger.warning("espeak produced empty output")
        return None
    except Exception as e:
        logger.error("TTS fallback error: %s", e)
        return None


async def synthesize_wav_bytes(text: str) -> bytes | None:
    """Sintetiza y devuelve WAV en memoria (modo llamada: sin ffmpeg/OGG).

    El navegador decodifica WAV nativamente con decodeAudioData, asi que la
    conversion a OGG (un proceso ffmpeg por fragmento) era latencia pura.
    """
    wav_path = await text_to_speech(text)
    if wav_path is None:
        return None
    try:
        return wav_path.read_bytes()
    except OSError as e:
        logger.warning("No se pudo leer el WAV sintetizado: %s", e)
        return None
    finally:
        shutil.rmtree(wav_path.parent, ignore_errors=True)


async def convert_to_ogg(wav_path: Path) -> Path | None:
    ogg_path = wav_path.with_suffix(".ogg")
    try:
        cmd = ["ffmpeg", "-y", "-i", str(wav_path), "-c:a", "libopus", "-b:a", "24k", str(ogg_path)]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
        if ogg_path.exists() and ogg_path.stat().st_size > 0:
            return ogg_path
        return wav_path
    except Exception:
        return wav_path
