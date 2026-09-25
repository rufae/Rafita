"""AI provider factory (task 2.5).

Selects the backend from `AI_PROVIDER`:
  - `ollama` (default): local models via the Ollama API.
  - `openai`: any OpenAI-compatible endpoint (OpenAI, DeepSeek, Groq,
    OpenRouter, vLLM, or Ollama's own `/v1` compatibility layer).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.ai.base import AIProvider


def create_ai_client() -> AIProvider:
    from src.config import settings

    provider = (settings.ai_provider or "ollama").strip().lower()
    if provider == "openai":
        from src.ai.openai_compat import OpenAICompatClient

        return OpenAICompatClient()

    from src.ollama_client import OllamaClient

    return OllamaClient()
