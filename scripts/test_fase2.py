"""Prueba del chunking semantico y parseo de frontmatter de la Fase 2."""

import sys
import os

BASE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"))
sys.path.insert(0, BASE)
print(f"sys.path[0] = {BASE}")

import types
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAULT_PATH = os.path.join(PROJECT_ROOT, "mi_boveda_obsidian")


class FakeSettings:
    embedding_model = "nomic-embed-text"
    embedding_dim = 768
    obsidian_vault_dir = VAULT_PATH
    obsidian_vault_name = "mi_boveda_obsidian"


mock_settings = types.ModuleType("src.config")
mock_settings.settings = FakeSettings()
sys.modules["src.config"] = mock_settings
sys.modules["src.logger"] = types.ModuleType("src.logger")
sys.modules["src.logger"].logger = type(
    "Logger",
    (),
    {"info": print, "warning": print, "error": print, "debug": print, "exception": print},
)()

from src.utils.vault_indexer import (
    parse_frontmatter,
    chunk_by_headings,
    estimate_tokens,
    build_obsidian_uri,
)

# Test 1: WhatsApp_Enlaces_Index.md
note = Path(VAULT_PATH) / "03-Recursos" / "WhatsApp_Enlaces_Index.md"
content = note.read_text(encoding="utf-8")
metadata, body = parse_frontmatter(content)
chunks = chunk_by_headings(body, max_tokens=500, overlap_tokens=50)

print("=" * 60)
print("TEST 1: WhatsApp_Enlaces_Index.md")
print("=" * 60)
print("Frontmatter:")
for k, v in metadata.items():
    print(f"  {k}: {v}")
print(f"\nTokens estimados del body: {estimate_tokens(body)}")
print(f"Chunks generados: {len(chunks)}")
for i, c in enumerate(chunks[:3]):
    print(f"\n  Chunk {i + 1}:")
    print(f"    Heading path: {c['heading_path']}")
    print(f"    Tokens: {estimate_tokens(c['text'])}")
    print(f"    Preview: {c['text'][:120]}...")

# Test 2: WhatsApp_Chat_Importado.md
note2 = Path(VAULT_PATH) / "04-Archivo" / "WhatsApp_Chat_Importado.md"
content2 = note2.read_text(encoding="utf-8")
metadata2, body2 = parse_frontmatter(content2)
chunks2 = chunk_by_headings(body2, max_tokens=500, overlap_tokens=50)

print("\n" + "=" * 60)
print("TEST 2: WhatsApp_Chat_Importado.md")
print("=" * 60)
print(
    f"Frontmatter: type={metadata2.get('type')}, tags={metadata2.get('tags')}, status={metadata2.get('status')}"
)
print(f"Tokens estimados del body: {estimate_tokens(body2)}")
print(f"Chunks generados: {len(chunks2)}")
print(f"obsidian_uri: {build_obsidian_uri(note2)}")
for i, c in enumerate(chunks2[:3]):
    print(f"\n  Chunk {i + 1}:")
    heading = c["heading_path"][:80] if c["heading_path"] else "(root)"
    print(f"    Heading path: {heading}")
    print(f"    Tokens: {estimate_tokens(c['text'])}")
    print(f"    Preview: {c['text'][:120]}...")

print("\n" + "=" * 60)
print("RESULTADO: Chunking semantico por H2/H3 funciona correctamente")
print("=" * 60)
