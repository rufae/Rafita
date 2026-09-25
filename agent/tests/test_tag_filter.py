"""Tag filtering tests (task 1.6): query-side filter with metadata flags."""

import os

import httpx
import pytest

from src.config import settings
from src.utils.vector_manager import build_tag_where, normalize_tag


def test_normalize_tag():
    assert normalize_tag("Finanzas") == "finanzas"
    assert normalize_tag("Día-a-día") == "dia_a_dia"
    assert normalize_tag("  ") == ""
    assert normalize_tag("mascotas!") == "mascotas"
    assert len(normalize_tag("x" * 80)) == 40


def test_build_tag_where():
    assert build_tag_where(None) is None
    assert build_tag_where([]) is None
    assert build_tag_where(["   ", "!!!"]) is None
    assert build_tag_where(["finanzas"]) == {"tag__finanzas": 1}
    assert build_tag_where(["finanzas", "Salud"]) == {
        "$or": [{"tag__finanzas": 1}, {"tag__salud": 1}]
    }
    assert build_tag_where(["finanzas", "FINANZAS"]) == {"tag__finanzas": 1}
    many = build_tag_where(["tag%d" % i for i in range(30)])
    assert many is not None
    assert len(many["$or"]) == 20


def _ollama_reachable() -> bool:
    if os.environ.get("CI"):
        return False
    try:
        resp = httpx.get("%s/api/tags" % settings.ollama_host.rstrip("/"), timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


pytestmark_live = pytest.mark.skipif(
    not _ollama_reachable(), reason="Live Ollama required for tag filter integration test"
)

NOISE = "Presupuesto mensual de finanzas: control de gastos mensuales y presupuesto."
TAGGED = "Presupuesto mensual de mascotas: pienso y veterinario."
QUERY = "presupuesto mensual de finanzas"


@pytestmark_live
async def test_tag_filter_recovers_note_outside_top_k(tmp_path, monkeypatch):
    from src.utils import vault_indexer as vi
    from src.utils import vector_manager as vml

    vault = tmp_path / "vault"
    vault.mkdir()
    # Noise notes win the unfiltered top-k; the tagged note must be recovered
    # only through the Chroma-side tag filter.
    for i in range(8):
        (vault / ("Ruido %d.md" % i)).write_text(
            "---\ntags: [ruido]\n---\n## Presupuesto\n%s\n" % NOISE, encoding="utf-8"
        )
    (vault / "Mascotas.md").write_text(
        "---\ntags: [mascotas]\n---\n## Presupuesto\n%s\n" % TAGGED, encoding="utf-8"
    )

    monkeypatch.setattr(settings, "vector_db_dir", str(tmp_path / "vdb"))
    manager = vml.VectorManager()
    try:
        await manager.initialize()
        monkeypatch.setattr(vml, "vector_db", manager)
        monkeypatch.setattr(vi, "VAULT_PATH", vault)
        monkeypatch.setattr(vi, "VAULT_NAME", "testvault")
        indexer = vi.VaultIndexer()
        await indexer.index_all()

        unfiltered = await manager.query(QUERY, top_k=5, apply_threshold=False)
        unfiltered_notes = [r["note_path"] for r in unfiltered["results"]]
        assert unfiltered_notes
        assert "Mascotas.md" not in unfiltered_notes

        filtered = await manager.query(
            QUERY, top_k=5, filter_tags=["mascotas"], apply_threshold=False
        )
        assert [r["note_path"] for r in filtered["results"]] == ["Mascotas.md"]

        uppercase = await manager.query(
            QUERY, top_k=5, filter_tags=["MASCOTAS"], apply_threshold=False
        )
        assert [r["note_path"] for r in uppercase["results"]] == ["Mascotas.md"]

        noisy = await manager.query(QUERY, top_k=5, filter_tags=["ruido"], apply_threshold=False)
        assert noisy["results"]
        assert all(r["note_path"] != "Mascotas.md" for r in noisy["results"])
    finally:
        await manager.close()
