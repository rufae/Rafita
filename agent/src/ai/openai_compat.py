"""OpenAI-compatible provider adapter (task 2.5).

Works with OpenAI itself and any OpenAI-compatible endpoint (DeepSeek, Groq,
OpenRouter, vLLM, and Ollama's own `/v1` compatibility layer), configured with
`OPENAI_API_KEY`, `OPENAI_BASE_URL` and `OPENAI_MODEL`.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI, OpenAI

from src.config import settings
from src.logger import logger


class OpenAICompatClient:
    def __init__(self) -> None:
        self.base_url = settings.openai_base_url.rstrip("/")
        self.model = settings.openai_model
        self.vision_model = settings.openai_vision_model or settings.openai_model
        self.reasoning_effort = settings.openai_reasoning_effort
        self.temperature = settings.llm_temperature
        self.max_tokens = settings.llm_max_tokens
        self._client: AsyncOpenAI | None = None
        self._embed_client: OpenAI | None = None

    async def initialize(self) -> None:
        api_key = settings.openai_api_key or "not-needed"
        self._client = AsyncOpenAI(base_url=self.base_url, api_key=api_key, timeout=600.0)
        self._embed_client = OpenAI(base_url=self.base_url, api_key=api_key, timeout=600.0)
        logger.info(
            "OpenAI-compatible AI provider initialized: model=%s vision=%s base_url=%s",
            self.model,
            self.vision_model,
            self.base_url,
        )

    async def close(self) -> None:
        if self._client:
            await self._client.close()
        self._client = None
        self._embed_client = None

    async def check_health(self) -> dict[str, Any]:
        if not self._client:
            return {"status": "uninitialized"}
        try:
            import time

            start = time.time()
            await self._client.models.list()
            return {
                "status": "healthy",
                "model": self.model,
                "latency_ms": round((time.time() - start) * 1000),
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    def _extra_body(self) -> dict[str, Any] | None:
        """Optional provider-specific field; omitted unless configured.

        Useful with Ollama's `/v1` layer to disable thinking
        (`OPENAI_REASONING_EFFORT=none`) when using `AI_PROVIDER=openai`.
        """
        if self.reasoning_effort:
            return {"reasoning_effort": self.reasoning_effort}
        return None

    async def chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
        images: list[str] | None = None,
    ) -> str:
        if not self._client:
            raise RuntimeError("AI provider not initialized. Call initialize() first.")
        if images:
            messages = self._inject_images(messages, images)
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature if temperature is not None else self.temperature,
            max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            extra_body=self._extra_body(),
        )
        return response.choices[0].message.content or ""

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        images: list[str] | None = None,
    ) -> tuple[str, list[dict[str, Any]] | None]:
        if not self._client:
            raise RuntimeError("AI provider not initialized. Call initialize() first.")
        if images:
            messages = self._inject_images(messages, images)
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            tools=tools,  # type: ignore[arg-type]
            temperature=temperature if temperature is not None else self.temperature,
            max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            extra_body=self._extra_body(),
        )
        message = response.choices[0].message
        tool_calls = None
        if message.tool_calls:
            tool_calls = []
            for call in message.tool_calls:
                tool_calls.append(
                    {
                        "id": call.id,
                        "type": call.type,
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                )
        return message.content or "", tool_calls

    async def chat_stream_tokens(
        self,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        if not self._client:
            raise RuntimeError("AI provider not initialized. Call initialize() first.")
        stream = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature if temperature is not None else self.temperature,
            max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            stream=True,
            extra_body=self._extra_body(),
        )
        async for chunk in stream:  # type: ignore[union-attr]
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def chat_vision(
        self,
        messages: list[dict[str, Any]],
        images: list[str],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        return await self.chat(
            messages=messages, temperature=temperature, max_tokens=max_tokens, images=images
        )

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not self._embed_client:
            raise RuntimeError("AI provider not initialized. Call initialize() first.")
        response = self._embed_client.embeddings.create(
            model=settings.openai_embedding_model or settings.openai_model,
            input=texts,
        )
        return [list(item.embedding) for item in response.data]

    def _encode_image(self, image_path: str) -> str:
        raw = Path(image_path).read_bytes()
        return base64.b64encode(raw).decode("utf-8")

    def _inject_images(
        self, messages: list[dict[str, Any]], images: list[str]
    ) -> list[dict[str, Any]]:
        result = [dict(message) for message in messages]
        if result and result[-1].get("role") == "user":
            last = dict(result[-1])
            content: list[dict[str, Any]] = []
            if last.get("content"):
                content.append({"type": "text", "text": last["content"]})
            for image in images:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64,%s" % self._encode_image(image)
                        },
                    }
                )
            last["content"] = content
            result[-1] = last
        return result

    def __repr__(self) -> str:
        return "OpenAICompatClient(model=%s, base_url=%s)" % (self.model, self.base_url)
