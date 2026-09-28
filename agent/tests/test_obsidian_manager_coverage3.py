"""Cobertura de utils/obsidian_manager.py sobre un vault temporal en tmp_path."""

from pathlib import Path

import pytest

from src.utils import obsidian_manager as om


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "obsidian_vault"
    v.mkdir()
    monkeypatch.setattr(om, "OBSIDIAN_VAULT", v)
    return v


class TestHelpersInternos:
    def test_normalize_folder(self):
        assert om._normalize_folder(" /a/b/ ") == "a/b"
        assert om._normalize_folder("") == ""
        assert om._normalize_folder(None) == ""

    def test_safe_filename(self):
        assert om._safe_filename("a?b*c") == "a_b_c"
        assert om._safe_filename('x<>:"/\\|') == "x_______"
        assert om._safe_filename("  con   espacios  ") == "con espacios"
        assert len(om._safe_filename("x" * 500)) == 200

    def test_resolve_folder_rejects_absolute(self, vault):
        with pytest.raises(ValueError):
            om._resolve_folder("/etc")
        with pytest.raises(ValueError):
            om._resolve_folder("///")

    def test_resolve_folder_empty_is_vault_root(self, vault):
        assert om._resolve_folder("") == vault.resolve()
        assert om._resolve_folder("   ") == vault.resolve()

    def test_resolve_path_invalid_title(self, vault):
        with pytest.raises(ValueError):
            om._resolve_path("..", "")

    def test_resolve_path_adds_md_suffix(self, vault):
        p = om._resolve_path("Nota", "01-Proyectos")
        assert p.name == "Nota.md"
        assert p.parent.name == "01-Proyectos"


class TestCreateAppendOverwrite:
    async def test_create_then_append(self, vault):
        result = await om.create_or_append_note("Nota", "contenido uno", folder="00-Inbox")
        assert result["success"] is True
        assert result["action"] == "created"
        note = vault / "00-Inbox" / "Nota.md"
        assert note.exists()
        first = note.read_text(encoding="utf-8")
        assert "contenido uno" in first
        assert first.startswith("---")

        result2 = await om.create_or_append_note("Nota", "contenido dos", folder="00-Inbox")
        assert result2["action"] == "appended"
        second = note.read_text(encoding="utf-8")
        assert "contenido dos" in second
        assert "Actualizado el" in second

    async def test_overwrite_creates_and_replaces(self, vault):
        r1 = await om.overwrite_note("Vieja", "abc", folder="docs")
        assert r1["action"] == "created"
        r2 = await om.overwrite_note("Vieja", "xyz", folder="docs")
        assert r2["action"] == "overwritten"
        note = vault / "docs" / "Vieja.md"
        assert note.read_text(encoding="utf-8") == "xyz"

    async def test_overwrite_oserror_reported(self, vault, monkeypatch):
        def _boom(*_args, **_kwargs):
            raise OSError("disco lleno")

        monkeypatch.setattr("builtins.open", _boom)
        result = await om.overwrite_note("Rota", "x")
        assert result["success"] is False
        assert "disco lleno" in result["message"]


class TestReadNote:
    async def test_missing_note(self, vault):
        result = await om.read_note("Fantasma")
        assert result["success"] is False
        assert "Fantasma" in result["message"]

    async def test_read_truncates_preview(self, vault):
        content = "\n".join("linea %d" % i for i in range(60))
        await om.create_or_append_note("Larga", content)
        result = await om.read_note("Larga")
        assert result["success"] is True
        assert "líneas más" in result["content"]
        assert result["full_content"].count("linea ") == 60
        assert "linea 59" in result["full_content"]


class TestSearchNotes:
    async def test_missing_vault(self, tmp_path, monkeypatch):
        monkeypatch.setattr(om, "OBSIDIAN_VAULT", tmp_path / "no_existe")
        result = await om.search_notes_content("hola")
        assert result["success"] is True
        assert result["results"] == []

    async def test_matches_ranked_by_count(self, vault):
        await om.create_or_append_note("Poca", "una vez hola")
        await om.create_or_append_note("Mucha", "hola hola hola otra")
        (vault / "binaria.md").write_bytes(b"\xff\xfe hola roto")

        result = await om.search_notes_content("hola")
        assert result["success"] is True
        names = [r["title"] for r in result["results"]]
        assert names[0] == "Mucha"
        assert "Poca" in names
        assert "binaria" not in names
        snippet = result["results"][0]["snippets"][0]
        assert "hola" in snippet

    async def test_no_matches(self, vault):
        await om.create_or_append_note("Otra", "contenido cualquiera")
        result = await om.search_notes_content("inexistente")
        assert result["results"] == []
        assert "0 notas" in result["message"]


