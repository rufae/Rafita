import asyncio
import audioop
import io
import json
import struct
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from src.config import settings
from src.i18n import stt_prompt
from src.logger import logger

app = FastAPI(
    title="Rafita Voice Stream",
    description="Real-time voice interaction via WebSocket",
    version="1.2.0",
)


def _allowed_origins() -> list[str]:
    """CORS restrictivo (2026-09-28): antes era '*' (cualquier web podia
    hablar con este servidor desde el navegador del usuario)."""
    raw = (settings.web_allowed_origins or "").strip()
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return ["http://localhost:8001", "http://127.0.0.1:8001"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Frases de espera / fillers (Fase 2, 2026-09-29) ----------
# Si la respuesta tarda mas de FILLER_DELAY_S en empezar (busquedas, tools),
# se emite una frase natural para evitar el silencio. La piscina rota sin
# repetir y un cooldown evita saturar si hay varias consultas seguidas.
FILLER_PHRASES = (
    "Dame un momento, estoy buscando...",
    "Dejame consultar eso, un segundo...",
    "Voy a mirarlo, dame un instante...",
    "Un momento, lo compruebo...",
    "Estoy en ello, enseguida te digo...",
)
FILLER_DELAY_S = 1.5
FILLER_COOLDOWN_S = 25.0


class _FillerController:
    """Rotacion + cooldown de frases de espera por sesion de llamada."""

    def __init__(self) -> None:
        self._last_at = 0.0
        self._index = 0

    def should_speak(self, now: float) -> bool:
        return (now - self._last_at) >= FILLER_COOLDOWN_S

    def next_phrase(self, now: float) -> str:
        frase = FILLER_PHRASES[self._index % len(FILLER_PHRASES)]
        self._index += 1
        self._last_at = now
        return frase


async def _filler_si_tarda(websocket: WebSocket, session: dict[str, Any]) -> None:
    """Emite una frase de espera si la respuesta no ha empezado a tiempo."""
    try:
        await asyncio.sleep(FILLER_DELAY_S)
    except asyncio.CancelledError:
        return
    if session.get("state") == "ended" or session.get("respuesta_iniciada"):
        return
    controller = session.get("filler")
    if not isinstance(controller, _FillerController):
        return
    ahora = time.time()
    if not controller.should_speak(ahora):
        return
    frase = controller.next_phrase(ahora)
    session["respuesta_iniciada"] = True  # una sola frase por turno
    audio = await _synthesize_speech_bytes(frase)
    if not audio:
        return
    logger.info("VoiceStream: filler emitido [%s]", frase)
    await _safe_send_json(
        websocket, session, {"type": "filler", "text": frase, "timestamp": time.time()}
    )
    await _safe_send_bytes(websocket, session, audio)


def _call_token_valid(token: str | None) -> bool:
    """Token de acceso de la pagina de llamadas (VOICE_CALL_TOKEN en .env).

    Sin token configurado el servidor queda abierto (retrocompatibilidad);
    con token, cualquier peticion sin el token correcto se rechaza.
    """
    expected = (settings.voice_call_token or "").strip()
    if not expected:
        return True
    import hmac

    if not token:
        return False
    return hmac.compare_digest(expected, token)


_active_sessions: dict[str, dict[str, Any]] = {}
_whisper_model = None
_tts_engine = None

_HTML_FILE_PATH = Path("/workspace/web/call_rafita.html")
_LEGACY_HTML_PATH = Path("/workspace/call_rafita.html")

TARGET_SAMPLE_RATE = 16000
SILENCE_RMS_THRESHOLD = 150

# STT especulativo (2026-09-28, voz nativa): se transcribe MIENTRAS el usuario
# habla (cada SPEC_MIN_SECONDS de audio nuevo); si al terminar el turno la
# hipotesis cubre casi todo el audio, se reutiliza y el STT sale del camino
# critico de latencia.
SPEC_MIN_SECONDS = 2.0
SPEC_COVER_RATIO = 0.85


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel

            model_name = getattr(settings, "whisper_model", None) or "base"
            if model_name == "tiny":
                model_name = "base"  # voice stream needs better accuracy
            cpu_threads = int(getattr(settings, "whisper_cpu_threads", 4))
            _whisper_model = WhisperModel(
                model_name,
                device="cpu",
                compute_type="int8",
                num_workers=1,
                cpu_threads=cpu_threads,
            )
            logger.info(
                "VoiceStream: Whisper %s loaded (int8, %d threads)", model_name, cpu_threads
            )
        except ImportError:
            logger.warning("VoiceStream: faster-whisper not installed")
        except OSError as e:
            logger.warning("VoiceStream: Whisper model file error: %s", e)
    return _whisper_model


@app.get("/")
async def serve_call_page():
    for path in (_HTML_FILE_PATH, _LEGACY_HTML_PATH):
        if path.exists():
            return FileResponse(str(path), media_type="text/html")
    return JSONResponse(
        status_code=404,
        content={"error": "call_rafita.html not found at %s" % str(_HTML_FILE_PATH)},
    )


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "rafita-voice-stream",
        "active_sessions": len(_active_sessions),
        "timestamp": time.time(),
    }


