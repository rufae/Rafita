"""Live test for the calibrated relevance threshold (task 1.5).

Requires a live Ollama with the configured embedding model; skipped in CI.
"""

import importlib.util
import os
from pathlib import Path

import httpx
import pytest

from src.config import settings

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rag_eval.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("rag_eval_script_threshold", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ollama_reachable() -> bool:
    if os.environ.get("CI"):
        return False
    try:
        resp = httpx.get("%s/api/tags" % settings.ollama_host.rstrip("/"), timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _ollama_reachable(), reason="Live Ollama required for threshold test"
)

# Dataset negative whose top relevance (0.482) is closest to the threshold.
TIGHT_NEGATIVE = "¿Cuánto pagué por el vuelo a Lisboa?"


async def test_threshold_filters_irrelevant_and_keeps_relevant():
    rag_eval = _load_script()
    manager, _ = await rag_eval._build_indexed_manager()
    try:
        threshold = settings.relevance_threshold

        filtered = await manager.query(TIGHT_NEGATIVE, top_k=5)
        assert filtered["results"] == []
        assert "NO_ENCONTRADO" in filtered["message"]

        raw = await manager.query(TIGHT_NEGATIVE, top_k=5, apply_threshold=False)
        assert raw["results"], "raw query should still return candidates"
        assert max(float(r["relevance"]) for r in raw["results"]) < threshold

        relevant = await manager.query("¿A qué soy alérgico?", top_k=5)
        assert relevant["results"]
        assert all(float(r["relevance"]) >= threshold for r in relevant["results"])
    finally:
        await manager.close()
