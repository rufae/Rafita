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


async def _check_ollama() -> dict[str, Any]:
    import httpx

    from src.config import settings

    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("%s/api/tags" % settings.ollama_host.rstrip("/"))
            resp.raise_for_status()
            models = [m.get("name", "") for m in resp.json().get("models", [])]
    except Exception as e:
        return {"status": "error", "detail": "Ollama unreachable: %s" % str(e)[:150]}
    chat_model = settings.ollama_model
    available = any(m == chat_model or m.startswith(chat_model + ":") for m in models)
    result: dict[str, Any] = {
        "status": "ok" if available else "degraded",
        "latency_ms": round((time.time() - t0) * 1000),
        "chat_model": chat_model,
        "chat_model_available": available,
    }
    if not available:
        result["detail"] = "chat model '%s' not pulled yet" % chat_model
    return result


async def _check_vector_db() -> dict[str, Any]:
    from src.utils.vector_manager import vector_db

    return await vector_db.health()


def _check_telegram() -> dict[str, Any]:
    if _bot_ref is None:
        return {"status": "error", "detail": "bot not configured"}
    status_fn = getattr(_bot_ref, "polling_status", None)
    if status_fn is None:
        return {"status": "unknown", "detail": "polling state not available"}
    return status_fn()


@app.get("/ready")
async def readiness():
    """Readiness: verifies the critical dependencies before serving traffic."""
    checks = {
        "ollama": await _check_ollama(),
        "vector_db": await _check_vector_db(),
        "telegram": _check_telegram(),
    }
    ready = all(check.get("status") == "ok" for check in checks.values())
    payload = {
        "status": "ready" if ready else "not_ready",
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
