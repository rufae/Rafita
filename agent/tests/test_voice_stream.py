"""Modo llamada: corte de frases, limpieza de voz, interrupcion y cache TTS."""

import asyncio
import tempfile
from pathlib import Path

import src.utils.tts_manager as tts_mod
from src.core import orchestrator
from src.utils.voice_text import sanitize_for_tts
from src.voice_stream.server import (
    _active_sessions,
    _interrupt,
    _is_sentence_boundary,
    _split_fragment,
    end_call,
)


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


class _FakeRequest:
    class _Params(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    def __init__(self, token=None):
        self.query_params = self._Params(token=token) if token else self._Params()
        self.headers = {}


def test_call_token_gate(monkeypatch):
    from src.config import settings
    from src.voice_stream.server import _call_token_valid

    monkeypatch.setattr(settings, "voice_call_token", "")
    assert _call_token_valid(None) is True  # sin token: retrocompatible
    monkeypatch.setattr(settings, "voice_call_token", "secreto123")
    assert _call_token_valid("secreto123") is True
    assert _call_token_valid(None) is False
    assert _call_token_valid("otra") is False


async def test_start_call_rejects_bad_token(monkeypatch):
    from src.config import settings
    from src.voice_stream.server import start_call

    monkeypatch.setattr(settings, "voice_call_token", "secreto123")
    bad = await start_call(_FakeRequest(token="malo"))
    assert getattr(bad, "status_code", None) == 401


async def test_speculative_stt_schedules_with_new_audio(monkeypatch):
    from src.config import settings
    from src.voice_stream import server as voice_server

    monkeypatch.setattr(settings, "voice_speculative_stt", True)

    async def fake_stt(audio_bytes, source_rate=48000):
        return "hola que tal"

    monkeypatch.setattr(voice_server, "_transcribe_audio_bytes", fake_stt)

    session = {"audio_buffer": __import__("io").BytesIO(b"\x01" * 70000), "spec_stt": None}
    voice_server._maybe_schedule_speculative_stt(session, 16000)
    assert session["spec_stt"] is not None
    assert session["spec_stt"]["covered"] == 70000
    assert await session["spec_stt"]["task"] == "hola que tal"

    # sin audio nuevo suficiente: no se reprograma
    voice_server._maybe_schedule_speculative_stt(session, 16000)
    assert session["spec_stt"]["covered"] == 70000

    session["audio_buffer"].seek(0, 2)
    session["audio_buffer"].write(b"\x01" * 70000)
    voice_server._maybe_schedule_speculative_stt(session, 16000)
    assert session["spec_stt"]["covered"] == 140000
    assert await session["spec_stt"]["task"] == "hola que tal"


# ---------- 11.1 corte de frases ----------


def test_clean_stt_rejects_loop_hallucination():
    from src.utils.voice_text import clean_stt_transcript

    hallucination = "En el momento de la vida, " + "la vida, " * 20 + "la vida"
    assert clean_stt_transcript(hallucination) is None
    assert clean_stt_transcript("Hola, buenos días") == "Hola, buenos días"
    assert clean_stt_transcript("si si si") == "si"
    assert clean_stt_transcript("") is None


def test_sentence_boundary_ignores_trailing_space():
    assert _is_sentence_boundary("Hola, ¿cómo estás? ") is True
    assert _is_sentence_boundary("Hola ") is False
    assert _is_sentence_boundary("") is False


def test_split_fragment_keeps_sentence_and_rest():
    fragment, rest = _split_fragment("Hola, ¿qué tal? Esto es el resto")
    assert fragment == "Hola, ¿qué tal?"
    assert rest == "Esto es el resto"


def test_split_fragment_never_cuts_a_word():
    buffer = "palabra " * 30  # 240 chars, sin puntuacion
    fragment, rest = _split_fragment(buffer)
    assert fragment.endswith("palabra")
    assert rest.startswith("palabra")
    assert len(fragment) <= 140


def test_split_fragment_short_buffer_not_ready():
    fragment, rest = _split_fragment("esto aún no termina")
    assert fragment == ""
    assert rest == "esto aún no termina"


# ---------- 11.2 limpieza para voz ----------


def test_sanitize_removes_markdown_and_urls():
    text = "**Hola** [enlace](https://ejemplo.com) visita https://rafita.dev `código`"
    out = sanitize_for_tts(text)
    assert "**" not in out
    assert "https://" not in out
    assert "enlace" in out
    assert "`" not in out


def test_sanitize_removes_tables_and_emojis():
    text = "Mira:\n| a | b |\n|---|---|\n| 1 | 2 |\nListo 🎉🚀"
    out = sanitize_for_tts(text)
    assert "|" not in out
    assert "🎉" not in out
    assert "Listo" in out


def test_sanitize_collapses_degenerate_repetition():
    assert sanitize_for_tts("re re re re") == "re"
    assert sanitize_for_tts("sí sí sí") == "sí"
    assert sanitize_for_tts("rerere") == "re"


def test_sanitize_removes_long_ids():
    out = sanitize_for_tts("El id es abcdef1234567890abcdef1234567890 y ya está")
    assert "abcdef1234567890abcdef1234567890" not in out


# ---------- 11.3 interrupcion y colgado ----------


async def test_interrupt_cancels_processing_task():
    session = {"state": "processing", "processing_task": None}
    task = asyncio.create_task(asyncio.sleep(30))
    session["processing_task"] = task
    ws = _FakeWS()

    await _interrupt(session, ws, reason="barge_in")

    assert task.cancelled()
    assert session["processing_task"] is None
    assert session["state"] == "listening"
    assert ws.sent and ws.sent[0]["type"] == "interrupted"
    assert ws.sent[0]["reason"] == "barge_in"


async def test_end_call_cancels_task_and_marks_ended():
    task = asyncio.create_task(asyncio.sleep(30))
    _active_sessions["s-test"] = {
        "chat_id": 0,
        "started_at": 0.0,
        "transcript": "",
        "vad_chunks": 0,
        "state": "processing",
        "processing_task": task,
    }
    result = await end_call("s-test", _FakeRequest())
    assert task.cancelled()
    assert "s-test" not in _active_sessions
    assert result["session_id"] == "s-test"


# ---------- 11.1 cache de Piper ----------


def test_load_piper_voice_uses_cache(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(tts_mod, "_piper_voice", sentinel)
    assert tts_mod._load_piper_voice("no-existe.onnx") is sentinel


async def test_synthesize_wav_bytes_reads_and_cleans(monkeypatch):
    tmp_dir = Path(tempfile.mkdtemp(prefix="tts_test_"))
    wav_path = tmp_dir / "response.wav"
    wav_path.write_bytes(b"RIFFfakewav")

    async def fake_tts(text):
        return wav_path

    monkeypatch.setattr(tts_mod, "text_to_speech", fake_tts)
    data = await tts_mod.synthesize_wav_bytes("hola")
    assert data == b"RIFFfakewav"
    assert not tmp_dir.exists()


# ---------- 11.2 prompt de voz y streaming ----------


def test_voice_prompt_replaces_format_rules():
    from src.core.orchestrator import build_system_prompt

    voice_prompt = build_system_prompt(voice=True)
    assert "VOICE_RULE" in voice_prompt
    assert "FORMAT_RULE" not in voice_prompt
    assert "FORMAT_RULE" in build_system_prompt()


async def test_generate_response_stream_yields_tokens(monkeypatch):
    async def fake_prepare(text, chat_id, voice=False):
        return [{"role": "system", "content": "x"}], "", [{"id": "t1"}], []

    async def fake_stream(messages, temperature=None, max_tokens=None, repeat_penalty=None):
        for token in ["Hola", " mundo"]:
            yield token

    saved = []

    async def fake_save(chat_id, role, content):
        saved.append((chat_id, role, content))

    monkeypatch.setattr(orchestrator, "_prepare_tool_phase", fake_prepare)
    monkeypatch.setattr(orchestrator.llm, "chat_stream_tokens", fake_stream)
    monkeypatch.setattr(orchestrator.db, "save_chat_message", fake_save)

    tokens = [t async for t in orchestrator.generate_response_stream("hola", 7, voice=True)]
    assert "".join(tokens) == "Hola mundo"
    assert saved and saved[0][2] == "Hola mundo"


async def test_generate_response_stream_without_tools_chunks_words(monkeypatch):
    async def fake_prepare(text, chat_id, voice=False):
        return [], "Una respuesta corta.", [], []

    saved = []

    async def fake_save(chat_id, role, content):
        saved.append(content)

    monkeypatch.setattr(orchestrator, "_prepare_tool_phase", fake_prepare)
    monkeypatch.setattr(orchestrator.db, "save_chat_message", fake_save)

    tokens = [t async for t in orchestrator.generate_response_stream("hola", 7, voice=True)]
    assert "".join(tokens) == "Una respuesta corta."
    assert saved == ["Una respuesta corta."]
