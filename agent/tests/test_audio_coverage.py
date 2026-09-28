"""Cobertura de handlers/audio.py (logica no-Telegram: STT, split, TTS, pipeline)."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.config import settings
from src.handlers import audio
from src.utils.access_control import SlidingWindowLimiter


class FakeMessage:
    def __init__(self, voice=None):
        self.voice = voice
        self.caption = None
        self.replies = []
        self.voice_replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)

    async def reply_chat_action(self, *args, **kwargs):
        return None

    async def reply_voice(self, voice, **kwargs):
        self.voice_replies.append(voice)


def make_update(message, user_id=12345):
    return SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=user_id),
    )


def make_context(bot=None):
    context = MagicMock()
    if bot is not None:
        context.bot = bot
    context.user_data = {}
    return context


def open_access(monkeypatch, user_id=12345):
    monkeypatch.setattr(settings, "admin_ids", [user_id])
    import src.utils.access_control as access_control

    monkeypatch.setattr(
        access_control, "chat_limiter", SlidingWindowLimiter(max_events=1000, window_seconds=60.0)
    )


# ---------------------------------------------------------------------------
# _get_whisper_model
# ---------------------------------------------------------------------------


def test_whisper_model_is_cached(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(audio, "_whisper_model", sentinel)
    assert audio._get_whisper_model() is sentinel


def test_whisper_model_maps_tiny_to_base(monkeypatch):
    monkeypatch.setattr(settings, "whisper_model", "tiny")
    monkeypatch.setattr(audio, "_whisper_model", None)
    captured = {}

    class FakeWhisperModel:
        def __init__(self, size, **kwargs):
            captured["size"] = size
            captured.update(kwargs)

    fake_module = SimpleNamespace(WhisperModel=FakeWhisperModel)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)

    model = audio._get_whisper_model()

    assert isinstance(model, FakeWhisperModel)
    assert captured["size"] == "base"
    assert captured["device"] == "cpu"
    assert captured["compute_type"] == "int8"
    # La instancia queda cacheada
    assert audio._get_whisper_model() is model


def test_whisper_model_returns_none_on_import_error(monkeypatch):
    monkeypatch.setattr(audio, "_whisper_model", None)
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace())

    assert audio._get_whisper_model() is None


def test_whisper_model_returns_none_on_load_error(monkeypatch):
    monkeypatch.setattr(audio, "_whisper_model", None)

    class BoomModel:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("sin pesos")

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=BoomModel))

    assert audio._get_whisper_model() is None


# ---------------------------------------------------------------------------
# _transcribe_file
# ---------------------------------------------------------------------------


class FakeTranscribeModel:
    def __init__(self, segments=None, error=None):
        self.segments = segments or []
        self.error = error
        self.kwargs = None

    def transcribe(self, path, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.segments, SimpleNamespace(language="es")


async def test_transcribe_file_without_model(monkeypatch):
    monkeypatch.setattr(audio, "_get_whisper_model", lambda: None)
    assert await audio._transcribe_file(Path("/tmp/nope.ogg")) is None


async def test_transcribe_file_joins_segments(monkeypatch):
    model = FakeTranscribeModel(
        segments=[SimpleNamespace(text="Hola"), SimpleNamespace(text="mundo")]
    )
    monkeypatch.setattr(audio, "_get_whisper_model", lambda: model)

    result = await audio._transcribe_file(Path("/tmp/voice.ogg"))

    assert result == "Hola mundo"
    assert model.kwargs["beam_size"] == 1
    assert model.kwargs["language"] == settings.language
    assert model.kwargs["vad_filter"] is True


async def test_transcribe_file_empty_segments(monkeypatch):
    model = FakeTranscribeModel(segments=[])
    monkeypatch.setattr(audio, "_get_whisper_model", lambda: model)

    assert await audio._transcribe_file(Path("/tmp/voice.ogg")) is None


async def test_transcribe_file_error_returns_none(monkeypatch):
    model = FakeTranscribeModel(error=RuntimeError("audio corrupto"))
    monkeypatch.setattr(audio, "_get_whisper_model", lambda: model)

    assert await audio._transcribe_file(Path("/tmp/voice.ogg")) is None


# ---------------------------------------------------------------------------
# _split_text_for_streaming
# ---------------------------------------------------------------------------


def test_split_short_text_single_chunk():
    assert audio._split_text_for_streaming("Hola mundo") == ["Hola mundo"]


def test_split_long_text_by_sentences():
    text = ". ".join(["Frase numero %d con contenido" % i for i in range(10)])
    chunks = audio._split_text_for_streaming(text, max_chars=80)
    assert len(chunks) > 1
    assert all(len(c) <= 80 for c in chunks)
    assert " ".join(chunks).replace(" ", "") != ""
    assert all(c.strip() for c in chunks)


def test_split_treats_exclamation_and_question_as_separator():
    text = "Primera frase! Segunda frase? Tercera frase. Cuarta frase."
    chunks = audio._split_text_for_streaming(text, max_chars=30)
    # Los signos ! y ? actuan como separadores de frase igual que el punto
    assert chunks == ["Primera frase. Segunda frase", "Tercera frase. Cuarta frase"]


def test_split_oversized_sentence_kept_whole():
    long_sentence = "x" * 300
    chunks = audio._split_text_for_streaming(long_sentence + ".", max_chars=100)
    assert long_sentence in chunks


# ---------------------------------------------------------------------------
# _send_voice_reply_fast
# ---------------------------------------------------------------------------


async def test_send_voice_reply_skips_bad_chunks(monkeypatch, tmp_path):
    message = FakeMessage()
    update = SimpleNamespace(effective_message=message)
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    ogg = tmp_path / "a.ogg"
    ogg.write_bytes(b"OggS-ok")

    async def fake_tts(chunk, engine=None):
        if chunk == "sin-audio":
            return None
        return wav

    monkeypatch.setattr(audio, "text_to_speech", fake_tts)
    monkeypatch.setattr(audio, "convert_to_ogg", AsyncMock(return_value=ogg))
    monkeypatch.setattr(
        audio, "_split_text_for_streaming", lambda text, max_chars=200: ["  ", "sin-audio", "bueno"]
    )

    await audio._send_voice_reply_fast(update, make_context(), "texto")

    assert len(message.voice_replies) == 1
    assert message.voice_replies[0].getvalue() == b"OggS-ok"


async def test_send_voice_reply_tolerates_unreadable_chunk(monkeypatch, tmp_path):
    message = FakeMessage()
    update = SimpleNamespace(effective_message=message)
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    ogg = tmp_path / "a.ogg"
    ogg.write_bytes(b"OggS-ok")
    bad_ogg = MagicMock()
    bad_ogg.exists.return_value = True
    bad_ogg.read_bytes.side_effect = OSError("disco roto")

    async def fake_tts(chunk, engine=None):
        return wav

    results = [bad_ogg, ogg]

    async def fake_convert(path):
        return results.pop(0)

    monkeypatch.setattr(audio, "text_to_speech", fake_tts)
    monkeypatch.setattr(audio, "convert_to_ogg", fake_convert)
    monkeypatch.setattr(
        audio, "_split_text_for_streaming", lambda text, max_chars=200: ["primera", "segunda"]
    )

    await audio._send_voice_reply_fast(update, make_context(), "texto")

    assert message.voice_replies[0].getvalue() == b"OggS-ok"


async def test_send_voice_reply_tolerates_reply_failures(monkeypatch):
    message = FakeMessage()

    async def boom_reply(text, **kwargs):
        raise RuntimeError("telegram caido")

    message.reply_text = boom_reply
    update = SimpleNamespace(effective_message=message)

    async def fake_tts(chunk, engine=None):
        raise RuntimeError("piper caido")

    monkeypatch.setattr(audio, "text_to_speech", fake_tts)

    await audio._send_voice_reply_fast(update, make_context(), "Hola")
    # Ni siquiera el aviso de error debe romper el pipeline


async def test_send_voice_reply_without_message_is_noop():
    update = SimpleNamespace(effective_message=None)
    await audio._send_voice_reply_fast(update, None, "hola")
    # No debe lanzar excepcion


async def test_send_voice_reply_sends_combined_audio(monkeypatch, tmp_path):
    message = FakeMessage()
    update = SimpleNamespace(effective_message=message)
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    ogg = tmp_path / "a.ogg"
    ogg.write_bytes(b"OggS-part")

    async def fake_tts(chunk, engine=None):
        return wav

    async def fake_convert(path):
        return ogg

    monkeypatch.setattr(audio, "text_to_speech", fake_tts)
    monkeypatch.setattr(audio, "convert_to_ogg", fake_convert)

    await audio._send_voice_reply_fast(
        update, make_context(), "Hola. Mundo. Esto es una prueba larga."
    )

    assert message.replies == []
    assert len(message.voice_replies) == 1
    assert message.voice_replies[0].getvalue().startswith(b"OggS")


async def test_send_voice_reply_without_audio_reports_failure(monkeypatch):
    message = FakeMessage()
    update = SimpleNamespace(effective_message=message)

    async def fake_tts(chunk, engine=None):
        return None

    monkeypatch.setattr(audio, "text_to_speech", fake_tts)

    await audio._send_voice_reply_fast(update, make_context(), "Hola")

    assert message.replies == ["No se pudo generar el audio de respuesta."]


async def test_send_voice_reply_reports_errors(monkeypatch):
    message = FakeMessage()
    update = SimpleNamespace(effective_message=message)

    async def fake_tts(chunk, engine=None):
        raise RuntimeError("piper caido")

    monkeypatch.setattr(audio, "text_to_speech", fake_tts)

    await audio._send_voice_reply_fast(update, make_context(), "Hola")

    assert any("Error al generar respuesta de voz" in r for r in message.replies)


# ---------------------------------------------------------------------------
# voice_handler (pipeline completo con dependencias simuladas)
# ---------------------------------------------------------------------------


class FakeVoice:
    def __init__(self, file_id="voice-file", duration=3):
        self.file_id = file_id
        self.duration = duration


class FakeFile:
    def __init__(self, payload=b"x" * 200):
        self.payload = payload

    async def download_to_drive(self, path):
        Path(path).write_bytes(self.payload)


def install_downloads(monkeypatch, payload=b"x" * 200):
    bot = MagicMock()
    bot.get_file = AsyncMock(return_value=FakeFile(payload))
    return bot


async def test_voice_handler_ignores_non_voice(monkeypatch):
    open_access(monkeypatch)
    message = FakeMessage(voice=None)
    called = {"transcribe": 0}

    async def fake_transcribe(path):
        called["transcribe"] += 1
        return "hola"

    monkeypatch.setattr(audio, "_transcribe_file", fake_transcribe)
    await audio.voice_handler(make_update(message), make_context())

    assert called["transcribe"] == 0
    assert message.replies == []


async def test_voice_handler_rejects_unknown_user(monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", [999])
    message = FakeMessage(voice=FakeVoice())
    update = make_update(message, user_id=12345)

    await audio.voice_handler(update, make_context(install_downloads(monkeypatch)))

    assert message.replies == []


async def test_voice_handler_rate_limited(monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", [12345])
    import src.utils.access_control as access_control

    monkeypatch.setattr(
        access_control, "chat_limiter", SlidingWindowLimiter(max_events=1, window_seconds=60.0)
    )
    message = FakeMessage(voice=FakeVoice())
    update = make_update(message)

    async def fake_transcribe(path):
        return "hola"

    async def fake_ai(update_arg, text, context, from_voice=False):
        return "respuesta"

    monkeypatch.setattr(audio, "_transcribe_file", fake_transcribe)
    monkeypatch.setattr(audio, "_send_voice_reply_fast", AsyncMock())
    monkeypatch.setattr("src.handlers.chat._process_ai_message", fake_ai)
    context = make_context(install_downloads(monkeypatch))
    await audio.voice_handler(update, context)
    await audio.voice_handler(update, context)

    assert any("Vas muy rápido" in r for r in message.replies)


async def test_voice_handler_rejects_empty_audio(monkeypatch):
    open_access(monkeypatch)
    message = FakeMessage(voice=FakeVoice())
    update = make_update(message)

    async def fake_transcribe(path):
        return "no deberia llegar aqui"

    monkeypatch.setattr(audio, "_transcribe_file", fake_transcribe)
    await audio.voice_handler(update, make_context(install_downloads(monkeypatch, payload=b"tiny")))

    assert any("vacío o corrupto" in r for r in message.replies)


async def test_voice_handler_without_transcription(monkeypatch):
    open_access(monkeypatch)
    message = FakeMessage(voice=FakeVoice())
    update = make_update(message)

    async def fake_transcribe(path):
        return "   "

    monkeypatch.setattr(audio, "_transcribe_file", fake_transcribe)
    await audio.voice_handler(update, make_context(install_downloads(monkeypatch)))

    assert any("No pude entender el audio" in r for r in message.replies)


async def test_voice_handler_sends_voice_reply(monkeypatch):
    open_access(monkeypatch)
    message = FakeMessage(voice=FakeVoice())
    update = make_update(message)
    sent = {}

    async def fake_transcribe(path):
        return "Cual es el tiempo"

    async def fake_ai(update_arg, text, context, from_voice=False):
        sent["text"] = text
        sent["from_voice"] = from_voice
        return "Va a llover hoy"

    async def fake_voice_reply(update_arg, context_arg, text):
        sent["reply"] = text

    monkeypatch.setattr(audio, "_transcribe_file", fake_transcribe)
    monkeypatch.setattr(audio, "_send_voice_reply_fast", fake_voice_reply)
    monkeypatch.setattr("src.handlers.chat._process_ai_message", fake_ai)

    await audio.voice_handler(update, make_context(install_downloads(monkeypatch)))

    assert sent == {"text": "Cual es el tiempo", "from_voice": True, "reply": "Va a llover hoy"}
    assert any("Escuché: Cual es el tiempo" in r for r in message.replies)


async def test_voice_handler_text_request_skips_tts(monkeypatch):
    open_access(monkeypatch)
    message = FakeMessage(voice=FakeVoice())
    update = make_update(message)
    sent = {"voice": 0}

    async def fake_transcribe(path):
        return "escribemelo por favor"

    async def fake_ai(update_arg, text, context, from_voice=False):
        return "Aqui lo tienes escrito"

    async def fake_voice_reply(update_arg, context_arg, text):
        sent["voice"] += 1

    monkeypatch.setattr(audio, "_transcribe_file", fake_transcribe)
    monkeypatch.setattr(audio, "_send_voice_reply_fast", fake_voice_reply)
    monkeypatch.setattr("src.handlers.chat._process_ai_message", fake_ai)

    await audio.voice_handler(update, make_context(install_downloads(monkeypatch)))

    assert sent["voice"] == 0
    assert any("Escuché: escribemelo por favor" in r for r in message.replies)


async def test_voice_handler_reports_pipeline_errors(monkeypatch):
    open_access(monkeypatch)
    message = FakeMessage(voice=FakeVoice())
    update = make_update(message)

    async def fake_transcribe(path):
        raise RuntimeError("whisper explotó")

    monkeypatch.setattr(audio, "_transcribe_file", fake_transcribe)
    await audio.voice_handler(update, make_context(install_downloads(monkeypatch)))

    assert any("Error en el pipeline de voz" in r for r in message.replies)
