"""Cobertura de src/utils/tts_manager.py: funciones puras y fallback espeak.

Sin red y sin modelos reales: se sustituyen httpx, asyncio.create_subprocess_exec
y las cargas de onnxruntime/piper por stubs.
"""

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import src.utils.tts_manager as tts


class _FakeProc:
    def __init__(self, on_wait=None):
        self._on_wait = on_wait

    async def wait(self):
        if self._on_wait:
            self._on_wait()
        return 0


def _fake_exec(capture, write_path=None, empty=False):
    async def fake(*cmd, **kwargs):
        capture["cmd"] = cmd
        capture["kwargs"] = kwargs
        if write_path is not None:
            write_path.write_bytes(b"" if empty else b"RIFFDATA")
        return _FakeProc()

    return fake


def _raising_exec(error):
    async def fake(*cmd, **kwargs):
        raise error

    return fake


# ---------- _fallback_espeak ----------


async def test_fallback_espeak_writes_output(tmp_path, monkeypatch):
    output = tmp_path / "out.wav"
    capture = {}
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec(capture, output))
    result = await tts._fallback_espeak('di "hola"', output)
    assert result == output
    cmd = capture["cmd"]
    assert cmd[0] == "espeak-ng"
    assert "-v" in cmd and tts.FALLBACK_LANG in cmd
    assert "-w" in cmd
    assert any("hola" in part for part in cmd)


async def test_fallback_espeak_truncates_long_text(tmp_path, monkeypatch):
    output = tmp_path / "out.wav"
    capture = {}
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec(capture, output))
    result = await tts._fallback_espeak("x" * 900, output)
    assert result == output
    text_arg = capture["cmd"][-1]
    assert len(text_arg) < 520
    # Sin comillas literales en argv (fix 2026-09-28: espeak las pronunciaba)
    assert text_arg.endswith("...")
    assert not text_arg.startswith('"')


async def test_fallback_espeak_empty_output_returns_none(tmp_path, monkeypatch):
    output = tmp_path / "out.wav"
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec({}, output, empty=True))
    assert await tts._fallback_espeak("hola", output) is None


async def test_fallback_espeak_subprocess_error_returns_none(tmp_path, monkeypatch):
    output = tmp_path / "out.wav"
    monkeypatch.setattr("asyncio.create_subprocess_exec", _raising_exec(OSError("sin espeak")))
    assert await tts._fallback_espeak("hola", output) is None


# ---------- ensure_voice_model ----------


async def test_ensure_voice_model_returns_existing_model(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "TTS_MODELS_DIR", tmp_path / "models")
    model = tts.TTS_MODELS_DIR
    model.mkdir(parents=True)
    (model / "voz.onnx").write_bytes(b"model")
    monkeypatch.setattr(tts, "_tts_ready", False)

    result = await tts.ensure_voice_model()
    assert result == model / "voz.onnx"
    assert tts._tts_ready is True


async def test_ensure_voice_model_downloads_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "TTS_MODELS_DIR", tmp_path / "models")
    calls = []

    class _Resp:
        content = b"payload"

        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url):
            calls.append(url)
            return _Resp()

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(AsyncClient=_Client))
    monkeypatch.setattr(tts, "_tts_ready", False)

    result = await tts.ensure_voice_model()
    assert result is not None
    assert result.name.endswith(".onnx")
    assert result.exists()
    assert (result.parent / (result.name + ".json")).exists()
    assert any("huggingface" in str(c) for c in calls if isinstance(c, str))
    assert tts._tts_ready is True


async def test_ensure_voice_model_download_failure_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "TTS_MODELS_DIR", tmp_path / "models")

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url):
            raise RuntimeError("sin red")

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(AsyncClient=_Client))
    assert await tts.ensure_voice_model() is None


# ---------- text_to_speech ----------


async def test_text_to_speech_uses_espeak_when_no_model(tmp_path, monkeypatch):
    async def fake_ensure():
        return None

    monkeypatch.setattr(tts, "ensure_voice_model", fake_ensure)
    capture = {}

    async def fake_exec(*cmd, **kwargs):
        capture["cmd"] = cmd
        Path(cmd[cmd.index("-w") + 1]).write_bytes(b"RIFF")
        return _FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    result = await tts.text_to_speech("hola mundo")
    assert result is not None
    assert result.name == "response.wav"
    assert capture["cmd"][0] == "espeak-ng"
    shutil.rmtree(result.parent, ignore_errors=True)


