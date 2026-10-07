"""Cobertura de ollama_client.py: circuit breaker, hot-swap, streaming y salud.

Sin red: se sustituyen httpx y el cliente OpenAI por falsos controlados.
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIError, APIStatusError, APITimeoutError, RateLimitError

from src.config import settings
from src.ollama_client import OllamaCircuitBreaker, OllamaClient, OllamaClientError


def _request() -> httpx.Request:
    return httpx.Request("POST", "http://ollama.test/v1")


def _api_status_error(status_code: int) -> APIStatusError:
    return APIStatusError(
        "err", response=httpx.Response(status_code, request=_request()), body=None
    )


class FakeStream:
    """Async iterator de chunks al estilo del SDK de OpenAI."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


def _chunk(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content))])


def _response(content: str = "hola", tool_calls=None, usage=True):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    resp = SimpleNamespace(choices=[SimpleNamespace(message=message)])
    if usage:
        resp.usage = SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3)
    else:
        resp.usage = None
    return resp


def _fake_sdk_client(create_fn):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create_fn)))


def _no_sleep(monkeypatch):
    async def _instant(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant)


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


def test_circuit_breaker_backoff_and_reset():
    cb = OllamaCircuitBreaker(max_failures=2, reset_timeout=60.0)
    assert cb.is_open("op") is False
    assert cb.get_backoff("op") == 1.0

    cb.record_failure("op")
    assert cb.get_backoff("op") == 2.0
    cb.record_failure("op")
    assert cb.get_backoff("op") == 4.0
    assert cb.is_open("op") is True

    cb.record_success("op")
    assert cb.is_open("op") is False
    assert cb.get_backoff("op") == 1.0


def test_circuit_breaker_backoff_caps_at_30(monkeypatch):
    cb = OllamaCircuitBreaker()
    monkeypatch.setattr(cb, "_backoff", {"op": 20.0})
    assert cb.get_backoff("op") == 30.0


def test_circuit_breaker_resets_after_timeout(monkeypatch):
    cb = OllamaCircuitBreaker(max_failures=1, reset_timeout=10.0)
    monkeypatch.setattr(cb, "_failures", {"op": 5})
    monkeypatch.setattr(cb, "_last_failure", {"op": time.time() - 3600})
    assert cb.is_open("op") is False
    assert cb._failures["op"] == 0


async def test_circuit_breaker_execute_success(monkeypatch):
    cb = OllamaCircuitBreaker()
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        return "ok"

    assert await cb.execute("op", factory) == "ok"
    assert calls["n"] == 1


async def test_circuit_breaker_retries_timeouts_then_fails(monkeypatch):
    _no_sleep(monkeypatch)
    cb = OllamaCircuitBreaker()
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        raise APITimeoutError(request=_request())

    with pytest.raises(OllamaClientError, match="no responde tras 3 intentos"):
        await cb.execute("op", factory)
    assert calls["n"] == 3


async def test_circuit_breaker_retries_server_errors(monkeypatch):
    _no_sleep(monkeypatch)
    cb = OllamaCircuitBreaker()
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        raise _api_status_error(503)

    with pytest.raises(OllamaClientError, match="no responde tras 2 intentos"):
        await cb.execute("op", factory, max_retries=2)
    assert calls["n"] == 2


async def test_circuit_breaker_client_error_fails_fast():
    cb = OllamaCircuitBreaker()

    async def factory():
        raise _api_status_error(400)

    with pytest.raises(OllamaClientError, match="Error del modelo"):
        await cb.execute("op", factory)


async def test_circuit_breaker_unexpected_error_wrapped():
    cb = OllamaCircuitBreaker()

    async def factory():
        raise ValueError("boom")

    with pytest.raises(OllamaClientError, match="Error inesperado"):
        await cb.execute("op", factory)


