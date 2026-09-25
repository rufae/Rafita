"""AI provider adapter selection tests (task 2.5)."""

from src.ai.factory import create_ai_client
from src.config import settings


def test_default_provider_is_local(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "ollama")
    client = create_ai_client()
    assert client.__class__.__name__ == "OllamaClient"


def test_openai_provider_selected_by_config(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "openai")
    monkeypatch.setattr(settings, "openai_base_url", "http://localhost:9999/v1")
    monkeypatch.setattr(settings, "openai_model", "test-model")
    monkeypatch.setattr(settings, "openai_api_key", "dummy")
    client = create_ai_client()
    assert client.__class__.__name__ == "OpenAICompatClient"
    assert client.model == "test-model"
    assert client.base_url == "http://localhost:9999/v1"


def test_providers_expose_the_same_surface(monkeypatch):
    for provider in ("ollama", "openai"):
        monkeypatch.setattr(settings, "ai_provider", provider)
        client = create_ai_client()
        for method in (
            "initialize",
            "close",
            "check_health",
            "chat",
            "chat_with_tools",
            "chat_stream_tokens",
            "chat_vision",
            "embed_texts",
        ):
            assert hasattr(client, method), "%s missing %s" % (provider, method)
