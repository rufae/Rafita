"""Cobertura de utils/workspace_manager.py (listing, lectura y system health)."""

import pytest

from src.utils import workspace_manager as wm


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setattr(wm, "WORKSPACE_ROOT", root)
    return root


class TestFormatSize:
    def test_bytes(self):
        assert wm._format_size(512) == "512 B"

    def test_kilobytes(self):
        assert wm._format_size(2048) == "2.0 KB"

    def test_megabytes(self):
        assert wm._format_size(5 * 1024 * 1024) == "5.0 MB"

    def test_gigabytes(self):
        assert wm._format_size(3 * 1024 * 1024 * 1024) == "3.0 GB"


class TestIsTextFile:
    def test_known_extension(self, tmp_path):
        assert wm._is_text_file(tmp_path / "a.py") is True

    def test_binary_extension(self, tmp_path):
        assert wm._is_text_file(tmp_path / "a.png") is False

    def test_no_extension_is_text(self, tmp_path):
        assert wm._is_text_file(tmp_path / "Makefile") is True


class TestSafePath:
    def test_empty_returns_root(self, ws):
        assert wm._safe_path("") == ws.resolve()
        assert wm._safe_path("  ") == ws.resolve()

    def test_relative_inside(self, ws):
        (ws / "sub").mkdir()
        assert wm._safe_path("sub") == (ws / "sub").resolve()
        assert wm._safe_path("/sub/") == (ws / "sub").resolve()

    def test_traversal_rejected(self, ws):
        with pytest.raises(ValueError):
            wm._safe_path("../outside")

    def test_missing_path(self, ws):
        with pytest.raises(FileNotFoundError):
            wm._safe_path("no_existe")


class TestListWorkspaceFiles:
    async def test_lists_files_and_dirs(self, ws):
        (ws / "docs").mkdir()
        (ws / "docs" / "a.md").write_text("x", encoding="utf-8")
        (ws / "main.py").write_text("print(1)", encoding="utf-8")
        (ws / ".env").write_text("K=1", encoding="utf-8")
        (ws / ".hidden").write_text("x", encoding="utf-8")
        (ws / ".git").mkdir()
        (ws / "node_modules").mkdir()

        result = await wm.list_workspace_files()
        assert result["success"] is True
        names = [i["name"] for i in result["items"]]
        assert "docs" in names
        assert "main.py" in names
        assert ".env" in names
        assert ".hidden" not in names
        assert ".git" not in names
        assert "node_modules" not in names
        assert result["relative"] == "."
        assert "directorios" in result["summary"]

    async def test_subdirectory_listing(self, ws):
        (ws / "sub").mkdir()
        (ws / "sub" / "b.txt").write_text("hola", encoding="utf-8")
        result = await wm.list_workspace_files("sub")
        assert result["success"] is True
        assert result["relative"] == "sub"
        assert result["items"][0]["name"] == "b.txt"
        assert result["items"][0]["ext"] == ".txt"

    async def test_traversal_error_reported(self, ws):
        result = await wm.list_workspace_files("../x")
        assert result["success"] is False
        assert "traversal" in result["message"].lower()

    async def test_missing_dir_error_reported(self, ws):
        result = await wm.list_workspace_files("fantasma")
        assert result["success"] is False
        assert "not found" in result["message"]

    async def test_dir_children_count(self, ws):
        sub = ws / "con_hijos"
        sub.mkdir()
        (sub / "a.md").write_text("x", encoding="utf-8")
        (sub / "b.md").write_text("y", encoding="utf-8")
        result = await wm.list_workspace_files()
        item = next(i for i in result["items"] if i["name"] == "con_hijos")
        assert item["type"] == "dir"
        assert item["children"] == 2


