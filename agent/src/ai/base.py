"""Common AI provider interface (task 2.5).

Both the local Ollama backend and the external OpenAI-compatible backend
implement this surface, so consumers (orchestrator, chat, voice) do not change.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AIProvider(Protocol):
    model: str
    vision_model: str

    async def initialize(self) -> None: ...

    async def close(self) -> None: ...

    async def check_health(self) -> dict[str, Any]: ...

    async def chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
        images: list[str] | None = None,
    ) -> str: ...

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        images: list[str] | None = None,
    ) -> tuple[str, list[dict[str, Any]] | None]: ...

    def chat_stream_tokens(
        self,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Any: ...

    async def chat_vision(
        self,
        messages: list[dict[str, Any]],
        images: list[str],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str: ...

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...