@app.get("/call/test_tts")
async def test_tts():
    try:
        from src.utils.tts_manager import convert_to_ogg, text_to_speech

        t0 = time.time()
        wav = await text_to_speech("Hola, soy Rafita. Prueba de voz.")
        if wav is None:
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "reason": "text_to_speech returned None (Piper y espeak fallaron)",
                },
            )
        ogg = await convert_to_ogg(wav)
        if ogg and ogg.exists():
            dur = round(time.time() - t0, 2)
            return Response(
                content=ogg.read_bytes(),
                media_type="audio/ogg",
                headers={
                    "X-TTS-Duration": str(dur),
                    "X-TTS-Bytes": str(ogg.stat().st_size),
                    "Content-Disposition": "inline; filename=test.ogg",
                },
            )
        return JSONResponse(
            status_code=500, content={"status": "error", "reason": "converted file not found"}
        )
    except Exception as e:
        logger.exception("VoiceStream test_tts error")
        return JSONResponse(status_code=500, content={"status": "error", "reason": str(e)})


@app.post("/call/start")
async def start_call(request: Request):
    token = request.query_params.get("token") or request.headers.get("X-Call-Token")
    if not _call_token_valid(token):
        return JSONResponse(status_code=401, content={"error": "invalid call token"})
    body = await request.body()
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        payload = {}

    chat_id = payload.get("chat_id", 0)
    session_id = str(uuid.uuid4())

    _active_sessions[session_id] = {
        "chat_id": chat_id,
        "started_at": time.time(),
        "audio_buffer": io.BytesIO(),
        "transcript": "",
        "state": "listening",
        "vad_chunks": 0,
        "sample_rate": 48000,
        "processing_task": None,
        "spec_stt": None,
    }

    logger.info("VoiceStream: call started session=%s chat=%d", session_id, chat_id)
    return {"session_id": session_id, "ws_url": "/call/ws/%s" % session_id}


@app.post("/call/{session_id}/end")
async def end_call(session_id: str, request: Request):
    token = request.query_params.get("token") or request.headers.get("X-Call-Token")
    if not _call_token_valid(token):
        return JSONResponse(status_code=401, content={"error": "invalid call token"})
    session = _active_sessions.pop(session_id, None)
    if not session:
        return JSONResponse(status_code=404, content={"error": "session not found"})

    # Colgado limpio (2026-09-27): antes la generacion/TTS seguian vivos tras
    # colgar y el navegador seguia reproduciendo audio en cola.
    session["state"] = "ended"
    task = session.get("processing_task")
    if task and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    duration = time.time() - session["started_at"]
    logger.info("VoiceStream: call ended session=%s duration=%.1fs", session_id, duration)
    return {
        "session_id": session_id,
        "duration": round(duration, 1),
        "transcript": session.get("transcript", ""),
        "vad_chunks": session.get("vad_chunks", 0),
    }


async def _safe_send_json(websocket: WebSocket, session: dict, payload: dict) -> bool:
    if session.get("state") == "ended":
        return False
    try:
        await websocket.send_json(payload)
        return True
    except Exception:
        session["state"] = "ended"
        return False


async def _safe_send_bytes(websocket: WebSocket, session: dict, data: bytes) -> bool:
    if session.get("state") == "ended":
        return False
    try:
        await websocket.send_bytes(data)
        return True
    except Exception:
        session["state"] = "ended"
        return False


