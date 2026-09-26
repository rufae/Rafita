import asyncio
import hashlib
import hmac
import json
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from src.database import db
from src.logger import logger
from src.models.schemas import MessageRole

app = FastAPI(
    title="Rafita Gateway",
    description="Webhook endpoint for external app integrations",
    version="1.0.0",
)

_webhook_secret: str | None = None
_bot_ref = None
_message_queue: asyncio.Queue = None


def configure_gateway(secret: str, bot_ref=None) -> None:
    global _webhook_secret, _bot_ref
    _webhook_secret = secret
    _bot_ref = bot_ref


def _verify_signature(body: bytes, signature: str) -> bool:
    if not _webhook_secret or not signature:
        return False
    expected = hmac.new(_webhook_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _check_webhook_auth(body: bytes, signature: str) -> None:
    """Fail-closed webhook auth: reject when unconfigured or signature invalid."""
    if not _webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook secret not configured")
    if not _verify_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")


@app.get("/health")
async def health():
    """Liveness: the process is up. Does not check dependencies (see /ready)."""
    return {"status": "ok", "service": "rafita-gateway", "timestamp": time.time()}


async def _check_ai() -> dict[str, Any]:
    """AI provider health via the generic `AIProvider.check_health()` (task 2.5).

    Task 3.3: no Ollama-specific `/api/tags` here; the selected provider
    (ollama or openai-compatible) reports its own state, including whether the
    chat model is available and loaded.
    """
    from src.ollama_client import llm

    try:
        return await llm.check_health()
    except Exception as e:
        return {
            "status": "unhealthy",
            "detail": "AI health check failed: %s" % str(e)[:150],
        }


async def _check_vector_db() -> dict[str, Any]:
    from src.utils.vector_manager import vector_db

    return await vector_db.health()


def _check_vault() -> dict[str, Any]:
    """The Obsidian vault must be mounted, readable and writable (task 3.3)."""
    import os

    from src.config import settings

    path = settings.obsidian_vault_path
    if not path.exists():
        return {"status": "unhealthy", "path": str(path), "detail": "vault path not found"}
    if not path.is_dir():
        return {"status": "unhealthy", "path": str(path), "detail": "vault path is not a directory"}
    readable = os.access(path, os.R_OK)
    writable = os.access(path, os.W_OK)
    if not readable:
        return {"status": "unhealthy", "path": str(path), "detail": "vault not readable"}
    if not writable:
        return {"status": "degraded", "path": str(path), "detail": "vault is read-only"}
    return {"status": "ok", "path": str(path), "writable": True}


def _check_telegram() -> dict[str, Any]:
    if _bot_ref is None:
        return {"status": "unhealthy", "detail": "bot not configured"}
    status_fn = getattr(_bot_ref, "polling_status", None)
    if status_fn is None:
        return {"status": "unhealthy", "detail": "polling state not available"}
    return status_fn()


_NOT_READY_STATES = {"unhealthy", "error", "uninitialized", "unknown"}


@app.get("/ready")
async def readiness():
    """Readiness: distinguishes process liveness from service readiness (3.3).

    - `ready` (HTTP 200): every dependency is ok.
    - `degraded` (HTTP 200): the service can answer, but with caveats (e.g.
      the chat model is not loaded yet and will load on the first request).
    - `not_ready` (HTTP 503): some dependency is down (AI backend unreachable,
      vector DB broken, vault missing, Telegram polling dead).
    """
    checks = {
        "ai": await _check_ai(),
        "vector_db": await _check_vector_db(),
        "vault": _check_vault(),
        "telegram": _check_telegram(),
    }
    statuses = {name: check.get("status") for name, check in checks.items()}
    if all(status == "ok" for status in statuses.values()):
        overall = "ready"
    elif any(status in _NOT_READY_STATES for status in statuses.values()):
        overall = "not_ready"
    else:
        overall = "degraded"
    ready = overall != "not_ready"
    payload = {
        "status": overall,
        "ready": ready,
        "checks": checks,
        "timestamp": time.time(),
    }
    return JSONResponse(status_code=200 if ready else 503, content=payload)


@app.get("/metrics")
async def get_metrics():
    from src.utils.telemetry import metrics as tm

    return {
        "status": "ok",
        **tm.snapshot(),
    }


@app.get("/connectors")
async def list_connectors():
    from src.utils.app_connector import connector

    return {"connectors": connector.list_connectors()}


@app.post("/webhook/{source}")
async def receive_webhook(source: str, request: Request):
    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    _check_webhook_auth(body, signature)

    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        payload = {"raw": body.decode("utf-8", errors="replace")}

    text = payload.get("message", payload.get("text", payload.get("raw", "")))
    chat_id = payload.get("chat_id")

    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id required")

    logger.info("Webhook received from '%s' for chat %d: %s", source, chat_id, str(text)[:200])

    if _bot_ref and text:
        try:
            await _bot_ref.send_proactive_message(
                chat_id,
                "🔔 *[Webhook: %s]*\n%s" % (source, text),
            )
            await db.save_chat_message(
                chat_id, MessageRole.user.value, "[Webhook:%s] %s" % (source, text)
            )
            return {"status": "delivered", "source": source, "chat_id": chat_id}
        except Exception as e:
            logger.error("Webhook delivery failed: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    return {"status": "queued", "source": source}


@app.post("/connector/{name}")
async def register_connector_endpoint(name: str, request: Request):
    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    _check_webhook_auth(body, signature)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    from src.utils.app_connector import connector

    connector_type = payload.get("type", "custom")
    credentials = payload.get("credentials", {})
    config = payload.get("config", {})

    try:
        record_id = await connector.register_connector(name, connector_type, credentials, config)
        return {"status": "registered", "name": name, "id": record_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/connector/{name}")
async def remove_connector_endpoint(name: str, request: Request):
    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    _check_webhook_auth(body, signature)
    from src.utils.app_connector import connector

    removed = await connector.remove_connector(name)
    if removed:
        return {"status": "removed", "name": name}
    raise HTTPException(status_code=404, detail="Connector not found")


@app.post("/gmail/check")
async def check_gmail(request: Request):
    from src.utils.app_connector import connector

    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    _check_webhook_auth(body, signature)
    emails = await connector.fetch_urgent_emails()
    return {"urgent_emails": emails, "count": len(emails)}


@app.post("/homeassistant/{entity_id}")
async def control_home_assistant(entity_id: str, request: Request):
    from src.utils.app_connector import connector

    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    _check_webhook_auth(body, signature)
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        payload = {}
    action = payload.get("action", "toggle")
    result = await connector.call_home_assistant(entity_id, action)
    return result


@app.get("/homeassistant/state")
async def get_ha_state(request: Request):
    from src.utils.app_connector import connector

    entity_id = request.query_params.get("entity_id", "")
    result = await connector.get_home_assistant_state(entity_id)
    return result


async def start_gateway_server(host: str = "0.0.0.0", port: int = 8000):
    config_obj = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config_obj)
    logger.info("Starting Rafita Gateway on %s:%d", host, port)
    await server.serve()
