"""Shared fixtures for Rafita AVP tests."""

import tempfile
import shutil
from pathlib import Path

import pytest


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
