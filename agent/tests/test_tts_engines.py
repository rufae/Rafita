"""Tests de los motores TTS nuevos (2026-09-28): Kokoro y Voicebox.

Sin red y sin modelos reales: se sustituyen httpx, kokoro_onnx y las
funciones de sintesis por stubs.
"""

import base64
import shutil
import sys
from types import SimpleNamespace

import numpy as np
import pytest

import src.utils.tts_kokoro as kokoro
import src.utils.tts_manager as tts
from src.config import settings

# ---------- helpers de httpx falso ----------


class _StreamResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def raise_for_status(self):
        return None

    async def aiter_bytes(self, chunk_size):
        for i in range(0, len(self._payload), chunk_size):
            yield self._payload[i : i + chunk_size]


class _StreamCtx:
    def __init__(self, payload: bytes):
        self._resp = _StreamResp(payload)

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *args):
        return None


class _DownloadClient:
    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def stream(self, method, url):
        return _StreamCtx(b"KOKORO-" + url.encode()[-20:])


def _patch_httpx(monkeypatch, client_cls):
    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(AsyncClient=client_cls))


# ---------- funciones puras ----------


def test_resolve_voice():
    assert kokoro.resolve_voice("") == kokoro.DEFAULT_VOICE
    assert kokoro.resolve_voice(None) == kokoro.DEFAULT_VOICE
    assert kokoro.resolve_voice("es_ES-davefx-medium") == kokoro.DEFAULT_VOICE
    assert kokoro.resolve_voice("em_alex") == "em_alex"


def test_speed_from_length_scale():
    assert kokoro.speed_from_length_scale(0) == 1.0
    assert kokoro.speed_from_length_scale(-1) == 1.0
    assert kokoro.speed_from_length_scale(1.25) == pytest.approx(0.8)
    assert kokoro.speed_from_length_scale(0.5) == 2.0
    assert kokoro.speed_from_length_scale(3.0) == 0.5


# ---------- ensure_kokoro_model ----------


async def test_ensure_kokoro_model_existing(tmp_path, monkeypatch):
    monkeypatch.setattr(kokoro, "KOKORO_DIR", tmp_path)
    (tmp_path / kokoro.MODEL_FILE).write_bytes(b"m")
    (tmp_path / kokoro.VOICES_FILE).write_bytes(b"v")
    result = await kokoro.ensure_kokoro_model()
    assert result == (tmp_path / kokoro.MODEL_FILE, tmp_path / kokoro.VOICES_FILE)


async def test_ensure_kokoro_model_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr(kokoro, "KOKORO_DIR", tmp_path / "kokoro")
    _patch_httpx(monkeypatch, _DownloadClient)
    result = await kokoro.ensure_kokoro_model()
    assert result is not None
    model_path, voices_path = result
    assert model_path.exists() and voices_path.exists()
    assert model_path.stat().st_size > 0


async def test_ensure_kokoro_model_failure_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(kokoro, "KOKORO_DIR", tmp_path / "kokoro")

    class _Broken(_DownloadClient):
        def stream(self, method, url):
            raise RuntimeError("sin red")

    _patch_httpx(monkeypatch, _Broken)
    assert await kokoro.ensure_kokoro_model() is None


# ---------- synthesize_kokoro / prewarm ----------


async def test_synthesize_kokoro_without_model_returns_none(monkeypatch):
    async def no_model():
        return None

    monkeypatch.setattr(kokoro, "ensure_kokoro_model", no_model)
    assert await kokoro.synthesize_kokoro("hola") is None


async def test_synthesize_kokoro_ok(tmp_path, monkeypatch):
    async def fake_model():
        return (tmp_path / "m.onnx", tmp_path / "v.bin")

    monkeypatch.setattr(kokoro, "ensure_kokoro_model", fake_model)
    monkeypatch.setattr(
        kokoro, "_synthesize_blocking", lambda *a: (np.zeros(240, dtype="float32"), 24000)
    )
    result = await kokoro.synthesize_kokoro("hola", voice="ef_dora")
    assert result is not None
    samples, rate = result
    assert rate == 24000 and len(samples) == 240


async def test_prewarm_kokoro_paths(tmp_path, monkeypatch):
    async def no_model():
        return None

    monkeypatch.setattr(kokoro, "ensure_kokoro_model", no_model)
    assert await kokoro.prewarm_kokoro() is False

    async def fake_model():
        return (tmp_path / "m.onnx", tmp_path / "v.bin")

    monkeypatch.setattr(kokoro, "ensure_kokoro_model", fake_model)
    monkeypatch.setattr(kokoro, "_load_kokoro", lambda *a: object())
    assert await kokoro.prewarm_kokoro() is True


