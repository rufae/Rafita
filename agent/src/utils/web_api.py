"""API de la web SPA (Fase 3, 2026-09-29).

Expone auth (email/clave + Google), chat (mismo Agent Core que Telegram,
con chat_id propio por usuario web), boveda (vista Baul) y el token de
llamada. Telegram sigue funcionando igual: ambos canales comparten cerebro.
"""

import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse

from src.config import settings
from src.database import db
from src.logger import logger
from src.utils.path_safety import resolve_within
from src.utils.web_auth import (
    create_token,
    decode_token,
    exchange_google_code,
    google_auth_url,
    google_configured,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api")

# Espacio de chat_id propio para la web (no colisiona con los de Telegram).
WEB_CHAT_BASE = 900_000_000
# Estados OAuth en memoria (un solo proceso) con caducidad.
_GOOGLE_STATES: dict[str, float] = {}
_STATE_TTL_S = 600
_MAX_NOTE_BYTES = 512_000


def _vault_root() -> Path:
    return settings.obsidian_vault_path


def _web_dir() -> Path:
    for candidate in (
        Path("/workspace/web/app"),
        Path(__file__).resolve().parents[3] / "web" / "app",
    ):
        if candidate.exists():
            return candidate
    return Path("/workspace/web/app")


async def require_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="No autenticado")
    user = await db.get_web_user(int(payload.get("sub", 0)))
    if not user:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")
    return user


async def require_admin(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Solo administradores")
    return user


def _user_public(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "email": user["email"],
        "is_admin": bool(user.get("is_admin")),
    }


# ---------- auth ----------


@router.post("/auth/login")
async def auth_login(request: Request):
    body = await request.json()
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))
    user = await db.get_web_user_by_email(email)
    if not user or not verify_password(password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")
    if not (settings.web_auth_secret or "").strip():
        raise HTTPException(status_code=503, detail="Login web deshabilitado (WEB_AUTH_SECRET)")
    return {"token": create_token(user), "user": _user_public(user)}


@router.post("/auth/register")
async def auth_register(request: Request):
    if not settings.web_allow_registration:
        raise HTTPException(
            status_code=403,
            detail="Registro deshabilitado (WEB_ALLOW_REGISTRATION=false)",
        )
    body = await request.json()
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))
    if "@" not in email or len(email) < 5:
        raise HTTPException(status_code=400, detail="Email invalido")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="La contraseña debe tener 8+ caracteres")
    if await db.get_web_user_by_email(email):
        raise HTTPException(status_code=409, detail="Ese email ya esta registrado")
    user_id = await db.create_web_user(email, hash_password(password), is_admin=False)
    user = await db.get_web_user(user_id)
    if not user:
        raise HTTPException(status_code=500, detail="No se pudo crear el usuario")
    logger.info("Web: usuario registrado (%s)", email)
    return {"token": create_token(user), "user": _user_public(user)}


@router.get("/auth/me")
async def auth_me(user: dict[str, Any] = Depends(require_user)):
    return {"user": _user_public(user)}


@router.get("/auth/google/start")
async def auth_google_start(request: Request):
    if not google_configured():
        return JSONResponse(
            status_code=503,
            content={
                "error": "Google no configurado para la web",
                "hint": (
                    "Define GOOGLE_WEB_CLIENT_ID y GOOGLE_WEB_CLIENT_SECRET (y "
                    "GOOGLE_WEB_REDIRECT_URI) en el .env para activar Sign in with Google."
                ),
            },
        )
    base = str(request.base_url)
    state = secrets.token_urlsafe(24)
    _GOOGLE_STATES[state] = time.time() + _STATE_TTL_S
    return RedirectResponse(google_auth_url(state, base))


@router.get("/auth/google/callback")
async def auth_google_callback(request: Request, code: str = "", state: str = ""):
    expira = _GOOGLE_STATES.pop(state, 0.0)
    if not state or expira < time.time():
        raise HTTPException(status_code=400, detail="Estado OAuth invalido o caducado")
    info = await exchange_google_code(code, str(request.base_url))
    if not info or not info.get("email"):
        raise HTTPException(status_code=401, detail="No se pudo validar la cuenta de Google")
    email = str(info["email"]).strip().lower()
    user = await db.get_web_user_by_email(email)
    if not user:
        sin_usuarios = await db.count_web_users() == 0
        es_admin = sin_usuarios or email == (settings.web_admin_email or "").strip().lower()
        user_id = await db.create_web_user(email, "", is_admin=es_admin)
        user = await db.get_web_user(user_id)
        if not user:
            raise HTTPException(status_code=500, detail="No se pudo crear el usuario")
        logger.info("Web: usuario creado via Google (%s)", email)
    if not (settings.web_auth_secret or "").strip():
        raise HTTPException(status_code=503, detail="Login web deshabilitado (WEB_AUTH_SECRET)")
    return RedirectResponse("/app/#token=%s" % create_token(user))


@router.get("/auth/users")
async def auth_users(_admin: dict[str, Any] = Depends(require_admin)):
    return {"users": await db.list_web_users()}


# ---------- chat (mismo cerebro que Telegram) ----------


