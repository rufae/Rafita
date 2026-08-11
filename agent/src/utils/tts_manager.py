import asyncio
import tempfile
from pathlib import Path

from src.logger import logger

TTS_MODELS_DIR = Path("/app/tts_models")
VOICE_URL_PREFIX = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
FALLBACK_LANG = "es"

_tts_ready = False


async def ensure_voice_model() -> Path | None:
    TTS_MODELS_DIR.mkdir(parents=True, exist_ok=True)

    model_files = sorted(TTS_MODELS_DIR.glob("*.onnx"))
    if not model_files:
        logger.info("No Piper model found. Downloading Spanish voice...")
        import httpx
        model_name = "es_ES-carlfm-x_low"
        model_url = f"{VOICE_URL_PREFIX}/es/es_ES/carlfm/x_low/{model_name}.onnx"
        config_url = f"{VOICE_URL_PREFIX}/es/es_ES/carlfm/x_low/{model_name}.onnx.json"
        model_path = TTS_MODELS_DIR / f"{model_name}.onnx"
        config_path = TTS_MODELS_DIR / f"{model_name}.onnx.json"

        try:
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                logger.info("Downloading model from %s ...", model_url)
                resp = await client.get(model_url)
                resp.raise_for_status()
                model_path.write_bytes(resp.content)
                logger.info("Model saved (%d bytes)", len(resp.content))
                logger.info("Downloading config from %s ...", config_url)
                resp = await client.get(config_url)
                resp.raise_for_status()
                config_path.write_bytes(resp.content)
                logger.info("Config saved (%d bytes)", len(resp.content))
            model_files = [model_path]
        except Exception as e:
            logger.warning("Failed to download Piper model: %s. Will use espeak fallback.", e)
            return None

    global _tts_ready
    _tts_ready = True
    return model_files[0]


async def text_to_speech(text: str) -> Path | None:
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


def _synthesize_piper(text: str, model_path: str, output_path: str):
    try:
        import json
        import wave

        import onnxruntime
        from piper import PiperVoice
        from piper.config import PhonemeType, PiperConfig

        model_path = str(model_path)
        base = model_path.rsplit(".", 1)[0]
        config_path = base + ".onnx.json"

        with open(config_path) as f:
            cfg = json.load(f)

        phoneme_type = PhonemeType.ESPEAK

        config = PiperConfig(
            num_symbols=cfg["num_symbols"],
            num_speakers=cfg["num_speakers"],
            sample_rate=cfg.get("audio", {}).get("sample_rate", 22050),
            espeak_voice=cfg.get("espeak", {}).get("voice", ""),
            length_scale=cfg.get("inference", {}).get("length_scale", 1.0),
            noise_scale=cfg.get("inference", {}).get("noise_scale", 0.667),
            noise_w=cfg.get("inference", {}).get("noise_w", 0.8),
            phoneme_id_map=cfg["phoneme_id_map"],
            phoneme_type=phoneme_type,
        )

        session = onnxruntime.InferenceSession(model_path)
        voice = PiperVoice(session, config)

        with wave.open(output_path, "w") as wav_file:
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
        safe_text = text.replace('"', '\\"')
        cmd = [
            "espeak-ng",
            "-v", FALLBACK_LANG,
            "-w", str(output_path),
            f'"{safe_text}"',
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
