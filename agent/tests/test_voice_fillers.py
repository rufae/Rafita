"""Tests de las frases de espera del modo llamada (Fase 2, 2026-09-29)."""

import src.voice_stream.server as vs


def test_rotacion_con_cooldown_sin_repetir_seguidos():
    c = vs._FillerController()
    assert c.should_speak(1000.0) is True
    primera = c.next_phrase(1000.0)
    assert primera == vs.FILLER_PHRASES[0]
    # dentro del cooldown no habla
    assert c.should_speak(1000.0 + vs.FILLER_COOLDOWN_S - 1) is False
    assert c.should_speak(1000.0 + vs.FILLER_COOLDOWN_S) is True
    segunda = c.next_phrase(1000.0 + vs.FILLER_COOLDOWN_S)
    assert segunda != primera
    # rotacion completa: nunca repite consecutivos
    frases = [primera, segunda]
    ahora = 1000.0 + 2 * vs.FILLER_COOLDOWN_S
    for _ in range(len(vs.FILLER_PHRASES) * 2):
        frases.append(c.next_phrase(ahora))
        ahora += vs.FILLER_COOLDOWN_S
    assert all(a != b for a, b in zip(frases, frases[1:]))


def test_frases_sin_markdown_ni_emojis():
    for frase in vs.FILLER_PHRASES:
        assert not any(ch in frase for ch in "*_#`[]()")
        assert frase.isascii()
        assert frase.endswith("...")


async def test_filler_no_habla_si_la_respuesta_ya_empezo(monkeypatch):
    enviados: list = []

    async def fake_json(ws, session, payload):
        enviados.append(payload)
        return True

    async def fake_bytes(ws, session, data):
        enviados.append(data)
        return True

    monkeypatch.setattr(vs, "FILLER_DELAY_S", 0.01)
    monkeypatch.setattr(vs, "_safe_send_json", fake_json)
    monkeypatch.setattr(vs, "_safe_send_bytes", fake_bytes)
    session = {
        "state": "active",
        "respuesta_iniciada": True,
        "filler": vs._FillerController(),
    }
    await vs._filler_si_tarda(None, session)  # type: ignore[arg-type]
    assert enviados == []


async def test_filler_no_habla_si_la_llamada_termino(monkeypatch):
    enviados: list = []

    async def fake_json(ws, session, payload):
        enviados.append(payload)
        return True

    monkeypatch.setattr(vs, "FILLER_DELAY_S", 0.01)
    monkeypatch.setattr(vs, "_safe_send_json", fake_json)
    session = {"state": "ended", "respuesta_iniciada": False, "filler": vs._FillerController()}
    await vs._filler_si_tarda(None, session)  # type: ignore[arg-type]
    assert enviados == []


async def test_filler_habla_una_vez_y_respeta_cooldown(monkeypatch):
    enviados: list = []

    async def fake_synth(text):
        assert text in vs.FILLER_PHRASES
        return b"AUDIO"

    async def fake_json(ws, session, payload):
        enviados.append(payload)
        return True

    async def fake_bytes(ws, session, data):
        enviados.append(data)
        return True

    monkeypatch.setattr(vs, "FILLER_DELAY_S", 0.01)
    monkeypatch.setattr(vs, "_synthesize_speech_bytes", fake_synth)
    monkeypatch.setattr(vs, "_safe_send_json", fake_json)
    monkeypatch.setattr(vs, "_safe_send_bytes", fake_bytes)

    session = {
        "state": "active",
        "respuesta_iniciada": False,
        "filler": vs._FillerController(),
    }
    await vs._filler_si_tarda(None, session)  # type: ignore[arg-type]
    tipos = [p.get("type") for p in enviados if isinstance(p, dict)]
    assert "filler" in tipos
    assert b"AUDIO" in enviados
    assert session["respuesta_iniciada"] is True

    # dentro del mismo turno ya no habla otra vez
    enviados.clear()
    await vs._filler_si_tarda(None, session)  # type: ignore[arg-type]
    assert enviados == []

    # en un turno nuevo, con cooldown vencido, vuelve a hablar (otra frase)
    session["respuesta_iniciada"] = False
    session["filler"]._last_at = 0.0
    await vs._filler_si_tarda(None, session)  # type: ignore[arg-type]
    nuevos = [p.get("text") for p in enviados if isinstance(p, dict)]
    assert nuevos and nuevos[0] != vs.FILLER_PHRASES[0]
