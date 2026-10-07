"""Procesamiento automático de documentos y enriquecimiento (tarea 10).

Un PDF/DOCX/TXT/CSV que cae en la bóveda genera su nota compañera con el
texto extraído, y las notas nuevas sin `tags` o sin `## Resumen` se
enriquecen con el LLM (etiquetas + resumen). Todo best-effort e idempotente.
"""

from types import SimpleNamespace

from src.utils import vault_indexer as vi


def _vault(tmp_path, monkeypatch):
    monkeypatch.setattr(vi, "VAULT_PATH", tmp_path)
    return tmp_path


def _evento(ruta):
    return SimpleNamespace(is_directory=False, src_path=str(ruta))


# ---------------- intake de documentos ----------------


async def test_intake_txt_crea_nota_companera(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    doc = tmp_path / "presupuesto.txt"
    doc.write_text("El presupuesto asciende a 1000 euros.", encoding="utf-8")
    res = await vi.VaultIndexer().intake_document(doc)
    assert res["success"] is True
    nota = tmp_path / "presupuesto.md"
    assert nota.exists()
    texto = nota.read_text(encoding="utf-8")
    assert "Archivo original" in texto
    assert "presupuesto.txt" in texto
    assert "Contenido extraido" in texto
    assert "1000 euros" in texto


async def test_intake_es_idempotente(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    doc = tmp_path / "informe.txt"
    doc.write_text("texto v1", encoding="utf-8")
    await vi.VaultIndexer().intake_document(doc)
    nota = tmp_path / "informe.md"
    nota.write_text(nota.read_text(encoding="utf-8") + "\nEDICION MANUAL", encoding="utf-8")
    res = await vi.VaultIndexer().intake_document(doc)
    assert res.get("skipped")
    assert "EDICION MANUAL" in nota.read_text(encoding="utf-8")


async def test_intake_pdf_usa_extractor(tmp_path, monkeypatch):
    import src.handlers.files as files_mod

    _vault(tmp_path, monkeypatch)

    def fake_extract(path):
        return "texto extraido del pdf"

    monkeypatch.setattr(files_mod, "_extract_text_from_file", fake_extract)
    doc = tmp_path / "escaneado.pdf"
    doc.write_bytes(b"%PDF-1.4 binario")
    res = await vi.VaultIndexer().intake_document(doc)
    assert res["success"] is True
    texto = (tmp_path / "escaneado.md").read_text(encoding="utf-8")
    assert "texto extraido del pdf" in texto


async def test_intake_rechaza_archivo_grande(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    monkeypatch.setattr(vi, "INTAKE_MAX_BYTES", 5)
    doc = tmp_path / "grande.txt"
    doc.write_text("demasiado largo para el limite")
    res = await vi.VaultIndexer().intake_document(doc)
    assert res["success"] is False
    assert "grande" in res["message"]
    assert not (tmp_path / "grande.md").exists()


# ---------------- enriquecimiento de notas ----------------


class _LLMFalso:
    def __init__(self, respuesta):
        self.respuesta = respuesta
        self.llamadas = 0

    async def chat(self, **kwargs):
        self.llamadas += 1
        if isinstance(self.respuesta, Exception):
            raise self.respuesta
        return self.respuesta


async def test_enrich_completa_tags_y_resumen(tmp_path, monkeypatch):
    nota = tmp_path / "nueva.md"
    nota.write_text(
        "---\ntitle: Nueva\ntags: []\n---\n# Nueva\n\nContenido de la nota sin catalogar.\n",
        encoding="utf-8",
    )
    falso = _LLMFalso('{"tags": ["finanzas", "personal"], "summary": "Nota de prueba."}')
    monkeypatch.setattr("src.ollama_client.llm", falso)
    ok = await vi.VaultIndexer()._enrich_note(nota)
    assert ok is True
    texto = nota.read_text(encoding="utf-8")
    assert "finanzas" in texto
    assert "## Resumen" in texto
    assert "Nota de prueba." in texto
    assert falso.llamadas == 1


async def test_enrich_omite_si_ya_esta_completa(tmp_path, monkeypatch):
    nota = tmp_path / "hecha.md"
    nota.write_text(
        "---\ntags: [x]\nentities: [Algo]\n---\n# Hecha\n\n## Resumen\nya tiene\n\ncuerpo\n",
        encoding="utf-8",
    )
    falso = _LLMFalso(AssertionError("no debe llamar al LLM"))
    monkeypatch.setattr("src.ollama_client.llm", falso)
    ok = await vi.VaultIndexer()._enrich_note(nota)
    assert ok is False
    assert falso.llamadas == 0


async def test_enrich_anade_entidades_si_solo_faltan_ellas(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    nota = tmp_path / "catalogada.md"
    nota.write_text(
        "---\ntags: [x]\n---\n# Catalogada\n\n## Resumen\nya tiene\n\ncuerpo\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "src.ollama_client.llm",
        _LLMFalso('{"tags": ["x"], "summary": "", "entities": ["Proyecto Prueba"]}'),
    )
    ok = await vi.VaultIndexer()._enrich_note(nota)
    assert ok is True
    texto = nota.read_text(encoding="utf-8")
    assert "Proyecto Prueba" in texto


async def test_enrich_entidades_se_enlazan_solo_si_existe_la_nota(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    (tmp_path / "Ana Perez.md").write_text("# Ana\n", encoding="utf-8")
    nota = tmp_path / "reunion.md"
    nota.write_text(
        "---\ntags: []\n---\n# Reunion\n\nNotas con Ana Perez y Martillo.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "src.ollama_client.llm",
        _LLMFalso(
            '{"tags": ["reuniones"], "summary": "S.", "entities": ["Ana Perez", "Martillo"]}'
        ),
    )
    ok = await vi.VaultIndexer()._enrich_note(nota)
    assert ok is True
    texto = nota.read_text(encoding="utf-8")
    assert "[[Ana Perez]]" in texto
    assert "- Martillo" in texto
    assert "[[Martillo]]" not in texto


async def test_enrich_llm_caido_no_rompe(tmp_path, monkeypatch):
    original = "---\ntags: []\n---\n# T\n\ncontenido\n"
    nota = tmp_path / "t.md"
    nota.write_text(original, encoding="utf-8")
    monkeypatch.setattr("src.ollama_client.llm", _LLMFalso(RuntimeError("ollama caido")))
    ok = await vi.VaultIndexer()._enrich_note(nota)
    assert ok is False
    assert nota.read_text(encoding="utf-8") == original


async def test_enrich_sin_respuesta_util_no_escribe(tmp_path, monkeypatch):
    original = "---\ntags: []\n---\n# T\n\ncontenido\n"
    nota = tmp_path / "t2.md"
    nota.write_text(original, encoding="utf-8")
    monkeypatch.setattr("src.ollama_client.llm", _LLMFalso("no soy json"))
    ok = await vi.VaultIndexer()._enrich_note(nota)
    assert ok is False
    assert nota.read_text(encoding="utf-8") == original


async def test_enrich_conserva_tags_existentes_si_solo_da_resumen(tmp_path, monkeypatch):
    nota = tmp_path / "con_tags.md"
    nota.write_text(
        "---\ntags: [existente]\n---\n# T\n\nsin resumen aun\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "src.ollama_client.llm", _LLMFalso('{"tags": [], "summary": "Resumen nuevo."}')
    )
    ok = await vi.VaultIndexer()._enrich_note(nota)
    assert ok is True
    texto = nota.read_text(encoding="utf-8")
    assert "existente" in texto
    assert "Resumen nuevo." in texto


# ---------------- eventos del watcher ----------------


def test_eventos_aceptan_md_y_documentos(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    idx = vi.VaultIndexer()
    idx._on_file_event(_evento(tmp_path / "nota.md"))
    idx._on_file_event(_evento(tmp_path / "doc.pdf"))
    idx._on_file_event(_evento(tmp_path / "hoja.csv"))
    idx._on_file_event(_evento(tmp_path / "imagen.png"))
    idx._on_file_event(_evento(tmp_path / ".obsidian /oculta.pdf".replace(" ", "")))
    idx._on_file_event(_evento(tmp_path / ".obsidian/oculta.pdf"))
    with idx._pending_lock:
        claves = set(idx._pending_paths)
    assert claves == {"nota.md", "doc.pdf", "hoja.csv"}


def test_eventos_ignoran_directorios(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    idx = vi.VaultIndexer()
    idx._on_file_event(SimpleNamespace(is_directory=True, src_path=str(tmp_path)))
    with idx._pending_lock:
        assert idx._pending_paths == {}
