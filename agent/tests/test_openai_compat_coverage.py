"""Cobertura de ai/openai_compat.py y ai/factory.py (sin red: SDK simulado)."""

from types import SimpleNamespace

import pytest

from src.ai.factory import create_ai_client
from src.ai.openai_compat import OpenAICompatClient
from src.config import settings


def make_client(monkeypatch, **attrs):
    monkeypatch.setattr(settings, "openai_model", "gpt-test")
    monkeypatch.setattr(settings, "openai_base_url", "http://localhost:9999/v1/")
    monkeypatch.setattr(settings, "openai_reasoning_effort", attrs.get("reasoning_effort", ""))
    client = OpenAICompatClient()
    for key, value in attrs.items():
        setattr(client, key, value)
    return client


def fake_sdk_client(captured, content="hola", tool_calls=None, models=None):
    async def fake_create(**kwargs):
        captured.update(kwargs)
        message = SimpleNamespace(content=content, tool_calls=tool_calls)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))


# ---------------------------------------------------------------------------
# initialize / close / repr
# ---------------------------------------------------------------------------


async def test_initialize_builds_sdk_clients(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "openai_base_url", "http://localhost:9999/v1/")
    monkeypatch.setattr(settings, "openai_model", "gpt-test")
    monkeypatch.setattr(settings, "openai_vision_model", "")
    monkeypatch.setattr(settings, "openai_reasoning_effort", "")
    monkeypatch.setattr(settings, "llm_temperature", 0.5)
    monkeypatch.setattr(settings, "llm_max_tokens", 128)
    created = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            created["async"] = kwargs

    class FakeOpenAI:
        def __init__(self, **kwargs):
            created["sync"] = kwargs

    monkeypatch.setattr("src.ai.openai_compat.AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setattr("src.ai.openai_compat.OpenAI", FakeOpenAI)

    client = OpenAICompatClient()
    assert client.model == "gpt-test"
    assert client.vision_model == "gpt-test"
    assert client.base_url == "http://localhost:9999/v1"
    await client.initialize()

    assert created["async"]["base_url"] == "http://localhost:9999/v1"
    assert created["async"]["api_key"] == "sk-test"
    assert created["sync"]["api_key"] == "sk-test"
    assert isinstance(client._client, FakeAsyncOpenAI)
    assert isinstance(client._embed_client, FakeOpenAI)


async def test_initialize_uses_placeholder_key(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "")
    captured = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("src.ai.openai_compat.AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setattr("src.ai.openai_compat.OpenAI", FakeAsyncOpenAI)

    await OpenAICompatClient().initialize()
    assert captured["api_key"] == "not-needed"


async def test_close_resets_clients(monkeypatch):
    client = make_client(monkeypatch)
    calls = []

    async def fake_close():
        calls.append(True)

    client._client = SimpleNamespace(close=fake_close)
    client._embed_client = object()
    await client.close()
    assert calls == [True]
    assert client._client is None
    assert client._embed_client is None

    await client.close()  # sin cliente: no falla


def test_repr(monkeypatch):
    client = make_client(monkeypatch)
    assert repr(client) == "OpenAICompatClient(model=gpt-test, base_url=http://localhost:9999/v1)"


# ---------------------------------------------------------------------------
# check_health
# ---------------------------------------------------------------------------


async def test_check_health_degraded_when_model_missing(monkeypatch):
    client = make_client(monkeypatch)

    async def fake_list():
        return SimpleNamespace(data=[SimpleNamespace(id="otro-modelo")])

    client._client = SimpleNamespace(models=SimpleNamespace(list=fake_list))
    health = await client.check_health()
    assert health["status"] == "degraded"
    assert health["model_available"] is False
    assert "not listed" in health["detail"]


async def test_check_health_unhealthy_on_error(monkeypatch):
    client = make_client(monkeypatch)

    async def fake_list():
        raise ConnectionError("sin ruta al host")

    client._client = SimpleNamespace(models=SimpleNamespace(list=fake_list))
    health = await client.check_health()
    assert health["status"] == "unhealthy"
    assert "unreachable" in health["detail"]


async def test_check_health_matches_model_with_tag_suffix(monkeypatch):
    client = make_client(monkeypatch)

    async def fake_list():
        return SimpleNamespace(data=[SimpleNamespace(id="gpt-test:latest")])

    client._client = SimpleNamespace(models=SimpleNamespace(list=fake_list))
    health = await client.check_health()
    assert health["status"] == "ok"
    assert health["model_available"] is True


# ---------------------------------------------------------------------------
# chat / chat_with_tools
# ---------------------------------------------------------------------------


async def test_chat_requires_initialization():
    with pytest.raises(RuntimeError, match="not initialized"):
        await OpenAICompatClient().chat([{"role": "user", "content": "hola"}])


async def test_chat_sends_configured_defaults(monkeypatch):
    monkeypatch.setattr(settings, "llm_temperature", 0.3)
    monkeypatch.setattr(settings, "llm_max_tokens", 99)
    monkeypatch.setattr(settings, "openai_reasoning_effort", "low")
    client = make_client(monkeypatch, temperature=0.3, max_tokens=99, reasoning_effort="low")
    captured = {}
    client._client = fake_sdk_client(captured, content="respuesta")

    result = await client.chat([{"role": "user", "content": "hola"}])

    assert result == "respuesta"
    assert captured["model"] == "gpt-test"
    assert captured["temperature"] == 0.3
    assert captured["max_tokens"] == 99
    assert captured["extra_body"] == {"reasoning_effort": "low"}


async def test_chat_overrides_and_empty_content(monkeypatch):
    client = make_client(monkeypatch)
    captured = {}
    client._client = fake_sdk_client(captured, content=None)

    result = await client.chat([{"role": "user", "content": "hola"}], temperature=0.9, max_tokens=5)

    assert result == ""
    assert captured["temperature"] == 0.9
    assert captured["max_tokens"] == 5


async def test_chat_with_tools_parses_calls(monkeypatch):
    client = make_client(monkeypatch)
    tool_calls = [
        SimpleNamespace(
            id="call_1",
            type="function",
            function=SimpleNamespace(name="save_expense", arguments='{"amount": 1}'),
        )
    ]
    captured = {}
    client._client = fake_sdk_client(captured, content="voy", tool_calls=tool_calls)

    content, calls = await client.chat_with_tools(
        [{"role": "user", "content": "gasta 1"}],
        tools=[{"type": "function", "function": {"name": "save_expense"}}],
    )

    assert content == "voy"
    assert calls == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "save_expense", "arguments": '{"amount": 1}'},
        }
    ]
    assert captured["tools"] == [{"type": "function", "function": {"name": "save_expense"}}]


