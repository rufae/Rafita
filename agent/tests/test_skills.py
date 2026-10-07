"""Skills (fase 1/2): manager, catalogo de prompt y herramientas nuevas."""

from pathlib import Path

import pytest

from src.handlers import chat as chat_mod
from src.utils import skills_manager as sm


@pytest.fixture
def raices(tmp_path, monkeypatch):
    repo = tmp_path / "repo-skills"
    vault = tmp_path / "vault"
    repo.mkdir()
    vault.mkdir()
    monkeypatch.setattr(sm, "REPO_SKILLS_DIR", repo)
    monkeypatch.setattr(sm, "VAULT_PATH", vault)
    return repo, vault


def test_list_get_save_y_override_de_boveda(raices):
    repo, vault = raices
    (repo / "backup.md").write_text(
        "---\ndescription: Como restaurar copias.\n---\n\n# Backup\n\nPasos.\n",
        encoding="utf-8",
    )
    assert [s["name"] for s in sm.list_skills()] == ["backup"]
    skill = sm.get_skill("backup")
    assert skill is not None
    assert "restaurar" in skill["description"]
    assert "Pasos" in skill["body"]

    # La boveda pisa el repo con el mismo nombre.
    sm.save_skill("backup", "Version revisada.", "Otro procedimiento.")
    skills = sm.list_skills()
    assert len(skills) == 1
    assert skills[0]["description"] == "Version revisada."
    assert sm.get_skill("backup")["body"] == "Otro procedimiento."


def test_get_skill_desconocida_y_nombre_malo(raices):
    assert sm.get_skill("no-existe") is None
    assert sm.get_skill("") is None
    assert sm.get_skill("../etc/passwd") is None


def test_catalog_prompt_vacio_y_lleno(raices):
    assert sm.skills_catalog_prompt() == ""
    sm.save_skill("triage", "Que revisar cuando algo no llega.", "1. Mirar notas.")
    prompt = sm.skills_catalog_prompt()
    assert "SKILL_RULE" in prompt
    assert "triage — Que revisar" in prompt


async def test_tool_guardar_aprendizaje(raices, monkeypatch):
    escritas = []

    async def fake_overwrite(title, content, folder=""):
        escritas.append((title, content, folder))
        return {"success": True}

    monkeypatch.setattr(chat_mod.ob, "overwrite_note", fake_overwrite)
    res = await chat_mod._execute_tool(
        1,
        "guardar_aprendizaje",
        {"titulo": "Fix DNS", "contenido": "Reiniciar dnsmasq.", "tags": ["red"]},
    )
    assert res["success"] is True
    assert escritas and escritas[0][0] == "Fix DNS"
    assert escritas[0][2] == "Aprendizajes"
    assert "Reiniciar dnsmasq." in escritas[0][1]

    res = await chat_mod._execute_tool(1, "guardar_aprendizaje", {"titulo": "", "contenido": ""})
    assert res["success"] is False


async def test_tools_listar_y_cargar_skill(raices):
    sm.save_skill("procedimiento", "Que hace.", "Paso uno.")
    res = await chat_mod._execute_tool(1, "listar_skills", {})
    assert res["success"] is True
    assert "procedimiento" in res["message"]

    res = await chat_mod._execute_tool(1, "cargar_skill", {"nombre": "procedimiento"})
    assert res["success"] is True
    assert "Paso uno." in res["message"]

    res = await chat_mod._execute_tool(1, "cargar_skill", {"nombre": "fantasma"})
    assert res["success"] is False


def test_write_tools_incluye_guardar_aprendizaje():
    from src.handlers.chat_tools import WRITE_TOOLS

    assert "guardar_aprendizaje" in WRITE_TOOLS


def test_skills_seed_del_repo_estan_parseables():
    # Las skills versionadas en agent/skills/ deben poder cargarse siempre.
    for skill in sm.list_skills():
        if Path(skill["source"]).name == "skills" and skill["name"]:
            assert skill["description"]
            assert skill["body"]
