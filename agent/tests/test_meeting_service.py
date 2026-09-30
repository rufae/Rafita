"""Tests del servicio de reuniones (Fase 4)."""

import json
from pathlib import Path

import numpy as np
import soundfile as sf

import src.services.meeting_service as ms
from src.config import settings


class _FakeDB:
    def __init__(self):
        self.rows: dict[int, dict] = {}
        self._id = 0

    async def create_meeting(self, user_id, title):
        self._id += 1
        self.rows[self._id] = {
            "id": self._id,
            "user_id": user_id,
            "title": title,
            "status": "processing",
            "duration_s": 0,
            "speakers": "",
            "transcript": "",
            "summary": "",
            "tasks": "",
            "note_path": "",
            "audio_path": "",
            "created_at": "2026-09-29 12:00",
        }
        return self._id

    async def update_meeting(self, meeting_id, **campos):
        self.rows[meeting_id].update(campos)

    async def get_meeting(self, meeting_id):
        return self.rows.get(int(meeting_id))

    async def list_meetings(self, user_id=None, limit=50):
        return [r for r in self.rows.values() if user_id is None or r["user_id"] == user_id]

    async def delete_meeting(self, meeting_id):
        self.rows.pop(int(meeting_id), None)


# ---------- parsers y utilidades ----------


def test_parsear_resumen_tolera_texto_alrededor():
    texto = 'Aqui va el JSON:\n{"resumen": "ok", "puntos_clave": ["a", ""], "decisiones": [], "tareas": ["hacer x"]}\nfin'
    datos = ms._parsear_resumen(texto)
    assert datos is not None
    assert datos["resumen"] == "ok"
    assert datos["puntos_clave"] == ["a"]
    assert datos["tareas"] == ["hacer x"]
    assert ms._parsear_resumen("sin json") is None
    assert ms._parsear_resumen("{roto}") is None


def test_construir_transcripcion():
    segmentos = [
        {"start": 0.0, "end": 2.0, "text": "Hola"},
        {"start": 65.0, "end": 70.0, "text": "Adios"},
    ]
    texto = ms.construir_transcripcion(segmentos, ["Hablante 1", "Hablante 2"])
    assert "[00:00] Hablante 1: Hola" in texto
    assert "[01:05] Hablante 2: Adios" in texto


# ---------- diarizacion ----------


def _wav_con_dos_tonos(tmp_path: Path) -> tuple[Path, list[dict]]:
    sr = 16000

    def tono(freq, dur, amp=0.4):
        t = np.linspace(0, dur, int(sr * dur), endpoint=False)
        return (amp * np.sin(2 * np.pi * freq * t)).astype("float32")

    silencio = np.zeros(int(sr * 0.4), dtype="float32")
    audio = np.concatenate([tono(120, 2.0), silencio, tono(230, 2.0)])
    ruta = tmp_path / "dos_tonos.wav"
    sf.write(str(ruta), audio, sr)
    segmentos = [
        {"start": 0.0, "end": 2.0, "text": "voz grave"},
        {"start": 2.4, "end": 4.4, "text": "voz aguda"},
    ]
    return ruta, segmentos


def test_etiquetar_interlocutores_separa_tonos(tmp_path):
    ruta, segmentos = _wav_con_dos_tonos(tmp_path)
    etiquetas = ms.etiquetar_interlocutores(ruta, segmentos)
    assert len(etiquetas) == 2
    assert etiquetas[0] != etiquetas[1]
    assert set(etiquetas) == {"Hablante 1", "Hablante 2"}


def test_etiquetar_un_solo_tono(tmp_path):
    sr = 16000
    t = np.linspace(0, 4.0, int(sr * 4.0), endpoint=False)
    audio = (0.4 * np.sin(2 * np.pi * 150 * t)).astype("float32")
    ruta = tmp_path / "un_tono.wav"
    sf.write(str(ruta), audio, sr)
    segmentos = [
        {"start": 0.0, "end": 2.0, "text": "uno"},
        {"start": 2.0, "end": 4.0, "text": "dos"},
    ]
    assert ms.etiquetar_interlocutores(ruta, segmentos) == ["Hablante 1", "Hablante 1"]


def test_etiquetar_sin_audio(tmp_path):
    ruta = tmp_path / "no_existe.wav"
    segmentos = [{"start": 0.0, "end": 1.0, "text": "x"}]
    assert ms.etiquetar_interlocutores(ruta, segmentos) == ["Hablante 1"]


# ---------- resumen y pipeline ----------


async def test_resumir_con_llm(monkeypatch):
    class _LLM:
        async def chat(self, messages, max_tokens=None, **kwargs):
            return '{"resumen": "fue bien", "puntos_clave": ["p1"], "decisiones": ["d1"], "tareas": ["t1"]}'

    monkeypatch.setattr("src.ollama_client.llm", _LLM())
    datos = await ms.resumir("[00:00] Hablante 1: hola")
    assert datos["resumen"] == "fue bien"
    assert datos["tareas"] == ["t1"]


async def test_resumir_llm_falla(monkeypatch):
    class _LLM:
        async def chat(self, messages, max_tokens=None, **kwargs):
            raise RuntimeError("modelo caido")

    monkeypatch.setattr("src.ollama_client.llm", _LLM())
    datos = await ms.resumir("texto")
    assert "Sin resumen" in datos["resumen"]
    assert datos["tareas"] == []


