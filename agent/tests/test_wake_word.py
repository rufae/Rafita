"""Helpers puros del daemon de wake word (el hardware se prueba en la torre)."""

import importlib.util
import struct
import wave
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "wake_word.py"
_spec = importlib.util.spec_from_file_location("wake_word", _SCRIPT)
wake_word = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wake_word)


def test_wake_triggered():
    assert wake_word.wake_triggered({"hey_jarvis": 0.7}, 0.5)
    assert not wake_word.wake_triggered({"hey_jarvis": 0.2, "alexa": 0.1}, 0.5)
    assert not wake_word.wake_triggered({}, 0.5)


def test_frame_rms():
    silencio = struct.pack("<100h", *([0] * 100))
    assert wake_word.frame_rms(silencio) == 0.0
    ruido = struct.pack("<100h", *([1000] * 100))
    assert wake_word.frame_rms(ruido) == 1000.0
    assert wake_word.frame_rms(b"") == 0.0


def test_wav_to_pcm(tmp_path):
    path = tmp_path / "x.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        wf.writeframes(struct.pack("<220h", *([123] * 220)))
    rate, pcm = wake_word.wav_to_pcm(path.read_bytes())
    assert rate == 22050
    assert len(pcm) == 220 * 2