# ---------- tts_manager: backend kokoro ----------


async def test_synth_kokoro_to_wav_writes_file(tmp_path, monkeypatch):
    async def fake_synth(text, voice=None, speed=1.0):
        return np.zeros(2400, dtype="float32"), 24000

    monkeypatch.setattr(kokoro, "synthesize_kokoro", fake_synth)
    out = tmp_path / "o.wav"
    result = await tts._synth_kokoro_to_wav("hola", out)
    assert result == out
    assert out.stat().st_size > 0


async def test_synth_kokoro_to_wav_empty_returns_none(tmp_path, monkeypatch):
    async def fake_synth(text, voice=None, speed=1.0):
        return np.array([], dtype="float32"), 24000

    monkeypatch.setattr(kokoro, "synthesize_kokoro", fake_synth)
    assert await tts._synth_kokoro_to_wav("hola", tmp_path / "o.wav") is None


async def test_text_to_speech_kokoro_falls_back_to_piper(tmp_path, monkeypatch):
    async def fake_kokoro(text, output_path):
        return None

    async def fake_piper(text, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"piper")
        return output_path

    monkeypatch.setattr(tts, "_synth_kokoro_to_wav", fake_kokoro)
    monkeypatch.setattr(tts, "_synth_piper_to_wav", fake_piper)
    result = await tts.text_to_speech("hola", engine="kokoro")
    assert result is not None
    assert result.read_bytes() == b"piper"
    shutil.rmtree(result.parent, ignore_errors=True)


# ---------- tts_manager: backend voicebox ----------


async def test_voicebox_without_url_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "voicebox_url", "")
    assert await tts._synth_voicebox_to_wav("hola", tmp_path / "o.wav") is None


class _AudioResp:
    content = b"AUDIO-BYTES"
    headers = {"content-type": "audio/wav"}

    def raise_for_status(self):
        return None


class _AudioClient:
    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, json=None):
        return _AudioResp()


async def test_voicebox_audio_response(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "voicebox_url", "http://voicebox:17493")
    _patch_httpx(monkeypatch, _AudioClient)
    out = tmp_path / "o.wav"
    result = await tts._synth_voicebox_to_wav("hola", out)
    assert result == out
    assert out.read_bytes() == b"AUDIO-BYTES"


class _B64Resp:
    headers = {"content-type": "application/json"}

    def raise_for_status(self):
        return None

    def json(self):
        return {"audio_base64": base64.b64encode(b"B64-AUDIO").decode()}


class _B64Client(_AudioClient):
    async def post(self, url, json=None):
        return _B64Resp()


async def test_voicebox_json_base64(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "voicebox_url", "http://voicebox:17493")
    _patch_httpx(monkeypatch, _B64Client)
    out = tmp_path / "o.wav"
    result = await tts._synth_voicebox_to_wav("hola", out)
    assert result == out
    assert out.read_bytes() == b"B64-AUDIO"


class _UnknownResp(_B64Resp):
    def json(self):
        return {"status": "queued", "id": "abc"}


class _UnknownClient(_AudioClient):
    async def post(self, url, json=None):
        return _UnknownResp()


async def test_voicebox_unknown_json_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "voicebox_url", "http://voicebox:17493")
    _patch_httpx(monkeypatch, _UnknownClient)
    assert await tts._synth_voicebox_to_wav("hola", tmp_path / "o.wav") is None


async def test_text_to_speech_voicebox_falls_back_to_piper(tmp_path, monkeypatch):
    async def fake_voicebox(text, output_path):
        return None

    async def fake_piper(text, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"piper")
        return output_path

    monkeypatch.setattr(tts, "_synth_voicebox_to_wav", fake_voicebox)
    monkeypatch.setattr(tts, "_synth_piper_to_wav", fake_piper)
    result = await tts.text_to_speech("hola", engine="voicebox")
    assert result is not None
    assert result.read_bytes() == b"piper"
    shutil.rmtree(result.parent, ignore_errors=True)


# ---------- synthesize_wav_bytes propaga el motor ----------


async def test_synthesize_wav_bytes_passes_engine(tmp_path, monkeypatch):
    seen = {}

    async def fake_tts(text, engine=None):
        seen["engine"] = engine
        out = tmp_path / "r.wav"
        out.write_bytes(b"WAV")
        return out

    monkeypatch.setattr(tts, "text_to_speech", fake_tts)
    data = await tts.synthesize_wav_bytes("hola", engine="kokoro")
    assert data == b"WAV"
    assert seen["engine"] == "kokoro"


# ---------- prewarm con motor kokoro ----------


