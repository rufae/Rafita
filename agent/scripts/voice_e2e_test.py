"""E2E del modo llamada (2026-09-27).

Genera voz con el propio Piper del contenedor, la envia por el WebSocket como
si fuera el microfono del usuario y mide el ciclo completo (STT, primer audio,
TTS). Tambien prueba el corte por boton (stop_speaking) y dos llamadas seguidas.

Uso dentro del contenedor:
    python /workspace/agent/scripts/voice_e2e_test.py
"""

import asyncio
import audioop
import json
import time
import wave

import httpx
import websockets

BASE = "http://127.0.0.1:8001"
WS_BASE = "ws://127.0.0.1:8001"


async def make_speech_pcm(text: str, target_rate: int = 16000) -> bytes:
    from src.utils.tts_manager import text_to_speech

    wav_path = await text_to_speech(text)
    if wav_path is None:
        raise RuntimeError("TTS no disponible para generar la voz de prueba")
    with wave.open(str(wav_path), "rb") as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        data = w.readframes(w.getnframes())
    if channels == 2:
        data = audioop.tomono(data, width, 0.5, 0.5)
    if width != 2:
        data = audioop.lin2lin(data, width, 2)
    if rate != target_rate:
        data, _ = audioop.ratecv(data, 2, 1, rate, target_rate, None)
    return data


async def _stream_utterance(ws, speech_pcm: bytes) -> None:
    for i in range(0, len(speech_pcm), 4096):
        await ws.send(speech_pcm[i : i + 4096])
    silence = b"\x00" * 4096
    for _ in range(6):
        await ws.send(silence)


async def run_call(client: httpx.AsyncClient, speech_pcm: bytes, label: str) -> dict:
    resp = await client.post(BASE + "/call/start", json={})
    session_id = resp.json()["session_id"]
    report = None
    audio_chunks = 0
    first_fragment_text = ""
    t_start = time.time()

    async with websockets.connect(WS_BASE + "/call/ws/" + session_id, max_size=None) as ws:
        await ws.send(json.dumps({"type": "audio_config", "sample_rate": 16000}))
        await _stream_utterance(ws, speech_pcm)

        while time.time() - t_start < 240:
            msg = await asyncio.wait_for(ws.recv(), timeout=240)
            if isinstance(msg, bytes):
                audio_chunks += 1
                continue
            data = json.loads(msg)
            mtype = data.get("type")
            if mtype == "speaking_fragment" and not first_fragment_text:
                first_fragment_text = data.get("text", "")
            if mtype == "latency_report":
                report = data
                break
            if mtype == "error":
                break

    await client.post(BASE + "/call/%s/end" % session_id)
    return {
        "label": label,
        "wall_ms": round((time.time() - t_start) * 1000),
        "audio_chunks": audio_chunks,
        "first_fragment": first_fragment_text[:60],
        "report": report,
    }


async def run_interrupt_test(client: httpx.AsyncClient, speech_pcm: bytes) -> dict:
    resp = await client.post(BASE + "/call/start", json={})
    session_id = resp.json()["session_id"]
    interrupted = False

    async with websockets.connect(WS_BASE + "/call/ws/" + session_id, max_size=None) as ws:
        await ws.send(json.dumps({"type": "audio_config", "sample_rate": 16000}))
        await _stream_utterance(ws, speech_pcm)

        deadline = time.time() + 60
        while time.time() < deadline:
            msg = await asyncio.wait_for(ws.recv(), timeout=60)
            if isinstance(msg, bytes):
                await ws.send(json.dumps({"type": "stop_speaking"}))
                continue
            data = json.loads(msg)
            if data.get("type") == "interrupted":
                interrupted = True
                break

    await client.post(BASE + "/call/%s/end" % session_id)
    return {"interrupted": interrupted}


async def main() -> int:
    speech = await make_speech_pcm("Hola Rafita, dime que tiempo hace hoy en Madrid.")
    print("PCM de prueba: %d bytes (%.1fs a 16kHz)" % (len(speech), len(speech) / 32000))

    async with httpx.AsyncClient(timeout=300) as client:
        first = await run_call(client, speech, "llamada 1")
        second = await run_call(client, speech, "llamada 2 (sin recargar)")
        interrupt = await run_interrupt_test(client, speech)

    for result in (first, second):
        print("\n== %s ==" % result["label"])
        print("  wall=%dms audio_chunks=%d" % (result["wall_ms"], result["audio_chunks"]))
        print("  primer fragmento: %s" % result["first_fragment"])
        report = result["report"] or {}
        print(
            "  STT=%sms LLM=%sms TTS=%sms primer_audio=%sms total=%sms fragments=%s"
            % (
                report.get("stt_ms"),
                report.get("llm_ms"),
                report.get("tts_ms"),
                report.get("first_audio_ms"),
                report.get("total_ms"),
                report.get("fragments"),
            )
        )
    print("\n== interrupcion por boton ==")
    print("  interrumpida: %s" % interrupt["interrupted"])
    ok = all(r["report"] for r in (first, second)) and interrupt["interrupted"]
    print("\nRESULTADO: %s" % ("OK" if ok else "REVISAR"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