class TestAttachments:
    def test_missing_source(self, vault, tmp_path):
        result = om.save_attachment(tmp_path / "nada.png", "foto")
        assert result["success"] is False

    def test_save_and_collision_rename(self, vault, tmp_path):
        src = tmp_path / "origen.png"
        src.write_bytes(b"png-bytes")
        r1 = om.save_attachment(src, "mi foto?")
        assert r1["success"] is True
        assert r1["obsidian_link"] == "![[mi foto_.png]]"

        r2 = om.save_attachment(src, "mi foto?")
        assert r2["filename"] == "mi foto__1.png"

    def test_create_note_with_image(self, vault, tmp_path):
        result = om.create_note_with_image("Imagen Nota", "descripcion", "foto.png")
        assert result["success"] is True
        note = Path(result["filepath"])
        assert note.exists()
        text = note.read_text(encoding="utf-8")
        assert "![[foto.png]]" in text
        assert "descripcion" in text

    def test_create_note_with_image_explicit_folder(self, vault):
        result = om.create_note_with_image("Otra", "cuerpo", "a.png", folder="99-Fotos")
        assert result["success"] is True
        assert (vault / "99-Fotos" / "Otra.md").exists()


class TestVaultStructure:
    async def test_initialize_creates_taxonomy_folders(self, vault):
        from src.vault_config import get_taxonomy

        await om.initialize_vault_structure()
        for folder in get_taxonomy().structure:
            assert (vault / folder).exists()


class TestMoveOrRename:
    async def test_missing_source(self, vault):
        result = await om.move_or_rename_file(str(vault / "nada.md"), "01-Proyectos", "x")
        assert result["success"] is False

    async def test_invalid_new_name(self, vault):
        src = vault / "a.md"
        src.write_text("x", encoding="utf-8")
        result = await om.move_or_rename_file(str(src), "01-Proyectos", "..")
        assert result["success"] is False
        assert "invalido" in result["message"]
        assert src.exists()

    async def test_empty_new_name(self, vault):
        src = vault / "b.md"
        src.write_text("x", encoding="utf-8")
        result = await om.move_or_rename_file(str(src), "01-Proyectos", "   ")
        assert result["success"] is False

    async def test_move_keeps_parent_without_dest_folder(self, vault):
        sub = vault / "00-Inbox"
        sub.mkdir()
        src = sub / "c.md"
        src.write_text("x", encoding="utf-8")
        result = await om.move_or_rename_file(str(src), "", "renombrada")
        assert result["success"] is True
        assert (sub / "renombrada.md").exists()

    async def test_move_with_collision_counter(self, vault):
        dest = vault / "01-Proyectos"
        dest.mkdir()
        (dest / "dup.md").write_text("viejo", encoding="utf-8")
        src = vault / "dup.md"
        src.write_text("nuevo", encoding="utf-8")
        result = await om.move_or_rename_file(str(src), "01-Proyectos", "dup")
        assert result["success"] is True
        assert (dest / "dup_1.md").exists()


class TestDeleteNote:
    async def test_delete_missing(self, vault):
        result = await om.delete_note("Fantasma")
        assert result["success"] is False

    async def test_delete_existing(self, vault):
        await om.create_or_append_note("Borrable", "x")
        result = await om.delete_note("Borrable")
        assert result["success"] is True
        assert not (vault / "Borrable.md").exists()


class TestSyncCalendar:
    async def test_no_events(self, vault):
        result = await om.sync_calendar_to_obsidian([])
        assert result["success"] is True
        assert "No hay eventos" in result["message"]

    async def test_sync_formats_events_and_appends(self, vault):
        events = [
            {
                "start": "2026-10-01T10:30:00Z",
                "end": "2026-10-01T11:30:00Z",
                "title": "Reunion",
                "html_link": "https://cal.example/1",
            },
            {"start": "2026-10-02", "end": "raro", "title": "Todo el dia"},
        ]
        result = await om.sync_calendar_to_obsidian(events)
        assert result["success"] is True
        assert "2 eventos" in result["message"]
        note = Path(result["filepath"])
        text = note.read_text(encoding="utf-8")
        assert "10:30" in text
        assert "[link](https://cal.example/1)" in text
        assert "Todo el dia" in text

        result2 = await om.sync_calendar_to_obsidian(events)
        text2 = note.read_text(encoding="utf-8")
        assert "Sincronizado el" in text2
        assert result2["success"] is True
