import asyncio
import hashlib
import hmac
import json
import time

import uvicorn
from fastapi import FastAPI, HTTPException, Request

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
    expected = hmac.new(
        _webhook_secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "rafita-gateway", "timestamp": time.time()}


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

    if _webhook_secret:
        if not _verify_signature(body, signature):
            raise HTTPException(status_code=401, detail="Invalid signature")

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
            await db.save_chat_message(chat_id, MessageRole.user.value, "[Webhook:%s] %s" % (source, text))
            return {"status": "delivered", "source": source, "chat_id": chat_id}
        except Exception as e:
            logger.error("Webhook delivery failed: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    return {"status": "queued", "source": source}


@app.post("/connector/{name}")
async def register_connector_endpoint(name: str, request: Request):
    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    if _webhook_secret:
        if not _verify_signature(body, signature):
            raise HTTPException(status_code=401, detail="Invalid signature")
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
    if _webhook_secret:
        if not _verify_signature(body, signature):
            raise HTTPException(status_code=401, detail="Invalid signature")
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
    if _webhook_secret:
        if not _verify_signature(body, signature):
            raise HTTPException(status_code=401, detail="Invalid signature")
    emails = await connector.fetch_urgent_emails()
    return {"urgent_emails": emails, "count": len(emails)}


@app.post("/homeassistant/{entity_id}")
async def control_home_assistant(entity_id: str, request: Request):
    from src.utils.app_connector import connector
    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    if _webhook_secret:
        if not _verify_signature(body, signature):
            raise HTTPException(status_code=401, detail="Invalid signature")
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
        app, host=host, port=port, log_level="info",
        access_log=False, lifespan="on",
    )
    server = uvicorn.Server(config_obj)
    logger.info("Starting Rafita Gateway on %s:%d", host, port)
    await server.serve()