async def _interrupt(session: dict, websocket: WebSocket, reason: str = "barge_in") -> None:
    """Corta la respuesta en curso (barge-in por voz o boton Parar)."""
    task = session.get("processing_task")
    if task and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    session["processing_task"] = None
    stale_spec = session.pop("spec_stt", None)
    if stale_spec and not stale_spec["task"].done():
        stale_spec["task"].cancel()
    if session.get("state") != "ended":
        session["state"] = "listening"
    await _safe_send_json(websocket, session, {"type": "interrupted", "reason": reason})
    logger.info("VoiceStream: respuesta interrumpida (%s)", reason)


def _start_utterance(websocket: WebSocket, session: dict, session_id: str) -> None:
    """Lanza el procesado del turno sin bloquear la recepcion (barge-in)."""
    if session.get("state") == "ended":
        return
    task = session.get("processing_task")
    if task and not task.done():
        return
    audio_data = session["audio_buffer"].getvalue()
    session["processing_task"] = asyncio.create_task(
        _process_utterance(websocket, session, session_id, audio_data)
    )


@app.websocket("/call/ws/{session_id}")
async def voice_websocket(websocket: WebSocket, session_id: str):
    if not _call_token_valid(websocket.query_params.get("token")):
        await websocket.accept()
        await websocket.send_json({"type": "error", "message": "invalid call token"})
        await websocket.close(code=4401)
        return
    await websocket.accept()
    session = _active_sessions.get(session_id)
    if not session:
        await websocket.send_json({"type": "error", "message": "session not found"})
        await websocket.close()
        return

    logger.info("VoiceStream: WebSocket connected session=%s", session_id)
    await websocket.send_json({"type": "ready", "session_id": session_id})

    try:
        while True:
            data = await websocket.receive()

            if data.get("type") == "websocket.disconnect":
                break

            if "bytes" in data and data["bytes"]:
                audio_chunk = data["bytes"]
                task = session.get("processing_task")

                if task and not task.done():
                    # Rafita esta respondiendo: si el usuario habla, se corta
                    # (barge-in) y su voz pasa a ser el nuevo turno.
                    if not session.get("ptt_mode") and _simple_vad(audio_chunk):
                        await _interrupt(session, websocket, reason="barge_in")
                        session["audio_buffer"] = io.BytesIO()
                        session["audio_buffer"].write(audio_chunk)
                        session["vad_chunks"] = 1
                    continue

                if session.get("ptt_mode", False):
                    session["audio_buffer"].write(audio_chunk)
                    session["vad_chunks"] += 1
                else:
                    is_speech = _simple_vad(audio_chunk)
                    if is_speech:
                        session["audio_buffer"].write(audio_chunk)
                        session["vad_chunks"] += 1
                        _maybe_schedule_speculative_stt(session, session.get("sample_rate", 48000))
                    elif session["vad_chunks"] > 0:
                        _start_utterance(websocket, session, session_id)
                        session["audio_buffer"] = io.BytesIO()
                        session["vad_chunks"] = 0

            elif "text" in data and data["text"]:
                try:
                    msg = json.loads(data["text"])
                    msg_type = msg.get("type")
                    if msg_type == "end_speech":
                        if session.get("vad_chunks", 0) > 0:
                            _start_utterance(websocket, session, session_id)
                        session["audio_buffer"] = io.BytesIO()
                        session["vad_chunks"] = 0
                        session["ptt_mode"] = False
                    elif msg_type == "ptt_start":
                        task = session.get("processing_task")
                        if task and not task.done():
                            await _interrupt(session, websocket, reason="barge_in")
                        session["ptt_mode"] = True
                        session["vad_chunks"] = 0
                        session["audio_buffer"] = io.BytesIO()
                    elif msg_type == "stop_speaking":
                        await _interrupt(session, websocket, reason="user_stop")
                    elif msg_type == "audio_config":
                        sr = int(msg.get("sample_rate", 48000))
                        session["sample_rate"] = sr
                        logger.info("VoiceStream: client sample_rate=%d session=%s", sr, session_id)
                    elif msg_type == "ping":
                        await websocket.send_json({"type": "pong"})
                except json.JSONDecodeError:
                    pass

    except WebSocketDisconnect:
        logger.info("VoiceStream: WebSocket disconnected session=%s", session_id)
    except Exception as e:
        logger.warning("VoiceStream: WebSocket error: %s", e)
    finally:
        if session_id in _active_sessions:
            session = _active_sessions[session_id]
            session["state"] = "ended"
            task = session.get("processing_task")
            if task and not task.done():
                task.cancel()


