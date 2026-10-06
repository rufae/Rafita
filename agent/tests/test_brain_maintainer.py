"""BrainMaintainer git versioning and revert tests (task 2.4)."""

import asyncio

from src.config import settings
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


# ---------- ramas de error y ciclo de vida (cobertura 2026-10-06) ----------


async def test_initialize_existente_y_log_sin_commits(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    maintainer = BrainMaintainer(vault)
    assert (await maintainer.initialize())["status"] == "initialized"
    assert (await maintainer.initialize())["status"] == "existing"  # 65
    # repo recien creado (sin commits) -> git log falla -> debug + []
    assert await maintainer.log() == []  # 44, 87-88


async def test_revert_revision_inexistente_es_honesta(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "nota.md").write_text("v1", encoding="utf-8")
    maintainer = BrainMaintainer(vault)
    await maintainer.initialize()
    await maintainer.snapshot("v1")
    res = await maintainer.revert("deadbeef")
    assert res["success"] is False
    assert "Revision no encontrada" in res["message"]  # 101-102


async def test_revert_archivo_anadido_lo_elimina(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "nota.md").write_text("v1", encoding="utf-8")
    maintainer = BrainMaintainer(vault)
    await maintainer.initialize()
    base = await maintainer.snapshot("v1")
    assert base
    nueva = vault / "nueva.md"
    nueva.write_text("x", encoding="utf-8")
    assert await maintainer.snapshot("anade") is not None

    res = await maintainer.revert(base)
    assert res["success"] is True
    assert res["removed"] == 1  # 116 (status A) y 128 (git rm)
    assert not nueva.exists()
    assert (vault / "nota.md").read_text(encoding="utf-8") == "v1"


async def test_revert_solo_protectidas_no_hace_nada(tmp_path, monkeypatch):
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
    assert await maintainer.snapshot("cambio") is not None

    res = await maintainer.revert(base)
    assert res["success"] is False  # 120-124: nada que restaurar fuera de protegidas
    assert res["protected_skipped"] == 1
    assert (vault / "protegida" / "salud.md").read_text(encoding="utf-8") == "s2"


class _ResultadoGit:
    def __init__(self, code, out="", err=""):
        self.returncode = code
        self.stdout = out
        self.stderr = err


async def test_snapshot_commit_falla_devuelve_none(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    maintainer = BrainMaintainer(vault)
    respuestas = [
        _ResultadoGit(0),
        _ResultadoGit(0, "?? nota.md\n"),
        _ResultadoGit(1, err="commit rechazado"),
    ]

    def _git_falso(*args, **kwargs):
        return respuestas.pop(0)

    monkeypatch.setattr(maintainer, "_git", _git_falso)
    assert await maintainer.snapshot("x") is None  # 77-79
    assert respuestas == []


async def test_revert_linea_vacia_en_diff_y_commit_falla(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    maintainer = BrainMaintainer(vault)
    respuestas = [
        _ResultadoGit(0, "M\tnota.md\n\n"),  # linea vacia -> continue (109)
        _ResultadoGit(0),  # checkout
        _ResultadoGit(1, err="commit rechazado"),  # 131-135
    ]

    def _git_falso(*args, **kwargs):
        return respuestas.pop(0)

    monkeypatch.setattr(maintainer, "_git", _git_falso)
    res = await maintainer.revert("cualquiera")
    assert res["success"] is False
    assert "commit rechazado" in res["message"]
    assert respuestas == []


async def test_start_stop_y_snapshot_periodico(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    maintainer = BrainMaintainer(vault, interval_seconds=1)
    evento = asyncio.Event()

    monkeypatch.setattr(settings, "brain_maintenance", False)
    await maintainer.start(evento)
    assert maintainer._task is None  # 155-157: deshabilitado no crea tarea

    monkeypatch.setattr(settings, "brain_maintenance", True)
    await maintainer.start(evento)  # 158-166: init + snapshot + tarea
    assert maintainer._task is not None

    await asyncio.sleep(1.3)  # supera un timeout -> snapshot periodico (174-175)
    await maintainer.stop()  # 178-185
    assert maintainer._task is None
    await maintainer.stop()  # sin tarea: no-op


async def test_loop_sale_limpio_al_recibir_shutdown(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    maintainer = BrainMaintainer(vault, interval_seconds=10)
    evento = asyncio.Event()
    monkeypatch.setattr(settings, "brain_maintenance", True)
    await maintainer.start(evento)
    await asyncio.sleep(0.05)  # el loop ya esta dentro de wait_for
    evento.set()
    await asyncio.wait_for(maintainer._task, timeout=5)  # 173: return limpio
    await maintainer.stop()
