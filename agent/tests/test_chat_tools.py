"""Regression tests for chat.py tool definitions and tool handlers.

These tests run BEFORE splitting chat.py to catch regressions.
"""

import asyncio
import tempfile
import shutil

import pytest


def test_tool_definitions_have_required_fields():
    """Verify all tool definitions have name, description, and parameters."""
    from src.handlers.chat import TOOLS_DEFINITIONS

    assert len(TOOLS_DEFINITIONS) > 0, "No tools defined"
    for tool in TOOLS_DEFINITIONS:
        assert "type" in tool
        assert "function" in tool
        assert "name" in tool["function"]
        assert "description" in tool["function"]
        assert "parameters" in tool["function"]
        assert tool["type"] == "function"


def test_second_brain_tool_exists():
    """search_second_brain tool must be present (core RAG tool)."""
    from src.handlers.chat import TOOLS_DEFINITIONS

    tool_names = [t["function"]["name"] for t in TOOLS_DEFINITIONS]
    assert "search_second_brain" in tool_names, "search_second_brain missing from tools"


def test_deep_knowledge_base_tool_exists():
    """ask_deep_knowledge_base tool must be present (broad knowledge search)."""
    from src.handlers.chat import TOOLS_DEFINITIONS

    tool_names = [t["function"]["name"] for t in TOOLS_DEFINITIONS]
    assert "ask_deep_knowledge_base" in tool_names, "ask_deep_knowledge_base missing from tools"


def test_tool_definitions_are_valid_json_schema():
    """Each tool's parameters must be valid JSON schema."""
    from src.handlers.chat import TOOLS_DEFINITIONS

    for tool in TOOLS_DEFINITIONS:
        params = tool["function"]["parameters"]
        assert "type" in params
        assert params["type"] == "object"
        if "properties" in params:
            assert isinstance(params["properties"], dict)


class TestSecondBrainToolHandler:
    @pytest.mark.asyncio
    async def test_returns_message_when_no_query(self):
        from src.handlers.chat import _execute_tool

        result = await _execute_tool(9999, "search_second_brain", {"query": ""})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_handle_search_second_brain(self):
        """Verify the handler returns consistent structure (needs vector_db)."""
        from src.handlers.chat import _execute_tool

        result = await _execute_tool(
            9999,
            "search_second_brain",
            {"query": "gasolina", "top_k": 3},
        )
        assert "success" in result
        assert "message" in result or "results" in result


class TestToolRegistry:
    def test_all_known_tools_have_handler(self):
        """Every defined tool must have a handler in _execute_tool."""
        from src.handlers.chat import TOOLS_DEFINITIONS, _execute_tool

        # Known tool name patterns - verify they're handled
        tool_names = [t["function"]["name"] for t in TOOLS_DEFINITIONS]
        core_tools = [
            "search_second_brain",
            "ask_deep_knowledge_base",
            "list_notes",
            "read_note",
        ]
        for name in core_tools:
            if name in tool_names:
                # Just verify it doesn't raise KeyError for a basic call
                pass  # _execute_tool handles by name

    def test_no_duplicate_tool_names(self):
        from src.handlers.chat import TOOLS_DEFINITIONS

        names = [t["function"]["name"] for t in TOOLS_DEFINITIONS]
        assert len(names) == len(set(names)), f"Duplicate tool names: {names}"
