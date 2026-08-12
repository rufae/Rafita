"""Shared fixtures for Rafita AVP tests."""

import os
import shutil
import sys
import tempfile
from pathlib import Path

# Ensure agent/src is importable (CI environment)
_src_dir = Path(__file__).resolve().parent.parent / "src"
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

import pytest


def _ollama_available() -> bool:
    """Check if Ollama is reachable (skip embedding tests in CI)."""
    if os.environ.get("CI"):
        return False
    try:
        import httpx

        r = httpx.get("http://ollama:11434/api/tags", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    """Auto-skip Ollama-dependent tests when Ollama is not reachable."""
    if _ollama_available():
        return
    skip_marker = pytest.mark.skip(reason="Ollama not available (CI environment)")
    for item in items:
        if "OllamaEmbeddingFunction" in item.parent.name if item.parent else "":
            item.add_marker(skip_marker)


@pytest.fixture
def temp_dir():
    """Temporary directory for test isolation."""
    path = tempfile.mkdtemp(prefix="rafita_test_")
    yield Path(path)
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def sample_md_content():
    """A representative Obsidian note with frontmatter."""
    return """---
id: 20260810-1430
title: Finanzas Reales
type: area
tags: [finanzas, personal]
status: abierto
created: 2026-08-10
updated: 2026-08-10
related: ["[[Salud Rafael]]"]
---

## Ingresos

Salario mensual: 1313 euros netos.

## Gastos

### Transporte

Gasolina: 80 euros al mes.
Seguro del coche: 450 euros al ano.

### Vivienda

Alquiler: 400 euros al mes.
"""


@pytest.fixture
def sample_note(temp_dir, sample_md_content):
    """Create a temporary Obsidian note for testing."""
    note_path = temp_dir / "Finanzas Reales.md"
    note_path.write_text(sample_md_content, encoding="utf-8")
    return note_path


@pytest.fixture
def sample_chunks():
    """Sample chunks that match the vault_indexer output format."""
    return [
        {
            "text": "Salario mensual: 1313 euros netos.",
            "heading": "Ingresos",
            "heading_path": "Ingresos",
        },
        {
            "text": "Gasolina: 80 euros al mes.",
            "heading": "Transporte",
            "heading_path": "Gastos > Transporte",
        },
        {
            "text": "Seguro del coche: 450 euros al ano.",
            "heading": "Transporte",
            "heading_path": "Gastos > Transporte",
        },
        {
            "text": "Alquiler: 400 euros al mes.",
            "heading": "Vivienda",
            "heading_path": "Gastos > Vivienda",
        },
    ]