async def test_text_to_speech_piper_empty_audio_falls_back(tmp_path, monkeypatch):
    async def fake_ensure():
        return tmp_path / "voz.onnx"

    async def fake_fallback(text, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"espeak")
        return output_path

    def fake_synth(text, model_path, output_path):
        return None, 22050

    monkeypatch.setattr(tts, "ensure_voice_model", fake_ensure)
    monkeypatch.setattr(tts, "_synthesize_piper", fake_synth)
    monkeypatch.setattr(tts, "_fallback_espeak", fake_fallback)
    result = await tts.text_to_speech("hola")
    assert result is not None
    assert result.read_bytes() == b"espeak"
    shutil.rmtree(result.parent, ignore_errors=True)


async def test_text_to_speech_piper_error_falls_back(tmp_path, monkeypatch):
    async def fake_ensure():
        return tmp_path / "voz.onnx"

    async def fake_fallback(text, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"espeak")
        return output_path

    def fake_synth(text, model_path, output_path):
        raise RuntimeError("onnx roto")

    monkeypatch.setattr(tts, "ensure_voice_model", fake_ensure)
    monkeypatch.setattr(tts, "_synthesize_piper", fake_synth)
    monkeypatch.setattr(tts, "_fallback_espeak", fake_fallback)
    result = await tts.text_to_speech("hola")
    assert result is not None
    assert result.read_bytes() == b"espeak"
    shutil.rmtree(result.parent, ignore_errors=True)


async def test_text_to_speech_piper_success(tmp_path, monkeypatch):
    async def fake_ensure():
        return tmp_path / "voz.onnx"

    def fake_synth(text, model_path, output_path):
        Path(output_path).write_bytes(b"RIFF")
        return [0.0, 0.1], 22050

    monkeypatch.setattr(tts, "ensure_voice_model", fake_ensure)
    monkeypatch.setattr(tts, "_synthesize_piper", fake_synth)
    result = await tts.text_to_speech("hola")
    assert result is not None
    assert result.name == "response.wav"
    shutil.rmtree(result.parent, ignore_errors=True)


# ---------- _load_piper_voice ----------


def _piper_stubs(monkeypatch, created):
    class _Voice:
        def __init__(self, session, config):
            created["session"] = session
            created["config"] = config

    class _Session:
        def __init__(self, path):
            created["path"] = path

    def _piper_config(**kwargs):
        created["cfg"] = kwargs
        return kwargs

    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(InferenceSession=_Session))
    monkeypatch.setitem(sys.modules, "piper", SimpleNamespace(PiperVoice=_Voice))
    monkeypatch.setitem(
        sys.modules,
        "piper.config",
        SimpleNamespace(PhonemeType=SimpleNamespace(ESPEAK="espeak"), PiperConfig=_piper_config),
    )


def test_load_piper_voice_builds_and_caches(tmp_path, monkeypatch):
    model = tmp_path / "voz.onnx"
    model.write_bytes(b"m")
    config = {
        "num_symbols": 12,
        "num_speakers": 1,
        "audio": {"sample_rate": 16000},
        "espeak": {"voice": "es"},
        "inference": {"length_scale": 1.1, "noise_scale": 0.5, "noise_w": 0.7},
        "phoneme_id_map": {"a": 1},
    }
    (tmp_path / "voz.onnx.json").write_text(json.dumps(config), encoding="utf-8")

    created = {}
    _piper_stubs(monkeypatch, created)
    monkeypatch.setattr(tts, "_piper_voice", None)

    voice = tts._load_piper_voice(str(model))
    assert voice is tts._piper_voice
    assert created["cfg"]["num_symbols"] == 12
    assert created["cfg"]["sample_rate"] == 16000
    assert created["cfg"]["length_scale"] == 1.1
    assert created["cfg"]["phoneme_id_map"] == {"a": 1}
    # segunda llamada usa la caché (no vuelve a construir)
    again = tts._load_piper_voice(str(model))
    assert again is voice


