"""STT unificado: servicio remoto (GPU de la torre) con fallback local.

Contexto (2026-09-30): el HP de produccion tiene 6 GB de RAM y 4 nucleos sin
GPU. Whisper `small` en CPU alucina con ruido (una reunion real se transcribio
como basura). En la torre corre `large-v3` en la RTX 3060
(deploy/tower/whisper_service.sh). Este modulo intenta primero el servicio
remoto y, si no responde o no esta configurado, usa el Whisper local.

Ademas filtra ruido en los dos caminos:
- segmentos con `no_speech_prob` alto o `avg_logprob` bajo se descartan;
- frases tipicas de alucinacion de Whisper sobre ruido ("suscribete",
  "subtitulos realizados por...", etc.) se rechazan.
"""

import asyncio
import io
import logging
import struct
from typing import Any

from src.config import settings
from src.utils.voice_text import clean_stt_transcript

logger = logging.getLogger("rafita")

TARGET_SAMPLE_RATE = 16000
_LOCAL_MODEL = None

NSP_MAX = 0.6
LP_MIN = -1.0

# Alucinaciones tipicas de Whisper sobre ruido/silencio (espanol e ingles).
NOISE_HALLUCINATIONS = (
    "suscribete",
    "suscríbete",
    "no olvides suscribirte",
    "subtitulos realizados",
    "subtítulos realizados",
    "amara.org",
    "gracias por ver el video",
    "gracias por ver el vídeo",
    "traduccion realizada con la version gratuita",
    "traducción realizada con la versión gratuita",
    "www.deepl.com",
    "www.mooji.org",
    "mas informacion en www",
    "más información en www",
    "¡hasta luego!",
)


def _get_local_model():
    global _LOCAL_MODEL
    if _LOCAL_MODEL is None:
        try:
            from faster_whisper import WhisperModel

            _LOCAL_MODEL = WhisperModel(
                settings.whisper_model,
                device="cpu",
                compute_type="int8",
                cpu_threads=settings.whisper_cpu_threads,
            )
            logger.info(
                "STT local: Whisper %s cargado (int8, %d hilos)",
                settings.whisper_model,
                settings.whisper_cpu_threads,
            )
        except Exception as e:
            logger.warning("STT local no disponible: %s", e)
            return None
    return _LOCAL_MODEL


def build_wav(pcm_bytes: bytes, source_rate: int) -> bytes:
    """Envuelve PCM s16le mono en un WAV a 16 kHz (remuestrea si hace falta)."""
    if source_rate != TARGET_SAMPLE_RATE:
        import audioop

        pcm_bytes, _ = audioop.ratecv(pcm_bytes, 2, 1, source_rate, TARGET_SAMPLE_RATE, None)
    byte_rate = TARGET_SAMPLE_RATE * 2
    header = struct.pack(
        "<4sI4s4sIHHIihh4sI",
        b"RIFF",
        36 + len(pcm_bytes),
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        TARGET_SAMPLE_RATE,
        byte_rate,
        2,
        16,
        b"data",
        len(pcm_bytes),
    )
    return header + pcm_bytes


def _es_alucinacion(texto: str) -> bool:
    t = (texto or "").lower()
    return any(h in t for h in NOISE_HALLUCINATIONS)


def _filtrar_segmentos(segmentos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        s
        for s in segmentos
        if (s.get("text") or "").strip()
        and float(s.get("no_speech_prob", 0.0)) <= NSP_MAX
        and float(s.get("avg_logprob", 0.0)) >= LP_MIN
    ]


async def _transcribir_remoto(
    audio_bytes: bytes, language: str, prompt: str
) -> dict[str, Any] | None:
    remote = (settings.whisper_remote_url or "").strip().rstrip("/")
    if not remote:
        return None
    import httpx

    headers = {"Content-Type": "application/octet-stream"}
    if settings.whisper_remote_token:
        headers["X-Whisper-Token"] = settings.whisper_remote_token
    try:
        async with httpx.AsyncClient(timeout=settings.whisper_remote_timeout) as client:
            resp = await client.post(
                "%s/transcribe" % remote,
                params={"language": language, "prompt": prompt},
                content=audio_bytes,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning("STT remoto no disponible (%s); uso Whisper local", str(e)[:120])
        return None
    segmentos = _filtrar_segmentos(data.get("segments") or [])
    texto = " ".join(s.get("text", "").strip() for s in segmentos).strip()
    if _es_alucinacion(texto):
        logger.info("STT remoto: alucinacion descartada (%r)", texto[:80])
        texto = ""
    return {
        "text": texto,
        "segments": segmentos,
        "source": "remote",
        "elapsed": data.get("elapsed"),
    }


async def transcribe_bytes(
    audio_bytes: bytes,
    language: str | None = None,
    prompt: str = "",
    beam_size: int = 5,
) -> dict[str, Any]:
    """Transcribe audio (cualquier formato que decodifique PyAV) -> dict.

    Claves: text (limpio; "" si no hay voz), segments, source.
    """
    language = language or settings.language
    remoto = await _transcribir_remoto(audio_bytes, language, prompt)
    if remoto is not None:
        return remoto

    modelo = _get_local_model()
    if modelo is None:
        return {"text": "", "segments": [], "source": "none"}

    def _do():
        segments, _info = modelo.transcribe(
            io.BytesIO(audio_bytes),
            beam_size=beam_size,
            language=language,
            temperature=[0.0, 0.2, 0.4],
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300, "speech_pad_ms": 300},
            condition_on_previous_text=False,
            no_speech_threshold=NSP_MAX,
            log_prob_threshold=LP_MIN,
            initial_prompt=prompt or None,
        )
        salida = []
        for seg in segments:
            salida.append(
                {
                    "start": round(seg.start, 2),
                    "end": round(seg.end, 2),
                    "text": seg.text.strip(),
                    "no_speech_prob": round(seg.no_speech_prob, 3),
                    "avg_logprob": round(seg.avg_logprob, 3),
                }
            )
        return salida

    try:
        segmentos = await asyncio.to_thread(_do)
    except Exception as e:
        logger.warning("STT local fallo: %s", str(e)[:150])
        return {"text": "", "segments": [], "source": "local_error"}
    segmentos = _filtrar_segmentos(segmentos)
    texto = " ".join(s["text"] for s in segmentos).strip()
    if _es_alucinacion(texto):
        logger.info("STT local: alucinacion descartada (%r)", texto[:80])
        texto = ""
    texto = clean_stt_transcript(texto) or ""
    return {"text": texto, "segments": segmentos, "source": "local"}


async def transcribe_pcm(
    pcm_bytes: bytes,
    source_rate: int,
    language: str | None = None,
    prompt: str = "",
    beam_size: int = 5,
) -> dict[str, Any]:
    """PCM s16le mono -> transcripcion (remota si esta configurada)."""
    if len(pcm_bytes) < 1000:
        return {"text": "", "segments": [], "source": "too_short"}
    return await transcribe_bytes(
        build_wav(pcm_bytes, source_rate),
        language=language,
        prompt=prompt,
        beam_size=beam_size,
    )
