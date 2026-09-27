"""Detector de intención de herramientas y edición de notas (2026-09-27)."""

import src.utils.obsidian_manager as obsidian_manager
from src.handlers.chat import _detect_tool_intent
from src.utils.obsidian_manager import overwrite_note, read_note


def test_detect_tool_intent_google_phrases():
    assert _detect_tool_intent("cual es el telefono de Ana!!")
    assert _detect_tool_intent("¿cuál es el teléfono de mamá?")
    assert _detect_tool_intent("dime el ultimo correo que tengo en mi bandeja de entrada")
    assert _detect_tool_intent("cuantos pasos llevo hoy")
    assert _detect_tool_intent("añademe una tarea: comprar pan")
    assert _detect_tool_intent("busca en mi drive el informe")


def test_detect_tool_intent_vault_editing():
    assert _detect_tool_intent("edita la nota de reuniones")
    assert _detect_tool_intent("guarda este correo como nota")


def test_detect_tool_intent_smalltalk_is_false():
    assert not _detect_tool_intent("hola, ¿qué tal?")


async def test_overwrite_note_replaces_content(tmp_path, monkeypatch):
    monkeypatch.setattr(obsidian_manager, "OBSIDIAN_VAULT", tmp_path)
    first = await overwrite_note("Prueba", "contenido original", "00-Inbox")
    assert first["success"] is True
    assert first["action"] == "created"

    second = await overwrite_note("Prueba", "contenido nuevo", "00-Inbox")
    assert second["success"] is True
    assert second["action"] == "overwritten"

    read = await read_note("Prueba", "00-Inbox")
    assert "contenido nuevo" in read.get("content", "")
    assert "contenido original" not in read.get("content", "")