async def test_circuit_breaker_abierto_falla_rapido(monkeypatch):
    _no_sleep(monkeypatch)
    cb = OllamaCircuitBreaker(max_failures=1, reset_timeout=600.0)
    cb.record_failure("op")
    assert cb.is_open("op") is True

    async def factory():
        return "recuperado"

    # P2 (2026-10-07): con el breaker abierto no se espera ni se reintenta;
    # se falla rapido con un mensaje honesto y el reintento es automatico
    # cuando caduca el reset_timeout.
    with pytest.raises(OllamaClientError) as exc:
        await cb.execute("op", factory)
    assert "degradado" in str(exc.value)
    # Tambien expuesto para el streaming (chat_stream_tokens).
    with pytest.raises(OllamaClientError):
        cb.fail_if_open("op")


async def test_circuit_breaker_rate_limit_retried(monkeypatch):
    _no_sleep(monkeypatch)
    cb = OllamaCircuitBreaker()
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RateLimitError(
                "slow", response=httpx.Response(429, request=_request()), body=None
            )
        return "ok"

    assert await cb.execute("op", factory) == "ok"
    assert calls["n"] == 2


async def test_circuit_breaker_api_error_without_status():
    cb = OllamaCircuitBreaker()

    async def factory():
        raise APIError("m", request=_request(), body=None)

    with pytest.raises(OllamaClientError, match="Error del modelo"):
        await cb.execute("op", factory)


# ---------------------------------------------------------------------------
# Backend GPU / CPU
# ---------------------------------------------------------------------------


class FakeAsyncClient:
    """Sustituto de httpx.AsyncClient con respuestas fijas por URL."""

    def __init__(self, responses=None, fail=False, **_kwargs):
        self._responses = responses or {}
        self._fail = fail
        self.posts = []
        self.gets = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def _match(self, url):
        for key, resp in self._responses.items():
            if key in url:
                return resp
        raise httpx.ConnectError("unreachable")

    async def get(self, url, **_kwargs):
        self.gets.append(url)
        if self._fail:
            raise httpx.ConnectError("down")
        return self._match(url)

    async def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if self._fail:
            raise httpx.ConnectError("down")
        return self._match(url)


def _tags_response(names):
    return SimpleNamespace(
        status_code=200,
        raise_for_status=lambda: None,
        json=lambda: {"models": [{"name": n} for n in names]},
    )


def _gpu_client(monkeypatch, model="gemma4:12b"):
    monkeypatch.setattr(settings, "ollama_gpu_host", "http://gpu:11434")
    monkeypatch.setattr(settings, "ollama_model", model)
    client = OllamaClient()
    client._client = _fake_sdk_client(None)
    client._gpu_client = _fake_sdk_client(None)
    return client


def test_active_host_depends_on_backend(monkeypatch):
    client = _gpu_client(monkeypatch)
    client._active_backend = "cpu"
    assert client._active_host() == client.ollama_host
    client._active_backend = "gpu"
    assert client._active_host() == client.gpu_host

    monkeypatch.setattr(settings, "ollama_gpu_host", "")
    plain = OllamaClient()
    plain._client = _fake_sdk_client(None)
    assert plain._active_host() == plain.ollama_host


async def test_probe_gpu_without_gpu_client():
    assert await OllamaClient()._probe_gpu() is False


async def test_probe_gpu_model_present_and_missing(monkeypatch):
    client = _gpu_client(monkeypatch)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: FakeAsyncClient({"/api/tags": _tags_response(["gemma4:12b:latest"])}),
    )
    assert await client._probe_gpu() is True

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: FakeAsyncClient({"/api/tags": _tags_response(["otro"])})
    )
    assert await client._probe_gpu() is False

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: FakeAsyncClient(fail=True))
    assert await client._probe_gpu() is False


async def test_pick_backend_without_gpu_client():
    client = OllamaClient()
    client._client = _fake_sdk_client(None)
    backend, picked = await client._pick_backend()
    assert backend == "cpu"
    assert picked is client._client


async def test_pick_backend_switches_to_gpu(monkeypatch):
    client = _gpu_client(monkeypatch)
    probed = {"n": 0}

    async def fake_probe():
        probed["n"] += 1
        return True

    monkeypatch.setattr(client, "_probe_gpu", fake_probe)
    backend, picked = await client._pick_backend(force=True)
    assert backend == "gpu"
    assert picked is client._gpu_client
    assert probed["n"] == 1

    backend, _ = await client._pick_backend()
    assert backend == "gpu"
    assert probed["n"] == 1


