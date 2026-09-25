"""Configurable vault taxonomy (task 2.1).

Folder names used to be hardcoded across the codebase. They now come from the
`vault:` section of `agent/config.yml` (override the file path with the
`RAFAITA_CONFIG` environment variable), keeping the PARA/Zettelkasten
structure as built-in default.

This module intentionally imports nothing from `src`: `config.py` imports it
and importing the logger here would create a cycle.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_FOLDERS: dict[str, str] = {
    "inbox": "00-Inbox",
    "projects": "01-Proyectos",
    "areas": "02-Areas",
    "areas_salud": "02-Areas/Salud",
    "areas_finanzas": "02-Areas/Finanzas",
    "areas_trabajo": "02-Areas/Trabajo",
    "areas_casa": "02-Areas/Casa",
    "agenda": "02-Areas/Agenda",
    "resources": "03-Recursos",
    "archive": "04-Archivo",
    "zettelkasten": "05-Zettelkasten",
    "diary": "06-Diario",
    "attachments": "Attachments",
    "indexed_docs": "03-Recursos/Documentos_Indexados",
}

# Keys whose folder values form the default structure created at startup.
STRUCTURE_KEYS = [
    "inbox",
    "projects",
    "areas_finanzas",
    "areas_salud",
    "areas_casa",
    "areas_trabajo",
    "resources",
    "archive",
    "zettelkasten",
    "diary",
    "attachments",
]

DEFAULT_IGNORED_DIRS = frozenset(
    {".obsidian", ".git", ".trash", "Documentos_Indexados", "templates"}
)


@dataclass(frozen=True)
class VaultTaxonomy:
    folders: dict[str, str]
    structure: list[str]
    ignored_dirs: frozenset[str]
    protected_folders: tuple[str, ...] = ()

    def path(self, key: str) -> str:
        """Return the configured folder for a symbolic key (defaults if absent)."""
        return self.folders.get(key, DEFAULT_FOLDERS.get(key, key))


def default_config_path() -> Path | None:
    """Resolve the config file: RAFAITA_CONFIG > /workspace > repo-relative."""
    env = os.environ.get("RAFAITA_CONFIG")
    if env:
        return Path(env)
    container_path = Path("/workspace/agent/config.yml")
    if container_path.exists():
        return container_path
    local_path = Path(__file__).resolve().parents[1] / "config.yml"
    if local_path.exists():
        return local_path
    return None


def _as_clean_string(value: Any) -> str:
    return str(value).strip().strip("/\\")


def load_vault_taxonomy(path: Path | None = None) -> VaultTaxonomy:
    """Load the taxonomy, falling back to PARA defaults on any problem."""
    folders = dict(DEFAULT_FOLDERS)
    structure: list[str] | None = None
    ignored_dirs = set(DEFAULT_IGNORED_DIRS)
    protected_folders: list[str] | None = None

    config_path = path or default_config_path()
    if config_path is not None:
        try:
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            vault = data.get("vault", {}) if isinstance(data, dict) else {}
            if isinstance(vault, dict):
                custom_folders = vault.get("folders", {})
                if isinstance(custom_folders, dict):
                    for key, value in custom_folders.items():
                        if isinstance(value, str) and value.strip():
                            folders[str(key)] = _as_clean_string(value)
                raw_structure = vault.get("structure")
                if isinstance(raw_structure, list) and raw_structure:
                    structure = [
                        _as_clean_string(item) for item in raw_structure if str(item).strip()
                    ]
                raw_ignored = vault.get("ignored_dirs")
                if isinstance(raw_ignored, list) and raw_ignored:
                    ignored_dirs = {
                        _as_clean_string(item) for item in raw_ignored if str(item).strip()
                    }
                raw_protected = vault.get("protected_folders")
                if isinstance(raw_protected, list):
                    protected_folders = [
                        _as_clean_string(item) for item in raw_protected if str(item).strip()
                    ]
        except Exception:
            # Fail open to the PARA defaults: a broken config must not leave the
            # vault without structure.
            folders = dict(DEFAULT_FOLDERS)
            structure = None
            ignored_dirs = set(DEFAULT_IGNORED_DIRS)

    if structure is None:
        structure = [folders[key] for key in STRUCTURE_KEYS]

    if protected_folders is None:
        protected_folders = [
            folders["areas_finanzas"],
            folders["areas_salud"],
            folders["diary"],
        ]

    seen: set[str] = set()
    deduped: list[str] = []
    for folder in structure:
        if folder and folder not in seen:
            seen.add(folder)
            deduped.append(folder)

    ignored_dirs.add("Documentos_Indexados")
    return VaultTaxonomy(
        folders=folders,
        structure=deduped,
        ignored_dirs=frozenset(ignored_dirs),
        protected_folders=tuple(protected_folders),
    )


taxonomy = load_vault_taxonomy()


def get_taxonomy() -> VaultTaxonomy:
    """Return the active taxonomy (module global so tests can monkeypatch it)."""
    return taxonomy
