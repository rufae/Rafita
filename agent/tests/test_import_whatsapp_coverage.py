"""Cobertura de tools/import_whatsapp.py (parseo, clasificacion e importacion)."""

from unittest.mock import MagicMock

import pytest

from src.tools import import_whatsapp as iw

CHAT_SAMPLE = """12/05/2026, 10:30 - Rafael: Hola que tal
esta es una linea
continuada del mensaje
12/05/2026, 10:31:45 - Rafael: Mira este video https://youtu.be/abc123

13/05/2026, 9:05 - Rafael: <Multimedia omitido>
13/05/2026, 9:06 - Rafael: Este repo https://github.com/foo/bar y https://example.org/x
"""


@pytest.fixture
def chat_file(tmp_path):
    path = tmp_path / "whatsapp_chat.txt"
    path.write_text(CHAT_SAMPLE, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# parse_whatsapp
# ---------------------------------------------------------------------------


def test_parse_whatsapp_groups_continuation_lines(chat_file):
    entries = iw.parse_whatsapp(chat_file)

    assert len(entries) == 4
    assert entries[0] == {
        "date": "12/05/2026 10:30",
        "text": "Hola que tal esta es una linea continuada del mensaje",
    }
    assert entries[1]["date"] == "12/05/2026 10:31:45"
    assert "youtu.be/abc123" in entries[1]["text"]


def test_parse_whatsapp_ignores_blank_lines(tmp_path):
    path = tmp_path / "chat.txt"
    path.write_text(
        "1/2/2026, 1:02 - A: primero\n\n\n1/2/2026, 1:03 - A: segundo\n", encoding="utf-8"
    )
    entries = iw.parse_whatsapp(path)
    assert [e["text"] for e in entries] == ["primero", "segundo"]


def test_parse_whatsapp_without_matches(tmp_path):
    path = tmp_path / "chat.txt"
    path.write_text("sin formato\notra linea\n", encoding="utf-8")
    assert iw.parse_whatsapp(path) == []


# ---------------------------------------------------------------------------
# _extract_urls / classify_entry / summarize_group_for_llm
# ---------------------------------------------------------------------------


def test_extract_urls():
    text = "mira https://youtu.be/abc (y) [nota] http://ejemplo.es/path?a=1"
    urls = iw._extract_urls(text)
    assert urls == ["https://youtu.be/abc", "http://ejemplo.es/path?a=1"]


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("https://www.youtube.com/watch?v=1", "YouTube_Videos"),
        ("https://youtu.be/xyz", "YouTube_Videos"),
        ("https://twitter.com/user/status/1", "Twitter_X_Enlaces"),
        ("https://x.com/user/status/1", "Twitter_X_Enlaces"),
        ("https://github.com/foo", "GitHub_Recursos"),
        ("https://www.amazon.es/dp/1", "Compras_Productos"),
        ("https://www.instagram.com/p/1", "Instagram_Enlaces"),
        ("https://maps.google.com/?q=x", "Ubicaciones"),
        ("https://chat.whatsapp.com/1", "Grupos_WhatsApp"),
        ("https://www.pampling.com/1", "Compras_Productos"),
        ("https://mi.000webhostapp.com/1", "Proyectos_Web"),
        ("https://ejemplo-desconocido.org/1", "Enlaces_Web"),
        ("sin enlaces ninguno", "Notas_Texto"),
    ],
)
def test_classify_entry_by_domain(text, category):
    assert iw.classify_entry({"text": text, "date": "1/2/2026"}) == category


def test_summarize_group_for_llm_limits_sample():
    entries = [{"date": "d%d" % i, "text": "t%d" % i} for i in range(40)]
    prompt = iw.summarize_group_for_llm("YouTube_Videos", entries, max_items=5)

    assert "Tema: YouTube_Videos" in prompt
    assert "(40 mensajes totales, mostrando 5)" in prompt
    assert "- [d0] t0" in prompt
    assert "- [d4] t4" in prompt
    assert "t5" not in prompt


# ---------------------------------------------------------------------------
# ai_summarize_group
# ---------------------------------------------------------------------------


class FakeLLM:
    def __init__(self, result="Resumen del grupo", error=None):
        self.result = result
        self.error = error
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


async def test_ai_summarize_group_empty_entries():
    assert await iw.ai_summarize_group("Grupo", [], FakeLLM()) == ""


