import asyncio
import hashlib
import hmac
import json
import re
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.config import settings
from src.database import db
from src.logger import logger
from src.models.schemas import MessageRole

app = FastAPI(
    title="Rafita Gateway",
    description="Webhook endpoint for external app integrations",
    version="0.3.0",
)

# API de la web SPA (auth, chat, boveda; Fase 3) — Telegram sigue igual.
from src.utils.web_api import router as _web_router  # noqa: E402

app.include_router(_web_router)


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    """Detalle dict en nivel raiz del cuerpo (esquema unico {success, error}).

    FastAPI envuelve `detail` en {"detail": ...} y el nodo 'Firmar informe'
    de n8n lee $json.error en la raiz; los detail-string se mantienen como
    {"detail": ...} por compatibilidad.
    """
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


def _mount_web_app() -> None:
    """Sirve la SPA en /app: build de produccion (web/app-dist) si existe y,
    si no, los sources (web/app) para desarrollo."""
    from pathlib import Path

    for candidate in (
        Path("/workspace/web/app-dist"),
        Path(__file__).resolve().parents[3] / "web" / "app-dist",
        Path("/workspace/web/app"),
        Path(__file__).resolve().parents[3] / "web" / "app",
    ):
        if candidate.exists():
            app.mount("/app", StaticFiles(directory=str(candidate), html=True), name="webapp")

            @app.get("/")
            async def _root_redirect() -> RedirectResponse:  # pragma: no cover - trivial
                return RedirectResponse("/app/")

            logger.info("Web SPA montada en /app (%s)", candidate)
            return


