"""Tests for vault_indexer.py — chunking, parsing, indexing logic."""

import pytest

from src.utils.vault_indexer import (
    parse_frontmatter,
    chunk_by_headings,
    build_obsidian_uri,
)


class TestParseFrontmatter:
    def test_parses_valid_frontmatter(self, sample_md_content):
        metadata, body = parse_frontmatter(sample_md_content)
        assert metadata["title"] == "Finanzas Reales"
        assert metadata["type"] == "area"
        assert metadata["tags"] == ["finanzas", "personal"]
        assert "Ingresos" in body
        assert "---" not in body[:50]

    def test_handles_no_frontmatter(self):
        content = "## Just a heading\nSome text without frontmatter."
        metadata, body = parse_frontmatter(content)
        assert metadata == {}
        assert "## Just a heading" in body

    def test_handles_broken_frontmatter(self):
        content = "---\nbroken: yes\n---\n## Content\nMore text --- still content."
        metadata, body = parse_frontmatter(content)
        assert "Content" in body

    def test_preserves_related_links(self):
        content = """---
title: Test
related: ["[[Nota A]]", "[[Nota B]]"]
---
## Body"""
        metadata, _ = parse_frontmatter(content)
        assert "[[Nota A]]" in str(metadata.get("related", []))
        assert "[[Nota B]]" in str(metadata.get("related", []))


class TestChunkByHeadings:
    def test_chunks_h2_headings(self, sample_md_content):
        chunks = chunk_by_headings(sample_md_content, max_tokens=500, overlap_tokens=50)
        assert len(chunks) > 0
        headings = [c["heading"] for c in chunks]
        assert "Ingresos" in headings or any(
            "Ingresos" in c.get("heading_path", "") for c in chunks
        )

    def test_chunks_h3_subheadings(self):
        content = """---
title: Test
type: nota-atomica
---
## Finanzas
### Ingresos
Texto de ingresos.
### Gastos
Texto de gastos.
"""
        chunks = chunk_by_headings(content, max_tokens=500)
        assert len(chunks) >= 2
        paths = [c["heading_path"] for c in chunks]
        assert any("Ingresos" in p for p in paths)
        assert any("Gastos" in p for p in paths)

    def test_empty_content_returns_empty(self):
        chunks = chunk_by_headings("", max_tokens=500)
        assert chunks == []

    def test_small_content_no_frontmatter_single_chunk(self):
        chunks = chunk_by_headings("Just a small paragraph.", max_tokens=500)
        assert len(chunks) == 1
        assert len(chunks[0]["text"]) > 0

    def test_large_content_splits(self):
        words = " ".join(["word"] * 1000)
        content = f"---\ntitle: Test\ntype: nota-atomica\n---\n{words}"
        chunks = chunk_by_headings(content, max_tokens=200, overlap_tokens=20)
        assert len(chunks) > 3

    def test_chunks_have_required_fields(self, sample_md_content):
        chunks = chunk_by_headings(sample_md_content, max_tokens=500)
        for chunk in chunks:
            assert "text" in chunk
            assert "heading" in chunk
            assert "heading_path" in chunk
            assert len(chunk["text"]) > 0

    def test_heading_path_uses_parents(self):
        content = """---
title: Test
type: nota-atomica
---
## Finanzas
Intro texto.
### Presupuesto
Detalle del presupuesto.
"""
        chunks = chunk_by_headings(content, max_tokens=500)
        # The Presupuesto chunk should have path "Finanzas > Presupuesto"
        presupuesto = [c for c in chunks if "Presupuesto" in c["heading"]]
        assert len(presupuesto) > 0
        if presupuesto:
            assert "Finanzas" in presupuesto[0]["heading_path"]


class TestBuildObsidianUri:
    def test_returns_non_empty_for_valid_path(self, temp_dir):
        """build_obsidian_uri needs vault context; skip in CI."""
        import os

        if os.environ.get("CI"):
            pytest.skip("Vault path not available in CI")
        from src.utils.vault_indexer import VAULT_PATH, build_obsidian_uri

        note = VAULT_PATH / "test.md"
        note.touch()
        uri = build_obsidian_uri(note)
        note.unlink()
        assert uri.startswith("obsidian://open")