async def test_ai_summarize_group_success():
    fake_llm = FakeLLM(result="  Punto importante  ")
    entries = [{"date": "1/2/2026 10:00", "text": "mensaje"}]

    section = await iw.ai_summarize_group("YouTube_Videos", entries, fake_llm)

    assert section == "## YouTube Videos\n\nPunto importante\n"
    prompt = fake_llm.calls[0]["messages"][1]["content"]
    assert "YouTube_Videos" in prompt
    assert "mensaje" in prompt


async def test_ai_summarize_group_falls_back_on_error():
    entries = [
        {
            "date": "1/2/2026 10:00",
            "text": "mira https://youtu.be/abc y mas cosas",
        },
        {"date": "2/2/2026 11:00", "text": "otro mensaje sin enlaces"},
    ]
    fake_llm = FakeLLM(error=RuntimeError("ollama caido"))

    section = await iw.ai_summarize_group("Grupo", entries, fake_llm)

    assert section.startswith("## Grupo\n")
    assert "- [1/2/2026] mira https://youtu.be/abc y mas cosas -> https://youtu.be/abc" in section
    assert "- [2/2/2026] otro mensaje sin enlaces" in section


async def test_ai_summarize_group_falls_back_on_timeout():
    entries = [{"date": "1/2/2026 10:00", "text": "mensaje"}]
    fake_llm = FakeLLM(error=TimeoutError())

    section = await iw.ai_summarize_group("Grupo", entries, fake_llm)

    assert section.startswith("## Grupo\n")
    assert "mensaje" in section


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


class FakeImportLLM:
    def __init__(self):
        self.model = "modelo-falso"
        self.initialized = False
        self.chat_calls = 0

    async def initialize(self):
        self.initialized = True

    async def chat(self, **kwargs):
        self.chat_calls += 1
        return "Resumen generado"


async def test_main_imports_chat_and_writes_notes(chat_file, monkeypatch):
    monkeypatch.setattr(iw, "CHAT_FILE", chat_file)
    fake_llm = FakeImportLLM()
    monkeypatch.setattr("src.ollama_client.OllamaClient", MagicMock(return_value=fake_llm))
    written = {}

    async def fake_create_or_append(title, content, folder=""):
        written.setdefault(title, []).append((content, folder))
        return {"success": True, "filepath": "/vault/%s.md" % title}

    monkeypatch.setattr(iw, "create_or_append_note", fake_create_or_append)

    await iw.main()

    assert fake_llm.initialized is True
    assert fake_llm.chat_calls > 0
    # Nota principal + indice de enlaces
    assert "WhatsApp_Chat_Importado" in written
    assert "WhatsApp_Enlaces_Index" in written
    main_content, main_folder = written["WhatsApp_Chat_Importado"][0]
    assert "Resumen generado" in main_content
    assert "Total mensajes:" in main_content
    assert main_folder == iw.get_taxonomy().path("archive")
    index_content, index_folder = written["WhatsApp_Enlaces_Index"][0]
    assert "Indice de Enlaces" in index_content
    assert "youtu.be/abc123" in index_content
    assert index_folder == iw.get_taxonomy().path("resources")


async def test_main_missing_file_exits(monkeypatch, tmp_path):
    monkeypatch.setattr(iw, "CHAT_FILE", tmp_path / "no-existe.txt")

    with pytest.raises(SystemExit):
        await iw.main()


async def test_main_filters_system_messages(tmp_path, monkeypatch):
    path = tmp_path / "chat.txt"
    path.write_text(
        "1/2/2026, 1:02 - A: <Multimedia omitido>\n"
        "1/2/2026, 1:03 - A: Los mensajes que envias se cifran\n"
        "1/2/2026, 1:04 - A: con cifrados de extremo a extremo\n"
        "1/2/2026, 1:05 - A: unico util\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(iw, "CHAT_FILE", path)
    fake_llm = FakeImportLLM()
    monkeypatch.setattr("src.ollama_client.OllamaClient", MagicMock(return_value=fake_llm))
    written = {}

    async def fake_create_or_append(title, content, folder=""):
        written[title] = content
        return {"success": False, "message": "no importa"}

    monkeypatch.setattr(iw, "create_or_append_note", fake_create_or_append)

    await iw.main()

    main_content = written["WhatsApp_Chat_Importado"]
    assert "Total mensajes: 1 utiles de 4 lineas" in main_content
    assert "<Multimedia omitido>" not in main_content
