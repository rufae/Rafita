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
from src.logger import logger

app = FastAPI(
    title="Rafita Voice Stream",
    description="Real-time voice interaction via WebSocket",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_active_sessions: dict[str, dict[str, Any]] = {}
_whisper_model = None
_tts_engine = None

_HTML_FILE_PATH = Path("/workspace/call_rafita.html")

TARGET_SAMPLE_RATE = 16000
SILENCE_RMS_THRESHOLD = 150


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel

            model_name = getattr(settings, "whisper_model", None) or "base"
            if model_name == "tiny":
                model_name = "base"  # voice stream needs better accuracy
            _whisper_model = WhisperModel(
                model_name,
                device="cpu",
                compute_type="int8",
                num_workers=1,
                cpu_threads=4,
            )
            logger.info("VoiceStream: Whisper %s loaded (int8, 4 threads)", model_name)
        except ImportError:
            logger.warning("VoiceStream: faster-whisper not installed")
        except OSError as e:
            logger.warning("VoiceStream: Whisper model file error: %s", e)
    return _whisper_model


@app.get("/")
async def serve_call_page():
    if _HTML_FILE_PATH.exists():
        return FileResponse(str(_HTML_FILE_PATH), media_type="text/html")
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
    }

    logger.info("VoiceStream: call started session=%s chat=%d", session_id, chat_id)
    return {"session_id": session_id, "ws_url": "/call/ws/%s" % session_id}


@app.post("/call/{session_id}/end")
async def end_call(session_id: str):
    session = _active_sessions.pop(session_id, None)
    if not session:
        return JSONResponse(status_code=404, content={"error": "session not found"})
    duration = time.time() - session["started_at"]
    logger.info("VoiceStream: call ended session=%s duration=%.1fs", session_id, duration)
    return {
        "session_id": session_id,
        "duration": round(duration, 1),
        "transcript": session.get("transcript", ""),
        "vad_chunks": session.get("vad_chunks", 0),
    }


@app.websocket("/call/ws/{session_id}")
async def voice_websocket(websocket: WebSocket, session_id: str):
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

                if session.get("ptt_mode", False):
                    session["audio_buffer"].write(audio_chunk)
                    session["vad_chunks"] += 1
                else:
                    is_speech = _simple_vad(audio_chunk)
                    if is_speech:
                        session["audio_buffer"].write(audio_chunk)
                        session["vad_chunks"] += 1
                    elif session["vad_chunks"] > 0:
                        await _process_utterance(websocket, session, session_id)
                        session["audio_buffer"] = io.BytesIO()
                        session["vad_chunks"] = 0

            elif "text" in data and data["text"]:
                try:
                    msg = json.loads(data["text"])
                    if msg.get("type") == "end_speech":
                        if session["vad_chunks"] > 0:
                            await _process_utterance(websocket, session, session_id)
                        session["audio_buffer"] = io.BytesIO()
                        session["vad_chunks"] = 0
                        session["ptt_mode"] = False
                    elif msg.get("type") == "ptt_start":
                        session["ptt_mode"] = True
                        session["vad_chunks"] = 0
                        session["audio_buffer"] = io.BytesIO()
                    elif msg.get("type") == "audio_config":
                        sr = int(msg.get("sample_rate", 48000))
                        session["sample_rate"] = sr
                        logger.info("VoiceStream: client sample_rate=%d session=%s", sr, session_id)
                    elif msg.get("type") == "ping":
                        await websocket.send_json({"type": "pong"})
                except json.JSONDecodeError:
                    pass

    except WebSocketDisconnect:
        logger.info("VoiceStream: WebSocket disconnected session=%s", session_id)
    except Exception as e:
        logger.warning("VoiceStream: WebSocket error: %s", e)
    finally:
        if session_id in _active_sessions:
            _active_sessions[session_id]["state"] = "ended"


def _simple_vad(audio_bytes: bytes, threshold: int = 300) -> bool:
    if len(audio_bytes) < 4:
        return False
    try:
        rms_val = audioop.rms(audio_bytes, 2)
        return rms_val > threshold
    except Exception:
        return False


