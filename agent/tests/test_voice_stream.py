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


def test_sanitize_units_and_loanwords():
    out = sanitize_for_tts("Tienes 24 h, paga el 90 % (8,88 €) en 30 min")
    assert "24 horas" in out
    assert "por ciento" in out
    assert "euros" in out
    assert "minutos" in out
    out2 = sanitize_for_tts("☀️ *Briefing de hoy*: **importante**")
    assert "resumen de hoy" in out2.lower()
    assert "*" not in out2 and "☀" not in out2


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

    async def fake_tts(text, engine=None):
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


def test_sanitize_normalizes_units_for_speech():
    from src.utils.voice_text import sanitize_for_tts

    assert "24 horas" in sanitize_for_tts("Tienes 24 h para responder")
    assert "90 por ciento" in sanitize_for_tts("Hay un 90 % de lluvia")
    assert "23 grados" in sanitize_for_tts("Temperatura 23 °C")
    assert "8,88 euros" in sanitize_for_tts("Total: 8,88 €")
    assert "5 minutos" in sanitize_for_tts("En 5 min empieza")


def test_sanitize_removes_stray_markdown_and_leading_symbols():
    from src.utils.voice_text import sanitize_for_tts

    assert sanitize_for_tts("hola * mundo _ raro #") == "hola mundo raro"
    assert sanitize_for_tts("🎉🎉 Hola Rafael") == "Hola Rafael"
    assert sanitize_for_tts("--- ¿Qué tal?") == "¿Qué tal?"


# ---------- VAD adaptativo (2026-09-30) ----------


def _pcm_rms(rms_objetivo: int, n_muestras: int = 128) -> bytes:
    import struct as _struct

    return _struct.pack("<%dh" % n_muestras, *([int(rms_objetivo)] * n_muestras))


def test_vad_adaptativo_aprende_el_ruido_ambiente():
    from src.voice_stream.server import _ms_del_chunk, _vad_adaptativo

    session = {"sample_rate": 16000}
    # Silencio/ruido suave: nunca es voz.
    for _ in range(50):
        assert _vad_adaptativo(_pcm_rms(200), session) is False
    # Voz clara: supera el umbral adaptado (200*3=600, tope 1000).
    assert _vad_adaptativo(_pcm_rms(1500), session) is True
    # Sala ruidosa: el piso sube (umbral 1000) y 900 ya no es voz; 2500 si.
    for _ in range(200):
        _vad_adaptativo(_pcm_rms(700), session)
    assert _vad_adaptativo(_pcm_rms(900), session) is False
    assert _vad_adaptativo(_pcm_rms(2500), session) is True
    # Chunks de 8 ms (128 muestras a 16 kHz).
    assert abs(_ms_del_chunk(_pcm_rms(100), session) - 8.0) < 0.01


def test_vad_adaptativo_no_confunde_silencio_con_voz():
    from src.voice_stream.server import _vad_adaptativo

    session = {"sample_rate": 16000}
    for _ in range(20):
        assert _vad_adaptativo(_pcm_rms(50), session) is False
