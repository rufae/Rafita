"""Autenticacion de la web SPA (Fase 3, 2026-09-29).

Sin dependencias externas: contrasenas con scrypt (stdlib) y sesiones con un
JWT HS256 minimo firmado con WEB_AUTH_SECRET (fail-closed si falta).
El login con Google (OAuth 2.0) es opcional: si GOOGLE_WEB_CLIENT_ID/SECRET
no estan configurados, el endpoint responde 503 con instrucciones.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from urllib.parse import urlencode

from src.config import settings
from src.logger import logger

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 32
TOKEN_TTL_S = 7 * 24 * 3600


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def hash_password(password: str) -> str:
    """scrypt con sal aleatoria por usuario (formato autodescriptivo)."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        (password or "").encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN
    )
    return "scrypt$%d$%d$%d$%s$%s" % (
        _SCRYPT_N,
        _SCRYPT_R,
        _SCRYPT_P,
        _b64e(salt),
        _b64e(digest),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algoritmo, n, r, p, salt_b64, digest_b64 = (stored or "").split("$")
        if algoritmo != "scrypt":
            return False
        digest = hashlib.scrypt(
            (password or "").encode(),
            salt=_b64d(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=_DKLEN,
        )
        return hmac.compare_digest(digest, _b64d(digest_b64))
    except (ValueError, TypeError):
        return False


def _secret() -> str:
    return (settings.web_auth_secret or "").strip()


def create_token(user: dict[str, Any], ttl_s: int = TOKEN_TTL_S) -> str:
    """JWT HS256 minimo (sin librerias) con expiracion."""
    secret = _secret()
    if not secret:
        raise RuntimeError("WEB_AUTH_SECRET no configurado")
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": int(user["id"]),
        "email": user.get("email", ""),
        "adm": 1 if user.get("is_admin") else 0,
        "iat": now,
        "exp": now + int(ttl_s),
    }
    partes = [
        _b64e(json.dumps(header, separators=(",", ":")).encode()),
        _b64e(json.dumps(payload, separators=(",", ":")).encode()),
    ]
    firma = hmac.new(secret.encode(), ".".join(partes).encode(), hashlib.sha256).digest()
    partes.append(_b64e(firma))
    return ".".join(partes)


def decode_token(token: str) -> dict[str, Any] | None:
    secret = _secret()
    if not secret or not token:
        return None
    try:
        header_b64, payload_b64, firma_b64 = token.split(".")
        esperado = hmac.new(
            secret.encode(), (header_b64 + "." + payload_b64).encode(), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(esperado, _b64d(firma_b64)):
            return None
        payload: dict[str, Any] = json.loads(_b64d(payload_b64))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


async def bootstrap_admin() -> None:
    """Crea el admin web desde WEB_ADMIN_EMAIL/PASSWORD si no hay usuarios."""
    from src.database import db

    email = (settings.web_admin_email or "").strip().lower()
    password = settings.web_admin_password or ""
    if not email or not password:
        return
    try:
        if await db.count_web_users() > 0:
            return
        await db.create_web_user(email, hash_password(password), is_admin=True)
        logger.info("Web: usuario admin creado (%s)", email)
    except Exception as e:
        logger.warning("Web: no se pudo crear el admin: %s", e)


# ---------- Sign in with Google (opcional) ----------


def google_configured() -> bool:
    return bool(
        (settings.google_web_client_id or "").strip()
        and (settings.google_web_client_secret or "").strip()
    )


def google_redirect_uri(request_base: str = "") -> str:
    return (settings.google_web_redirect_uri or "").strip() or (
        request_base.rstrip("/") + "/api/auth/google/callback"
    )


def google_auth_url(state: str, request_base: str = "") -> str:
    params = {
        "client_id": settings.google_web_client_id.strip(),
        "redirect_uri": google_redirect_uri(request_base),
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "prompt": "select_account",
        "state": state,
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


async def exchange_google_code(code: str, request_base: str = "") -> dict[str, Any] | None:
    """Canjea el codigo por un id_token/userinfo; devuelve {email, sub, name}."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": settings.google_web_client_id.strip(),
                    "client_secret": settings.google_web_client_secret.strip(),
                    "redirect_uri": google_redirect_uri(request_base),
                    "grant_type": "authorization_code",
                },
            )
            resp.raise_for_status()
            tokens = resp.json()
            info = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": "Bearer %s" % tokens.get("access_token", "")},
            )
            info.raise_for_status()
            datos: dict[str, Any] = info.json()
            return datos
    except Exception as e:
        logger.warning("Web: fallo el login con Google: %s", e)
        return None