async def test_pick_backend_falls_back_to_cpu_and_caches(monkeypatch):
    client = _gpu_client(monkeypatch)

    async def fake_probe():
        return False

    monkeypatch.setattr(client, "_probe_gpu", fake_probe)
    backend, picked = await client._pick_backend(force=True)
    assert backend == "cpu"
    assert picked is client._client

    backend, _ = await client._pick_backend()
    assert backend == "cpu"
    assert client._active_backend == "cpu"


def test_mark_gpu_down(monkeypatch):
    client = _gpu_client(monkeypatch)
    client._active_backend = "gpu"
    client._mark_gpu_down("timeout")
    assert client._active_backend == "cpu"
    assert client._last_probe > 0
    client._mark_gpu_down("otra")


# ---------------------------------------------------------------------------
# initialize / prewarm / unload / hot-swap
# ---------------------------------------------------------------------------


async def test_initialize_healthy_and_degraded(monkeypatch):
    monkeypatch.setattr(settings, "ollama_gpu_host", "")
    client = OllamaClient()
    checks = {"ok": 0, "warm": 0}

    async def fake_check():
        checks["ok"] += 1

    async def fake_warm():
        checks["warm"] += 1

    monkeypatch.setattr(client, "_check_model_available", fake_check)
    monkeypatch.setattr(client, "_prewarm_model", fake_warm)
    await client.initialize()
    assert client._ready is True
    assert checks == {"ok": 1, "warm": 1}
    await client.close()

    degraded = OllamaClient()

    async def broken_check():
        raise OllamaClientError("down")

    monkeypatch.setattr(degraded, "_check_model_available", broken_check)
    await degraded.initialize()
    assert degraded._ready is True
    assert degraded._client is not None


async def test_initialize_with_gpu_host(monkeypatch):
    client = _gpu_client(monkeypatch)

    async def fake_probe():
        return False

    monkeypatch.setattr(client, "_probe_gpu", fake_probe)

    async def fake_check():
        return None

    async def fake_warm():
        return None

    monkeypatch.setattr(client, "_check_model_available", fake_check)
    monkeypatch.setattr(client, "_prewarm_model", fake_warm)
    await client.initialize()
    assert client._gpu_client is not None
    assert client._active_backend == "cpu"


async def test_check_model_available_logs(monkeypatch):
    monkeypatch.setattr(settings, "ollama_gpu_host", "")
    monkeypatch.setattr(settings, "ollama_model", "modelo-a")
    monkeypatch.setattr(settings, "ollama_vision_model", "modelo-vision")
    client = OllamaClient()

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: FakeAsyncClient({"/api/tags": _tags_response(["modelo-a", "modelo-vision"])}),
    )
    await client._check_model_available()

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: FakeAsyncClient({"/api/tags": _tags_response(["otro"])})
    )
    await client._check_model_available()


async def test_prewarm_unload_and_hot_swap(monkeypatch):
    monkeypatch.setattr(settings, "ollama_gpu_host", "")
    client = OllamaClient()
    # El precalentado solo se hace en la torre GPU (en el Dell no se retiene RAM).
    client.gpu_host = "http://gpu.test"
    client._active_backend = "gpu"
    client._client = _fake_sdk_client(None)
    fake = FakeAsyncClient({"/api/generate": SimpleNamespace(raise_for_status=lambda: None)})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: fake)

    await client._prewarm_specific("modelo-a", "main")
    await client._prewarm_model()
    await client._prewarm_vision_model()
    assert len(fake.posts) == 3

    await client.unload_model("modelo-a")
    assert fake.posts[-1][1]["json"]["keep_alive"] == 0

    await client.hot_swap_to_vision()
    await client.hot_swap_to_text()
    assert len(fake.posts) >= 5

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: FakeAsyncClient(fail=True))
    await client.unload_model("modelo-a")
    await client._prewarm_specific("modelo-a", "main")


