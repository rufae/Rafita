"""Cobertura de voice_stream/server.py: rutas HTTP, WebSocket y helpers."""

import asyncio
import io
import struct
import threading
import time

import pytest
from fastapi.testclient import TestClient

from src.config import settings
from src.voice_stream import server as voice_server
from src.voice_stream.server import (
    _compute_rms,
    _is_sentence_boundary,
    _maybe_schedule_speculative_stt,
    _split_fragment,
)

LOUD = struct.pack("<100h", *([20000] * 100))
SILENT = struct.pack("<100h", *([10] * 100))


class _FakeTask:
    """Interfaz minima de una tarea cancelable (sin loop asociado)."""

    def __init__(self):
        self.cancelled = False

    def done(self):
        return False

    def cancel(self):
        self.cancelled = True

    def __await__(self):
        async def _noop():
            return None

        return _noop().__await__()


@pytest.fixture
def client():
    return TestClient(voice_server.app)


@pytest.fixture(autouse=True)
def _clean_sessions():
    voice_server._active_sessions.clear()
    yield
    voice_server._active_sessions.clear()


def _seed_session(session_id="s1", **extra):
    session = {
        "chat_id": 7,
        "started_at": 1000.0,
        "audio_buffer": io.BytesIO(),
        "transcript": "",
        "state": "listening",
        "vad_chunks": 0,
        "sample_rate": 48000,
        "processing_task": None,
        "spec_stt": None,
    }
    session.update(extra)
    voice_server._active_sessions[session_id] = session
    return session


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def test_health_reports_sessions(client):
    _seed_session("a")
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["active_sessions"] == 1
    assert "timestamp" in data


def test_start_call_without_token_configured(client):
    response = client.post("/call/start", json={"chat_id": 5})
    assert response.status_code == 200
    data = response.json()
    assert data["ws_url"] == "/call/ws/%s" % data["session_id"]
    session = voice_server._active_sessions[data["session_id"]]
    assert session["chat_id"] == 5
    assert session["state"] == "listening"


def test_start_call_rejects_missing_token(client, monkeypatch):
    monkeypatch.setattr(settings, "voice_call_token", "secreto123")
    assert client.post("/call/start").status_code == 401


def test_start_call_accepts_query_and_header_token(client, monkeypatch):
    monkeypatch.setattr(settings, "voice_call_token", "secreto123")
    assert client.post("/call/start?token=secreto123").status_code == 200
    assert client.post("/call/start", headers={"X-Call-Token": "secreto123"}).status_code == 200
    assert client.post("/call/start?token=malo").status_code == 401


def test_start_call_invalid_json_body_defaults_chat(client):
    response = client.post("/call/start", content=b"{not json")
    assert response.status_code == 200
    session_id = response.json()["session_id"]
    assert voice_server._active_sessions[session_id]["chat_id"] == 0


def test_end_call_reports_duration_and_transcript(client):
    session = _seed_session("e1", transcript="hola mundo ", vad_chunks=3, started_at=0.0)
    response = client.post("/call/e1/end")
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == "e1"
    assert data["transcript"] == "hola mundo "
    assert data["vad_chunks"] == 3
    assert session["state"] == "ended"
    assert "e1" not in voice_server._active_sessions


def test_end_call_cancels_processing_task(client):
    task = _FakeTask()
    _seed_session("e2", processing_task=task)
    assert client.post("/call/e2/end").status_code == 200
    assert task.cancelled


def test_end_call_token_and_not_found(client, monkeypatch):
    monkeypatch.setattr(settings, "voice_call_token", "secreto123")
    assert client.post("/call/x/end?token=malo").status_code == 401
    assert client.post("/call/x/end?token=secreto123").status_code == 404


def test_serve_call_page_from_primary_path(client, tmp_path, monkeypatch):
    html = tmp_path / "call.html"
    html.write_text("<html>rafita</html>", encoding="utf-8")
    monkeypatch.setattr(voice_server, "_HTML_FILE_PATH", html)
    monkeypatch.setattr(voice_server, "_LEGACY_HTML_PATH", tmp_path / "no.html")
    response = client.get("/")
    assert response.status_code == 200
    assert "rafita" in response.text


