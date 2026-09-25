"""Path traversal regression tests for vault operations (task 0.3).

Covers `resolve_within` and the tool-facing functions in `obsidian_manager`
plus the `_safe_vault_subpath` helper in `files.py`.
"""

from pathlib import Path

import pytest

from src.utils import obsidian_manager
from src.utils.path_safety import resolve_within


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "obsidian_vault"
    v.mkdir()
    monkeypatch.setattr(obsidian_manager, "OBSIDIAN_VAULT", v)
    return v


def _symlink_dir(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported in this environment")


TRAVERSAL_FOLDERS = [
    "../outside",
    "../../outside",
    "01-Proyectos/../../outside",
    "/tmp/outside",
    "..\\outside",
]


class TestResolveWithin:
    def test_inside_path_ok(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        assert resolve_within(root, root / "a" / "b.md") == (root / "a" / "b.md").resolve()

    def test_sibling_prefix_rejected(self, tmp_path):
        """The old `str(path).startswith(str(root))` check accepted this."""
        root = tmp_path / "vault"
        root.mkdir()
        sibling = tmp_path / "vault_fake"
        sibling.mkdir()
        evil = sibling / "nota.md"
        evil.write_text("x", encoding="utf-8")

        assert str(evil.resolve()).startswith(str(root.resolve()))  # old check passed
        with pytest.raises(ValueError):
            resolve_within(root, evil)

    def test_dotdot_escape_rejected(self, tmp_path):
        root = tmp_path / "vault"
        root.mkdir()
        with pytest.raises(ValueError):
            resolve_within(root, root / ".." / "outside.md")

    def test_symlink_escape_rejected(self, tmp_path):
        root = tmp_path / "vault"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        _symlink_dir(root / "link", outside)
        with pytest.raises(ValueError):
            resolve_within(root, root / "link" / "nota.md")


class TestObsidianManagerTraversal:
    @pytest.mark.parametrize("folder", TRAVERSAL_FOLDERS)
    async def test_create_rejects_traversal_folder(self, vault, folder):
        with pytest.raises(ValueError):
            await obsidian_manager.create_or_append_note("nota", "x", folder=folder)

    @pytest.mark.parametrize("folder", TRAVERSAL_FOLDERS)
    async def test_read_rejects_traversal_folder(self, vault, folder):
        with pytest.raises(ValueError):
            await obsidian_manager.read_note("nota", folder=folder)

    @pytest.mark.parametrize("folder", TRAVERSAL_FOLDERS)
    async def test_delete_rejects_traversal_folder(self, vault, folder):
        with pytest.raises(ValueError):
            await obsidian_manager.delete_note("nota", folder=folder)

    async def test_create_rejects_symlinked_folder_outside(self, vault, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        _symlink_dir(vault / "link_out", outside)
        with pytest.raises(ValueError):
            await obsidian_manager.create_or_append_note("nota", "x", folder="link_out")

    async def test_move_rejects_source_with_sibling_prefix(self, vault, tmp_path):
        fake = tmp_path / "obsidian_vault_fake"
        fake.mkdir()
        src = fake / "nota.md"
        src.write_text("x", encoding="utf-8")

        result = await obsidian_manager.move_or_rename_file(str(src), "01-Proyectos", "nueva")

        assert result["success"] is False
        assert src.exists(), "source outside the vault must not be moved"

    async def test_move_rejects_destination_traversal(self, vault):
        src = vault / "nota.md"
        src.write_text("x", encoding="utf-8")

        result = await obsidian_manager.move_or_rename_file(str(src), "../outside", "nueva")

        assert result["success"] is False
        assert src.exists()
        assert not (vault.parent / "outside").exists()

    async def test_move_rejects_symlinked_source_outside(self, vault, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "nota.md"
        target.write_text("x", encoding="utf-8")
        link = vault / "nota_link.md"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported in this environment")

        result = await obsidian_manager.move_or_rename_file(str(link), "01-Proyectos", "nueva")

        assert result["success"] is False
        assert target.exists(), "target outside the vault must not be moved"

    async def test_create_and_read_inside_nested_folder_ok(self, vault):
        result = await obsidian_manager.create_or_append_note(
            "Nota", "contenido", folder="01-Proyectos/Sub"
        )
        assert result["success"] is True
        note = vault / "01-Proyectos" / "Sub" / "Nota.md"
        assert note.exists()

        read = await obsidian_manager.read_note("Nota", folder="01-Proyectos/Sub")
        assert read["success"] is True
        assert "contenido" in read["full_content"]

    async def test_move_inside_vault_ok(self, vault):
        inbox = vault / "00-Inbox"
        inbox.mkdir()
        src = inbox / "nota.md"
        src.write_text("x", encoding="utf-8")

        result = await obsidian_manager.move_or_rename_file(str(src), "01-Proyectos", "renombrada")

        assert result["success"] is True
        assert (vault / "01-Proyectos" / "renombrada.md").exists()
        assert not src.exists()


class TestFilesSafeVaultSubpath:
    def test_normal_subpath_ok(self, tmp_path):
        from src.handlers.files import _safe_vault_subpath

        vault = tmp_path / "vault"
        vault.mkdir()
        assert (
            _safe_vault_subpath(vault, "03-Recursos/sub")
            == (vault / "03-Recursos" / "sub").resolve()
        )

    def test_symlink_prefix_escape_rejected(self, tmp_path):
        """Old startswith check would have accepted this sibling-prefixed escape."""
        from src.handlers.files import _safe_vault_subpath

        vault = tmp_path / "vault"
        vault.mkdir()
        sibling = tmp_path / "vault_fake"
        sibling.mkdir()
        _symlink_dir(vault / "link", sibling)

        assert str((sibling / "nota").resolve()).startswith(str(vault.resolve()))  # old check
        with pytest.raises(ValueError):
            _safe_vault_subpath(vault, "link/nota")

    def test_dotdot_parts_are_filtered(self, tmp_path):
        from src.handlers.files import _safe_vault_subpath

        vault = tmp_path / "vault"
        vault.mkdir()
        result = _safe_vault_subpath(vault, "../outside/nota")
        assert vault.resolve() in result.parents or result == vault.resolve()