def test_synthesize_piper_error_returns_empty_audio(tmp_path, monkeypatch):
    def boom(model_path):
        raise RuntimeError("fallo de carga")

    monkeypatch.setattr(tts, "_load_piper_voice", boom)
    data, rate = tts._synthesize_piper("hola", str(tmp_path / "voz.onnx"), str(tmp_path / "o.wav"))
    assert data is None
    assert rate == 22050


def test_synthesize_piper_writes_wav_and_reads_samples(tmp_path, monkeypatch):
    class _Voice:
        def synthesize(self, text, wav_file):
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(b"\x00\x00" * 32)

    monkeypatch.setattr(tts, "_load_piper_voice", lambda model_path: _Voice())
    out = tmp_path / "o.wav"
    data, rate = tts._synthesize_piper("hola", str(tmp_path / "voz.onnx"), str(out))
    assert rate == 16000
    assert data is not None
    assert len(data) == 32


# ---------- prewarm_tts ----------


async def test_prewarm_without_model_returns_false(monkeypatch):
    async def fake_ensure():
        return None

    monkeypatch.setattr(tts, "ensure_voice_model", fake_ensure)
    assert await tts.prewarm_tts() is False


async def test_prewarm_success_returns_true(monkeypatch):
    async def fake_ensure():
        return Path("/tmp/voz.onnx")

    def fake_load(model_path):
        return object()

    monkeypatch.setattr(tts, "ensure_voice_model", fake_ensure)
    monkeypatch.setattr(tts, "_load_piper_voice", fake_load)
    assert await tts.prewarm_tts() is True


async def test_prewarm_load_failure_returns_false(monkeypatch):
    async def fake_ensure():
        return Path("/tmp/voz.onnx")

    def fake_load(model_path):
        raise RuntimeError("onnx no disponible")

    monkeypatch.setattr(tts, "ensure_voice_model", fake_ensure)
    monkeypatch.setattr(tts, "_load_piper_voice", fake_load)
    assert await tts.prewarm_tts() is False


# ---------- convert_to_ogg ----------


async def test_convert_to_ogg_creates_ogg(tmp_path, monkeypatch):
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"RIFF")
    ogg = tmp_path / "audio.ogg"
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec({}, ogg))
    result = await tts.convert_to_ogg(wav)
    assert result == ogg


async def test_convert_to_ogg_without_output_returns_wav(tmp_path, monkeypatch):
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"RIFF")
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec({}, None))
    result = await tts.convert_to_ogg(wav)
    assert result == wav


async def test_convert_to_ogg_error_returns_wav(tmp_path, monkeypatch):
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"RIFF")
    monkeypatch.setattr("asyncio.create_subprocess_exec", _raising_exec(OSError("sin ffmpeg")))
    result = await tts.convert_to_ogg(wav)
    assert result == wav


# ---------- synthesize_wav_bytes ----------


async def test_synthesize_wav_bytes_without_tts_returns_none(monkeypatch):
    async def fake_tts(text):
        return None

    monkeypatch.setattr(tts, "text_to_speech", fake_tts)
    assert await tts.synthesize_wav_bytes("hola") is None


async def test_synthesize_wav_bytes_returns_bytes_and_cleans(tmp_path, monkeypatch):
    out_dir = tmp_path / "tts_job"
    out_dir.mkdir()
    wav = out_dir / "response.wav"
    wav.write_bytes(b"RIFFWAV")

    async def fake_tts(text):
        return wav

    monkeypatch.setattr(tts, "text_to_speech", fake_tts)
    data = await tts.synthesize_wav_bytes("hola")
    assert data == b"RIFFWAV"
    assert not out_dir.exists()


async def test_synthesize_wav_bytes_unreadable_returns_none(tmp_path, monkeypatch):
    out_dir = tmp_path / "tts_job"
    out_dir.mkdir()
    missing = out_dir / "response.wav"

    async def fake_tts(text):
        return missing

    monkeypatch.setattr(tts, "text_to_speech", fake_tts)
    assert await tts.synthesize_wav_bytes("hola") is None
    assert not out_dir.exists()
