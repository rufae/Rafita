"""Cobertura de handlers/files.py (ingesta de archivos, notas companeras, vision)."""

import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import settings
from src.handlers import files
from src.vault_config import get_taxonomy


class FakeMessage:
    def __init__(self, **attrs):
        self.replies = []
        self.audio = []
        self.caption = attrs.pop("caption", None)
        self.document = attrs.pop("document", None)
        self.photo = attrs.pop("photo", None)
        for key, value in attrs.items():
            setattr(self, key, value)

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)

    async def reply_chat_action(self, *args, **kwargs):
        return None

    async def reply_audio(self, audio, **kwargs):
        self.audio.append(audio)


def make_update(message, user_id=12345):
    return SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=user_id),
    )


class FakeDB:
    def __init__(self, prefs=None, history=None):
        self.prefs = prefs or {}
        self.history = history or []
        self.saved = []
        self.updated = []

    async def get_or_create_preferences(self, chat_id):
        return self.prefs

    async def save_chat_message(self, chat_id, role, content):
        self.saved.append((chat_id, role, content))
        return 1

    async def update_last_chat_message(self, chat_id, role, content):
        self.updated.append((chat_id, role, content))

    async def get_chat_history(self, chat_id, limit=50):
        return self.history


class FakeLLM:
    vision_model = "llava:7b"
    model = "gemma4:12b"

    def __init__(self):
        self.vision_calls = []
        self.tools_calls = []
        self.chat_calls = []
        self.unloaded = []
        self.vision_result = "Ticket de compra por 10 euros"
        self.vision_error = None
        self.vision_script = None
        self.chat_result = ""
        self.chat_error = None
        self.tools_result = ("Listo", None)
        self.tools_error = None

    async def chat(self, **kwargs):
        self.chat_calls.append(kwargs)
        if self.chat_error is not None:
            raise self.chat_error
        return self.chat_result

    async def chat_vision(self, **kwargs):
        self.vision_calls.append(kwargs)
        if self.vision_script is not None:
            action = self.vision_script.pop(0)
            if isinstance(action, Exception):
                raise action
            return action
        if self.vision_error is not None:
            raise self.vision_error
        return self.vision_result

    async def chat_with_tools(self, **kwargs):
        self.tools_calls.append(kwargs)
        if self.tools_error is not None:
            raise self.tools_error
        return self.tools_result

    async def unload_model(self, model):
        self.unloaded.append(model)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Desvia /data/obsidian_vault y el tmp de subidas hacia tmp_path."""
    root = tmp_path / "obsidian_vault"
    root.mkdir()
    real_path = pathlib.Path

    def remapped(*args, **kwargs):
        if args and str(args[0]) == "/data/obsidian_vault":
            return root
        return real_path(*args, **kwargs)

    monkeypatch.setattr(files, "Path", remapped)
    monkeypatch.setattr(files, "VAULT_ROOT", root)
    monkeypatch.setattr(files, "TEMP_DIR", tmp_path / "uploads")
    monkeypatch.setattr(files, "CREDENTIALS_DIR", tmp_path / "credentials")
    return root


def install_db(monkeypatch, **kwargs):
    fake_db = FakeDB(**kwargs)
    monkeypatch.setattr(files, "db", fake_db)
    return fake_db


# ---------------------------------------------------------------------------
# helpers puros
# ---------------------------------------------------------------------------


def test_safe_filename_sanitizes_and_truncates():
    assert files._safe_filename('mi/archivo*con?"raros') == "mi_archivo_con__raros"
    assert files._safe_filename("  con  espacios  ") == "con_espacios"
    assert len(files._safe_filename("x" * 300)) == 100


def test_safe_vault_subpath_strips_traversal(vault):
    result = files._safe_vault_subpath(vault, "../03-Recursos/nota.md")
    assert result == vault.resolve() / "03-Recursos" / "nota.md"

    result = files._safe_vault_subpath(vault, "a/./b/../c")
    assert result == vault.resolve() / "a" / "b" / "c"


def test_safe_vault_subpath_blocks_symlink_escape(vault, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (vault / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="Path traversal blocked"):
        files._safe_vault_subpath(vault, "escape/secret.txt")


def test_extract_text_from_plain_formats(tmp_path):
    txt = tmp_path / "nota.txt"
    txt.write_text("contenido en texto", encoding="utf-8")
    assert files._extract_text_from_file(txt) == "contenido en texto"

    md = tmp_path / "nota.md"
    md.write_text("# Titulo", encoding="utf-8")
    assert files._extract_text_from_file(md) == "# Titulo"

    csv = tmp_path / "datos.csv"
    csv.write_text("a,b\n1,2", encoding="utf-8")
    assert files._extract_text_from_file(csv) == "a,b\n1,2"


def test_extract_text_from_unknown_extension_is_empty(tmp_path):
    blob = tmp_path / "binario.bin"
    blob.write_bytes(b"\x00\x01")
    assert files._extract_text_from_file(blob) == ""


def test_extract_text_from_pdf_uses_pypdf(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    class FakePage:
        def extract_text(self):
            return "Texto del PDF"

    class FakePdfReader:
        def __init__(self, path):
            self.pages = [FakePage(), FakePage()]

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=FakePdfReader))
    assert files._extract_text_from_file(pdf) == "Texto del PDF\n\nTexto del PDF"


def test_extract_text_from_docx(tmp_path):
    docx_mod = pytest.importorskip("docx")
    doc = docx_mod.Document()
    doc.add_paragraph("Parrafo importante")
    doc.add_paragraph("   ")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Concepto"
    table.rows[0].cells[1].text = "Monto"
    path = tmp_path / "doc.docx"
    doc.save(str(path))

    text = files._extract_text_from_file(path)
    assert "Parrafo importante" in text
    assert "Concepto | Monto" in text


def test_extract_text_reports_failures(tmp_path, monkeypatch):
    pdf = tmp_path / "roto.pdf"
    pdf.write_bytes(b"%PDF")

    class BoomReader:
        def __init__(self, path):
            raise RuntimeError("pdf ilegible")

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=BoomReader))
    assert files._extract_text_from_file(pdf) == ""


def test_generate_frontmatter_contains_metadata(tmp_path):
    path = tmp_path / "mi_archivo.pdf"
    fm = files._generate_frontmatter(path, "recurso", ["etiqueta1", "etiqueta2"], "un resumen")
    assert fm.startswith("---\n")
    assert 'title: "mi archivo"' in fm
    assert "type: recurso" in fm
    assert "tags: [etiqueta1, etiqueta2]" in fm
    assert "source_file: mi_archivo.pdf" in fm
    assert fm.endswith("---\n")


def test_generate_frontmatter_without_tags():
    fm = files._generate_frontmatter(pathlib.Path("x.pdf"), "area", [], "")
    assert "tags: []" in fm


def test_create_companion_note(vault):
    saved = vault / "03-Recursos" / "informe.txt"
    saved.parent.mkdir(parents=True)
    saved.write_text("hola", encoding="utf-8")

    note = files._create_companion_note(
        vault, saved, "texto extraido largo", "recurso", ["tag"], "resumen breve"
    )

    assert note is not None and note.exists()
    content = note.read_text(encoding="utf-8")
    assert "## Archivo original" in content
    assert "![[informe.txt]]" in content
    assert "## Resumen" in content
    assert "resumen breve" in content
    assert "## Contenido extraido" in content
    assert "texto extraido largo" in content


def test_create_companion_note_truncates_long_text(vault):
    saved = vault / "doc.txt"
    saved.write_text("x", encoding="utf-8")

    note = files._create_companion_note(vault, saved, "y" * 12000, "recurso", [], "")

    content = note.read_text(encoding="utf-8")
    assert "texto truncado" in content
    assert "2000 caracteres mas" in content


def test_create_companion_note_edge_cases(vault, tmp_path):
    missing = vault / "no-existe.txt"
    assert files._create_companion_note(vault, missing, "t", "recurso", [], "") is None

    outside = tmp_path / "fuera.txt"
    outside.write_text("x", encoding="utf-8")
    assert files._create_companion_note(vault, outside, "t", "recurso", [], "") is None


def test_guess_folder_from_extension():
    taxo = get_taxonomy()
    assert files._guess_folder_from_extension(".pdf") == taxo.path("resources")
    assert files._guess_folder_from_extension(".xlsx") == taxo.path("areas_finanzas")
    assert files._guess_folder_from_extension(".desconocida") == taxo.path("inbox")


def test_format_size():
    assert files._format_size(512) == "512 B"
    assert files._format_size(2048) == "2.0 KB"
    assert files._format_size(3 * 1024 * 1024) == "3.0 MB"
    assert files._format_size(2 * 1024 * 1024 * 1024) == "2.0 GB"


def test_extract_semantic_name_patterns():
    name = files._extract_semantic_name("escudo del Real Madrid")
    assert name.startswith("Real_Madrid_")
    assert len(name) > len("Real_Madrid_")

    caps = files._extract_semantic_name("captura de LOGO FIFA")
    assert caps.startswith("LOGO_FIFA_")

    empty = files._extract_semantic_name("")
    assert empty.startswith("imagen_")


def test_extract_semantic_name_ignores_stop_words():
    name = files._extract_semantic_name("guardame la foto de mi gato persa")
    assert "gato" in name
    assert "persa" in name
    assert "guarda" not in name.lower()
    assert name.startswith("gato_")


def test_extract_semantic_name_fallback_to_long_words():
    name = files._extract_semantic_name("para para para")
    assert name.startswith("para_para_para")

    short = files._extract_semantic_name("de la el")
    assert short.startswith("imagen_")


def test_extract_semantic_name_prefers_extracted_text():
    name = files._extract_semantic_name("caption irrelevante", "escudo del Barcelona")
    assert name.startswith("Barcelona_")


# ---------------------------------------------------------------------------
# _classify_file_with_ai
# ---------------------------------------------------------------------------


async def test_classify_file_parses_json(monkeypatch):
    fake_llm = FakeLLM()
    fake_llm.chat_result = (
        'Aqui tienes: {"name": "2026-09-28_informe", "folder": "03-Recursos", '
        '"type": "recurso", "tags": ["a"], "summary": "resumen", "reason": "ok"}'
    )
    monkeypatch.setattr(files, "llm", fake_llm)

    result = await files._classify_file_with_ai(
        "informe.pdf", "un comentario", ".pdf", 100, "texto del pdf"
    )

    assert result["name"] == "2026-09-28_informe"
    assert result["folder"] == "03-Recursos"
    # El prompt incluye comentario y muestra de contenido
    prompt = fake_llm.chat_calls[0]["messages"][1]["content"]
    assert "un comentario" in prompt
    assert "texto del pdf" in prompt


async def test_classify_file_failure_returns_empty(monkeypatch):
    fake_llm = FakeLLM()
    fake_llm.chat_result = "no es json"
    monkeypatch.setattr(files, "llm", fake_llm)
    assert await files._classify_file_with_ai("a.pdf", "", ".pdf", 1) == {}

    fake_llm.chat_error = RuntimeError("llm caido")
    assert await files._classify_file_with_ai("a.pdf", "", ".pdf", 1) == {}


# ---------------------------------------------------------------------------
# _handle_credentials_file
# ---------------------------------------------------------------------------


async def test_handle_credentials_ignores_other_names(vault, monkeypatch):
    update = make_update(FakeMessage())
    result = await files._handle_credentials_file(
        update, pathlib.Path("/tmp/x.json"), "otra_cosa.json", 12345
    )
    assert result is False


async def test_handle_credentials_rejects_non_admin(vault, monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", [999])
    update = make_update(FakeMessage())
    result = await files._handle_credentials_file(
        update, pathlib.Path("/tmp/x.json"), "credentials.json", 12345
    )
    assert result is False
    assert not files.CREDENTIALS_DIR.exists()


async def test_handle_credentials_stores_admin_upload(vault, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "admin_ids", [12345])
    message = FakeMessage()
    update = make_update(message)
    source = tmp_path / "credentials.json"
    source.write_text("{}", encoding="utf-8")

    result = await files._handle_credentials_file(update, source, "credentials.json", 12345)

    assert result is True
    assert (files.CREDENTIALS_DIR / "credentials.json").exists()
    assert any("Credencial guardada" in r for r in message.replies)


# ---------------------------------------------------------------------------
# document_handler / photo_handler
# ---------------------------------------------------------------------------


class FakeFile:
    def __init__(self, payload=b"contenido del archivo"):
        self.payload = payload

    async def download_to_drive(self, path):
        pathlib.Path(path).write_bytes(self.payload)


class FakeDoc:
    def __init__(self, file_name="doc.txt", file_size=1024, payload=b"hola"):
        self.file_name = file_name
        self.file_size = file_size
        self._file = FakeFile(payload)

    async def get_file(self):
        return self._file


async def test_document_handler_too_large(vault, monkeypatch):
    message = FakeMessage(document=FakeDoc(file_size=files.MAX_FILE_SIZE + 1))
    monkeypatch.setattr(files, "_process_uploaded_file", AsyncMock())

    await files.document_handler(make_update(message), MagicMock())

    assert any("demasiado grande" in r for r in message.replies)
    files._process_uploaded_file.assert_not_called()


async def test_document_handler_without_document_is_noop(vault, monkeypatch):
    message = FakeMessage(document=None)
    await files.document_handler(make_update(message), MagicMock())
    assert message.replies == []


async def test_document_handler_without_message_is_noop(vault, monkeypatch):
    update = MagicMock()
    update.effective_message = None
    update.effective_user = SimpleNamespace(id=1)
    await files.document_handler(update, MagicMock())


async def test_photo_handler_without_message_is_noop(vault, monkeypatch):
    update = MagicMock()
    update.effective_message = None
    update.effective_user = SimpleNamespace(id=1)
    await files.photo_handler(update, MagicMock())


async def test_document_handler_stores_credentials_and_skips_ingest(vault, monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", [12345])
    processed = AsyncMock()
    monkeypatch.setattr(files, "_process_uploaded_file", processed)
    message = FakeMessage(document=FakeDoc(file_name="credentials.json", payload=b"{}"))

    await files.document_handler(make_update(message), MagicMock())

    processed.assert_not_awaited()
    assert any("Credencial guardada" in r for r in message.replies)
    # El fichero temporal se limpia
    assert list(files.TEMP_DIR.glob("*.json")) == []


async def test_document_handler_ingests_file(vault, monkeypatch):
    processed = AsyncMock()
    monkeypatch.setattr(files, "_process_uploaded_file", processed)
    message = FakeMessage(document=FakeDoc(file_name="informe.txt"), caption="un comentario")

    await files.document_handler(make_update(message), MagicMock())

    processed.assert_awaited_once()
    args = processed.await_args.args
    assert args[2] == "informe.txt"
    assert args[3] == ".txt"
    assert args[4] == "un comentario"
    assert any("Descargando y clasificando" in r for r in message.replies)


async def test_document_handler_reports_errors(vault, monkeypatch):
    doc = FakeDoc()
    doc.get_file = AsyncMock(side_effect=RuntimeError("descarga fallida"))
    message = FakeMessage(document=doc)

    await files.document_handler(make_update(message), MagicMock())

    assert any("Error al procesar el archivo" in r for r in message.replies)


async def test_photo_handler_without_photos_is_noop(vault, monkeypatch):
    message = FakeMessage(photo=[])
    await files.photo_handler(make_update(message), MagicMock())
    assert message.replies == []


async def test_photo_handler_too_large(vault, monkeypatch):
    photo = SimpleNamespace(file_size=files.MAX_FILE_SIZE + 1, get_file=AsyncMock())
    message = FakeMessage(photo=[photo])
    await files.photo_handler(make_update(message), MagicMock())
    assert any("demasiado grande" in r for r in message.replies)


async def test_photo_handler_with_caption_uses_vision(vault, monkeypatch):
    photo = SimpleNamespace(file_size=100, get_file=AsyncMock(return_value=FakeFile()))
    message = FakeMessage(photo=[photo], caption="mira este ticket")
    vision = AsyncMock()
    monkeypatch.setattr(files, "_process_vision_image", vision)

    await files.photo_handler(make_update(message), MagicMock())

    vision.assert_awaited_once()
    assert any("Analizando imagen" in r for r in message.replies)


async def test_photo_handler_without_caption_classifies(vault, monkeypatch):
    photo = SimpleNamespace(file_size=100, get_file=AsyncMock(return_value=FakeFile()))
    message = FakeMessage(photo=[photo], caption="   ")
    processed = AsyncMock()
    monkeypatch.setattr(files, "_process_uploaded_file", processed)

    await files.photo_handler(make_update(message), MagicMock())

    processed.assert_awaited_once()
    assert any("Descargando y clasificando" in r for r in message.replies)


async def test_photo_handler_reports_errors(vault, monkeypatch):
    photo = SimpleNamespace(file_size=100, get_file=AsyncMock(side_effect=RuntimeError("roto")))
    message = FakeMessage(photo=[photo])

    await files.photo_handler(make_update(message), MagicMock())

    assert any("Error al procesar la imagen" in r for r in message.replies)


# ---------------------------------------------------------------------------
# _process_uploaded_file
# ---------------------------------------------------------------------------


async def test_process_uploaded_file_full_flow(vault, monkeypatch):
    install_db(monkeypatch, prefs={"voice_replies": False})
    monkeypatch.setattr(
        files,
        "_classify_file_with_ai",
        AsyncMock(
            return_value={
                "name": "2026-09-28_informe",
                "folder": "03-Recursos",
                "type": "recurso",
                "tags": ["trabajo"],
                "summary": "resumen del informe",
                "reason": "documento de trabajo",
            }
        ),
    )
    source = vault.parent / "origen.txt"
    source.write_text("contenido del informe", encoding="utf-8")
    message = FakeMessage()

    await files._process_uploaded_file(
        make_update(message), source, "informe.txt", ".txt", "comentario", 10, 12345
    )

    dest = vault / "03-Recursos" / "2026-09-28_informe.txt"
    assert dest.exists()
    note = vault / "03-Recursos" / "2026-09-28_informe.md"
    assert note.exists()
    note_text = note.read_text(encoding="utf-8")
    assert "resumen del informe" in note_text
    assert "contenido del informe" in note_text
    text = message.replies[0]
    assert "Archivo clasificado y guardado" in text
    assert "2026-09-28_informe.txt" in text
    assert "trabajo" in text
    assert "documento de trabajo" in text
    assert "Nota indexada" in text
    assert message.audio == []


async def test_process_uploaded_file_image_and_voice_reply(vault, monkeypatch):
    install_db(monkeypatch, prefs={"voice_replies": True})
    monkeypatch.setattr(files, "_classify_file_with_ai", AsyncMock(return_value={}))
    wav = vault.parent / "r.wav"
    wav.write_bytes(b"RIFF")
    ogg = vault.parent / "r.ogg"
    ogg.write_bytes(b"OggS")

    async def fake_tts(text):
        return wav

    async def fake_convert(path):
        return ogg

    monkeypatch.setattr(files, "text_to_speech", fake_tts)
    monkeypatch.setattr(files, "convert_to_ogg", fake_convert)
    source = vault.parent / "foto.jpg"
    source.write_bytes(b"jpeg-bytes")
    message = FakeMessage()

    await files._process_uploaded_file(
        make_update(message), source, "foto.jpg", ".jpg", None, 8, 12345
    )

    dest_dir = vault / get_taxonomy().path("attachments")
    saved = list(dest_dir.glob("*.jpg"))
    assert len(saved) == 1
    assert saved[0].name.startswith("2026-")
    assert len(message.audio) == 1
    assert not any("Nota indexada" in r for r in message.replies)


async def test_process_uploaded_file_handles_name_collision(vault, monkeypatch):
    install_db(monkeypatch)
    monkeypatch.setattr(
        files, "_classify_file_with_ai", AsyncMock(return_value={"name": "repetido"})
    )
    dest_dir = vault / get_taxonomy().path("inbox")
    dest_dir.mkdir(parents=True)
    (dest_dir / "repetido.txt").write_text("previo", encoding="utf-8")
    source = vault.parent / "nuevo.txt"
    source.write_text("nuevo", encoding="utf-8")
    message = FakeMessage()

    await files._process_uploaded_file(
        make_update(message), source, "nuevo.txt", ".txt", None, 5, 12345
    )

    assert (dest_dir / "repetido_1.txt").read_text(encoding="utf-8") == "nuevo"


async def test_process_uploaded_file_tolerates_voice_reply_errors(vault, monkeypatch):
    install_db(monkeypatch, prefs={"voice_replies": True})
    monkeypatch.setattr(files, "_classify_file_with_ai", AsyncMock(return_value={}))
    wav = vault.parent / "r.wav"
    wav.write_bytes(b"RIFF")
    ogg = vault.parent / "r.ogg"
    ogg.write_bytes(b"OggS")

    async def fake_tts(text):
        return wav

    async def fake_convert(path):
        return ogg

    monkeypatch.setattr(files, "text_to_speech", fake_tts)
    monkeypatch.setattr(files, "convert_to_ogg", fake_convert)
    message = FakeMessage()
    message.reply_audio = MagicMock(side_effect=RuntimeError("telegram caido"))
    source = vault.parent / "nota.txt"
    source.write_text("hola", encoding="utf-8")

    await files._process_uploaded_file(
        make_update(message), source, "nota.txt", ".txt", None, 4, 12345
    )

    assert any("Archivo clasificado" in r for r in message.replies)


async def test_process_uploaded_file_warns_unextractable(vault, monkeypatch):
    install_db(monkeypatch)
    monkeypatch.setattr(files, "_classify_file_with_ai", AsyncMock(return_value={}))
    source = vault.parent / "datos.bin"
    source.write_bytes(b"\x00\x01")
    message = FakeMessage()

    await files._process_uploaded_file(
        make_update(message), source, "datos.bin", ".bin", None, 2, 12345
    )

    assert any("No se pudo extraer texto" in r for r in message.replies)


# ---------------------------------------------------------------------------
# _process_vision_image
# ---------------------------------------------------------------------------


def install_vision_deps(monkeypatch, attach=None, note=None):
    fake_llm = FakeLLM()
    monkeypatch.setattr(files, "llm", fake_llm)
    monkeypatch.setattr(
        "src.utils.obsidian_manager.save_attachment",
        MagicMock(return_value=attach or {"success": True, "filename": "img.png"}),
    )
    monkeypatch.setattr(
        "src.utils.obsidian_manager.create_note_with_image",
        MagicMock(return_value=note or {"success": True, "filepath": "/vault/nota.md"}),
    )
    monkeypatch.setattr("src.handlers.chat_tools.get_tools_for_llm", MagicMock(return_value=[]))
    return fake_llm


async def test_process_vision_image_with_tool_calls(vault, monkeypatch, tmp_path):
    fake_llm = install_vision_deps(monkeypatch)
    fake_llm.tools_result = (
        "He guardado el gasto",
        [{"function": {"name": "save_expense", "arguments": '{"amount": 5}'}}],
    )
    executed = []

    async def fake_execute_tool(chat_id, name, args):
        executed.append((chat_id, name, args))
        return {"message": "Gasto guardado"}

    monkeypatch.setattr("src.handlers.chat._execute_tool", fake_execute_tool)
    fake_db = install_db(
        monkeypatch,
        history=[
            {"role": "user", "content": "hola " + "x" * 600},
            {"role": "assistant", "content": "que tal"},
        ],
    )
    image = tmp_path / "img.png"
    image.write_bytes(b"png")
    message = FakeMessage()
    context = MagicMock()
    context.user_data = {}

    await files._process_vision_image(make_update(message), 12345, str(image), "mira esto", context)

    assert executed == [(12345, "save_expense", {"amount": 5})]
    assert fake_llm.unloaded == ["llava:7b"]
    assert any("Analizando imagen" in r for r in message.replies)
    assert any("Procesando con Qwen" in r for r in message.replies)
    assert any("Gasto guardado" in r for r in message.replies)
    assert fake_db.updated
    assert context.user_data["processing_image"] is False
    assert not image.exists()
    # El historial largo se trunca antes de enviarse al modelo
    history_msgs = fake_llm.tools_calls[0]["messages"]
    assert any(m["content"].endswith("...") for m in history_msgs)


async def test_process_vision_image_empty_extraction(vault, monkeypatch, tmp_path):
    fake_llm = install_vision_deps(monkeypatch)
    fake_llm.vision_result = "   "
    install_db(monkeypatch)
    message = FakeMessage()
    context = MagicMock()
    context.user_data = {}

    await files._process_vision_image(
        make_update(message), 12345, str(tmp_path / "img.png"), "hola", context
    )

    assert any("No pude extraer información" in r for r in message.replies)
    assert context.user_data["processing_image"] is False
    assert fake_llm.tools_calls == []


async def test_process_vision_image_timeout_then_retry(vault, monkeypatch, tmp_path):
    fake_llm = install_vision_deps(monkeypatch)
    fake_llm.vision_script = [TimeoutError(), "contenido tras reintento"]
    fake_db = install_db(monkeypatch)
    message = FakeMessage()

    await files._process_vision_image(
        make_update(message), 12345, str(tmp_path / "img.png"), "hola"
    )

    assert any("tardando más de la cuenta" in r for r in message.replies)
    assert any("contenido tras reintento" in u[2] for u in fake_db.updated)
    assert len(fake_llm.vision_calls) == 2


async def test_process_vision_image_double_timeout(vault, monkeypatch, tmp_path):
    fake_llm = install_vision_deps(monkeypatch)
    fake_llm.vision_script = [TimeoutError(), TimeoutError()]
    install_db(monkeypatch)
    message = FakeMessage()
    context = MagicMock()
    context.user_data = {}

    await files._process_vision_image(
        make_update(message), 12345, str(tmp_path / "img.png"), "hola", context
    )

    assert any("superó el tiempo límite" in r for r in message.replies)
    assert context.user_data["processing_image"] is False


async def test_process_vision_image_vision_error(vault, monkeypatch, tmp_path):
    fake_llm = install_vision_deps(monkeypatch)
    fake_llm.vision_error = RuntimeError("gpu fuera de memoria")
    install_db(monkeypatch)
    message = FakeMessage()
    context = MagicMock()
    context.user_data = {}

    await files._process_vision_image(
        make_update(message), 12345, str(tmp_path / "img.png"), "hola", context
    )

    assert any("Error al analizar la imagen" in r for r in message.replies)
    assert context.user_data["processing_image"] is False


async def test_process_vision_image_tolerates_unload_failures(vault, monkeypatch, tmp_path):
    fake_llm = install_vision_deps(monkeypatch)
    fake_llm.tools_result = ("todo bien", None)

    async def boom_unload(model):
        raise RuntimeError("no se puede descargar")

    fake_llm.unload_model = boom_unload
    install_db(monkeypatch)
    message = FakeMessage()
    src = tmp_path / "img.png"
    src.write_bytes(b"png")

    await files._process_vision_image(make_update(message), 12345, str(src), "hola")

    assert any("todo bien" in r for r in message.replies)


async def test_process_vision_image_attachment_failure(vault, monkeypatch, tmp_path):
    fake_llm = install_vision_deps(
        monkeypatch,
        attach={"success": False, "message": "sin permisos en Obsidian"},
        note={"success": True, "filepath": "/vault/n.md"},
    )
    fake_llm.tools_result = ("todo bien", None)
    install_db(monkeypatch)
    message = FakeMessage()

    await files._process_vision_image(
        make_update(message), 12345, str(tmp_path / "img.png"), "hola"
    )

    assert any("sin permisos en Obsidian" in r for r in message.replies)
    assert not any("Guardado en Obsidian" in r for r in message.replies)


async def test_process_vision_image_tolerates_unlink_failures(vault, monkeypatch, tmp_path):
    fake_llm = install_vision_deps(monkeypatch)
    fake_llm.tools_result = ("todo bien", None)
    install_db(monkeypatch)
    message = FakeMessage()
    # Un directorio hace fallar el unlink posterior sin abortar el flujo
    src_dir = tmp_path / "carpeta.png"
    src_dir.mkdir()

    await files._process_vision_image(make_update(message), 12345, str(src_dir), "hola")

    assert any("todo bien" in r for r in message.replies)


async def test_process_vision_image_note_failure(vault, monkeypatch, tmp_path):
    install_vision_deps(
        monkeypatch,
        note={"success": False, "message": "vault lleno"},
    )
    install_db(monkeypatch)
    message = FakeMessage()

    await files._process_vision_image(
        make_update(message), 12345, str(tmp_path / "img.png"), "hola"
    )

    assert any("vault lleno" in r for r in message.replies)


async def test_process_vision_image_tools_timeout(vault, monkeypatch, tmp_path):
    fake_llm = install_vision_deps(monkeypatch)
    fake_llm.tools_error = TimeoutError()
    install_db(monkeypatch)
    message = FakeMessage()

    await files._process_vision_image(
        make_update(message), 12345, str(tmp_path / "img.png"), "hola"
    )

    assert any("La imagen fue guardada en Obsidian" in r for r in message.replies)


async def test_process_vision_image_tools_error(vault, monkeypatch, tmp_path):
    fake_llm = install_vision_deps(monkeypatch)
    fake_llm.tools_error = RuntimeError("backend caido")
    install_db(monkeypatch)
    message = FakeMessage()
    context = MagicMock()
    context.user_data = {}

    await files._process_vision_image(
        make_update(message), 12345, str(tmp_path / "img.png"), "hola", context
    )

    assert any("Error al procesar la imagen con herramientas" in r for r in message.replies)
    assert context.user_data["processing_image"] is False


async def test_process_vision_image_invalid_tool_arguments(vault, monkeypatch, tmp_path):
    fake_llm = install_vision_deps(monkeypatch)
    fake_llm.tools_result = (
        "",
        [{"function": {"name": "save_expense", "arguments": "no-es-json"}}],
    )
    executed = []

    async def fake_execute_tool(chat_id, name, args):
        executed.append(args)
        return {"message": "ok"}

    monkeypatch.setattr("src.handlers.chat._execute_tool", fake_execute_tool)
    install_db(monkeypatch)
    message = FakeMessage()

    await files._process_vision_image(
        make_update(message), 12345, str(tmp_path / "img.png"), "hola"
    )

    assert executed == [{}]
    assert any("ok" in r for r in message.replies)
