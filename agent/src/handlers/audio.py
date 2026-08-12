import asyncio
import io
import tempfile
import time
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from src.config import settings
from src.logger import logger
from src.utils.tts_manager import convert_to_ogg, text_to_speech

_whisper_model = None


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel

            model_size = settings.whisper_model
            if model_size == "tiny":
                model_size = "base"
            logger.info("Loading Whisper model '%s' (int8)...", model_size)
            _whisper_model = WhisperModel(
                model_size,
                device="cpu",
                compute_type="int8",
                num_workers=1,
                cpu_threads=4,
            )
            logger.info("Whisper model '%s' loaded (int8, 4 threads)", model_size)
        except ImportError:
            logger.warning("faster-whisper not installed")
            return None
        except Exception as e:
            logger.warning("Whisper model load failed: %s", e)
            return None
    return _whisper_model


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not message.voice or not user:
        return

    voice = message.voice
    chat_id = user.id

    t_start = time.time()
    ts_a = time.strftime("%H:%M:%S")
    logger.info(
        "[AUDIO A] Nota de voz recibida [%s] user=%d duration=%ds file_id=%s",
        ts_a,
        chat_id,
        voice.duration,
        voice.file_id[:20],
    )

    await message.reply_text("🎙️ Transcribiendo audio... Por favor, espera.")
    await message.reply_chat_action("typing")

    tmp_ogg_path = None
    try:
        file = await context.bot.get_file(voice.file_id)
        tmp_dir = Path(tempfile.mkdtemp(prefix="rafita_voice_"))
        tmp_ogg_path = tmp_dir / "voice.ogg"

        await file.download_to_drive(str(tmp_ogg_path))

        file_size = tmp_ogg_path.stat().st_size
        t_dl = time.time() - t_start
        logger.info("[AUDIO B] Audio descargado [%.1fs] %d bytes", t_dl, file_size)

        if file_size < 100:
            await message.reply_text("❌ El archivo de audio está vacío o corrupto.")
            return

        transcribed = await _transcribe_file(tmp_ogg_path)

        t_stt = time.time() - t_start
        logger.info("[AUDIO C] STT completado [%.1fs] texto=%s", t_stt, (transcribed or "")[:100])

        if not transcribed or not transcribed.strip():
            await message.reply_text("❌ No pude entender el audio. Habla más claro o envía texto.")
            return

        await message.reply_text("✅ Escuché: %s" % transcribed)

        from src.handlers.chat import _process_ai_message

        response_text = await _process_ai_message(update, transcribed, context, from_voice=True)

        t_llm = time.time() - t_start
        logger.info(
            "[AUDIO D] LLM completado [%.1fs] response=%s", t_llm, (response_text or "")[:80]
        )

        text_request_keywords = [
            "escribemelo",
            "escríbemelo",
            "responde en texto",
            "respondeme en texto",
            "en texto",
            "no me hables",
            "ponlo por escrito",
            "escribelo",
            "escríbelo",
            "texto plano",
            "solo texto",
        ]
        user_wants_text = any(kw in transcribed.lower() for kw in text_request_keywords)

        if not user_wants_text and response_text:
            await _send_voice_reply_fast(update, context, response_text)
        elif user_wants_text:
            logger.info("[AUDIO] Usuario pidió respuesta en texto, omitiendo TTS")

        t_total = time.time() - t_start
        logger.info("[AUDIO E] Pipeline completo [%.1fs]", t_total)

    except Exception as e:
        logger.exception("[AUDIO ERROR] Pipeline de voz falló para user %d: %s", chat_id, e)
        try:
            await message.reply_text("❌ Error en el pipeline de voz: %s" % str(e)[:300])
        except Exception:
            pass
    finally:
        if tmp_ogg_path and tmp_ogg_path.exists():
            try:
                tmp_ogg_path.unlink()
            except Exception:
                pass
            try:
                tmp_ogg_path.parent.rmdir()
            except Exception:
                pass


async def _transcribe_file(audio_path: Path) -> str | None:
    model = _get_whisper_model()
    if model is None:
        return None

    try:
        loop = asyncio.get_event_loop()

        def _do_transcribe():
            segments, info = model.transcribe(
                str(audio_path),
                beam_size=1,
                language="es",
                vad_filter=True,
                vad_parameters={
                    "min_silence_duration_ms": 300,
                    "threshold": 0.5,
                },
            )
            parts = []
            for seg in segments:
                parts.append(seg.text)
            return " ".join(parts) if parts else None

        result = await loop.run_in_executor(None, _do_transcribe)
        return result
    except Exception as e:
        logger.exception("[AUDIO ERROR] Transcripción falló: %s", e)
        return None


async def _send_voice_reply_fast(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    message = update.effective_message
    if not message:
        return

    try:
        chunks = _split_text_for_streaming(text)
        audio_parts = []

        for i, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
            wav_path = await text_to_speech(chunk)
            if wav_path is None:
                continue
            ogg_path = await convert_to_ogg(wav_path)
            if ogg_path and ogg_path.exists():
                try:
                    audio_data = ogg_path.read_bytes()
                    audio_parts.append(audio_data)
                    logger.info(
                        "[AUDIO STREAM] Chunk %d/%d generado (%d bytes)",
                        i + 1,
                        len(chunks),
                        len(audio_data),
                    )
                except Exception as e:
                    logger.warning("Failed to read audio chunk: %s", e)

        if not audio_parts:
            await message.reply_text("No se pudo generar el audio de respuesta.")
            return

        combined = io.BytesIO()
        for part in audio_parts:
            combined.write(part)
        combined.seek(0)

        await message.reply_voice(voice=combined, read_timeout=60, write_timeout=60)
        logger.info(
            "[AUDIO STREAM] Respuesta de voz enviada (%d bytes total)", combined.getbuffer().nbytes
        )

    except Exception as e:
        logger.exception("[AUDIO ERROR] TTS/envío de voz falló: %s", e)
        try:
            await message.reply_text("❌ Error al generar respuesta de voz: %s" % str(e)[:200])
        except Exception:
            pass


def _split_text_for_streaming(text: str, max_chars: int = 200) -> list:
    if len(text) <= max_chars:
        return [text]
    sentences = text.replace("!", ".").replace("?", ".").split(".")
    chunks = []
    current = ""
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if len(current) + len(sent) + 1 <= max_chars:
            current = (current + ". " + sent) if current else sent
        else:
            if current:
                chunks.append(current)
            current = sent
    if current:
        chunks.append(current)
    return chunks
