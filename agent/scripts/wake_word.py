"""Wake word manos libres para la torre (2026-10-06).

Escucha el microfono LOCALMENTE (openwakeword; el audio NO sale del equipo
hasta que se dice la palabra de activacion), graba la frase y la envia a la
llamada de voz de Rafita (`voice_stream` en el HP) usando el mismo protocolo
que la web: POST /call/start + WS /call/ws/{id} con `ptt_start`/`end_speech`.
La respuesta (TTS WAV) se reproduce por los altavoces.

Dependencias SOLO de la maquina con micro (no van en requirements del agente):

    pip install numpy sounddevice openwakeword websockets

Uso:

    RAFITA_BASE=http://<hp>:8001 CALL_TOKEN=<token de /call/token> \
        python agent/scripts/wake_word.py

Variables: WAKEWORD (hey_jarvis|alexa, por defecto hey_jarvis), WAKE_THRESHOLD
(0.5), INPUT_DEVICE / OUTPUT_DEVICE (indices de sounddevice, opcionales),
TALK_TIMEOUT_S (20). Arranque con el sistema: docs/wake-word.md.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import os
import struct
import sys
import threading
import urllib.request
import wave
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("wake_word")

RATE = 16000
FRAME_SAMPLES = 1280  # 80 ms a 16 kHz: trozo que espera openwakeword
SILENCE_RMS = 500.0  # RMS por debajo = silencio (int16)
SILENCE_END_S = 1.2  # silencio sostenido que cierra el turno
TALK_TIMEOUT_S = 20.0


def wake_triggered(scores: dict[str, float], threshold: float) -> bool:
    """True si alguna palabra de activacion supera el umbral."""
    return any(float(v) >= threshold for v in scores.values())


def frame_rms(pcm: bytes) -> float:
    """RMS de un trozo PCM int16 (sin numpy)."""
    if len(pcm) < 2:
        return 0.0
    count = len(pcm) // 2
    samples = struct.unpack("<%dh" % count, pcm[: count * 2])
    return math.sqrt(sum(s * s for s in samples) / count)


def wav_to_pcm(wav_bytes: bytes) -> tuple[int, bytes]:
    """Extrae (sample_rate, pcm int16) de un WAV en memoria."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        return wf.getframerate(), wf.readframes(wf.getnframes())


def _start_call_session(base: str, token: str) -> str:
    req = urllib.request.Request(
        base.rstrip("/") + "/call/start",
        data=json.dumps({"token": token, "chat_id": 0}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    session_id = payload.get("session_id") or ""
    if not session_id:
        raise RuntimeError("call/start sin session_id: %s" % payload)
    return session_id


async def run_turn(base: str, token: str, utterance: bytes) -> list[bytes]:
    """Envia la grabacion como una llamada PTT y devuelve los WAVs de respuesta."""
    import websockets

    session_id = _start_call_session(base, token)
    ws_url = base.rstrip("/").replace("http", "ws", 1) + "/call/ws/" + session_id
    wavs: list[bytes] = []
    async with websockets.connect(ws_url, max_size=8 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "auth", "token": token}))
        await ws.send(json.dumps({"type": "audio_config", "sample_rate": RATE}))
        await ws.send(json.dumps({"type": "ptt_start"}))
        step = FRAME_SAMPLES * 2
        for i in range(0, len(utterance), step):
            await ws.send(utterance[i : i + step])
        await ws.send(json.dumps({"type": "end_speech"}))
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=120.0)
            except TimeoutError:
                break
            if isinstance(msg, bytes):
                wavs.append(msg)
                continue
            data = json.loads(msg)
            kind = data.get("type")
            if kind in ("transcript", "response_text") and data.get("text"):
                logger.info("%s: %s", kind, data["text"])
            if kind in ("latency_report", "interrupted", "error"):
                break
    return wavs


def _start_reader(stream: Any, loop: asyncio.AbstractEventLoop, queue: Any) -> None:
    """Hilo lector: trozos del micro -> cola asyncio (call_soon_threadsafe)."""

    def _run() -> None:
        while True:
            data = stream.read(FRAME_SAMPLES)
            loop.call_soon_threadsafe(queue.put_nowait, bytes(data))

    threading.Thread(target=_run, daemon=True).start()


async def talk_loop() -> None:
    base = os.environ.get("RAFITA_BASE") or os.environ.get("CALL_URL") or ""
    token = os.environ.get("CALL_TOKEN") or ""
    if not base or not token:
        logger.error("Define RAFITA_BASE y CALL_TOKEN (web: /call/token)")
        sys.exit(2)
    wakeword = os.environ.get("WAKEWORD", "hey_jarvis")
    threshold = float(os.environ.get("WAKE_THRESHOLD", "0.5"))
    timeout_s = float(os.environ.get("TALK_TIMEOUT_S", str(TALK_TIMEOUT_S)))

    import numpy as np
    import sounddevice as sd
    from openwakeword.model import Model
    from openwakeword.utils import download_models

    download_models()
    model = Model(wakeword_models=[wakeword], inference_framework="onnx")
    input_device = os.environ.get("INPUT_DEVICE")
    output_device = os.environ.get("OUTPUT_DEVICE")
    play_device = int(output_device) if output_device else None
    logger.info("Escuchando '%s' (umbral %.2f): di la palabra y habla.", wakeword, threshold)

    stream = sd.InputStream(
        samplerate=RATE,
        channels=1,
        dtype="int16",
        blocksize=FRAME_SAMPLES,
        device=int(input_device) if input_device else None,
    )
    stream.start()
    loop = asyncio.get_running_loop()
    frames: asyncio.Queue[bytes] = asyncio.Queue()
    _start_reader(stream, loop, frames)
    try:
        while True:
            # 1) Escucha hasta la palabra de activacion (audio local, sin red).
            pcm = await frames.get()
            while not wake_triggered(model.predict(pcm), threshold):
                pcm = await frames.get()
            logger.info("Activada: grabando...")

            # 2) Graba la frase hasta silencio sostenido o timeout.
            utterance = bytearray()
            silence_s = 0.0
            start = loop.time()
            while silence_s < SILENCE_END_S:
                chunk = await frames.get()
                utterance += chunk
                silence_s = (
                    silence_s + FRAME_SAMPLES / RATE if frame_rms(chunk) < SILENCE_RMS else 0.0
                )
                if loop.time() - start > timeout_s:
                    break
            if frame_rms(bytes(utterance)) < SILENCE_RMS:
                logger.info("Sin voz, vuelvo a escuchar.")
                continue

            # 3) Turno completo (STT+LLM+TTS en el HP) y reproduccion.
            try:
                wavs = await run_turn(base, token, bytes(utterance))
            except Exception as e:
                logger.warning("Turno fallo: %s", e)
                continue
            for chunk in wavs:
                try:
                    rate, pcm_out = wav_to_pcm(chunk)
                    sd.play(
                        np.frombuffer(pcm_out, dtype="int16"),
                        samplerate=rate,
                        device=play_device,
                    )
                    sd.wait()
                except Exception as e:
                    logger.warning("No pude reproducir el audio: %s", e)
    finally:
        stream.stop()
        stream.close()


if __name__ == "__main__":
    asyncio.run(talk_loop())
