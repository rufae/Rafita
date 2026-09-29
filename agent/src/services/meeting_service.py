"""Reuniones tipo NotebookLM (Fase 4, 2026-09-29).

Recibe el audio grabado en el navegador (MediaRecorder), lo transcribe con
Whisper local, estima los interlocutores (diarizacion ligera por tono
fundamental), genera un resumen ejecutivo con puntos clave, decisiones y
tareas/compromisos, y guarda todo como nota en la boveda (vista Baul).
"""

import asyncio
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from src.config import settings
from src.database import db
from src.logger import logger

MAX_AUDIO_BYTES = 200 * 1024 * 1024
VAULT_FOLDER = "Reuniones"
SAMPLE_RATE = 16000


def _meetings_dir() -> Path:
    carpeta = Path(settings.data_dir) / "meetings"
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


def _ahora() -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


async def _convertir_a_wav(origen: Path, destino: Path) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-i",
            str(origen),
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "wav",
            str(destino),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return destino.exists() and destino.stat().st_size > 44
    except Exception as e:
        logger.warning("Reuniones: fallo convirtiendo audio: %s", e)
        return False


async def guardar_subida(nombre: str, datos: bytes) -> Path:
    if len(datos) > MAX_AUDIO_BYTES:
        raise ValueError("Audio demasiado grande (max %d MB)" % (MAX_AUDIO_BYTES // 1024 // 1024))
    extension = Path(nombre or "audio.webm").suffix.lower() or ".webm"
    ruta = _meetings_dir() / ("%s%s" % (uuid.uuid4().hex, extension))
    ruta.write_bytes(datos)
    return ruta


def transcribir(audio_wav: Path) -> tuple[list[dict[str, Any]], str]:
    """Segmenta y transcribe con Whisper local; devuelve (segmentos, idioma)."""
    from src.handlers.audio import _get_whisper_model

    modelo = _get_whisper_model()
    if modelo is None:
        return [], ""
    segmentos_iter, info = modelo.transcribe(
        str(audio_wav),
        language="es",
        vad_filter=True,
        beam_size=1,
        condition_on_previous_text=False,
        initial_prompt="Conversación en español con un asistente personal.",
    )
    segmentos = [
        {"start": float(s.start), "end": float(s.end), "text": (s.text or "").strip()}
        for s in segmentos_iter
        if (s.text or "").strip()
    ]
    return segmentos, getattr(info, "language", "es") or "es"


def _f0_mediana(muestras: np.ndarray, sr: int) -> float:
    """Tono fundamental aproximado por autocorrelacion (70-300 Hz)."""
    if muestras.size < sr * 0.05:
        return 0.0
    ventana = int(sr * 0.04)
    paso = max(1, ventana // 2)
    valores: list[float] = []
    for inicio in range(0, len(muestras) - ventana, paso):
        marco = muestras[inicio : inicio + ventana].astype(np.float64)
        marco -= marco.mean()
        if float(np.sqrt(np.mean(marco**2))) < 1e-4:
            continue
        corr = np.correlate(marco, marco, mode="full")[ventana - 1 :]
        if corr[0] <= 0:
            continue
        corr = corr / (corr[0] + 1e-9)
        min_lag, max_lag = int(sr / 300), int(sr / 70)
        if max_lag >= len(corr) or min_lag >= max_lag:
            continue
        pico = int(np.argmax(corr[min_lag:max_lag])) + min_lag
        if corr[pico] > 0.3:
            valores.append(sr / pico)
    return float(np.median(valores)) if valores else 0.0


def _cargar_audio_mono(audio_wav: Path) -> tuple[np.ndarray, int]:
    import soundfile as sf

    datos, sr = sf.read(str(audio_wav), dtype="float32", always_2d=True)
    mono = datos.mean(axis=1)
    return mono, int(sr)


def etiquetar_interlocutores(audio_wav: Path, segmentos: list[dict[str, Any]]) -> list[str]:
    """Diarizacion ligera: agrupa segmentos por tono (1 o 2 hablantes).

    No pretende ser pyannote: separa voces claramente distintas (p. ej. dos
    personas) y cae a un unico hablante cuando no hay señal suficiente.
    """
    if not segmentos:
        return []
    try:
        audio, sr = _cargar_audio_mono(audio_wav)
    except Exception as e:
        logger.warning("Reuniones: no se pudo leer el audio para diarizar: %s", e)
        return ["Hablante 1"] * len(segmentos)

    tonos: list[float] = []
    for seg in segmentos:
        i0 = max(0, int(seg["start"] * sr))
        i1 = min(len(audio), int(seg["end"] * sr))
        tonos.append(_f0_mediana(audio[i0:i1], sr))

    validos = [t for t in tonos if t > 0]
    if len(validos) < 2 or (max(validos) - min(validos)) < 25:
        return ["Hablante 1"] * len(segmentos)

    c1, c2 = min(validos), max(validos)
    for _ in range(25):
        grupo1 = [t for t in validos if abs(t - c1) <= abs(t - c2)]
        grupo2 = [t for t in validos if abs(t - c1) > abs(t - c2)]
        if not grupo1 or not grupo2:
            return ["Hablante 1"] * len(segmentos)
        n1, n2 = float(np.mean(grupo1)), float(np.mean(grupo2))
        if abs(n1 - c1) < 0.5 and abs(n2 - c2) < 0.5:
            c1, c2 = n1, n2
            break
        c1, c2 = n1, n2
    menor = min(len(grupo1), len(grupo2))
    # Con pocos segmentos (reuniones muy cortas) basta 1; en el resto, al menos
    # un 15% para no inventar un hablante a partir de un outlier.
    minimo = 1 if len(validos) < 4 else max(2, int(0.15 * len(validos)))
    if menor < minimo:
        return ["Hablante 1"] * len(segmentos)

    etiquetas = []
    for tono in tonos:
        if tono <= 0:
            etiquetas.append("Hablante 1")
        else:
            etiquetas.append("Hablante 1" if abs(tono - c1) <= abs(tono - c2) else "Hablante 2")
    return etiquetas


def _mmss(segundos: float) -> str:
    total = int(max(0, segundos))
    return "%02d:%02d" % (total // 60, total % 60)


def construir_transcripcion(segmentos: list[dict[str, Any]], etiquetas: list[str]) -> str:
    lineas = []
    for i, seg in enumerate(segmentos):
        hablante = etiquetas[i] if i < len(etiquetas) else "Hablante 1"
        lineas.append("[%s] %s: %s" % (_mmss(seg["start"]), hablante, seg["text"]))
    return "\n".join(lineas)


def _parsear_resumen(texto: str) -> dict[str, Any] | None:
    """Extrae el JSON del resumen del LLM (tolera texto alrededor)."""
    if not texto:
        return None
    match = re.search(r"\{.*\}", texto, re.DOTALL)
    if not match:
        return None
    try:
        datos = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(datos, dict):
        return None
    return {
        "resumen": str(datos.get("resumen", "")).strip(),
        "puntos_clave": [str(x) for x in datos.get("puntos_clave", []) if str(x).strip()],
        "decisiones": [str(x) for x in datos.get("decisiones", []) if str(x).strip()],
        "tareas": [str(x) for x in datos.get("tareas", []) if str(x).strip()],
    }


async def resumir(transcripcion: str) -> dict[str, Any]:
    """Resumen ejecutivo + puntos clave + decisiones + tareas con el LLM local."""
    from src.ollama_client import llm

    sistema = (
        "Eres un asistente que resume reuniones en espanol. Devuelve SOLO un JSON "
        'con esta forma: {"resumen": "...", "puntos_clave": ["..."], '
        '"decisiones": ["..."], "tareas": ["..."]}. Basate solo en la '
        "transcripcion; si algo no aparece, deja la lista vacia."
    )
    try:
        respuesta = await llm.chat(
            messages=[
                {"role": "system", "content": sistema},
                {
                    "role": "user",
                    "content": "Transcripcion de la reunion:\n\n" + transcripcion[:12000],
                },
            ],
            max_tokens=900,
        )
    except Exception as e:
        logger.warning("Reuniones: resumen con LLM fallo: %s", e)
        respuesta = ""
    datos = _parsear_resumen(respuesta)
    if datos:
        return datos
    return {
        "resumen": (respuesta or "").strip() or "Sin resumen disponible.",
        "puntos_clave": [],
        "decisiones": [],
        "tareas": [],
    }


async def _guardar_nota_boveda(
    titulo: str, duracion_s: float, transcripcion: str, resumen: dict[str, Any]
) -> str:
    from src.utils.obsidian_manager import overwrite_note

    ahora = _ahora()
    interlocutores = sorted(
        {ln.split(": ", 1)[0].split("] ", 1)[-1] for ln in transcripcion.splitlines()}
    )
    lineas = [
        "---",
        "title: %s" % titulo,
        "tipo: reunion",
        "fecha: %s" % ahora.strftime("%Y-%m-%d %H:%M"),
        "duracion_min: %.1f" % (duracion_s / 60.0),
        "interlocutores: [%s]" % ", ".join(interlocutores),
        "---",
        "",
        "# Resumen ejecutivo",
        resumen.get("resumen", ""),
        "",
        "## Puntos clave",
    ]
    for punto in resumen.get("puntos_clave", []):
        lineas.append("- %s" % punto)
    if not resumen.get("puntos_clave"):
        lineas.append("- (sin puntos destacados)")
    lineas.append("")
    lineas.append("## Decisiones")
    for decision in resumen.get("decisiones", []):
        lineas.append("- %s" % decision)
    if not resumen.get("decisiones"):
        lineas.append("- (sin decisiones registradas)")
    lineas.append("")
    lineas.append("## Tareas y compromisos")
    for tarea in resumen.get("tareas", []):
        lineas.append("- [ ] %s" % tarea)
    if not resumen.get("tareas"):
        lineas.append("- (sin tareas)")
    lineas.append("")
    lineas.append("## Transcripción")
    lineas.append(transcripcion or "(sin transcripcion)")
    contenido = "\n".join(lineas) + "\n"
    nombre = "%s %s" % (ahora.strftime("%Y-%m-%d %H%M"), titulo)
    resultado = await overwrite_note(nombre, contenido, folder=VAULT_FOLDER)
    ruta = str(resultado.get("filepath", ""))
    try:
        # Ruta relativa a la boveda (es la que usa la vista Baul).
        return str(Path(ruta).relative_to(settings.obsidian_vault_path))
    except ValueError:
        return ruta


async def _procesar(meeting_id: int, audio_path: Path) -> None:
    """Pipeline completo en segundo plano: transcribir, diarizar, resumir, nota."""
    try:
        wav = audio_path.with_suffix(".wav")
        if not await _convertir_a_wav(audio_path, wav):
            raise RuntimeError("no se pudo convertir el audio a wav")
        loop = asyncio.get_event_loop()
        segmentos, _idioma = await loop.run_in_executor(None, transcribir, wav)
        if not segmentos:
            await db.update_meeting(meeting_id, status="error", transcript="")
            logger.warning("Reuniones #%d: sin transcripcion", meeting_id)
            return
        etiquetas = await loop.run_in_executor(None, etiquetar_interlocutores, wav, segmentos)
        transcripcion = construir_transcripcion(segmentos, etiquetas)
        duracion = segmentos[-1]["end"] if segmentos else 0.0
        resumen = await resumir(transcripcion)
        meeting = await db.get_meeting(meeting_id)
        titulo = (meeting or {}).get("title") or "Reunion"
        nota = await _guardar_nota_boveda(titulo, duracion, transcripcion, resumen)
        await db.update_meeting(
            meeting_id,
            status="done",
            duration_s=duracion,
            speakers=", ".join(sorted(set(etiquetas))),
            transcript=transcripcion,
            summary=resumen.get("resumen", ""),
            tasks=json.dumps(resumen.get("tareas", []), ensure_ascii=False),
            note_path=nota,
            audio_path=str(audio_path),
        )
        logger.info("Reuniones #%d lista (%.1f min, %s)", meeting_id, duracion / 60.0, nota)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception("Reuniones #%d fallo: %s", meeting_id, e)
        await db.update_meeting(meeting_id, status="error")


async def crear_desde_subida(
    user_id: int, titulo: str, nombre_fichero: str, datos: bytes
) -> dict[str, Any]:
    titulo = (titulo or "").strip() or "Reunion %s" % _ahora().strftime("%Y-%m-%d %H:%M")
    audio_path = await guardar_subida(nombre_fichero, datos)
    meeting_id = await db.create_meeting(user_id, titulo)
    await db.update_meeting(meeting_id, audio_path=str(audio_path))
    asyncio.create_task(_procesar(meeting_id, audio_path))
    return {"id": meeting_id, "title": titulo, "status": "processing"}


async def listar(user_id: int) -> list[dict[str, Any]]:
    reuniones = await db.list_meetings(user_id=user_id)
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "status": r["status"],
            "duration_s": r["duration_s"],
            "speakers": r["speakers"],
            "note_path": r["note_path"],
            "created_at": r["created_at"],
        }
        for r in reuniones
    ]


async def detalle(meeting_id: int, user_id: int) -> dict[str, Any] | None:
    meeting = await db.get_meeting(meeting_id)
    if not meeting or int(meeting.get("user_id", 0)) != int(user_id):
        return None
    return {
        **meeting,
        "tasks_list": json.loads(meeting.get("tasks") or "[]"),
        "audio_path": meeting.get("audio_path", ""),
    }


async def borrar(meeting_id: int, user_id: int) -> bool:
    meeting = await db.get_meeting(meeting_id)
    if not meeting or int(meeting.get("user_id", 0)) != int(user_id):
        return False
    audio = meeting.get("audio_path", "")
    if audio:
        for sufijo in ("", ".wav"):
            try:
                Path(audio + sufijo).unlink(missing_ok=True)
            except OSError:
                pass
    await db.delete_meeting(meeting_id)
    return True