def _maybe_schedule_speculative_stt(session: dict, source_rate: int) -> None:
    """Programa un STT del buffer actual si hay audio nuevo suficiente."""
    if not settings.voice_speculative_stt:
        return
    spec = session.get("spec_stt")
    if spec and not spec["task"].done():
        return
    data = session["audio_buffer"].getvalue()
    min_bytes = int(source_rate * 2 * SPEC_MIN_SECONDS)
    if len(data) < min_bytes:
        return
    if spec and len(data) - spec["covered"] < min_bytes:
        return
    session["spec_stt"] = {
        "task": asyncio.create_task(_transcribe_audio_bytes(data, source_rate)),
        "covered": len(data),
    }


def _simple_vad(audio_bytes: bytes, threshold: int = 300) -> bool:
    if len(audio_bytes) < 4:
        return False
    try:
        rms_val = audioop.rms(audio_bytes, 2)
        return rms_val > threshold
    except Exception:
        return False


async def _process_utterance(
    websocket: WebSocket, session: dict, session_id: str, audio_data: bytes
) -> None:
    """Turno completo: STT -> LLM en streaming real -> TTS por frases.

    Corre como tarea cancelable: el bucle del WebSocket sigue leyendo audio
    para permitir barge-in y para parar al colgar.
    """
    spec = session.pop("spec_stt", None)
    try:
        if len(audio_data) < 1000:
            return

        rms = _compute_rms(audio_data)
        if rms < SILENCE_RMS_THRESHOLD:
            logger.info(
                "VoiceStream: silence detected (RMS=%.0f), skipping STT session=%s",
                rms,
                session_id,
            )
            return

        session["state"] = "processing"
        source_rate = session.get("sample_rate", 48000)
        logger.info(
            "VoiceStream: processing utterance %d bytes RMS=%.0f source=%dHz session=%s",
            len(audio_data),
            rms,
            source_rate,
            session_id,
        )

        await _safe_send_json(
            websocket, session, {"type": "transcribing", "timestamp": time.time()}
        )

        t0 = time.time()
        transcript = None
        t_stt = 0.0
        if spec:
            if spec["task"].done():
                try:
                    spec_text = spec["task"].result()
                except Exception:
                    spec_text = None
                if spec_text and spec["covered"] >= len(audio_data) * SPEC_COVER_RATIO:
                    transcript = spec_text
                    logger.info(
                        "VoiceStream: STT especulativo reutilizado (%d/%d bytes) session=%s",
                        spec["covered"],
                        len(audio_data),
                        session_id,
                    )
            else:
                spec["task"].cancel()
        if transcript is None:
            try:
                transcript = await _transcribe_audio_bytes(audio_data, source_rate)
            except Exception as e:
                logger.warning("VoiceStream: STT error session=%s: %s", session_id, e)
                transcript = None
            t_stt = time.time() - t0

        if not transcript or not transcript.strip():
            await _safe_send_json(
                websocket,
                session,
                {"type": "transcript", "text": "", "error": "no speech detected"},
            )
            return

        from src.utils.voice_text import clean_stt_transcript

        transcript = clean_stt_transcript(transcript)
        if not transcript:
            logger.info(
                "VoiceStream: STT hallucination discarded (loop repetition) session=%s",
                session_id,
            )
            await _safe_send_json(
                websocket,
                session,
                {"type": "transcript", "text": "", "error": "no speech detected"},
            )
            return

        session["transcript"] += transcript + " "
        logger.info("VoiceStream: STT done [%.1fs] text=%s", t_stt, transcript[:100])
        await _safe_send_json(
            websocket,
            session,
            {"type": "transcript", "text": transcript, "stt_time": round(t_stt, 2)},
        )

        await _safe_send_json(websocket, session, {"type": "thinking", "timestamp": time.time()})

        from src.core import generate_response_stream
        from src.utils.voice_text import sanitize_for_tts

        t1 = time.time()
        full_response = ""
        fragment_buffer = ""
        t_tts_total = 0.0
        t_first_audio: float | None = None
        fragment_count = 0

        async def _emit_fragment(fragment: str) -> None:
            nonlocal t_tts_total, t_first_audio, fragment_count
            session["respuesta_iniciada"] = True
            spoken = sanitize_for_tts(fragment)
            await _safe_send_json(
                websocket,
                session,
                {"type": "speaking_fragment", "text": fragment, "index": fragment_count},
            )
            if not spoken:
                fragment_count += 1
                return
            t_tts_start = time.time()
            audio_chunk = await _synthesize_speech_bytes(spoken)
            t_tts_total += time.time() - t_tts_start
            if audio_chunk:
                if t_first_audio is None:
                    t_first_audio = time.time() - t0
                sent = await _safe_send_bytes(websocket, session, audio_chunk)
                if not sent:
                    return
                logger.info(
                    "VoiceStream: fragment %d TTS [%.1fs] %d bytes: %s",
                    fragment_count,
                    time.time() - t_tts_start,
                    len(audio_chunk),
                    fragment[:60],
                )
            fragment_count += 1

        # Fase 2: frase de espera si la respuesta tarda en arrancar (tools,
        # busquedas). Se cancela en cuanto llega el primer token/respuesta.
        if not isinstance(session.get("filler"), _FillerController):
            session["filler"] = _FillerController()
        session["respuesta_iniciada"] = False
        filler_task = asyncio.create_task(_filler_si_tarda(websocket, session))
        try:
            async for token in generate_response_stream(
                transcript, session.get("chat_id", 0), voice=True
            ):
                if session.get("state") == "ended":
                    return
                session["respuesta_iniciada"] = True
                full_response += token
                fragment_buffer += token
                await _safe_send_json(websocket, session, {"type": "token", "text": token})

                fragment, fragment_buffer = _split_fragment(fragment_buffer)
                if fragment:
                    await _emit_fragment(fragment)
        finally:
            filler_task.cancel()

        if fragment_buffer.strip():
            await _emit_fragment(fragment_buffer.strip())

        t_llm = time.time() - t1

        await _safe_send_json(
            websocket,
            session,
            {
                "type": "response_text",
                "text": sanitize_for_tts(full_response) or full_response,
                "llm_time": round(t_llm, 2),
            },
        )

        total_latency = time.time() - t0
        await _safe_send_json(
            websocket,
            session,
            {
                "type": "latency_report",
                "stt_ms": round(t_stt * 1000),
                "llm_ms": round(t_llm * 1000),
                "tts_ms": round(t_tts_total * 1000),
                "total_ms": round(total_latency * 1000),
                "first_audio_ms": round(t_first_audio * 1000) if t_first_audio else None,
                "fragments": fragment_count,
            },
        )
        logger.info(
            "VoiceStream: utterance complete total=%.0fms first_audio=%sms fragments=%d",
            total_latency * 1000,
            round(t_first_audio * 1000) if t_first_audio else "n/a",
            fragment_count,
        )
    except asyncio.CancelledError:
        logger.info("VoiceStream: utterance cancelled session=%s", session_id)
        raise
    finally:
        if session.get("processing_task") is asyncio.current_task():
            session["processing_task"] = None
        if session.get("state") != "ended":
            session["state"] = "listening"