class TestReadWorkspaceFile:
    async def test_reads_small_text_file(self, ws):
        (ws / "nota.md").write_text("\n".join(["linea"] * 10), encoding="utf-8")
        result = await wm.read_workspace_file("nota.md")
        assert result["success"] is True
        assert result["lines"] == 10
        assert "..." not in result["preview"]

    async def test_long_file_preview_truncated(self, ws):
        (ws / "largo.txt").write_text("\n".join(["l"] * 40), encoding="utf-8")
        result = await wm.read_workspace_file("largo.txt")
        assert result["success"] is True
        assert result["preview"].endswith("...")

    async def test_directory_rejected(self, ws):
        (ws / "carpeta").mkdir()
        result = await wm.read_workspace_file("carpeta")
        assert result["success"] is False
        assert "no es un archivo" in result["message"]

    async def test_binary_extension_rejected(self, ws):
        (ws / "foto.png").write_bytes(b"\x89PNG")
        result = await wm.read_workspace_file("foto.png")
        assert result["success"] is False
        assert "binaria" in result["message"]

    async def test_too_large_rejected(self, ws):
        (ws / "grande.txt").write_text("a" * (wm.MAX_READ_SIZE + 1), encoding="utf-8")
        result = await wm.read_workspace_file("grande.txt")
        assert result["success"] is False
        assert "demasiado grande" in result["message"]

    async def test_invalid_utf8_rejected(self, ws):
        (ws / "roto.txt").write_bytes(b"\xff\xfe\x00binario")
        result = await wm.read_workspace_file("roto.txt")
        assert result["success"] is False
        assert "UTF-8" in result["message"]

    async def test_missing_file(self, ws):
        result = await wm.read_workspace_file("nadie.md")
        assert result["success"] is False

    async def test_traversal_rejected(self, ws):
        result = await wm.read_workspace_file("../secreto")
        assert result["success"] is False


class _FakeDbOk:
    async def get_all_chat_ids(self):
        return [1, 2]


class _FakeDbBoom:
    async def get_all_chat_ids(self):
        raise RuntimeError("db caida")


class TestSystemHealth:
    async def test_healthy_with_db_and_logs(self, ws, tmp_path, monkeypatch):
        db_file = tmp_path / "rafita.db"
        db_file.write_bytes(b"sqlite" * 10)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "app.log").write_text(
            "INFO ok\nERROR fallo raro\nTraceback (most recent call last):", encoding="utf-8"
        )
        monkeypatch.setattr(wm.settings, "db_path", str(db_file))
        monkeypatch.setattr(wm.settings, "log_dir", str(log_dir))

        import src.database as db_mod

        monkeypatch.setattr(db_mod, "db", _FakeDbOk())

        report = await wm.get_system_health()
        assert report["health"] == "healthy"
        assert report["database"]["size_bytes"] == db_file.stat().st_size
        assert "free" in report["disk"]
        # El logger crea rafita.log/error.log en log_path (perezoso), asi que
        # puede haber mas ficheros ademas de app.log.
        assert report["logs"]["files_checked"] >= 1
        assert report["logs"]["error_count"] >= 1
        assert report["chats"]["active_chats"] == 2

    async def test_degraded_without_db(self, ws, tmp_path, monkeypatch):
        monkeypatch.setattr(wm.settings, "db_path", str(tmp_path / "no_existe.db"))
        monkeypatch.setattr(wm.settings, "log_dir", str(tmp_path / "no_logs"))
        report = await wm.get_system_health()
        assert report["health"] == "degraded"
        assert report["database"]["status"] == "not_found"
        assert report["logs"]["status"] == "no_logs_found"

    async def test_db_chat_error_reported(self, ws, tmp_path, monkeypatch):
        monkeypatch.setattr(wm.settings, "db_path", str(tmp_path / "no_existe.db"))
        monkeypatch.setattr(wm.settings, "log_dir", str(tmp_path / "no_logs"))

        import src.database as db_mod

        monkeypatch.setattr(db_mod, "db", _FakeDbBoom())
        report = await wm.get_system_health()
        assert "error" in report["chats"]
