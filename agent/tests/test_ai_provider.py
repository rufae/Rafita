"""AI provider adapter selection tests (task 2.5)."""

from types import SimpleNamespace

from src.ai.factory import create_ai_client
from src.ai.openai_compat import OpenAICompatClient
from src.config import settings
from src.ollama_client import OllamaClient


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


def test_ollama_disables_thinking_by_default(monkeypatch):
    monkeypatch.setattr(settings, "ollama_reasoning_effort", "none")
    extra = OllamaClient()._ollama_extra_body(2048)
    assert extra["reasoning_effort"] == "none"
    assert extra["keep_alive"] == -1
    assert extra["options"] == {"num_ctx": 2048}


def test_ollama_extra_body_omits_reasoning_effort_when_empty(monkeypatch):
    monkeypatch.setattr(settings, "ollama_reasoning_effort", "")
    extra = OllamaClient()._ollama_extra_body(4096)
    assert "reasoning_effort" not in extra


def test_ollama_num_thread_only_included_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "ollama_num_thread", 0)
    assert "num_thread" not in OllamaClient()._ollama_extra_body(2048)["options"]
    monkeypatch.setattr(settings, "ollama_num_thread", 8)
    assert OllamaClient()._ollama_extra_body(2048)["options"]["num_thread"] == 8


async def test_chat_sync_sends_reasoning_effort_to_ollama(monkeypatch):
    monkeypatch.setattr(settings, "ollama_reasoning_effort", "none")
    client = OllamaClient()
    captured: dict = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        message = SimpleNamespace(content="hola", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    result = await client._chat_sync({"model": "m", "messages": []})
    assert result == "hola"
    assert captured["extra_body"]["reasoning_effort"] == "none"


def test_openai_reasoning_effort_is_opt_in(monkeypatch):
    monkeypatch.setattr(settings, "openai_reasoning_effort", "")
    assert OpenAICompatClient()._extra_body() is None
    monkeypatch.setattr(settings, "openai_reasoning_effort", "low")
    assert OpenAICompatClient()._extra_body() == {"reasoning_effort": "low"}


def _fake_vision_client(captured: dict):
    async def fake_create(**kwargs):
        captured.update(kwargs)
        message = SimpleNamespace(content="ok", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))


async def test_chat_vision_same_model_skips_hot_swap(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ollama_model", "gemma4:12b")
    monkeypatch.setattr(settings, "ollama_vision_model", "gemma4:12b")
    client = OllamaClient()
    swaps = {"vision": 0, "text": 0}

    async def fake_swap():
        swaps["vision"] += 1

    async def fake_unswap():
        swaps["text"] += 1

    monkeypatch.setattr(client, "hot_swap_to_vision", fake_swap)
    monkeypatch.setattr(client, "hot_swap_to_text", fake_unswap)
    captured: dict = {}
    client._client = _fake_vision_client(captured)  # type: ignore[assignment]
    image = tmp_path / "img.png"
    image.write_bytes(b"fake-image-bytes")

    result = await client.chat_vision([{"role": "user", "content": "mira"}], [str(image)])

    assert result == "ok"
    assert swaps == {"vision": 0, "text": 0}
    assert captured["extra_body"]["keep_alive"] == -1


async def test_chat_vision_different_model_uses_hot_swap(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ollama_model", "chat-model")
    monkeypatch.setattr(settings, "ollama_vision_model", "vision-model")
    client = OllamaClient()
    swaps = {"vision": 0, "text": 0}

    async def fake_swap():
        swaps["vision"] += 1

    async def fake_unswap():
        swaps["text"] += 1

    monkeypatch.setattr(client, "hot_swap_to_vision", fake_swap)
    monkeypatch.setattr(client, "hot_swap_to_text", fake_unswap)
    captured: dict = {}
    client._client = _fake_vision_client(captured)  # type: ignore[assignment]
    image = tmp_path / "img.png"
    image.write_bytes(b"fake-image-bytes")

    result = await client.chat_vision([{"role": "user", "content": "mira"}], [str(image)])

    assert result == "ok"
    assert swaps == {"vision": 1, "text": 1}
    assert captured["extra_body"]["keep_alive"] == 0