async def test_chat_with_tools_without_calls(monkeypatch):
    client = make_client(monkeypatch)
    client._client = fake_sdk_client(captured={}, content=None, tool_calls=None)

    content, calls = await client.chat_with_tools([{"role": "user", "content": "hola"}], tools=[])

    assert content == ""
    assert calls is None


async def test_chat_with_tools_requires_initialization():
    with pytest.raises(RuntimeError, match="not initialized"):
        await OpenAICompatClient().chat_with_tools([], [])


async def test_chat_with_tools_injects_images(monkeypatch, tmp_path):
    client = make_client(monkeypatch)
    captured = {}
    client._client = fake_sdk_client(captured, content="listo")
    image = tmp_path / "img.jpg"
    image.write_bytes(b"jpeg")

    content, calls = await client.chat_with_tools(
        [{"role": "user", "content": "mira"}], tools=[], images=[str(image)]
    )

    assert content == "listo"
    assert calls is None
    last = captured["messages"][-1]["content"]
    assert last[1]["type"] == "image_url"


# ---------------------------------------------------------------------------
# chat_stream_tokens / chat_vision
# ---------------------------------------------------------------------------


class FakeStream:
    def __init__(self, chunks):
        self.chunks = chunks

    def __aiter__(self):
        self._iter = iter(self.chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


async def test_chat_stream_tokens_yields_deltas(monkeypatch):
    client = make_client(monkeypatch)
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return FakeStream(
            [
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="Hola"))]),
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=" mundo"))]),
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))]),
                SimpleNamespace(choices=[]),
            ]
        )

    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )

    tokens = [t async for t in client.chat_stream_tokens([{"role": "user", "content": "x"}])]

    assert tokens == ["Hola", " mundo"]
    assert captured["stream"] is True


