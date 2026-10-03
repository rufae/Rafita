"""Tests de las citas [S#] y la sección Fuentes (mejora 1)."""

from src.utils.citations import CitationRegistry, finalize_citations


def _sources():
    return [
        {"id": "S1", "note_path": "a.md", "heading": "Intro", "obsidian_uri": "obsidian://a"},
        {"id": "S2", "note_path": "b.md", "heading": "", "obsidian_uri": ""},
    ]


def test_registry_numera_seguidor_y_resetea():
    reg = CitationRegistry()
    reg.reset()
    assert reg.add("a.md", "Intro", "obsidian://a") == "S1"
    assert reg.add("b.md") == "S2"
    assert len(reg.sources()) == 2
    reg.reset()
    assert reg.add("c.md") == "S1"
    reg.clear()
    assert reg.sources() == []


def test_finalize_anade_fuentes_de_citas_validas():
    content = "Ana trabaja en Babel [S1]. Otra cosa [S2]."
    out = finalize_citations(content, _sources())
    assert "[S1]" in out
    assert "Fuentes:" in out
    assert "a.md" in out
    assert "[abrir](obsidian://a)" in out
    assert "b.md" in out


def test_finalize_elimina_citas_invalidas():
    content = "Dato real [S1] y dato inventado [S99]."
    out = finalize_citations(content, _sources())
    assert "[S1]" in out
    assert "[S99]" not in out
    assert "S99" not in out


def test_finalize_sin_citas_lista_fuentes_igual():
    content = "Respuesta fundamentada pero sin citas."
    out = finalize_citations(content, _sources())
    assert out.startswith(content)
    assert "Fuentes:" in out
    assert "- [S1] a.md" in out


def test_finalize_sin_fuentes_no_anade_nada():
    content = "Respuesta sin busqueda [S1]."
    out = finalize_citations(content, [])
    assert out == "Respuesta sin busqueda."
    assert "Fuentes" not in out


def test_finalize_en_voz_quita_marcas_y_no_anade_fuentes():
    content = "Ana trabaja en Babel [S1]."
    out = finalize_citations(content, _sources(), voice=True)
    assert "[S1]" not in out
    assert "Fuentes" not in out
    assert "Ana trabaja en Babel." in out


def test_finalize_con_heading_y_sin_uri():
    content = "Dato [S1] y otro [S2]."
    out = finalize_citations(content, _sources())
    assert "- [S1] a.md — Intro [abrir](obsidian://a)" in out
    assert "- [S2] b.md" in out