_mount_web_app()


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Cabeceras de seguridad de la SPA (Fase 0.5/0.6, 2026-09-29).

    - CSP restrictiva para /app (script/style solo del propio origen, el
      iframe de llamada en frame-src).
    - Permissions-Policy delega 'microphone' a los origenes confiables para
      que el iframe cross-origin de llamada pueda usar el micro.
    - WEB_ALLOWED_ORIGINS (lista separada por comas) es la lista de origenes
      confiables de la instalacion: la SPA, la pagina de llamada y localhost.
    """
    response = await call_next(request)
    confiables = [o.strip() for o in (settings.web_allowed_origins or "").split(",") if o.strip()]
    origen_csp = " ".join(["'self'"] + confiables)
    microfonos = " ".join(["self"] + ['"%s"' % o for o in confiables])
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy",
        "microphone=(%s), camera=(), geolocation=()" % microfonos,
    )
    path = request.url.path
    if path.startswith("/app"):
        # Cache (Fase 4): los assets con hash del build son inmutables;
        # config.js es editable por instalacion y no se cachea nunca.
        nombre = path.rsplit("/", 1)[-1]
        if nombre == "config.js":
            response.headers.setdefault("Cache-Control", "no-store")
        elif _HASH_ASSET_RE.search(nombre):
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        else:
            response.headers.setdefault("Cache-Control", "no-cache")
    if path == "/" or path.startswith("/app"):
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-src %s; "
            "worker-src 'self'; base-uri 'self'; form-action 'self'; "
            "object-src 'none'; frame-ancestors 'self'" % origen_csp,
        )
    return response


# Assets con hash de contenido del build (app-<hash8>.js, styles-<hash8>.css...).
_HASH_ASSET_RE = re.compile(r"-[0-9a-f]{8}\.(?:js|css)$")

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


def _fallo(mensaje: str) -> dict:
    """Esquema unico de error de los endpoints /automation/*.

    El nodo 'Firmar informe' de n8n detecta el fallo por la clave `error`;
    `message` es la version legible para humanos/herramientas.
    """
    return {"success": False, "error": mensaje[:200], "message": mensaje[:200]}


def _resultado(result):
    """Normaliza la respuesta de un servicio al esquema unico.

    Exito: {success: true, ...}. Fallo de servicio (HTTP 200): se anade la
    clave `error` para que n8n marque la ejecucion como error en vez de ok.
    """
    if isinstance(result, dict) and result.get("success") is False and "error" not in result:
        mensaje = str(result.get("message") or "fallo en el backend")
        result = dict(result)
        result["error"] = mensaje[:200]
    return result


def _check_webhook_auth(body: bytes, signature: str) -> None:
    """Fail-closed webhook auth: reject when unconfigured or signature invalid."""
    if not _webhook_secret:
        raise HTTPException(status_code=503, detail=_fallo("Webhook secret not configured"))
    if not _verify_signature(body, signature):
        raise HTTPException(status_code=401, detail=_fallo("Invalid signature"))


def _check_webhook_token(request: Request) -> None:
    """Auth para los GET del gateway (sin cuerpo que firmar).

    Mismo secreto que los webhooks, enviado en X-Webhook-Token. Antes
    /connectors y /homeassistant/state quedaban abiertos a toda la red.
    """
    import hmac

    if not _webhook_secret:
        raise HTTPException(status_code=503, detail=_fallo("Webhook secret not configured"))
    token = request.headers.get("X-Webhook-Token", "")
    if not token or not hmac.compare_digest(_webhook_secret, token):
        raise HTTPException(status_code=401, detail=_fallo("Invalid token"))


@app.get("/health")
async def health():
    """Liveness: the process is up. Does not check dependencies (see /ready)."""
    return {
        "status": "ok",
        "service": "rafita-gateway",
        "version": app.version,
        "timestamp": time.time(),
    }


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
async def list_connectors(request: Request):
    from src.utils.app_connector import connector

    _check_webhook_token(request)
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
        raise HTTPException(status_code=400, detail=_fallo("Invalid JSON"))

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

    _check_webhook_token(request)
    entity_id = request.query_params.get("entity_id", "")
    result = await connector.get_home_assistant_state(entity_id)
    return result


# --- Contestador automatico de llamadas (2026-09-27) ---------------------
# Endpoint agnostico de proveedor: cualquier centralita/SIP/IVR que pueda
# enviar el texto del llamante (STT propio o del proveedor) y recibir la
# respuesta para TTS puede usarlo. Auth: la misma firma HMAC del gateway.
# El resumen final se manda al Telegram del propietario.

_call_sessions: dict[str, dict[str, Any]] = {}
_CALL_SESSION_TTL = 3600


def _call_system_prompt() -> str:
    from src.config import settings

    return (
        "Eres %s, el asistente virtual de tu propietario. Estas atendiendo una "
        "llamada telefonica porque el no puede responder ahora mismo. "
        "OBLIGATORIO (transparencia, Ley UE de IA art. 50): en tu PRIMERA frase "
        "di claramente que eres un asistente de IA que atiende la llamada en "
        "nombre de su propietario. Habla en espanol, con frases cortas y "
        "naturales (tu respuesta se convierte a voz). Pregunta con educacion "
        "quien llama y el motivo de la llamada; si quiere dejar un recado o "
        "agendar una reunion, apunta: nombre, motivo, urgencia y un numero o "
        "medio de contacto. No prometas acciones concretas: di que le haras "
        "llegar el mensaje a tu propietario cuanto antes. Maximo dos frases "
        "por turno." % settings.assistant_name
    )


async def _call_summary(caller: str, turns: list[dict[str, str]]) -> str:
    """Resumen de la llamada (LLM con respaldo literal si falla)."""
    transcript = "\n".join(
        "%s: %s" % ("Llamante" if t["role"] == "caller" else "Rafita", t["text"]) for t in turns
    )
    fallback = "Llamada de %s:\n%s" % (caller or "desconocido", transcript)
    try:
        from src.ollama_client import llm

        summary = await asyncio.wait_for(
            llm.chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Resume esta llamada en 4-6 lineas con este formato:\n"
                            "Quien llama: ...\nMotivo: ...\nUrgencia: ...\n"
                            "Contacto: ...\nAccion sugerida: ...\n"
                            "Si falta un dato, escribe 'no indicado'. No inventes."
                        ),
                    },
                    {"role": "user", "content": transcript},
                ],
                max_tokens=220,
            ),
            timeout=120.0,
        )
        return summary.strip() or fallback
    except Exception as e:
        logger.warning("Resumen de llamada con LLM fallo: %s", e)
        return fallback


async def _notify_call_summary(caller: str, summary: str) -> None:
    from src.config import settings

    targets = list(settings.admin_ids or [])
    if not targets or _bot_ref is None:
        logger.warning("Contestador: sin ADMIN_IDS o bot; resumen no enviado")
        return
    text = "📞 *Llamada atendida por Rafita*\nDe: %s\n\n%s" % (caller or "desconocido", summary)
    for admin_id in targets:
        try:
            await _bot_ref.send_proactive_message(admin_id, text)
            await db.save_chat_message(admin_id, MessageRole.user.value, "[Llamada] %s" % summary)
        except Exception as e:
            logger.error("Contestador: fallo avisando a %s: %s", admin_id, e)


@app.post("/call")
async def call_answering_machine(request: Request):
    """Turno de conversacion del contestador automatico.

    Body JSON: {call_id, caller, text, end?}. Devuelve {reply, end, summary?}.
    """
    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    _check_webhook_auth(body, signature)

    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail=_fallo("Invalid JSON"))

    call_id = str(payload.get("call_id", "")).strip()
    caller = str(payload.get("caller", "")).strip()
    text = str(payload.get("text", "")).strip()
    if not call_id or not text:
        raise HTTPException(status_code=400, detail="call_id and text required")

    now = time.time()
    for stale in [k for k, s in _call_sessions.items() if now - s["updated"] > _CALL_SESSION_TTL]:
        _call_sessions.pop(stale, None)

    session = _call_sessions.get(call_id) or {"turns": [], "updated": now}
    session["turns"].append({"role": "caller", "text": text})
    session["updated"] = now

    from src.ollama_client import llm

    messages: list[dict[str, str]] = [{"role": "system", "content": _call_system_prompt()}]
    for turn in session["turns"][-12:]:
        messages.append(
            {
                "role": "user" if turn["role"] == "caller" else "assistant",
                "content": turn["text"],
            }
        )
    try:
        reply = (
            await asyncio.wait_for(llm.chat(messages=messages, max_tokens=160), timeout=120.0)
        ).strip()
    except Exception as e:
        logger.error("Contestador: fallo generando respuesta: %s", e)
        reply = "Disculpa, no te he entendido bien. ¿Puedes repetirlo, por favor?"
    if not reply:
        reply = "¿Sigues ahi? Dime quien eres y en que puedo ayudarte."

    session["turns"].append({"role": "rafita", "text": reply})
    _call_sessions[call_id] = session

    if payload.get("end"):
        summary = await _call_summary(caller, session["turns"])
        _call_sessions.pop(call_id, None)
        await _notify_call_summary(caller, summary)
        logger.info(
            "Contestador: llamada %s finalizada (%d turnos)", call_id, len(session["turns"])
        )
        return {"reply": reply, "end": True, "summary": summary}

    return {"reply": reply, "end": False}


# --- Automatizaciones profesionales (punto 1 de docs/automatizaciones.md) ---
# Endpoints HMAC que n8n orquesta: briefing contextual, inbox zero y captura
# a la boveda. Rafita pone los datos y la IA; n8n el disparo y la entrega.


@app.post("/automation/briefing")
async def automation_briefing(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    _check_webhook_auth(body, signature)
    from src.services.automation_service import build_briefing

    return _resultado(await build_briefing())


@app.post("/automation/inbox-scan")
async def automation_inbox_scan(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    _check_webhook_auth(body, signature)
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        payload = {}
    from src.services.automation_service import scan_inbox

    return _resultado(
        await scan_inbox(
            hours=int(payload.get("hours", 2) or 2),
            max_results=int(payload.get("max_results", 8) or 8),
        )
    )


@app.post("/automation/capture")
async def automation_capture(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    _check_webhook_auth(body, signature)
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail=_fallo("Invalid JSON"))
    from src.services.automation_service import capture_to_vault

    return _resultado(
        await capture_to_vault(
            text=str(payload.get("text", "")),
            title=str(payload.get("title", "")),
            tags=payload.get("tags") or [],
            source=str(payload.get("source", "n8n")),
        )
    )


@app.post("/automation/radar")
async def automation_radar(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    _check_webhook_auth(body, signature)
    from src.services.automation_service import radar

    return _resultado(await radar())


@app.post("/automation/infra-report")
async def automation_infra_report(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    _check_webhook_auth(body, signature)
    from src.services.automation_service import infra_report

    return _resultado(await infra_report())


@app.post("/automation/send-voice")
async def automation_send_voice(request: Request):
    """Sintetiza un texto y lo envia como nota de voz de Telegram (podcast)."""
    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    _check_webhook_auth(body, signature)
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail=_fallo("Invalid JSON"))
    text = str(payload.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail=_fallo("text required"))
    from src.config import settings

    target = payload.get("chat_id")
    if not target:
        admins = settings.admin_ids or []
        target = admins[0] if admins else None
    if not target or _bot_ref is None:
        return JSONResponse(status_code=503, content=_fallo("bot o chat_id no disponible"))

    from src.utils.tts_manager import cleanup_tts_dir, convert_to_ogg, text_to_speech

    wav = await text_to_speech(text[:1500])
    if wav is None:
        return JSONResponse(status_code=500, content=_fallo("TTS no disponible"))
    ogg = await convert_to_ogg(wav)
    if not ogg or not ogg.exists():
        cleanup_tts_dir(wav)
        return JSONResponse(status_code=500, content=_fallo("no se pudo convertir a ogg"))
    try:
        await _bot_ref.send_voice(int(target), str(ogg))
        logger.info("Automation: nota de voz enviada a %s", target)
        return {"success": True, "sent_to": target}
    except Exception as e:
        logger.error("Automation send-voice fallo: %s", e)
        return JSONResponse(status_code=500, content=_fallo(str(e)))
    finally:
        cleanup_tts_dir(wav)


@app.post("/automation/sync")
async def automation_sync(request: Request):
    """Sync bidireccional Google <-> boveda (automatizacion A)."""
    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    _check_webhook_auth(body, signature)
    from src.services.sync_service import sync_google_vault

    return _resultado(await sync_google_vault())


@app.post("/automation/crm-remind")
async def automation_crm_remind(request: Request):
    """Seguimientos de clientes pendientes (mini-CRM, Fase 1)."""
    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    _check_webhook_auth(body, signature)
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail=_fallo("Invalid JSON"))
    from src.services.crm_service import seguimientos_pendientes

    dias = int(payload.get("dias", 7) or 7)
    pendientes = await seguimientos_pendientes(dias=dias)
    if not pendientes:
        return {"success": True, "changes": 0, "clients": [], "message": ""}
    lineas = ["🔔 *Seguimiento de clientes*"]
    for cliente in pendientes[:10]:
        paso = (" → %s" % cliente["proximo_paso"]) if cliente.get("proximo_paso") else ""
        lineas.append(
            "  • *%s* (%s): %s%s" % (cliente["nombre"], cliente["estado"], cliente["motivo"], paso)
        )
    return {
        "success": True,
        "changes": len(pendientes),
        "clients": pendientes,
        "message": "\n".join(lineas),
    }


@app.post("/automation/sequences-run")
async def automation_sequences_run(request: Request):
    """Ejecuta las secuencias de email vencidas (Fase 2)."""
    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    _check_webhook_auth(body, signature)
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail=_fallo("Invalid JSON"))
    from src.services.sequence_service import ejecutar_secuencias

    return _resultado(
        await ejecutar_secuencias(
            dry_run=bool(payload.get("dry_run", False)),
            max_envios=int(payload.get("max_envios", 3) or 3),
        )
    )


async def _notify_automation_error(workflow: str, error: str | None) -> bool:
    """Aviso a admins SOLO tras 2 fallos seguidos del mismo workflow (sin spam)."""
    from src.config import settings
    from src.utils import severity as _sev

    text = "%s [%s] *Automatización con 2 fallos seguidos*\nWorkflow: %s\nError: %s" % (
        _sev.emoji(_sev.ERROR),
        _sev.tag(_sev.ERROR),
        workflow,
        error or "sin detalle",
    )
    # PWA (mejora 6): el mismo aviso como notificacion web push (best effort,
    # funciona incluso si no hay admins de Telegram configurados).
    try:
        from src.services.push_service import send_web_push

        await send_web_push(
            "Automatización con fallos",
            "Workflow: %s — %s" % (workflow, (error or "sin detalle")[:140]),
        )
    except Exception:
        logger.debug("Aviso de automatizacion: web push omitido", exc_info=True)
    targets = list(settings.admin_ids or [])
    if not targets or _bot_ref is None:
        return False
    ok = False
    for admin_id in targets:
        try:
            await _bot_ref.send_proactive_message(admin_id, text)
            ok = True
        except Exception as e:
            logger.error("n8n-run: fallo avisando a %s: %s", admin_id, e)
    return ok


@app.post("/api/n8n/run")
async def n8n_run_report(request: Request):
    """Informe de ejecución de automatizaciones n8n (tareas.md, Fase 1).

    Cada workflow notifica aquí al terminar (nodo «Reportar a Rafita»).
    Idempotente por `execution_id` (reintentos de n8n no duplican filas) y
    alerta a los admins solo si la MISMA automatización falla dos veces
    seguidas. Permite responder «¿qué automatizaciones han fallado esta
    semana?» con datos reales (tool `get_automation_runs`).
    """
    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    _check_webhook_auth(body, signature)
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail=_fallo("Invalid JSON"))
    workflow = str(payload.get("workflow") or "").strip()[:120]
    execution_id = str(payload.get("execution_id") or "").strip()[:80]
    status = str(payload.get("status") or "").strip()
    if not workflow or not execution_id or status not in ("ok", "error"):
        raise HTTPException(
            status_code=400,
            detail="workflow, execution_id y status (ok|error) son obligatorios",
        )
    error = str(payload.get("error") or "").strip()[:500] or None
    finished_at = str(payload.get("finished_at") or "").strip()[:40] or None
    # Taxonomia unica (D12): nivel opcional del informe n8n; si no viene, se
    # deriva de status (ok->info, error->error) y lo no reconocido tambien.
    from src.utils import severity as _sev

    nivel = _sev.normalize(payload.get("severity"), default="info" if status == "ok" else "error")
    previo = await db.last_automation_run(workflow)
    stored = await db.record_automation_run(
        execution_id, workflow, status, error, finished_at, severity=nivel
    )
    alerted = False
    if status == "error" and stored and previo and previo.get("status") == "error":
        alerted = await _notify_automation_error(workflow, error)
    return {"success": True, "duplicate": not stored, "alerted": alerted}


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