@router.post("/chat")
async def web_chat(request: Request, user: dict[str, Any] = Depends(require_user)):
    body = await request.json()
    message = str(body.get("message", "")).strip()
    if not message:
        raise HTTPException(status_code=400, detail="Mensaje vacio")
    chat_id = WEB_CHAT_BASE + int(user["id"])
    from src.core import generate_response

    reply = await generate_response(message, chat_id)
    return {"reply": reply, "chat_id": chat_id}


@router.get("/chat/history")
async def web_chat_history(limit: int = 30, user: dict[str, Any] = Depends(require_user)):
    chat_id = WEB_CHAT_BASE + int(user["id"])
    rows = await db.get_chat_history(chat_id, limit=max(1, min(100, limit)))
    return {"messages": list(reversed(rows))}


# ---------- llamada ----------


@router.get("/call/token")
async def call_token(_user: dict[str, Any] = Depends(require_user)):
    from src.utils.security_manager import get_or_create_voice_call_token

    return {"token": get_or_create_voice_call_token()}


# ---------- boveda (vista Baul) ----------


def _note_entry(ruta: Path, root: Path) -> dict[str, Any]:
    stat = ruta.stat()
    return {
        "path": str(ruta.relative_to(root)),
        "title": ruta.stem,
        "folder": str(ruta.parent.relative_to(root)) if ruta.parent != root else "",
        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        "size": stat.st_size,
    }


@router.get("/vault/notes")
async def vault_notes(
    query: str = "",
    folder: str = "",
    limit: int = 100,
    _user: dict[str, Any] = Depends(require_user),
):
    root = _vault_root()
    if not root.exists():
        return {"notes": [], "total": 0}
    query_lower = query.strip().lower()
    folder_lower = folder.strip().strip("/").lower()
    notas: list[dict[str, Any]] = []
    for ruta in sorted(root.rglob("*.md")):
        rel = ruta.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if folder_lower and not str(rel).lower().startswith(folder_lower):
            continue
        if query_lower:
            nombre = str(rel).lower()
            coincide = query_lower in nombre
            if not coincide:
                try:
                    coincide = (
                        query_lower
                        in ruta.read_text(encoding="utf-8", errors="ignore")[:4096].lower()
                    )
                except OSError:
                    coincide = False
            if not coincide:
                continue
        notas.append(_note_entry(ruta, root))
        if len(notas) >= max(1, min(300, limit)):
            break
    return {"notes": notas, "total": len(notas)}


@router.get("/vault/note")
async def vault_read(path: str, _user: dict[str, Any] = Depends(require_user)):
    root = _vault_root()
    try:
        destino = resolve_within(root, root / path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ruta fuera de la boveda")
    if not destino.exists() or not destino.is_file():
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    return {"path": path, "content": destino.read_text(encoding="utf-8", errors="ignore")}


@router.post("/vault/note")
async def vault_write(request: Request, _user: dict[str, Any] = Depends(require_user)):
    body = await request.json()
    path = str(body.get("path", "")).strip()
    content = str(body.get("content", ""))
    if not path.endswith(".md"):
        raise HTTPException(status_code=400, detail="Solo se permiten notas .md")
    if len(content.encode("utf-8")) > _MAX_NOTE_BYTES:
        raise HTTPException(status_code=413, detail="Nota demasiado grande")
    root = _vault_root()
    try:
        destino = resolve_within(root, root / path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ruta fuera de la boveda")
    if any(part.startswith(".") for part in destino.relative_to(root).parts):
        raise HTTPException(status_code=400, detail="Ruta no permitida")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(content, encoding="utf-8")
    logger.info("Web: nota guardada %s", destino.name)
    return {"success": True, "path": str(destino.relative_to(root))}


@router.delete("/vault/note")
async def vault_delete(path: str, _user: dict[str, Any] = Depends(require_user)):
    root = _vault_root()
    try:
        destino = resolve_within(root, root / path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ruta fuera de la boveda")
    if destino == root or not destino.exists():
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    destino.unlink()
    logger.info("Web: nota borrada %s", destino.name)
    return {"success": True}


# ---------- reuniones (Fase 4) ----------


@router.post("/meetings")
async def meetings_create(
    file: UploadFile = File(...),
    title: str = Form(""),
    user: dict[str, Any] = Depends(require_user),
):
    from src.services import meeting_service

    datos = await file.read()
    if not datos:
        raise HTTPException(status_code=400, detail="Audio vacio")
    try:
        resultado = await meeting_service.crear_desde_subida(
            int(user["id"]), title, file.filename or "audio.webm", datos
        )
    except ValueError as e:
        raise HTTPException(status_code=413, detail=str(e))
    return resultado


@router.get("/meetings")
async def meetings_list(user: dict[str, Any] = Depends(require_user)):
    from src.services import meeting_service

    return {"meetings": await meeting_service.listar(int(user["id"]))}


@router.get("/meetings/{meeting_id}")
async def meetings_detail(meeting_id: int, user: dict[str, Any] = Depends(require_user)):
    from src.services import meeting_service

    detalle = await meeting_service.detalle(meeting_id, int(user["id"]))
    if not detalle:
        raise HTTPException(status_code=404, detail="Reunion no encontrada")
    return detalle


@router.delete("/meetings/{meeting_id}")
async def meetings_delete(meeting_id: int, user: dict[str, Any] = Depends(require_user)):
    from src.services import meeting_service

    if not await meeting_service.borrar(meeting_id, int(user["id"])):
        raise HTTPException(status_code=404, detail="Reunion no encontrada")
    return {"success": True}