async def test_chat_stream_tokens_repeat_penalty(monkeypatch):
    monkeypatch.setattr(settings, "openai_reasoning_effort", "none")
    client = make_client(monkeypatch, reasoning_effort="none")
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return FakeStream([])

    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )

    _ = [t async for t in client.chat_stream_tokens([], repeat_penalty=1.1)]

    assert captured["extra_body"]["reasoning_effort"] == "none"
    assert captured["extra_body"]["options"]["repeat_penalty"] == 1.1


async def test_chat_stream_tokens_requires_initialization():
    with pytest.raises(RuntimeError, match="not initialized"):
        _ = [t async for t in OpenAICompatClient().chat_stream_tokens([])]


async def test_chat_vision_delegates_to_chat(monkeypatch, tmp_path):
    client = make_client(monkeypatch)
    captured = {}
    client._client = fake_sdk_client(captured, content="descripcion")
    image = tmp_path / "img.jpg"
    image.write_bytes(b"jpeg-bytes")

    result = await client.chat_vision([{"role": "user", "content": "mira"}], [str(image)])

    assert result == "descripcion"
    messages = captured["messages"]
    assert messages[-1]["role"] == "user"
    content = messages[-1]["content"]
    assert content[0] == {"type": "text", "text": "mira"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


# ---------------------------------------------------------------------------
# embed_texts / _encode_image / _inject_images
# ---------------------------------------------------------------------------


def test_embed_texts_returns_vectors(monkeypatch):
    monkeypatch.setattr(settings, "openai_embedding_model", "bge-m3")
    client = make_client(monkeypatch)
    captured = {}

    class FakeEmbeddings:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[0.1, 0.2]), SimpleNamespace(embedding=[0.3])]
            )

    client._embed_client = SimpleNamespace(embeddings=FakeEmbeddings())

    vectors = client.embed_texts(["a", "b"])

    assert vectors == [[0.1, 0.2], [0.3]]
    assert captured["model"] == "bge-m3"
    assert captured["input"] == ["a", "b"]


def test_embed_texts_falls_back_to_chat_model(monkeypatch):
    monkeypatch.setattr(settings, "openai_embedding_model", "")
    client = make_client(monkeypatch)
    captured = {}

    class FakeEmbeddings:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(data=[SimpleNamespace(embedding=[1.0])])

    client._embed_client = SimpleNamespace(embeddings=FakeEmbeddings())
    client.embed_texts(["x"])

    assert captured["model"] == "gpt-test"


def test_embed_texts_requires_initialization():
    with pytest.raises(RuntimeError, match="not initialized"):
        OpenAICompatClient().embed_texts(["a"])


def test_encode_image_base64(tmp_path):
    image = tmp_path / "img.png"
    image.write_bytes(b"abc")
    encoded = OpenAICompatClient()._encode_image(str(image))
    assert encoded == "YWJj"


def test_inject_images_keeps_non_user_tail():
    client = OpenAICompatClient()
    messages = [{"role": "system", "content": "hola"}]
    result = client._inject_images(messages, ["/tmp/x.jpg"])
    assert result == [{"role": "system", "content": "hola"}]


# ---------------------------------------------------------------------------
# factory
# ---------------------------------------------------------------------------


def test_factory_openai_with_whitespace(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "  OPENAI  ")
    assert create_ai_client().__class__.__name__ == "OpenAICompatClient"


def test_factory_unknown_provider_falls_back_to_local(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "otro-backend")
    assert create_ai_client().__class__.__name__ == "OllamaClient"


def test_factory_empty_provider_defaults_to_local(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "")
    assert create_ai_client().__class__.__name__ == "OllamaClient"