async def _process_utterance(websocket: WebSocket, session: dict, session_id: str) -> None:
    buffer = session["audio_buffer"]
    buffer.seek(0)
    audio_data = buffer.getvalue()

    if len(audio_data) < 1000:
        return

    rms = _compute_rms(audio_data)
    if rms < SILENCE_RMS_THRESHOLD:
        logger.info(
            "VoiceStream: silence detected (RMS=%.0f), skipping STT session=%s", rms, session_id
        )
        return

    source_rate = session.get("sample_rate", 48000)
    logger.info(
        "VoiceStream: processing utterance %d bytes RMS=%.0f source=%dHz session=%s",
        len(audio_data),
        rms,
        source_rate,
        session_id,
    )

    await websocket.send_json({"type": "transcribing", "timestamp": time.time()})

    t0 = time.time()
    try:
        transcript = await _transcribe_audio_bytes(audio_data, source_rate)
    except Exception as e:
        logger.warning("VoiceStream: STT error session=%s: %s", session_id, e)
        transcript = None
    t_stt = time.time() - t0

    if not transcript or not transcript.strip():
        await websocket.send_json({"type": "transcript", "text": "", "error": "no speech detected"})
        return

    session["transcript"] += transcript + " "
    logger.info("VoiceStream: STT done [%.1fs] text=%s", t_stt, transcript[:100])
    await websocket.send_json(
        {"type": "transcript", "text": transcript, "stt_time": round(t_stt, 2)}
    )

    await websocket.send_json({"type": "thinking", "timestamp": time.time()})

    t1 = time.time()
    full_response = ""
    fragment_buffer = ""
    t_tts_total = 0.0
    fragment_count = 0

    try:
        async for token in _generate_response_stream(transcript, session.get("chat_id", 0)):
            full_response += token
            fragment_buffer += token

            await websocket.send_json({"type": "token", "text": token})

            if _is_sentence_boundary(fragment_buffer):
                fragment = fragment_buffer.strip()
                fragment_buffer = ""

                if fragment:
                    await websocket.send_json(
                        {
                            "type": "speaking_fragment",
                            "text": fragment,
                            "index": fragment_count,
                        }
                    )

                    t_tts_start = time.time()
                    audio_chunk = await _synthesize_speech_bytes(fragment)
                    t_tts_frag = time.time() - t_tts_start
                    t_tts_total += t_tts_frag

                    if audio_chunk:
                        await websocket.send_bytes(audio_chunk)
                        logger.info(
                            "VoiceStream: fragment %d TTS [%.1fs] %d bytes: %s",
                            fragment_count,
                            t_tts_frag,
                            len(audio_chunk),
                            fragment[:60],
                        )
                    fragment_count += 1

    except Exception as e:
        logger.warning("VoiceStream: streaming error: %s", e)
        if not full_response:
            full_response = "Error de procesamiento."

    if fragment_buffer.strip():
        fragment = fragment_buffer.strip()
        await websocket.send_json(
            {
                "type": "speaking_fragment",
                "text": fragment,
                "index": fragment_count,
            }
        )
        t_tts_start = time.time()
        audio_chunk = await _synthesize_speech_bytes(fragment)
        t_tts_total += time.time() - t_tts_start
        if audio_chunk:
            await websocket.send_bytes(audio_chunk)
        fragment_count += 1

    t_llm = time.time() - t1

    await websocket.send_json(
        {
            "type": "response_text",
            "text": full_response,
            "llm_time": round(t_llm, 2),
        }
    )

    total_latency = time.time() - t0
    await websocket.send_json(
        {
            "type": "latency_report",
            "stt_ms": round(t_stt * 1000),
            "llm_ms": round(t_llm * 1000),
            "tts_ms": round(t_tts_total * 1000),
            "total_ms": round(total_latency * 1000),
            "fragments": fragment_count,
        }
    )
    logger.info(
        "VoiceStream: utterance complete total=%.0fms fragments=%d response=%s",
        total_latency * 1000,
        fragment_count,
        full_response[:100],
    )


_SENTENCE_ENDINGS = ".!?\n"
_CLAUSE_ENDINGS = ";,"


def _is_sentence_boundary(text: str) -> bool:
    if not text:
        return False
    if text[-1] in _SENTENCE_ENDINGS:
        return True
    return (len(text) > 60 and text[-1] in _CLAUSE_ENDINGS) or len(text) > 120


async def _generate_response_stream(text: str, chat_id: int):
    """Generate AI response using the shared orchestrator (tools + RAG)."""
    try:
        from src.core import generate_response

        response = await generate_response(text, chat_id)
        words = response.split()
        for i, word in enumerate(words):
            yield word + (" " if i < len(words) - 1 else "")
    except Exception as e:
        logger.warning("VoiceStream LLM error: %s", e)
        yield "Lo siento, no pude procesar eso."


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
                language="es",
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
                initial_prompt="A continuacion, una conversacion en espanol.",
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
    try:
        from src.utils.tts_manager import convert_to_ogg, text_to_speech

        wav_path = await text_to_speech(text)
        if wav_path is None:
            return None
        ogg_path = await convert_to_ogg(wav_path)
        if ogg_path and ogg_path.exists():
            return ogg_path.read_bytes()
        return None
    except Exception as e:
        logger.warning("VoiceStream TTS error: %s", e)
        return None


async def start_voice_stream_server(host: str = "0.0.0.0", port: int = 8001):
    import uvicorn

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
