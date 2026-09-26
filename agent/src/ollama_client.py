import asyncio
import base64
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import httpx
from openai import APIConnectionError, APIError, APITimeoutError, AsyncOpenAI, RateLimitError
from openai.types.chat import ChatCompletion

from src.config import settings
from src.logger import logger

T = TypeVar("T")


class OllamaClientError(Exception):
    pass


class OllamaCircuitBreaker:
    def __init__(self, max_failures: int = 5, reset_timeout: float = 60.0):
        self._max_failures = max_failures
        self._reset_timeout = reset_timeout
        self._failures: dict[str, int] = {}
        self._last_failure: dict[str, float] = {}
        self._backoff: dict[str, float] = {}

    def is_open(self, operation: str = "default") -> bool:
        failures = self._failures.get(operation, 0)
        if failures < self._max_failures:
            return False
        last_fail = self._last_failure.get(operation, 0.0)
        if time.time() - last_fail > self._reset_timeout:
            self._failures[operation] = 0
            self._backoff[operation] = 0.0
            return False
        return True

    def get_backoff(self, operation: str = "default") -> float:
        current = self._backoff.get(operation, 0.0)
        if current == 0.0:
            return 1.0
        return min(current * 2, 30.0)

    def record_success(self, operation: str = "default") -> None:
        self._failures[operation] = 0
        self._backoff[operation] = 0.0

    def record_failure(self, operation: str = "default") -> int:
        self._failures[operation] = self._failures.get(operation, 0) + 1
        self._last_failure[operation] = time.time()
        self._backoff[operation] = self.get_backoff(operation)
        return self._failures[operation]

    async def execute(
        self, operation: str, coro_factory: Callable[[], Awaitable[T]], max_retries: int = 3
    ) -> T:
        if self.is_open(operation):
            wait = self.get_backoff(operation)
            logger.warning(
                "Circuit breaker open for '%s'. Waiting %.1fs before retry.",
                operation,
                wait,
            )
            await asyncio.sleep(wait)
        last_exc = None
        for attempt in range(max_retries):
            try:
                result = await coro_factory()
                self.record_success(operation)
                return result
            except (APITimeoutError, RateLimitError) as e:
                last_exc = e
                self.record_failure(operation)
                if attempt < max_retries - 1:
                    backoff = self.get_backoff(operation)
                    logger.warning(
                        "Ollama %s (attempt %d/%d): %s. Backoff %.1fs.",
                        operation,
                        attempt + 1,
                        max_retries,
                        e,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
            except APIError as e:
                status_code = getattr(e, "status_code", None)
                if status_code and 500 <= status_code < 600:
                    last_exc = e
                    self.record_failure(operation)
                    if attempt < max_retries - 1:
                        backoff = self.get_backoff(operation)
                        logger.warning(
                            "Ollama server error %s (attempt %d/%d): %s. Backoff %.1fs.",
                            status_code,
                            attempt + 1,
                            max_retries,
                            e,
                            backoff,
                        )
                        await asyncio.sleep(backoff)
                else:
                    self.record_failure(operation)
                    raise OllamaClientError("Error del modelo: %s" % e)
            except Exception as e:
                self.record_failure(operation)
                raise OllamaClientError("Error inesperado: %s" % e)
        raise OllamaClientError("Ollama no responde tras %d intentos: %s" % (max_retries, last_exc))


class OllamaClient:
    def __init__(self):
        self._client: AsyncOpenAI | None = None
        self._gpu_client: AsyncOpenAI | None = None
        self.base_url: str = f"{settings.ollama_host.rstrip('/')}/v1"
        self.ollama_host: str = settings.ollama_host.rstrip("/")
        self.gpu_host: str = settings.ollama_gpu_host.strip().rstrip("/")
        self.gpu_probe_interval: int = settings.ollama_gpu_probe_interval
        self._active_backend: str = "cpu"
        self._last_probe: float = 0.0
        self.model: str = settings.ollama_model
        self.vision_model: str = settings.ollama_vision_model
        self.reasoning_effort: str = settings.ollama_reasoning_effort
        self.num_thread: int = settings.ollama_num_thread
        self.request_timeout: int = settings.ollama_request_timeout
        self.temperature: float = settings.llm_temperature
        self.max_tokens: int = settings.llm_max_tokens
        self._ready: bool = False
        self._cb = OllamaCircuitBreaker()

    def _active_host(self) -> str:
        if self._gpu_client and self._active_backend == "gpu":
            return self.gpu_host
        return self.ollama_host

    async def _probe_gpu(self) -> bool:
        """Torre encendida y con el modelo? (prueba corta, task 3.8)."""
        if not self._gpu_client:
            return False
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(4.0, connect=3.0)) as hc:
                resp = await hc.get("%s/api/tags" % self.gpu_host)
                resp.raise_for_status()
                names = [m.get("name", "") for m in resp.json().get("models", [])]
        except Exception:
            return False
        return any(n == self.model or n.startswith(self.model + ":") for n in names)

    async def _pick_backend(self, force: bool = False) -> tuple[str, Any]:
        """Elige GPU (torre) o CPU (Dell) con cache de sonda (task 3.8)."""
        if not self._gpu_client:
            return "cpu", self._client
        now = time.time()
        if not force and (now - self._last_probe) < self.gpu_probe_interval:
            if self._active_backend == "gpu":
                return "gpu", self._gpu_client
            return "cpu", self._client
        self._last_probe = now
        gpu_ok = await self._probe_gpu()
        new_backend = "gpu" if gpu_ok else "cpu"
        if new_backend != self._active_backend:
            if new_backend == "gpu":
                logger.info("LLM backend: torre GPU disponible (%s); usando GPU", self.gpu_host)
            else:
                logger.info("LLM backend: torre GPU no disponible; usando Dell (CPU)")
        self._active_backend = new_backend
        if gpu_ok:
            return "gpu", self._gpu_client
        return "cpu", self._client

    def _mark_gpu_down(self, reason: str) -> None:
        if self._active_backend == "gpu":
            logger.warning("LLM backend: GPU no responde (%s); cambiando al Dell (CPU)", reason)
        self._active_backend = "cpu"
        self._last_probe = time.time()

    async def initialize(self) -> None:
        self._client = AsyncOpenAI(
            base_url=self.base_url,
            api_key="ollama",
            timeout=httpx.Timeout(1200.0, connect=15.0),
            max_retries=2,
        )
        if self.gpu_host:
            self._gpu_client = AsyncOpenAI(
                base_url=f"{self.gpu_host}/v1",
                api_key="ollama",
                timeout=httpx.Timeout(1200.0, connect=15.0),
                max_retries=2,
            )
            await self._pick_backend(force=True)
        # The LLM may be down when the agent boots (e.g. the Dell is off):
        # startup must NOT block or fail on it. The gateway starts in
        # "not_ready" and recovers automatically when the backend returns
        # (task 3.4).
        backend_ok = True
        try:
            await self._cb.execute("health", self._check_model_available, max_retries=1)
        except Exception as e:
            backend_ok = False
            logger.warning("Ollama not reachable at startup (%s); starting in degraded mode", e)
        if backend_ok:
            await self._prewarm_model()
        else:
            logger.info("Skipping model pre-warm (backend unreachable)")
        self._ready = True
        logger.info(
            "Ollama client initialized: model=%s vision=%s host=%s backend=%s",
            self.model,
            self.vision_model,
            self.base_url,
            self._active_backend,
        )

    async def _check_model_available(self) -> None:
        # Short timeouts: this runs at startup and must fail fast when the
        # backend is down (native /api/tags instead of the SDK's long timeout).
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as hc:
            resp = await hc.get("%s/api/tags" % self._active_host())
            resp.raise_for_status()
            model_ids = [m.get("name", "") for m in resp.json().get("models", [])]
        if self.model in model_ids:
            logger.info("Model %s is available", self.model)
        else:
            logger.warning(
                "Model %s not found. Available: %s. Run: docker exec ollama-service ollama pull %s",
                self.model,
                model_ids,
                self.model,
            )
        if self.vision_model in model_ids:
            logger.info("Vision model %s is available", self.vision_model)
        else:
            logger.warning(
                "Vision model %s not found. Available: %s.",
                self.vision_model,
                model_ids,
            )

    async def _prewarm_model(self) -> None:
        await self._prewarm_specific(self.model, "main")

    async def _prewarm_vision_model(self) -> None:
        await self._prewarm_specific(self.vision_model, "vision")

    async def _prewarm_specific(self, model_name: str, label: str) -> None:
        try:
            import httpx

            logger.info("Pre-warming %s model '%s' (forcing load into RAM)...", label, model_name)
            async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0)) as hc:
                resp = await hc.post(
                    "%s/api/generate" % self._active_host(),
                    json={
                        "model": model_name,
                        "prompt": "hello",
                        "stream": False,
                        "think": False,
                        "keep_alive": -1,
                        "options": {"num_predict": 1, "temperature": 0.1},
                    },
                )
                resp.raise_for_status()
            logger.info("%s model '%s' pre-warmed and ready", label.capitalize(), model_name)
        except Exception as e:
            logger.warning(
                "%s model pre-warm failed (will load on first request): %s", label.capitalize(), e
            )

    async def unload_model(self, model_name: str) -> None:
        try:
            import httpx

            logger.info("[HOT-SWAP] Unloading model '%s' from RAM (keep_alive=0)...", model_name)
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as hc:
                resp = await hc.post(
                    "%s/api/generate" % self._active_host(),
                    json={
                        "model": model_name,
                        "prompt": "",
                        "stream": False,
                        "keep_alive": 0,
                    },
                )
                resp.raise_for_status()
            logger.info("[HOT-SWAP] Model '%s' unloaded from RAM", model_name)
        except Exception as e:
            logger.warning("[HOT-SWAP] Failed to unload '%s': %s", model_name, e)

    async def hot_swap_to_vision(self) -> None:
        logger.info(
            "[HOT-SWAP] Swapping %s -> %s (freeing RAM for vision model)...",
            self.model,
            self.vision_model,
        )
        await self.unload_model(self.model)
        import gc as _gc

        _gc.collect()

    async def hot_swap_to_text(self) -> None:
        logger.info(
            "[HOT-SWAP] Reloading %s after vision (%s unloaded via keep_alive=0)...",
            self.model,
            self.vision_model,
        )
        import gc as _gc

        _gc.collect()
        await self._prewarm_specific(self.model, "main")

    def _encode_image(self, image_path: str) -> str:
        path = Path(image_path)
        if not path.exists():
            raise OllamaClientError("Image not found: %s" % image_path)
        raw = path.read_bytes()
        return base64.b64encode(raw).decode("utf-8")

    def _build_multimodal_content(self, text: str, images: list[str]) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = []
        if text:
            content.append({"type": "text", "text": text})
        for img in images:
            b64 = self._encode_image(img)
            mime = "image/jpeg"
            if img.lower().endswith(".png"):
                mime = "image/png"
            elif img.lower().endswith(".webp"):
                mime = "image/webp"
            elif img.lower().endswith(".gif"):
                mime = "image/gif"
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": "data:%s;base64,%s" % (mime, b64)},
                }
            )
        return content

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
        images: list[str] | None = None,
    ) -> str:
        if not self._client:
            raise OllamaClientError("Client not initialized. Call initialize() first.")

        if images:
            messages = self._inject_images(messages, images)

        params = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "stream": stream,
        }

        async def _do_chat():
            if stream:
                return await self._chat_stream(params)
            return await self._chat_sync(params)

        try:
            return await self._cb.execute("chat", _do_chat)
        except OllamaClientError:
            raise
        except Exception as e:
            logger.exception("Unexpected error in Ollama chat")
            raise OllamaClientError(f"Error inesperado: {e}")

    async def chat_stream_tokens(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        """Yield individual tokens as they are generated by the LLM."""
        if not self._client:
            raise OllamaClientError("Client not initialized. Call initialize() first.")

        params = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "stream": True,
        }

        try:
            stream = await self._create_completion(
                **params,
                extra_body=self._ollama_extra_body(2048),
            )
            while True:
                try:
                    chunk = await self._next_chunk(stream)
                except StopAsyncIteration:
                    break
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        yield delta.content
        except Exception as e:
            logger.exception("Error in chat_stream_tokens")
            raise OllamaClientError(f"Error streaming: {e}")

    def _inject_images(
        self, messages: list[dict[str, Any]], images: list[str]
    ) -> list[dict[str, Any]]:
        result = list(messages)
        if result and result[-1]["role"] == "user":
            last = dict(result[-1])
            last["content"] = self._build_multimodal_content(last.get("content", ""), images)
            result[-1] = last
        else:
            result.append(
                {
                    "role": "user",
                    "content": self._build_multimodal_content("Analiza esta imagen.", images),
                }
            )
        return result

    @staticmethod
    def _is_connection_error(exc: BaseException) -> bool:
        return isinstance(
            exc,
            (
                APIConnectionError,
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.RemoteProtocolError,
            ),
        )

    async def _create_on_cpu(self, kwargs: dict[str, Any]) -> Any:
        try:
            return await asyncio.wait_for(
                self._client.chat.completions.create(**kwargs),
                timeout=float(self.request_timeout),
            )
        except TimeoutError as e:
            raise OllamaClientError(
                "El modelo no respondio en %ds (posible corte de red)" % self.request_timeout
            ) from e

    async def _create_completion(self, **kwargs: Any) -> Any:
        """Chat completion con backend preferido (GPU torre) y respaldo (Dell).

        - Elige backend con sonda cacheada (`OLLAMA_GPU_PROBE_INTERVAL`).
        - Si la GPU falla a mitad, reintenta una vez en el Dell (task 3.8).
        - Techo de tiempo configurable (`OLLAMA_REQUEST_TIMEOUT`).
        """
        backend, client = await self._pick_backend()
        try:
            return await asyncio.wait_for(
                client.chat.completions.create(**kwargs),
                timeout=float(self.request_timeout),
            )
        except TimeoutError as e:
            if backend == "gpu":
                self._mark_gpu_down("timeout")
                return await self._create_on_cpu(kwargs)
            raise OllamaClientError(
                "El modelo no respondio en %ds (posible corte de red)" % self.request_timeout
            ) from e
        except Exception as e:
            if backend == "gpu" and self._is_connection_error(e):
                self._mark_gpu_down(type(e).__name__)
                return await self._create_on_cpu(kwargs)
            raise

    async def _next_chunk(self, stream: Any, timeout: float = 120.0) -> Any:
        """Next streaming chunk with an inter-chunk ceiling (dead peer)."""
        try:
            return await asyncio.wait_for(stream.__anext__(), timeout=timeout)
        except TimeoutError as e:
            raise OllamaClientError(
                "El modelo dejo de responder a mitad de la generacion (corte de red)"
            ) from e

    def _ollama_extra_body(self, num_ctx: int, keep_alive: Any = -1) -> dict[str, Any]:
        """Native Ollama fields forwarded through the OpenAI-compatible layer.

        `reasoning_effort=none` disables "thinking" on models that reason by
        default (e.g. gemma4): otherwise the reasoning consumes `max_tokens` and
        the visible `content` arrives empty. Empty setting omits the field.
        """
        options: dict[str, Any] = {"num_ctx": num_ctx}
        if self.num_thread > 0:
            options["num_thread"] = self.num_thread
        extra: dict[str, Any] = {"keep_alive": keep_alive, "options": options}
        if self.reasoning_effort:
            extra["reasoning_effort"] = self.reasoning_effort
        return extra

    async def _chat_sync(self, params: dict[str, Any]) -> str:
        response: ChatCompletion = await self._create_completion(
            **params,
            extra_body=self._ollama_extra_body(2048),
        )
        content = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        if usage:
            logger.debug(
                "Ollama usage: prompt=%d completion=%d total=%d",
                usage.prompt_tokens,
                usage.completion_tokens,
                usage.total_tokens,
            )
        return content

    async def _chat_stream(self, params: dict[str, Any]) -> str:
        full_content: list[str] = []
        stream_response = await self._create_completion(
            **params,
            extra_body=self._ollama_extra_body(2048),
        )
        while True:
            try:
                chunk = await self._next_chunk(stream_response)
            except StopAsyncIteration:
                break
            if chunk.choices and len(chunk.choices) > 0:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    full_content.append(delta.content)
        return "".join(full_content)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Batch embeddings via Ollama's native /api/embed (sync, for Chroma)."""
        import httpx

        payload = {"model": settings.embedding_model, "input": texts}
        response = httpx.post(
            "%s/api/embed" % settings.ollama_host.rstrip("/"),
            json=payload,
            timeout=600.0,
        )
        response.raise_for_status()
        return response.json().get("embeddings", [])

    async def generate_embedding(self, text: str) -> list[float]:
        if not self._client:
            raise OllamaClientError("Client not initialized.")

        async def _do_embed():
            response = await self._client.embeddings.create(
                model=self.model,
                input=text,
            )
            return response.data[0].embedding

        try:
            return await self._cb.execute("embedding", _do_embed)
        except Exception as e:
            logger.error("Embedding generation failed: %s", e)
            raise OllamaClientError(f"Error generando embedding: {e}")

    async def chat_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        images: list[str] | None = None,
    ) -> tuple:
        if not self._client:
            raise OllamaClientError("Client not initialized. Call initialize() first.")

        if images:
            messages = self._inject_images(messages, images)

        params = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "stream": False,
        }

        import time as _time

        _ts_b2 = _time.strftime("%H:%M:%S") + ".%03d" % int((_time.time() % 1) * 1000)
        logger.info(
            "[TELEMETRY B2] chat_with_tools -> API Ollama [%s] model=%s msgs=%d tools=%d",
            _ts_b2,
            self.model,
            len(messages),
            len(tools),
        )
        _t_api_start = _time.time()

        async def _do_tool_chat():
            response = await self._create_completion(
                **params,
                extra_body=self._ollama_extra_body(4096),
            )
            _elapsed_api = _time.time() - _t_api_start
            _ts_d = _time.strftime("%H:%M:%S") + ".%03d" % int((_time.time() % 1) * 1000)
            logger.info(
                "[TELEMETRY D] Respuesta cruda de Ollama tras %.1f segundos [%s]",
                _elapsed_api,
                _ts_d,
            )
            message = response.choices[0].message
            content = message.content or ""
            tool_calls = None
            if message.tool_calls and len(message.tool_calls) > 0:
                tool_calls = []
                for tc in message.tool_calls:
                    tool_calls.append(
                        {
                            "id": tc.id,
                            "type": tc.type,
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                    )
            return content, tool_calls

        try:
            return await self._cb.execute("chat_with_tools", _do_tool_chat)
        except OllamaClientError:
            raise
        except Exception as e:
            logger.exception("Unexpected error in Ollama chat_with_tools")
            raise OllamaClientError(f"Error inesperado: {e}")

    async def chat_vision(
        self,
        messages: list[dict[str, str]],
        images: list[str],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        if not self._client:
            raise OllamaClientError("Client not initialized. Call initialize() first.")

        # If the vision model IS the chat model (e.g. gemma4:12b, which has
        # vision), swapping would unload and reload the same weights for every
        # image. Skip the swap and keep the model resident (keep_alive=-1).
        same_model = self.vision_model == self.model
        if not same_model:
            await self.hot_swap_to_vision()

        messages = self._inject_images(messages, images)

        params = {
            "model": self.vision_model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "stream": False,
        }

        try:
            response = await self._create_completion(
                **params,
                extra_body=self._ollama_extra_body(2048, keep_alive=-1 if same_model else 0),
            )
            content = response.choices[0].message.content or ""
            return content
        except (
            TimeoutError,
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.RemoteProtocolError,
        ) as e:
            logger.warning(
                "[VISION] Error de red (%s), reconstruyendo cliente HTTPX...", type(e).__name__
            )
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = AsyncOpenAI(
                base_url=self.base_url,
                api_key="ollama",
                timeout=httpx.Timeout(120.0, connect=30.0),
                max_retries=1,
            )
            raise OllamaClientError(f"Error de conexion en vision: {e}")
        except Exception as e:
            logger.exception("Unexpected error in Ollama chat_vision")
            raise OllamaClientError(f"Error inesperado: {e}")
        finally:
            if not same_model:
                await self.hot_swap_to_text()

    async def _loaded_models(self) -> list[str] | None:
        """Loaded models via Ollama's native `/api/ps`; None if unavailable."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as hc:
                resp = await hc.get("%s/api/ps" % self._active_host())
                resp.raise_for_status()
                return [m.get("name", "") for m in resp.json().get("models", [])]
        except Exception:
            return None

    async def check_health(self) -> dict[str, Any]:
        """Generic provider health (task 3.3), used by the gateway `/ready`.

        - `ok`: reachable and the chat model is available and loaded.
        - `degraded`: reachable and available, but not loaded yet (the first
          request will load it, which on CPU can take ~15 s).
        - `unhealthy`: unreachable or the chat model is not pulled.
        Fast failure: 10 s ceiling so a dead node does not stall readiness.
        """
        if not self._client:
            return {"status": "uninitialized", "provider": "ollama"}
        backend, client = await self._pick_backend()
        start = time.time()
        try:
            models = await asyncio.wait_for(client.models.list(), timeout=10.0)
        except Exception as e:
            return {
                "status": "unhealthy",
                "provider": "ollama",
                "backend": backend,
                "detail": "AI backend unreachable: %s" % str(e)[:150],
            }
        latency_ms = round((time.time() - start) * 1000)
        ids = [m.id for m in (models.data or [])]
        available = any(i == self.model or i.startswith(self.model + ":") for i in ids)
        result: dict[str, Any] = {
            "status": "ok",
            "provider": "ollama",
            "backend": backend,
            "model": self.model,
            "latency_ms": latency_ms,
            "model_available": available,
        }
        if self.gpu_host:
            result["gpu_host"] = self.gpu_host
            result["gpu_available"] = backend == "gpu"
        if not available:
            result["status"] = "unhealthy"
            result["detail"] = "chat model '%s' not pulled" % self.model
            return result
        loaded = await self._loaded_models()
        if loaded is not None:
            result["model_loaded"] = any(
                i == self.model or i.startswith(self.model + ":") for i in loaded
            )
            if not result["model_loaded"]:
                result["status"] = "degraded"
                result["detail"] = "model will load on first request"
        return result

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._ready = False
            logger.info("Ollama client closed")


from src.ai.factory import create_ai_client  # noqa: E402

llm = create_ai_client()
