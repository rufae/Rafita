"""PERSIST_TO_BRAIN debug-mode tests (task 2.3)."""

from src.config import settings
from src.handlers.chat import _execute_tool
from src.handlers.chat_tools import TOOLS_DEFINITIONS, WRITE_TOOLS, get_tools_for_llm


def test_write_tools_filtered_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "persist_to_brain", False)
    tools = get_tools_for_llm()
    names = {tool["function"]["name"] for tool in tools}
    assert not (WRITE_TOOLS & names)
    assert "search_second_brain" in names
    assert len(tools) == len(TOOLS_DEFINITIONS) - len(WRITE_TOOLS)


def test_write_tools_present_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "persist_to_brain", True)
    names = {tool["function"]["name"] for tool in get_tools_for_llm()}
    assert names >= WRITE_TOOLS


async def test_executor_rejects_write_tools(monkeypatch):
    monkeypatch.setattr(settings, "persist_to_brain", False)
    cases = [
        ("manage_obsidian_note", {"action": "create", "title": "X", "content": "y"}),
        ("manage_obsidian_note", {"action": "append", "title": "X", "content": "y"}),
        ("manage_obsidian_note", {"action": "delete", "title": "X"}),
        ("move_or_rename_file", {"source_path": "a.md", "dest_folder": "b"}),
        ("ingest_file", {"filename": "x.pdf", "folder": "r", "note_type": "recurso"}),
    ]
    for tool, args in cases:
        result = await _execute_tool(1, tool, args)
        assert result["success"] is False
        assert "PERSIST_TO_BRAIN=false" in result["message"]


async def test_executor_allows_read_action(monkeypatch):
    monkeypatch.setattr(settings, "persist_to_brain", False)
    result = await _execute_tool(1, "manage_obsidian_note", {"action": "read", "title": "nope"})
    assert "PERSIST_TO_BRAIN=false" not in result.get("message", "")