def test_serve_call_page_falls_back_to_legacy(client, tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.html"
    legacy.write_text("<html>legacy</html>", encoding="utf-8")
    monkeypatch.setattr(voice_server, "_HTML_FILE_PATH", tmp_path / "no.html")
    monkeypatch.setattr(voice_server, "_LEGACY_HTML_PATH", legacy)
    response = client.get("/")
    assert response.status_code == 200
    assert "legacy" in response.text


def test_serve_call_page_missing_html_returns_404(client, tmp_path, monkeypatch):
    monkeypatch.setattr(voice_server, "_HTML_FILE_PATH", tmp_path / "no.html")
    monkeypatch.setattr(voice_server, "_LEGACY_HTML_PATH", tmp_path / "tampoco.html")
    response = client.get("/")
    assert response.status_code == 404
    assert "not found" in response.json()["error"]


def test_test_tts_success(client, tmp_path, monkeypatch):
    wav = tmp_path / "in.wav"
    wav.write_bytes(b"RIFF")
    ogg = tmp_path / "out.ogg"
    ogg.write_bytes(b"OggS")

    async def fake_tts(_text):
        return wav

    async def fake_convert(_wav):
        return ogg

    monkeypatch.setattr("src.utils.tts_manager.text_to_speech", fake_tts)
    monkeypatch.setattr("src.utils.tts_manager.convert_to_ogg", fake_convert)
    response = client.get("/call/test_tts")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/ogg")
    assert response.headers["X-TTS-Bytes"] == "4"
    assert response.content == b"OggS"


def test_test_tts_reports_missing_voice(client, monkeypatch):
    async def fake_tts(_text):
        return None

    monkeypatch.setattr("src.utils.tts_manager.text_to_speech", fake_tts)
    response = client.get("/call/test_tts")
    assert response.status_code == 500
    assert "Piper" in response.json()["reason"]


def test_test_tts_reports_missing_conversion(client, tmp_path, monkeypatch):
    wav = tmp_path / "in.wav"
    wav.write_bytes(b"RIFF")

    async def fake_tts(_text):
        return wav

    async def fake_convert(_wav):
        return tmp_path / "missing.ogg"

    monkeypatch.setattr("src.utils.tts_manager.text_to_speech", fake_tts)
    monkeypatch.setattr("src.utils.tts_manager.convert_to_ogg", fake_convert)
    response = client.get("/call/test_tts")
    assert response.status_code == 500
    assert "converted file not found" in response.json()["reason"]


def test_test_tts_reports_exception(client, monkeypatch):
    async def fake_tts(_text):
        raise RuntimeError("sin piper")

    monkeypatch.setattr("src.utils.tts_manager.text_to_speech", fake_tts)
    response = client.get("/call/test_tts")
    assert response.status_code == 500
    assert "sin piper" in response.json()["reason"]


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


def test_ws_rejects_bad_token(client, monkeypatch):
    monkeypatch.setattr(settings, "voice_call_token", "secreto123")
    with client.websocket_connect("/call/ws/x?token=malo") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "invalid call token" in msg["message"]


def test_ws_session_not_found(client):
    with client.websocket_connect("/call/ws/desconocida") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "session not found" in msg["message"]


def test_ws_ping_and_audio_config(client, monkeypatch):
    monkeypatch.setattr(settings, "voice_speculative_stt", False)
    _seed_session("w1")
    with client.websocket_connect("/call/ws/w1") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_text('{"type": "ping"}')
        assert ws.receive_json()["type"] == "pong"
        ws.send_text('{"type": "audio_config", "sample_rate": 16000}')
        ws.send_text("esto no es json")
        ws.send_text('{"type": "desconocido"}')
    assert voice_server._active_sessions["w1"]["sample_rate"] == 16000


def test_ws_buffers_speech_and_utterance_on_silence(client, monkeypatch):
    monkeypatch.setattr(settings, "voice_speculative_stt", False)
    _seed_session("w2")
    processed = []
    started = threading.Event()

    async def fake_process(websocket, session, session_id, audio_data):
        processed.append((session_id, audio_data))
        started.set()

    monkeypatch.setattr(voice_server, "_process_utterance", fake_process)
    with client.websocket_connect("/call/ws/w2") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_bytes(LOUD)
        ws.send_bytes(SILENT)
        assert started.wait(2), "el turno no llego a procesarse"
        ws.send_text('{"type": "stop_speaking"}')
        assert ws.receive_json() == {"type": "interrupted", "reason": "user_stop"}
        assert voice_server._active_sessions["w2"]["state"] == "listening"
    assert processed and processed[0][0] == "w2"
    assert processed[0][1] == LOUD


def test_ws_end_speech_starts_utterance(client, monkeypatch):
    monkeypatch.setattr(settings, "voice_speculative_stt", False)
    session = _seed_session("w3")
    processed = []
    started = threading.Event()

    async def fake_process(websocket, session_, session_id, audio_data):
        processed.append(audio_data)
        started.set()

    monkeypatch.setattr(voice_server, "_process_utterance", fake_process)
    with client.websocket_connect("/call/ws/w3") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_bytes(LOUD)
        ws.send_text('{"type": "end_speech"}')
        assert started.wait(2), "el turno no llego a procesarse"
        ws.send_text('{"type": "stop_speaking"}')
        assert ws.receive_json() == {"type": "interrupted", "reason": "user_stop"}
        assert session["vad_chunks"] == 0
    assert processed == [LOUD]


def test_ws_barge_in_interrupts_running_response(client, monkeypatch):
    monkeypatch.setattr(settings, "voice_speculative_stt", False)
    _seed_session("w4")
    cancelled = []

    async def fake_process(websocket, session, session_id, audio_data):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(session_id)
            raise

    monkeypatch.setattr(voice_server, "_process_utterance", fake_process)
    with client.websocket_connect("/call/ws/w4") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_bytes(LOUD)
        ws.send_text('{"type": "end_speech"}')
        ws.send_bytes(LOUD)
        assert ws.receive_json() == {"type": "interrupted", "reason": "barge_in"}
    assert cancelled == ["w4"]


def test_ws_ptt_mode_accumulates_without_vad(client, monkeypatch):
    monkeypatch.setattr(settings, "voice_speculative_stt", False)
    session = _seed_session("w5")
    processed = []
    started = threading.Event()

    async def fake_process(websocket, session_, session_id, audio_data):
        processed.append(audio_data)
        started.set()

    monkeypatch.setattr(voice_server, "_process_utterance", fake_process)
    with client.websocket_connect("/call/ws/w5") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_text('{"type": "ptt_start"}')
        ws.send_bytes(SILENT)
        # El servidor procesa el chunk de forma asincrona: esperamos (con tope)
        # a que lo acumule en vez de asumir que ya lo hizo (flaky en suite).
        deadline = time.time() + 2
        while session["vad_chunks"] != 1 and time.time() < deadline:
            time.sleep(0.02)
        assert session["vad_chunks"] == 1
        ws.send_text('{"type": "end_speech"}')
        assert started.wait(2), "el turno no llego a procesarse"
        ws.send_text('{"type": "stop_speaking"}')
        assert ws.receive_json() == {"type": "interrupted", "reason": "user_stop"}
    assert processed == [SILENT]


def test_ws_ptt_start_interrupts_active_task(client, monkeypatch):
    monkeypatch.setattr(settings, "voice_speculative_stt", False)
    _seed_session("w6")
    cancelled = []

    async def fake_process(websocket, session, session_id, audio_data):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(1)
            raise

    monkeypatch.setattr(voice_server, "_process_utterance", fake_process)
    with client.websocket_connect("/call/ws/w6") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_bytes(LOUD)
        ws.send_text('{"type": "end_speech"}')
        ws.send_text('{"type": "ptt_start"}')
        assert ws.receive_json() == {"type": "interrupted", "reason": "barge_in"}
    assert cancelled == [1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_allowed_origins_variants(monkeypatch):
    monkeypatch.setattr(settings, "web_allowed_origins", "https://a.es, https://b.es ,")
    assert voice_server._allowed_origins() == ["https://a.es", "https://b.es"]
    monkeypatch.setattr(settings, "web_allowed_origins", "")
    assert voice_server._allowed_origins() == ["http://localhost:8001", "http://127.0.0.1:8001"]


def test_call_token_valid_edge_cases(monkeypatch):
    monkeypatch.setattr(settings, "voice_call_token", "abc")
    assert not voice_server._call_token_valid(None)
    assert not voice_server._call_token_valid("xyz")
    assert voice_server._call_token_valid("abc")


def test_simple_vad_threshold():
    assert not voice_server._simple_vad(b"")
    assert not voice_server._simple_vad(SILENT)
    assert voice_server._simple_vad(LOUD)


def test_compute_rms_variants():
    assert _compute_rms(b"") == 0.0
    assert _compute_rms(b"\x01\x02\x03") == 0.0
    assert _compute_rms(LOUD) > 300.0


def test_is_sentence_boundary_cases():
    assert not _is_sentence_boundary("")
    assert not _is_sentence_boundary("   ")
    assert _is_sentence_boundary("Hola, mundo.")
    assert _is_sentence_boundary("Hola, mundo.  ")
    assert _is_sentence_boundary("x" * voice_server._FRAGMENT_MAX_CHARS)
    assert _is_sentence_boundary(("palabra " * 9).strip() + ",")
    assert not _is_sentence_boundary("corta,")
    assert not _is_sentence_boundary("sin punto final")


def test_split_fragment_cases():
    assert _split_fragment("") == ("", "")
    fragment, rest = _split_fragment("Hola. ¿Que tal?")
    assert fragment == "Hola."
    assert rest == "¿Que tal?"
    fragment, rest = _split_fragment("texto largo " * 15)
    assert not fragment.endswith(" ")
    assert rest
    fragment, rest = _split_fragment("una frase muy larga sin espacios " * 8)
    assert fragment
    no_spaces = "x" * (voice_server._FRAGMENT_MAX_CHARS + 10)
    assert _split_fragment(no_spaces) == ("", no_spaces)
    clause = ("palabra " * 9).strip() + ","
    assert _split_fragment(clause) == (clause, "")


async def test_maybe_schedule_speculative_stt_disabled(monkeypatch):
    monkeypatch.setattr(settings, "voice_speculative_stt", False)
    session = {"audio_buffer": io.BytesIO(LOUD * 100), "spec_stt": None}
    _maybe_schedule_speculative_stt(session, 48000)
    assert session.get("spec_stt") is None


async def test_maybe_schedule_speculative_stt_needs_enough_audio(monkeypatch):
    monkeypatch.setattr(settings, "voice_speculative_stt", True)
    session = {"audio_buffer": io.BytesIO(LOUD), "spec_stt": None}
    _maybe_schedule_speculative_stt(session, 48000)
    assert session.get("spec_stt") is None


async def test_maybe_schedule_speculative_stt_schedules_and_skips(monkeypatch):
    monkeypatch.setattr(settings, "voice_speculative_stt", True)
    done = []

    async def fake_stt(data, source_rate):
        done.append(len(data))
        return "hola"

    monkeypatch.setattr(voice_server, "_transcribe_audio_bytes", fake_stt)
    min_bytes = int(48000 * 2 * voice_server.SPEC_MIN_SECONDS)
    big = LOUD * (min_bytes // len(LOUD) + 1)
    session = {"audio_buffer": io.BytesIO(big), "spec_stt": None}
    _maybe_schedule_speculative_stt(session, 48000)
    spec = session["spec_stt"]
    assert spec["covered"] == len(big)
    assert await spec["task"] == "hola"
    assert done == [len(big)]

    session["audio_buffer"].write(b"x")
    _maybe_schedule_speculative_stt(session, 48000)
    assert session["spec_stt"] is spec


async def test_maybe_schedule_speculative_stt_skips_running_task(monkeypatch):
    monkeypatch.setattr(settings, "voice_speculative_stt", True)
    session = {"audio_buffer": io.BytesIO(LOUD * 200), "spec_stt": None}
    session["spec_stt"] = {"task": asyncio.ensure_future(asyncio.sleep(60)), "covered": 0}
    running = session["spec_stt"]
    _maybe_schedule_speculative_stt(session, 48000)
    assert session["spec_stt"] is running
    running["task"].cancel()
    try:
        await running["task"]
    except asyncio.CancelledError:
        pass


async def test_safe_send_helpers_block_after_end():
    from src.voice_stream.server import _safe_send_bytes, _safe_send_json

    ws = type("WS", (), {})()
    sent = []

    async def send_json(payload):
        sent.append(payload)

    async def send_bytes(data):
        sent.append(data)

    ws.send_json = send_json
    ws.send_bytes = send_bytes
    session = {"state": "ended"}
    assert not await _safe_send_json(ws, session, {"a": 1})
    assert not await _safe_send_bytes(ws, session, b"x")

    class Boom:
        async def send_json(self, payload):
            raise RuntimeError("cerrado")

        async def send_bytes(self, data):
            raise RuntimeError("cerrado")

    session = {"state": "listening"}
    assert not await _safe_send_json(Boom(), session, {})
    assert session["state"] == "ended"
    session = {"state": "listening"}
    assert not await _safe_send_bytes(Boom(), session, b"")
    assert session["state"] == "ended"

    session = {"state": "listening"}
    assert await _safe_send_json(ws, session, {"ok": 1})
    assert await _safe_send_bytes(ws, session, b"ok")
    assert sent == [{"ok": 1}, b"ok"]


async def test_interrupt_resets_state_and_clears_tasks():
    from src.voice_stream.server import _interrupt

    task = _FakeTask()
    spec_task = _FakeTask()
    session = {
        "state": "processing",
        "processing_task": task,
        "spec_stt": {"task": spec_task, "covered": 1},
    }
    sent = []

    class WS:
        async def send_json(self, payload):
            sent.append(payload)

    await _interrupt(session, WS(), reason="user_stop")
    assert task.cancelled
    assert spec_task.cancelled
    assert session["state"] == "listening"
    assert session["processing_task"] is None
    assert session.get("spec_stt") is None
    assert sent == [{"type": "interrupted", "reason": "user_stop"}]


async def test_start_utterance_respects_state_and_running_task(monkeypatch):
    processed = []

    async def fake_process(websocket, session, session_id, audio_data):
        processed.append(audio_data)

    monkeypatch.setattr(voice_server, "_process_utterance", fake_process)
    session = {"state": "ended", "audio_buffer": io.BytesIO(b"abc")}
    voice_server._start_utterance(None, session, "x")
    assert processed == []

    session["state"] = "listening"
    session["processing_task"] = _FakeTask()
    voice_server._start_utterance(None, session, "x")
    assert processed == []

    session["processing_task"] = None
    voice_server._start_utterance(None, session, "x")
    await session["processing_task"]
    assert processed == [b"abc"]


# ---------------------------------------------------------------------------
# _process_utterance y _transcribe_audio_bytes (con dependencias falsas)
# ---------------------------------------------------------------------------


class _WS:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(("json", payload))

    async def send_bytes(self, data):
        self.sent.append(("bytes", data))


class _DoneTask:
    def __init__(self, result):
        self._result = result
        self.cancelled = False

    def done(self):
        return True

    def result(self):
        return self._result

    def cancel(self):
        self.cancelled = True


def _session(**extra):
    session = {
        "chat_id": 1,
        "state": "listening",
        "transcript": "",
        "sample_rate": 48000,
        "spec_stt": None,
        "processing_task": None,
    }
    session.update(extra)
    return session


def _types(ws):
    return [payload.get("type") for kind, payload in ws.sent if kind == "json"]


async def test_process_utterance_full_pipeline(monkeypatch):
    async def fake_stt(data, source_rate=48000):
        return "hola que tal"

    async def fake_tts(text):
        return b"WAV"

    async def fake_gen(text, chat_id, voice=True):
        yield "Hola, "
        yield "bien. "
        yield "¿Y tu?"

    monkeypatch.setattr(voice_server, "_transcribe_audio_bytes", fake_stt)
    monkeypatch.setattr(voice_server, "_synthesize_speech_bytes", fake_tts)
    monkeypatch.setattr("src.core.generate_response_stream", fake_gen)

    ws = _WS()
    session = _session()
    await voice_server._process_utterance(ws, session, "s1", LOUD * 10)

    kinds = _types(ws)
    assert "transcribing" in kinds
    assert "transcript" in kinds
    assert "thinking" in kinds
    assert "token" in kinds
    assert "speaking_fragment" in kinds
    assert "response_text" in kinds
    assert "latency_report" in kinds
    assert ("bytes", b"WAV") in ws.sent
    assert session["transcript"].strip() == "hola que tal"
    assert session["state"] == "listening"
    report = [p for k, p in ws.sent if k == "json" and p["type"] == "latency_report"][0]
    assert report["fragments"] >= 1


async def test_process_utterance_skips_short_and_silent(monkeypatch):
    async def boom(*_args, **_kwargs):
        raise AssertionError("no deberia transcribir")

    monkeypatch.setattr(voice_server, "_transcribe_audio_bytes", boom)
    ws = _WS()
    await voice_server._process_utterance(ws, _session(), "s1", b"abc")
    await voice_server._process_utterance(ws, _session(), "s1", SILENT * 10)
    assert ws.sent == []


async def test_process_utterance_reports_no_speech_and_errors(monkeypatch):
    async def empty_stt(data, source_rate=48000):
        return "   "

    monkeypatch.setattr(voice_server, "_transcribe_audio_bytes", empty_stt)
    ws = _WS()
    await voice_server._process_utterance(ws, _session(), "s1", LOUD * 10)
    assert _types(ws)[-1] == "transcript"
    assert ws.sent[-1][1]["error"] == "no speech detected"

    async def raising_stt(data, source_rate=48000):
        raise RuntimeError("whisper caido")

    monkeypatch.setattr(voice_server, "_transcribe_audio_bytes", raising_stt)
    ws = _WS()
    await voice_server._process_utterance(ws, _session(), "s1", LOUD * 10)
    assert ws.sent[-1][1]["error"] == "no speech detected"

    async def loop_stt(data, source_rate=48000):
        return "la vida, " * 12

    monkeypatch.setattr(voice_server, "_transcribe_audio_bytes", loop_stt)
    ws = _WS()
    await voice_server._process_utterance(ws, _session(), "s1", LOUD * 10)
    assert ws.sent[-1][1]["error"] == "no speech detected"


async def test_process_utterance_reuses_speculative_stt(monkeypatch):
    async def boom(*_args, **_kwargs):
        raise AssertionError("no deberia llamar al STT")

    async def fake_gen(text, chat_id, voice=True):
        yield "Vale."

    monkeypatch.setattr(voice_server, "_transcribe_audio_bytes", boom)
    monkeypatch.setattr(voice_server, "_synthesize_speech_bytes", lambda text: _async_bytes(b"W"))
    monkeypatch.setattr("src.core.generate_response_stream", fake_gen)

    audio = LOUD * 10
    ws = _WS()
    session = _session(spec_stt={"task": _DoneTask("hola especulado"), "covered": len(audio)})
    await voice_server._process_utterance(ws, session, "s1", audio)
    transcript = [p for k, p in ws.sent if k == "json" and p["type"] == "transcript"][0]
    assert transcript["text"] == "hola especulado"
    assert session.get("spec_stt") is None


async def _async_bytes(data):
    return data


async def test_process_utterance_cancels_running_spec(monkeypatch):
    async def boom(*_args, **_kwargs):
        raise AssertionError("no deberia llamar al STT")

    async def fake_gen(text, chat_id, voice=True):
        yield "Listo."

    monkeypatch.setattr(voice_server, "_transcribe_audio_bytes", boom)

    async def fake_stt2(data, source_rate=48000):
        return "transcript normal"

    # La STT normal se usa porque la especulativa no estaba lista.
    monkeypatch.setattr(voice_server, "_transcribe_audio_bytes", fake_stt2)
    monkeypatch.setattr(voice_server, "_synthesize_speech_bytes", lambda text: _async_bytes(b"W"))
    monkeypatch.setattr("src.core.generate_response_stream", fake_gen)

    spec_task = _FakeTask()
    ws = _WS()
    session = _session(spec_stt={"task": spec_task, "covered": 1})
    await voice_server._process_utterance(ws, session, "s1", LOUD * 10)
    assert spec_task.cancelled
    assert session.get("spec_stt") is None


async def test_process_utterance_propagates_cancellation(monkeypatch):
    async def cancelled_stt(data, source_rate=48000):
        raise asyncio.CancelledError

    monkeypatch.setattr(voice_server, "_transcribe_audio_bytes", cancelled_stt)
    with pytest.raises(asyncio.CancelledError):
        await voice_server._process_utterance(_WS(), _session(), "s1", LOUD * 10)


async def test_transcribe_audio_bytes_with_fake_whisper(tmp_path, monkeypatch):
    captured = {}

    class _Seg:
        def __init__(self, text):
            self.text = text

    class _Model:
        def transcribe(self, path, **kwargs):
            captured["path"] = path
            captured["kwargs"] = kwargs
            return [_Seg("hola"), _Seg("mundo")], None

    monkeypatch.setattr(voice_server, "_get_whisper", lambda: _Model())
    text = await voice_server._transcribe_audio_bytes(LOUD * 100, source_rate=48000)
    assert text == "hola mundo"
    assert captured["path"].endswith(".wav")
    assert captured["kwargs"]["language"] == settings.language

    text = await voice_server._transcribe_audio_bytes(LOUD * 100, source_rate=16000)
    assert text == "hola mundo"


async def test_transcribe_audio_bytes_failure_paths(monkeypatch):
    monkeypatch.setattr(voice_server, "_get_whisper", lambda: None)
    assert await voice_server._transcribe_audio_bytes(b"x") is None

    class _BrokenModel:
        def transcribe(self, path, **kwargs):
            raise RuntimeError("modelo corrupto")

    monkeypatch.setattr(voice_server, "_get_whisper", lambda: _BrokenModel())
    assert await voice_server._transcribe_audio_bytes(LOUD * 10) is None

    monkeypatch.setattr(voice_server, "_get_whisper", lambda: _BrokenModel())
    assert await voice_server._transcribe_audio_bytes(b"\x00\x01\x02\x03\x05", 48000) is None