# ---------------------------------------------------------------------------
# chat / streaming / tools
# ---------------------------------------------------------------------------


async def test_chat_requires_initialization():
    with pytest.raises(OllamaClientError, match="not initialized"):
        await OllamaClient().chat([{"role": "user", "content": "hola"}])


async def test_chat_sync_path(monkeypatch):
    monkeypatch.setattr(settings, "ollama_gpu_host", "")
    monkeypatch.setattr(settings, "ollama_reasoning_effort", "none")
    client = OllamaClient()
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _response("respuesta")

    client._client = _fake_sdk_client(fake_create)
    result = await client.chat(
        [{"role": "user", "content": "hola"}], temperature=0.2, max_tokens=32
    )
    assert result == "respuesta"
    assert captured["stream"] is False
    assert captured["temperature"] == 0.2
    assert captured["max_tokens"] == 32
    assert captured["extra_body"]["options"]["num_ctx"] == 2048


async def test_chat_stream_path_joins_tokens(monkeypatch):
    monkeypatch.setattr(settings, "ollama_gpu_host", "")
    client = OllamaClient()

    async def fake_create(**kwargs):
        return FakeStream([_chunk("un "), _chunk("dos"), _chunk(None)])

    client._client = _fake_sdk_client(fake_create)
    result = await client.chat([{"role": "user", "content": "hola"}], stream=True)
    assert result == "un dos"


async def test_chat_wraps_unexpected_errors(monkeypatch):
    monkeypatch.setattr(settings, "ollama_gpu_host", "")
    client = OllamaClient()
    # El precalentado solo se hace en la torre GPU (en el Dell no se retiene RAM).
    client.gpu_host = "http://gpu.test"
    client._active_backend = "gpu"
    client._client = _fake_sdk_client(None)

    async def broken_execute(_op, _factory, **_kw):
        raise ValueError("boom")

    monkeypatch.setattr(client._cb, "execute", broken_execute)
    with pytest.raises(OllamaClientError, match="Error inesperado"):
        await client.chat([{"role": "user", "content": "hola"}])


async def test_chat_with_images_injects_multimodal(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ollama_gpu_host", "")
    client = OllamaClient()
    captured = {}
    img = tmp_path / "foto.png"
    img.write_bytes(b"png-bytes")

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _response("vi")

    client._client = _fake_sdk_client(fake_create)
    result = await client.chat([{"role": "user", "content": "mira"}], images=[str(img)])
    assert result == "vi"
    content = captured["messages"][-1]["content"]
    assert content[0] == {"type": "text", "text": "mira"}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_chat_stream_tokens_yields_and_requires_init(monkeypatch):
    with pytest.raises(OllamaClientError, match="not initialized"):
        async for _ in OllamaClient().chat_stream_tokens([]):
            pass

    monkeypatch.setattr(settings, "ollama_gpu_host", "")
    client = OllamaClient()

    async def fake_create(**kwargs):
        return FakeStream([_chunk("hola"), SimpleNamespace(choices=[]), _chunk(" mundo")])

    client._client = _fake_sdk_client(fake_create)
    tokens = [t async for t in client.chat_stream_tokens([{"role": "user", "content": "x"}])]
    assert tokens == ["hola", " mundo"]


async def test_chat_stream_tokens_error_wrapped(monkeypatch):
    monkeypatch.setattr(settings, "ollama_gpu_host", "")
    client = OllamaClient()

    async def fake_create(**kwargs):
        raise RuntimeError("corte")

    client._client = _fake_sdk_client(fake_create)
    with pytest.raises(OllamaClientError, match="Error streaming"):
        async for _ in client.chat_stream_tokens([{"role": "user", "content": "x"}]):
            pass


async def test_next_chunk_timeout(monkeypatch):
    client = OllamaClient()

    async def slow_anext():
        await asyncio.sleep(30)

    stream = SimpleNamespace(__anext__=slow_anext)
    with pytest.raises(OllamaClientError, match="dejo de responder"):
        await client._next_chunk(stream, timeout=0.05)


async def test_chat_with_tools_returns_calls(monkeypatch):
    monkeypatch.setattr(settings, "ollama_gpu_host", "")
    client = OllamaClient()
    captured = {}
    tool_call = SimpleNamespace(
        id="call-1",
        type="function",
        function=SimpleNamespace(name="get_weather", arguments='{"city": "CDMX"}'),
    )

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _response("", tool_calls=[tool_call])

    client._client = _fake_sdk_client(fake_create)
    tools = [{"type": "function", "function": {"name": "get_weather"}}]
    content, calls = await client.chat_with_tools([{"role": "user", "content": "clima"}], tools)
    assert content == ""
    assert calls == [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city": "CDMX"}'},
        }
    ]
    assert captured["tools"] == tools
    assert captured["extra_body"]["options"]["num_ctx"] == 4096