_SENTENCE_ENDINGS = ".!?…\n"
_CLAUSE_ENDINGS = ";,"
_FRAGMENT_MAX_CHARS = 140


def _is_sentence_boundary(text: str) -> bool:
    """True si el buffer ya se puede sintetizar.

    Bug 2026-09-27: las palabras se emitian con espacio final, asi que
    `text[-1] in ".!?"` nunca daba True y los fragmentos se cortaban a 120
    chars en mitad de palabra (tartamudeo del TTS). Ahora se ignora el
    espacio final.
    """
    stripped = text.rstrip(" \t")  # conserva \n (que tambien es fin de frase)
    if not stripped:
        return False
    if stripped[-1] in _SENTENCE_ENDINGS:
        return True
    if len(stripped) >= _FRAGMENT_MAX_CHARS:
        return True
    return len(stripped) >= 60 and stripped[-1] in _CLAUSE_ENDINGS


def _split_fragment(buffer: str) -> tuple[str, str]:
    """Divide el buffer en (fragmento listo, resto) sin partir palabras.

    Corta en la primera frase terminada dentro del buffer (los tokens pueden
    llegar en rafagas), en una clausula si ya hay texto suficiente, o en el
    ultimo espacio antes del limite.
    """
    stripped = buffer.rstrip()
    if not stripped:
        return "", buffer

    for idx, ch in enumerate(stripped):
        if ch in _SENTENCE_ENDINGS and (idx + 1 >= len(stripped) or stripped[idx + 1] in " \n"):
            fragment = stripped[: idx + 1]
            return fragment, buffer[len(fragment) :].lstrip()

    if len(stripped) >= 60 and stripped[-1] in _CLAUSE_ENDINGS:
        return stripped, buffer[len(stripped) :].lstrip()

    if len(stripped) >= _FRAGMENT_MAX_CHARS:
        cut = stripped.rfind(" ", 0, _FRAGMENT_MAX_CHARS + 1)
        if cut <= 0:
            cut = stripped.find(" ")
        if cut <= 0:
            return "", buffer
        return stripped[:cut], buffer[cut + 1 :]

    return "", buffer


