"""BrainMaintainer git versioning and revert tests (task 2.4)."""

from src.utils import brain_maintainer as bm
from src.utils.brain_maintainer import BrainMaintainer
from src.vault_config import VaultTaxonomy


async def test_snapshot_and_revert(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "nota.md"
    note.write_text("v1", encoding="utf-8")

    maintainer = BrainMaintainer(vault)
    info = await maintainer.initialize()
    assert info["status"] == "initialized"
    assert (vault / ".git").exists()

    first = await maintainer.snapshot("v1")
    assert first
    note.write_text("v2", encoding="utf-8")
    second = await maintainer.snapshot("v2")
    assert second and second != first

    result = await maintainer.revert(first)
    assert result["success"] is True
    assert note.read_text(encoding="utf-8") == "v1"
    assert result["restored"] == 1

    assert await maintainer.snapshot("noop") is None
    history = await maintainer.log()
    assert any("Revert vault to" in line for line in history)


async def test_revert_respects_protected_folders(tmp_path, monkeypatch):
    tax = bm.get_taxonomy()
    protected_tax = VaultTaxonomy(
        folders=dict(tax.folders),
        structure=list(tax.structure),
        ignored_dirs=tax.ignored_dirs,
        protected_folders=("protegida",),
    )
    monkeypatch.setattr(bm, "get_taxonomy", lambda: protected_tax)

    vault = tmp_path / "vault"
    (vault / "protegida").mkdir(parents=True)
    (vault / "protegida" / "salud.md").write_text("s1", encoding="utf-8")
    (vault / "otra.md").write_text("o1", encoding="utf-8")

    maintainer = BrainMaintainer(vault)
    await maintainer.initialize()
    base = await maintainer.snapshot("base")
    assert base

    (vault / "protegida" / "salud.md").write_text("s2", encoding="utf-8")
    (vault / "otra.md").write_text("o2", encoding="utf-8")
    assert await maintainer.snapshot("cambios")

    result = await maintainer.revert(base)
    assert result["success"] is True
    assert (vault / "protegida" / "salud.md").read_text(encoding="utf-8") == "s2"
    assert (vault / "otra.md").read_text(encoding="utf-8") == "o1"
    assert result["protected_skipped"] == 1