async def test_chat_with_tools_no_calls_and_errors(monkeypatch):
    monkeypatch.setattr(settings, "ollama_gpu_host", "")
    client = OllamaClient()

    async def fake_create(**kwargs):
        return _response("texto", tool_calls=None)

    client._client = _fake_sdk_client(fake_create)
    content, calls = await client.chat_with_tools([{"role": "user", "content": "x"}], [])
    assert content == "texto"
    assert calls is None

    with pytest.raises(OllamaClientError, match="not initialized"):
        await OllamaClient().chat_with_tools([], [])

    async def broken_execute(_op, _factory, **_kw):
        raise ValueError("boom")

    monkeypatch.setattr(client._cb, "execute", broken_execute)
    with pytest.raises(OllamaClientError, match="Error inesperado"):
        await client.chat_with_tools([{"role": "user", "content": "x"}], [])


async def test_chat_vision_connection_error_rebuilds_client(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ollama_gpu_host", "")
    monkeypatch.setattr(settings, "ollama_model", "chat-model")
    monkeypatch.setattr(settings, "ollama_vision_model", "vision-model")
    client = OllamaClient()
    swaps = []

    async def fake_vision():
        swaps.append("vision")

    async def fake_text():
        swaps.append("text")

    monkeypatch.setattr(client, "hot_swap_to_vision", fake_vision)
    monkeypatch.setattr(client, "hot_swap_to_text", fake_text)

    async def boom(**kwargs):
        raise httpx.ConnectError("down")

    client._client = _fake_sdk_client(boom)
    img = tmp_path / "i.png"
    img.write_bytes(b"x")

    with pytest.raises(OllamaClientError, match="Error de conexion en vision"):
        await client.chat_vision([{"role": "user", "content": "mira"}], [str(img)])
    assert swaps == ["vision", "text"]
    assert client._client is not None


async def test_chat_vision_generic_error(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ollama_gpu_host", "")
    monkeypatch.setattr(settings, "ollama_model", "chat-model")
    monkeypatch.setattr(settings, "ollama_vision_model", "vision-model")
    client = OllamaClient()

    async def fake_swap():
        return None

    monkeypatch.setattr(client, "hot_swap_to_vision", fake_swap)
    monkeypatch.setattr(client, "hot_swap_to_text", fake_swap)

    async def boom(**kwargs):
        raise ValueError("algo raro")

    client._client = _fake_sdk_client(boom)
    img = tmp_path / "i.png"
    img.write_bytes(b"x")

    with pytest.raises(OllamaClientError, match="Error inesperado"):
        await client.chat_vision([{"role": "user", "content": "mira"}], [str(img)])


# ---------------------------------------------------------------------------
# _create_completion: fallback GPU -> CPU y timeouts
# ---------------------------------------------------------------------------


async def test_create_completion_gpu_timeout_falls_back_to_cpu(monkeypatch):
    client = _gpu_client(monkeypatch)
    client.request_timeout = 0.05

    async def slow_gpu(**kwargs):
        await asyncio.sleep(30)

    async def fast_cpu(**kwargs):
        return _response("cpu")

    client._gpu_client = _fake_sdk_client(slow_gpu)
    client._client = _fake_sdk_client(fast_cpu)

    async def gpu_backend():
        return "gpu", client._gpu_client

    monkeypatch.setattr(client, "_pick_backend", gpu_backend)
    result = await client._create_completion(model="m", messages=[])
    assert result.choices[0].message.content == "cpu"
    assert client._active_backend == "cpu"


async def test_create_completion_gpu_connection_error_falls_back(monkeypatch):
    client = _gpu_client(monkeypatch)

    async def dead_gpu(**kwargs):
        raise httpx.ConnectError("down")

    async def fast_cpu(**kwargs):
        return _response("cpu")

    client._gpu_client = _fake_sdk_client(dead_gpu)
    client._client = _fake_sdk_client(fast_cpu)

    async def gpu_backend():
        return "gpu", client._gpu_client

    monkeypatch.setattr(client, "_pick_backend", gpu_backend)
    result = await client._create_completion(model="m", messages=[])
    assert result.choices[0].message.content == "cpu"


async def test_create_completion_gpu_unexpected_error_reraise(monkeypatch):
    client = _gpu_client(monkeypatch)

    async def bad_gpu(**kwargs):
        raise ValueError("logic")

    client._gpu_client = _fake_sdk_client(bad_gpu)

    async def gpu_backend():
        return "gpu", client._gpu_client

    monkeypatch.setattr(client, "_pick_backend", gpu_backend)
    with pytest.raises(ValueError, match="logic"):
        await client._create_completion(model="m", messages=[])


async def test_create_completion_cpu_timeout(monkeypatch):
    monkeypatch.setattr(settings, "ollama_gpu_host", "")
    client = OllamaClient()
    client.request_timeout = 0.05

    async def slow_cpu(**kwargs):
        await asyncio.sleep(30)

    client._client = _fake_sdk_client(slow_cpu)
    with pytest.raises(OllamaClientError, match="no respondio"):
        await client._create_completion(model="m", messages=[])


async def test_create_on_cpu_timeout_direct(monkeypatch):
    monkeypatch.setattr(settings, "ollama_gpu_host", "")
    client = OllamaClient()
    client.request_timeout = 0.05

    async def slow_cpu(**kwargs):
        await asyncio.sleep(30)

    client._client = _fake_sdk_client(slow_cpu)
    with pytest.raises(OllamaClientError, match="no respondio"):
        await client._create_on_cpu({"model": "m"})


def test_is_connection_error_classification():
    assert OllamaClient._is_connection_error(APITimeoutError(request=_request()))
    assert OllamaClient._is_connection_error(httpx.ConnectError("x"))
    assert OllamaClient._is_connection_error(httpx.ConnectTimeout("x"))
    assert OllamaClient._is_connection_error(httpx.ReadTimeout("x"))
    assert OllamaClient._is_connection_error(httpx.RemoteProtocolError("x"))
    assert not OllamaClient._is_connection_error(ValueError("x"))


# ---------------------------------------------------------------------------
# embeddings
# ---------------------------------------------------------------------------


def test_embed_texts_uses_native_endpoint(monkeypatch):
    client = OllamaClient()
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured.update({"url": url, "json": json, "timeout": timeout})
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"embeddings": [[0.5]]})

    monkeypatch.setattr(httpx, "post", fake_post)
    assert client.embed_texts(["hola"]) == [[0.5]]
    assert captured["url"].endswith("/api/embed")
    assert captured["json"]["input"] == ["hola"]


