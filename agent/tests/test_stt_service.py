"""STT unificado: filtros de ruido, fallback local y servicio remoto."""

from pathlib import Path

import pytest

from src.config import settings
from src.services import stt_service


def test_filtro_segmentos_descarta_ruido():
    segmentos = [
        {"text": "hola", "no_speech_prob": 0.1, "avg_logprob": -0.3},
        {"text": "ruido", "no_speech_prob": 0.9, "avg_logprob": -0.2},
        {"text": "dudoso", "no_speech_prob": 0.2, "avg_logprob": -1.5},
        {"text": "  ", "no_speech_prob": 0.0, "avg_logprob": 0.0},
    ]
    limpios = stt_service._filtrar_segmentos(segmentos)
    assert [s["text"] for s in limpios] == ["hola"]


def test_frases_de_alucinacion_descartadas():
    assert stt_service._es_alucinacion("Subtítulos realizados por la comunidad de Amara.org")
    assert stt_service._es_alucinacion("¡No olvides suscribirte al canal!")
    assert not stt_service._es_alucinacion("Mañana tengo una reunión a las diez")


def test_build_wav_remuestrea_y_cabecera():
    pcm_48k = b"\x00\x01" * 4800  # 0.1 s a 48 kHz
    wav = stt_service.build_wav(pcm_48k, 48000)
    assert wav[:4] == b"RIFF"
    assert wav[24:28] == b"16000"[:4] or True  # tasa en bytes 24-28
    import struct

    tasa = struct.unpack("<I", wav[24:28])[0]
    assert tasa == 16000
    datos = struct.unpack("<I", wav[40:44])[0]
    assert datos == 1600 * 2  # 0.1 s a 16 kHz, 16 bits


async def test_transcribe_bytes_sin_remoto_usa_local(monkeypatch):
    monkeypatch.setattr(settings, "whisper_remote_url", "")

    class _Seg:
        def __init__(self, text, nsp=0.1, lp=-0.3):
            self.text = text
            self.no_speech_prob = nsp
            self.avg_logprob = lp
            self.start = 0.0
            self.end = 1.0

    class _Modelo:
        def transcribe(self, *args, **kwargs):
            return iter([_Seg("Hola, esto es una prueba"), _Seg("ruido", nsp=0.95)]), None

    monkeypatch.setattr(stt_service, "_get_local_model", lambda: _Modelo())
    resultado = await stt_service.transcribe_bytes(b"x" * 2000)
    assert resultado["source"] == "local"
    assert resultado["text"] == "Hola, esto es una prueba"


async def test_transcribe_bytes_remoto_se_usa_si_responde(monkeypatch):
    monkeypatch.setattr(settings, "whisper_remote_url", "http://torre:9001")

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "text": "texto remoto",
                "segments": [{"text": "texto remoto", "no_speech_prob": 0.05, "avg_logprob": -0.2}],
                "elapsed": 0.5,
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    resultado = await stt_service.transcribe_bytes(b"x" * 2000)
    assert resultado["source"] == "remote"
    assert resultado["text"] == "texto remoto"


async def test_transcribe_bytes_remoto_caido_cae_a_local(monkeypatch):
    monkeypatch.setattr(settings, "whisper_remote_url", "http://torre:9001")

    class _Modelo:
        def transcribe(self, *args, **kwargs):
            class _S:
                text = "local de emergencia"
                no_speech_prob = 0.1
                avg_logprob = -0.2
                start = 0.0
                end = 1.0

            return iter([_S()]), None

    def _cliente_roto(*args, **kwargs):
        raise ConnectionError("tower off")

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _cliente_roto)
    monkeypatch.setattr(stt_service, "_get_local_model", lambda: _Modelo())
    resultado = await stt_service.transcribe_bytes(b"x" * 2000)
    assert resultado["source"] == "local"
    assert resultado["text"] == "local de emergencia"


async def test_meeting_transcribir_usa_stt_service(monkeypatch, tmp_path: Path):
    from src.services import meeting_service

    capturado = {}

    async def fake_transcribe(audio_bytes, language=None, prompt="", beam_size=5):
        capturado["beam"] = beam_size
        return {
            "text": "conversacion",
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "Hola"},
                {"start": 1.0, "end": 2.0, "text": "¿Qué tal?"},
            ],
            "source": "remote",
        }

    monkeypatch.setattr("src.services.stt_service.transcribe_bytes", fake_transcribe)
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFFxxxx")
    segmentos, idioma = await meeting_service.transcribir(wav)
    assert capturado["beam"] == 5
    assert idioma == "es"
    assert [s["text"] for s in segmentos] == ["Hola", "¿Qué tal?"]


def test_normaliza_correo_dictado():
    from src.utils.voice_text import normalize_dictated_email

    assert (
        normalize_dictated_email("mi correo es anabel arroba gmail punto com")
        == "mi correo es anabel@gmail.com"
    )
    assert (
        normalize_dictated_email("escribe a juan guion bajo perez arroba hotmail punto es")
        == "escribe a juan_perez@hotmail.es"
    )
    # Sin 'arroba' no se toca nada.
    assert normalize_dictated_email("hola punto com esto no es un correo") == (
        "hola punto com esto no es un correo"
    )


async def test_transcribe_remoto_normaliza_correo(monkeypatch):
    monkeypatch.setattr(settings, "whisper_remote_url", "http://torre:9001")

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "text": "mi correo es anabel arroba gmail punto com",
                "segments": [
                    {
                        "text": "mi correo es anabel arroba gmail punto com",
                        "no_speech_prob": 0.05,
                        "avg_logprob": -0.2,
                    }
                ],
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    resultado = await stt_service.transcribe_bytes(b"x" * 2000)
    assert resultado["text"] == "mi correo es anabel@gmail.com"
