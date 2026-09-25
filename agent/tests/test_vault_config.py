"""Tests for the configurable vault taxonomy (task 2.1)."""

from pathlib import Path

from src.vault_config import (
    DEFAULT_FOLDERS,
    VaultTaxonomy,
    default_config_path,
    load_vault_taxonomy,
)


def test_defaults_when_config_missing(tmp_path):
    tax = load_vault_taxonomy(tmp_path / "missing.yml")
    assert isinstance(tax, VaultTaxonomy)
    assert tax.path("inbox") == "00-Inbox"
    assert tax.path("areas_finanzas") == "02-Areas/Finanzas"
    assert "02-Areas/Salud" in tax.structure


def test_custom_folders_and_derived_structure(tmp_path):
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "vault:\n"
        "  folders:\n"
        "    inbox: Bandeja\n"
        "    areas_finanzas: Contable/2026\n"
        "    zettelkasten: Ideas/Atomicas\n",
        encoding="utf-8",
    )
    tax = load_vault_taxonomy(cfg)
    assert tax.path("inbox") == "Bandeja"
    assert tax.path("areas_finanzas") == "Contable/2026"
    assert tax.path("zettelkasten") == "Ideas/Atomicas"
    assert tax.path("diary") == "06-Diario"  # unset keys keep defaults
    assert "Bandeja" in tax.structure
    assert "Contable/2026" in tax.structure
    assert "00-Inbox" not in tax.structure


def test_explicit_structure_and_ignored_dirs(tmp_path):
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "vault:\n"
        "  folders:\n"
        "    inbox: Bandeja\n"
        "  structure: [Bandeja, Otra]\n"
        "  ignored_dirs: [.obsidian, tmp]\n",
        encoding="utf-8",
    )
    tax = load_vault_taxonomy(cfg)
    assert tax.structure == ["Bandeja", "Otra"]
    assert "tmp" in tax.ignored_dirs
    assert "Documentos_Indexados" in tax.ignored_dirs


def test_broken_config_falls_back_to_defaults(tmp_path):
    cfg = tmp_path / "config.yml"
    cfg.write_text("vault: [unclosed", encoding="utf-8")
    tax = load_vault_taxonomy(cfg)
    assert tax.path("inbox") == DEFAULT_FOLDERS["inbox"]
    assert "00-Inbox" in tax.structure
    assert tax.path("areas_finanzas") == "02-Areas/Finanzas"


def test_default_config_path_env_override(tmp_path, monkeypatch):
    cfg = tmp_path / "custom.yml"
    cfg.write_text("vault: {}", encoding="utf-8")
    monkeypatch.setenv("RAFAITA_CONFIG", str(cfg))
    assert default_config_path() == Path(cfg)