async def test_generate_embedding_paths(monkeypatch):
    monkeypatch.setattr(settings, "ollama_gpu_host", "")
    with pytest.raises(OllamaClientError, match="not initialized"):
        await OllamaClient().generate_embedding("hola")

    client = OllamaClient()

    async def fake_create(**kwargs):
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2])])

    client._client = SimpleNamespace(embeddings=SimpleNamespace(create=fake_create))
    assert await client.generate_embedding("hola") == [0.1, 0.2]

    async def broken(**kwargs):
        raise ValueError("embed down")

    client._client = SimpleNamespace(embeddings=SimpleNamespace(create=broken))
    with pytest.raises(OllamaClientError, match="Error generando embedding"):
        await client.generate_embedding("hola")


# ---------------------------------------------------------------------------
# check_health
# ---------------------------------------------------------------------------


class FakeModelList:
    def __init__(self, ids):
        self.data = [SimpleNamespace(id=i) for i in ids]


async def test_check_health_uninitialized_and_unreachable(monkeypatch):
    monkeypatch.setattr(settings, "ollama_gpu_host", "")
    assert (await OllamaClient().check_health()) == {
        "status": "uninitialized",
        "provider": "ollama",
    }

    client = OllamaClient()

    async def broken_list():
        raise RuntimeError("down")

    client._client = SimpleNamespace(models=SimpleNamespace(list=broken_list))
    health = await client.check_health()
    assert health["status"] == "unhealthy"
    assert health["provider"] == "ollama"
    assert "unreachable" in health["detail"]