async def test_prewarm_tts_with_kokoro_engine(monkeypatch):
    async def fake_prewarm():
        return True

    monkeypatch.setattr(settings, "tts_engine", "kokoro")
    monkeypatch.setattr(kokoro, "prewarm_kokoro", fake_prewarm)
    assert await tts.prewarm_tts() is True


async def test_prewarm_tts_kokoro_fails_falls_back_to_piper(tmp_path, monkeypatch):
    async def fake_prewarm():
        return False

    async def fake_ensure():
        return tmp_path / "voz.onnx"

    monkeypatch.setattr(settings, "tts_engine", "kokoro")
    monkeypatch.setattr(kokoro, "prewarm_kokoro", fake_prewarm)
    monkeypatch.setattr(tts, "ensure_voice_model", fake_ensure)
    monkeypatch.setattr(tts, "_load_piper_voice", lambda p: object())
    assert await tts.prewarm_tts() is True


# ---------- _work_dir / _load_kokoro reales / rutas de error ----------


def test_work_dir_falla_y_vuelve_al_original(tmp_path, monkeypatch):
    bloqueador = tmp_path / "archivo"
    bloqueador.write_text("x")
    monkeypatch.setattr(kokoro, "KOKORO_DIR", bloqueador / "a")
    monkeypatch.setattr(kokoro, "FALLBACK_DIR", bloqueador / "b")
    assert kokoro._work_dir() == bloqueador / "a"


async def test_ensure_descarga_solo_el_archivo_faltante(tmp_path, monkeypatch):
    monkeypatch.setattr(kokoro, "KOKORO_DIR", tmp_path)
    (tmp_path / kokoro.MODEL_FILE).write_bytes(b"modelo-existente")
    _patch_httpx(monkeypatch, _DownloadClient)
    result = await kokoro.ensure_kokoro_model()
    assert result is not None
    modelo, voces = result
    assert modelo.read_bytes() == b"modelo-existente"
    assert voces.exists() and voces.read_bytes().startswith(b"KOKORO-")


class _RtFalso:
    class SessionOptions:
        def __init__(self):
            self.intra_op_num_threads = 0
            self.inter_op_num_threads = 0

    llamadas = []

    @classmethod
    def InferenceSession(cls, path, so, providers=None):
        cls.llamadas.append((path, so, providers))
        return "sesion-falsa"


class _EngineFalso:
    def create(self, text, voice=None, lang="es", speed=1.0):
        return np.zeros(80, dtype="float32"), 16000


class _KokoroFalso:
    @staticmethod
    def from_session(session, voices):
        return _EngineFalso()


def test_load_kokoro_con_dobles_y_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(kokoro, "_kokoro", None)
    monkeypatch.setattr(settings, "kokoro_threads", 2)
    monkeypatch.setitem(sys.modules, "onnxruntime", _RtFalso)
    monkeypatch.setitem(sys.modules, "kokoro_onnx", SimpleNamespace(Kokoro=_KokoroFalso))
    modelo, voces = tmp_path / "m.onnx", tmp_path / "v.bin"
    motor = kokoro._load_kokoro(modelo, voces)
    assert isinstance(motor, _EngineFalso)
    assert _RtFalso.llamadas and _RtFalso.llamadas[-1][1].intra_op_num_threads == 2
    # cache: la segunda llamada no vuelve a crear sesion
    assert kokoro._load_kokoro(modelo, voces) is motor
    assert len(_RtFalso.llamadas) == 1


def test_synthesize_blocking_con_motor_cargado(monkeypatch, tmp_path):
    monkeypatch.setattr(kokoro, "_kokoro", _EngineFalso())
    muestras, rate = kokoro._synthesize_blocking(
        tmp_path / "m", tmp_path / "v", "hola", "es_mia", 1.0
    )
    assert rate == 16000 and len(muestras) == 80


async def test_synthesize_kokoro_excepcion_devuelve_none(monkeypatch, tmp_path):
    async def fake_model():
        return (tmp_path / "m.onnx", tmp_path / "v.bin")

    def roto(*a, **k):
        raise RuntimeError("motor roto")

    monkeypatch.setattr(kokoro, "ensure_kokoro_model", fake_model)
    monkeypatch.setattr(kokoro, "_synthesize_blocking", roto)
    assert await kokoro.synthesize_kokoro("hola") is None


async def test_prewarm_kokoro_excepcion_devuelve_false(monkeypatch, tmp_path):
    async def fake_model():
        return (tmp_path / "m.onnx", tmp_path / "v.bin")

    def roto(*a, **k):
        raise RuntimeError("carga rota")

    monkeypatch.setattr(kokoro, "ensure_kokoro_model", fake_model)
    monkeypatch.setattr(kokoro, "_load_kokoro", roto)
    assert await kokoro.prewarm_kokoro() is False
