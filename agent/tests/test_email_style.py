"""2.b.3 — Estilo de correo aprendido: profesional y claro por defecto.

EMAIL_STYLE_RULE (siempre) + EMAIL_STYLE_HINT (ejemplos en la bóveda
00-Estilos-correo/, uno por destinatario) + LEEME.md creado al inicializar
la estructura de la bóveda.
"""

import src.utils.obsidian_manager as om
from src.core.orchestrator import EMAIL_STYLE_RULE, build_system_prompt
from src.vault_config import get_taxonomy


def test_regla_presente_en_chat_y_voz():
    for voice in (False, True):
        prompt = build_system_prompt(voice=voice)
        assert "EMAIL_STYLE_RULE" in prompt
        assert "profesional y claro" in prompt
        assert "Saludos cordiales" in prompt
        assert "nunca inventes datos" in prompt.lower()
        assert "[indicar ...]" in prompt


def test_regla_es_la_constante_exportada():
    assert "EMAIL_STYLE_RULE" in EMAIL_STYLE_RULE


def test_hint_vacia_sin_ejemplos(tmp_path, monkeypatch):
    monkeypatch.setattr(om, "OBSIDIAN_VAULT", tmp_path)
    (tmp_path / get_taxonomy().path("estilos_correo")).mkdir(parents=True)
    assert "EMAIL_STYLE_HINT" not in build_system_prompt()


def test_hint_lista_ejemplos_por_destinatario(tmp_path, monkeypatch):
    monkeypatch.setattr(om, "OBSIDIAN_VAULT", tmp_path)
    carpeta = tmp_path / get_taxonomy().path("estilos_correo")
    carpeta.mkdir(parents=True)
    (carpeta / "Ana.md").write_text("Hola Ana, ...", encoding="utf-8")
    (carpeta / "Clientes.md").write_text("Estimado cliente, ...", encoding="utf-8")
    (carpeta / "LEEME.md").write_text("guia", encoding="utf-8")
    prompt = build_system_prompt()
    assert "EMAIL_STYLE_HINT" in prompt
    hint = prompt.split("EMAIL_STYLE_HINT", 1)[1]
    assert "Ana.md" in hint
    assert "Clientes.md" in hint
    assert "LEEME.md" not in hint
    assert "00-Estilos-correo" in hint


async def test_init_crea_carpeta_y_leeme(tmp_path, monkeypatch):
    monkeypatch.setattr(om, "OBSIDIAN_VAULT", tmp_path)
    await om.initialize_vault_structure()
    carpeta = tmp_path / "00-Estilos-correo"
    assert carpeta.is_dir()
    leeme = carpeta / "LEEME.md"
    assert leeme.exists()
    texto = leeme.read_text(encoding="utf-8")
    assert "Estilos de correo" in texto
    assert "firma" in texto


def test_taxonomia_incluye_estilos_correo():
    tax = get_taxonomy()
    assert "estilos_correo" in tax.folders
    assert tax.path("estilos_correo") == "00-Estilos-correo"
    assert "00-Estilos-correo" in tax.structure