async def _transcribe_audio_bytes(audio_bytes: bytes, source_rate: int = 48000) -> str | None:
    model = _get_whisper()
    if model is None:
        return None

    import os
    import tempfile

    fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="voicestream_")
    os.close(fd)
    try:
        if source_rate != TARGET_SAMPLE_RATE:
            resampled, _ = audioop.ratecv(audio_bytes, 2, 1, source_rate, TARGET_SAMPLE_RATE, None)
            logger.info(
                "VoiceStream: resampled %d -> %d Hz (%d -> %d bytes)",
                source_rate,
                TARGET_SAMPLE_RATE,
                len(audio_bytes),
                len(resampled),
            )
        else:
            resampled = audio_bytes

        byte_rate = TARGET_SAMPLE_RATE * 2
        block_align = 2
        data_size = len(resampled)
        header = struct.pack(
            "<4sI4s4sIHHIihh4sI",
            b"RIFF",
            36 + data_size,
            b"WAVE",
            b"fmt ",
            16,
            1,
            1,
            TARGET_SAMPLE_RATE,
            byte_rate,
            block_align,
            16,
            b"data",
            data_size,
        )

        with open(tmp_path, "wb") as f:
            f.write(header)
            f.write(resampled)

        loop = asyncio.get_event_loop()

        def _do():
            segments, info = model.transcribe(
                tmp_path,
                beam_size=1,
                language=settings.language,
                temperature=0.0,
                vad_filter=True,
                vad_parameters={
                    "min_silence_duration_ms": 300,
                    "speech_pad_ms": 200,
                },
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
                compression_ratio_threshold=2.4,
                log_prob_threshold=-1.0,
                initial_prompt=stt_prompt(),
            )
            parts = [seg.text for seg in segments]
            return " ".join(parts).strip() if parts else None

        return await loop.run_in_executor(None, _do)
    except Exception as e:
        logger.warning("VoiceStream STT error: %s", e)
        return None
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _compute_rms(audio_bytes: bytes) -> float:
    if len(audio_bytes) < 4:
        return 0.0
    try:
        rms_val = audioop.rms(audio_bytes, 2)
        return float(rms_val)
    except Exception:
        return 0.0


async def _synthesize_speech_bytes(text: str) -> bytes | None:
    """WAV en memoria: sin ffmpeg/OGG (el navegador decodifica WAV).

    Bug 2026-09-27: convertir a OGG lanzaba un ffmpeg por fragmento (latencia)
    y el modelo Piper se recargaba en cada sintesis (tartamudeo).
    La llamada usa TTS_ENGINE_CALL (piper por defecto): Kokoro suena mejor
    pero es mas lento en CPU y rompe la fluidez en tiempo real.
    """
    try:
        from src.config import settings
        from src.utils.tts_manager import synthesize_wav_bytes

        engine = (settings.tts_engine_call or "piper").strip().lower()
        return await synthesize_wav_bytes(text, engine=engine)
    except Exception as e:
        logger.warning("VoiceStream TTS error: %s", e)
        return None


async def start_voice_stream_server(host: str = "0.0.0.0", port: int = 8001):
    import uvicorn

    # Precalentado (2026-09-27): antes la primera llamada pagaba la carga de
    # Whisper y de Piper.
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _get_whisper)
    except Exception as e:
        logger.warning("VoiceStream: no se pudo precalentar Whisper: %s", e)
    try:
        from src.utils.tts_manager import prewarm_tts

        if await prewarm_tts():
            logger.info("VoiceStream: Piper precalentado")
    except Exception as e:
        logger.warning("VoiceStream: no se pudo precalentar Piper: %s", e)

    config_obj = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config_obj)
    logger.info("Starting Rafita Voice Stream on %s:%d", host, port)
    await server.serve()