async def test_check_health_model_not_pulled(monkeypatch):
    monkeypatch.setattr(settings, "ollama_gpu_host", "")
    monkeypatch.setattr(settings, "ollama_model", "modelo-a")
    client = OllamaClient()

    async def fake_list():
        return FakeModelList(["otro"])

    client._client = SimpleNamespace(models=SimpleNamespace(list=fake_list))
    health = await client.check_health()
    assert health["status"] == "unhealthy"
    assert health["model_available"] is False
    assert "not pulled" in health["detail"]


async def test_check_health_ok_degraded_and_loaded_unknown(monkeypatch):
    monkeypatch.setattr(settings, "ollama_gpu_host", "")
    monkeypatch.setattr(settings, "ollama_model", "modelo-a")
    client = OllamaClient()

    async def fake_list():
        return FakeModelList(["modelo-a"])

    client._client = SimpleNamespace(models=SimpleNamespace(list=fake_list))

    async def loaded():
        return ["modelo-a"]

    monkeypatch.setattr(client, "_loaded_models", loaded)
    health = await client.check_health()
    assert health["status"] == "ok"
    assert health["model_loaded"] is True
    assert health["latency_ms"] >= 0

    async def not_loaded():
        return []

    monkeypatch.setattr(client, "_loaded_models", not_loaded)
    health = await client.check_health()
    assert health["status"] == "degraded"
    assert "load on first request" in health["detail"]

    async def unknown():
        return None

    monkeypatch.setattr(client, "_loaded_models", unknown)
    health = await client.check_health()
    assert health["status"] == "ok"
    assert "model_loaded" not in health


async def test_check_health_reports_gpu_fields(monkeypatch):
    client = _gpu_client(monkeypatch, model="modelo-a")

    async def fake_list():
        return FakeModelList(["modelo-a"])

    client._client = SimpleNamespace(models=SimpleNamespace(list=fake_list))
    client._gpu_client = client._client

    async def gpu_backend():
        return "gpu", client._client

    monkeypatch.setattr(client, "_pick_backend", gpu_backend)

    async def loaded():
        return ["modelo-a"]

    monkeypatch.setattr(client, "_loaded_models", loaded)
    health = await client.check_health()
    assert health["gpu_host"] == client.gpu_host
    assert health["gpu_available"] is True
    assert health["backend"] == "gpu"


async def test_loaded_models_none_on_failure(monkeypatch):
    monkeypatch.setattr(settings, "ollama_gpu_host", "")
    client = OllamaClient()
    # El precalentado solo se hace en la torre GPU (en el Dell no se retiene RAM).
    client.gpu_host = "http://gpu.test"
    client._active_backend = "gpu"
    client._client = _fake_sdk_client(None)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: FakeAsyncClient(fail=True))
    assert await client._loaded_models() is None

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: FakeAsyncClient(
            {
                "/api/ps": SimpleNamespace(
                    raise_for_status=lambda: None, json=lambda: {"models": [{"name": "modelo-a"}]}
                )
            }
        ),
    )
    assert await client._loaded_models() == ["modelo-a"]


async def test_close_without_client():
    client = OllamaClient()
    await client.close()
    client._client = SimpleNamespace(close=AsyncMock())
    await client.close()
    assert client._ready is False