async def test_crear_y_procesar_reunion(monkeypatch, tmp_path):
    fake_db = _FakeDB()
    monkeypatch.setattr(ms, "db", fake_db)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))

    async def fake_convertir(origen, destino):
        destino.write_bytes(b"RIFF" + b"\x00" * 100)
        return True

    monkeypatch.setattr(ms, "_convertir_a_wav", fake_convertir)

    async def fake_transcribir(wav):
        return [{"start": 0.0, "end": 3.0, "text": "hola a todos"}], "es"

    monkeypatch.setattr(ms, "transcribir", fake_transcribir)
    monkeypatch.setattr(
        ms, "etiquetar_interlocutores", lambda wav, segs: ["Hablante 1"] * len(segs)
    )

    async def fake_resumir(transcripcion):
        return {
            "resumen": "todo ok",
            "puntos_clave": [],
            "decisiones": [],
            "tareas": ["enviar acta"],
        }

    monkeypatch.setattr(ms, "resumir", fake_resumir)

    async def fake_nota(titulo, duracion, transcripcion, resumen):
        return "/vault/Reuniones/%s.md" % titulo

    monkeypatch.setattr(ms, "_guardar_nota_boveda", fake_nota)

    creada = await ms.crear_desde_subida(7, "Reunion de prueba", "audio.webm", b"datos")
    assert creada["status"] == "processing"
    # el procesado va en segundo plano: esperamos a que termine
    import asyncio

    for _ in range(50):
        await asyncio.sleep(0.02)
        if fake_db.rows[creada["id"]]["status"] != "processing":
            break
    fila = fake_db.rows[creada["id"]]
    assert fila["status"] == "done"
    assert "hola a todos" in fila["transcript"]
    assert fila["summary"] == "todo ok"
    assert json.loads(fila["tasks"]) == ["enviar acta"]
    assert fila["note_path"].endswith(".md")
    assert fila["duration_s"] == 3.0

    listado = await ms.listar(7)
    assert listado and listado[0]["title"] == "Reunion de prueba"
    assert await ms.detalle(creada["id"], 7) is not None
    assert await ms.detalle(creada["id"], 8) is None
    assert await ms.borrar(creada["id"], 7) is True
    assert await ms.borrar(creada["id"], 7) is False


async def test_procesar_sin_transcripcion_marca_error(monkeypatch, tmp_path):
    fake_db = _FakeDB()
    monkeypatch.setattr(ms, "db", fake_db)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))

    async def fake_convertir(origen, destino):
        destino.write_bytes(b"RIFF")
        return True

    monkeypatch.setattr(ms, "_convertir_a_wav", fake_convertir)
    monkeypatch.setattr(ms, "transcribir", lambda wav: ([], "es"))
    meeting_id = await fake_db.create_meeting(1, "vacia")
    audio = tmp_path / "a.webm"
    audio.write_bytes(b"x")
    await ms._procesar(meeting_id, audio)
    assert fake_db.rows[meeting_id]["status"] == "error"


async def test_editar_reunion_actualiza_nota_y_conserva_secciones(monkeypatch, tmp_path):
    fake_db = _FakeDB()
    monkeypatch.setattr(ms, "db", fake_db)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(settings, "obsidian_vault_dir", str(tmp_path / "vault"))
    carpeta = tmp_path / "vault" / "Reuniones"
    carpeta.mkdir(parents=True)
    nota = carpeta / "2026-09-29 1200 Reunion.md"
    nota.write_text(
        "---\ntitle: Reunion\ntipo: reunion\n---\n\n# Resumen ejecutivo\nok\n\n"
        "## Puntos clave\n- punto importante\n\n## Decisiones\n- decision\n\n"
        "## Tareas y compromisos\n- [ ] tarea\n\n## Transcripción\n[00:00] Hablante 1: hola\n",
        encoding="utf-8",
    )
    mid = await fake_db.create_meeting(1, "Reunion")
    await fake_db.update_meeting(
        mid,
        note_path="Reuniones/2026-09-29 1200 Reunion.md",
        transcript="[00:00] Hablante 1: hola",
    )
    actualizada = await ms.editar(
        mid, 1, titulo="Reunión con Ana", transcripcion="[00:00] Hablante 1: buenas tardes"
    )
    assert actualizada["title"] == "Reunión con Ana"
    assert actualizada["transcript"] == "[00:00] Hablante 1: buenas tardes"
    contenido = nota.read_text(encoding="utf-8")
    assert "title: Reunión con Ana" in contenido
    assert "buenas tardes" in contenido and "hola" not in contenido
    # el resto del acta se conserva
    assert "## Puntos clave" in contenido and "- punto importante" in contenido
    assert "## Decisiones" in contenido and "- decision" in contenido


async def test_borrar_reunion_borra_su_acta(monkeypatch, tmp_path):
    fake_db = _FakeDB()
    monkeypatch.setattr(ms, "db", fake_db)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(settings, "obsidian_vault_dir", str(tmp_path / "vault"))
    carpeta = tmp_path / "vault" / "Reuniones"
    carpeta.mkdir(parents=True)
    nota = carpeta / "acta.md"
    nota.write_text("# Acta\n", encoding="utf-8")
    mid = await fake_db.create_meeting(1, "Reunion")
    await fake_db.update_meeting(mid, note_path="Reuniones/acta.md")
    assert await ms.borrar(mid, 1) is True
    assert not nota.exists()
    # sin permiso no borra ni edita
    mid2 = await fake_db.create_meeting(1, "Otra")
    assert await ms.editar(mid2, 99, titulo="x") is None
    assert await ms.borrar(mid2, 99) is False
