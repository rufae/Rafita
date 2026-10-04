"""Notificaciones web push para la PWA (mejora 6, 2026-10-04).

Flujo:
1. `scripts/generate_vapid_keys.py` genera la clave privada VAPID en
   `settings.vapid_key_file` (PEM; la pública se deriva de ella).
2. El navegador pide la pública a `GET /api/push/config` y se suscribe con
   `pushManager.subscribe()`; la suscripción se guarda con
   `POST /api/push/subscribe`.
3. `send_web_push()` publica a todas las suscripciones (pywebpush, VAPID).
   Los endpoints 404/410 (suscripciones caducas) se limpian solos.

Sin clave VAPID el servicio queda desactivado (`enabled: false`) y los envíos
se omiten con aviso: nunca rompe el flujo de avisos por Telegram.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization

from src.config import settings
from src.database import db
from src.logger import logger

try:
    from pywebpush import webpush
except ImportError:  # pragma: no cover - el build de produccion lo instala
    webpush = None  # type: ignore[assignment]


def vapid_private_pem() -> str:
    try:
        return Path(settings.vapid_key_file).read_text(encoding="utf-8")
    except OSError:
        return ""


def vapid_public_key_b64() -> str:
    """Clave pública VAPID en base64url sin relleno (la que usa el navegador)."""
    pem = vapid_private_pem()
    if not pem:
        return ""
    try:
        private_key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )
    except Exception:
        return ""
    return base64.urlsafe_b64encode(public_bytes).decode("ascii").rstrip("=")


def push_enabled() -> bool:
    return bool(vapid_public_key_b64() and webpush is not None)


async def push_config() -> dict[str, Any]:
    return {"enabled": push_enabled(), "publicKey": vapid_public_key_b64()}


async def subscribe(payload: dict[str, Any], user_id: int) -> dict[str, Any]:
    endpoint = str(payload.get("endpoint") or "").strip()
    keys = payload.get("keys") if isinstance(payload.get("keys"), dict) else {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    if not endpoint.startswith("https://"):
        return {"success": False, "message": "Endpoint invalido (se espera https)."}
    if not p256dh or not auth:
        return {"success": False, "message": "Suscripcion incompleta: faltan keys."}
    await db.add_push_subscription(user_id, endpoint, p256dh, auth)
    logger.info("Web push: suscripcion guardada para el usuario %s", user_id)
    return {"success": True, "message": "Suscripcion de notificaciones guardada."}


async def unsubscribe(payload: dict[str, Any]) -> dict[str, Any]:
    endpoint = str(payload.get("endpoint") or "").strip()
    if not endpoint:
        return {"success": False, "message": "Falta el endpoint."}
    await db.delete_push_subscription(endpoint)
    return {"success": True, "message": "Suscripcion eliminada."}


def _sync_send(sub: dict[str, Any], title: str, body: str, url: str) -> None:
    webpush(
        subscription_info={
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        },
        data=json.dumps({"title": title, "body": body, "url": url}, ensure_ascii=False),
        vapid_private_key=vapid_private_pem(),
        vapid_claims={"sub": settings.vapid_subject},
    )


async def send_web_push(title: str, body: str, url: str = "/app") -> dict[str, Any]:
    """Envía una notificación a todas las suscripciones (best effort)."""
    if not push_enabled():
        return {"sent": 0, "skipped": "VAPID no configurado o pywebpush ausente"}
    subs = await db.list_push_subscriptions()
    if not subs:
        return {"sent": 0, "skipped": "sin suscripciones"}
    sent = 0
    fallos = 0
    caducas: list[str] = []
    for sub in subs:
        try:
            await asyncio.to_thread(_sync_send, sub, title, body, url)
            sent += 1
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", 0)
            if status in (404, 410):
                caducas.append(str(sub.get("endpoint") or ""))
            else:
                fallos += 1
                logger.warning("Web push fallo: %s", str(exc)[:150])
    for endpoint in caducas:
        if endpoint:
            await db.delete_push_subscription(endpoint)
    if caducas:
        logger.info("Web push: %d suscripciones caducas eliminadas", len(caducas))
    return {"sent": sent, "failed": fallos, "stale_removed": len(caducas)}
