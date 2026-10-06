"""Fase 3 — inyección de fallos: modo degradado con TODO mockeado.

Sin red, sin backend de IA real, sin disco real ni Telegram: CI-safe
(CI=true, TELEGRAM_TOKEN=dummy_token_for_ci, sin nodo de IA).

Escenarios y seams reales verificados:

a) El backend se cae a mitad de respuesta (2ª llamada) → `generate_response`
   responde con el fallback honesto «Consulta completada. Revisa el resultado
   de las herramientas.» y nunca afirma una acción inventada.
b) El backend no responde en la 1ª llamada → los mensajes de degradación
   reales de `_prepare_tool_phase` (timeout / error interno / Error del
   modelo), copiados del código, no inventados.
c) La herramienta falla (OAuth revocado, success=false) → la guardia
   `todas_fallidas` de `generate_response` sustituye el éxito inventado del
   modelo por el error real de la herramienta.
d) Disco lleno (ENOSPC) al guardar historial/estado → la petición no mata el
   proceso: el wrapper del bot (`RafitaBot._wrap`) contiene la excepción y la
   escritura de estado (`_save_diary_entry`) se degrada en silencio.
e) Infra sin backend de IA → `check_llm` marca el check fallido y el gauge
   `infra_checks_ok` queda en 0.0.

Los dobles de LLM/BD/Herramientas se definen AQUI (patrón de
`test_orchestrator_coverage3.py`); no se importa nada de otros tests.
"""

import errno
import logging
from types import SimpleNamespace

import pytest

from src.config import settings
from src.core import orchestrator as orch
from src.ollama_client import OllamaClientError

# ---------- dobles locales (patrón de test_orchestrator_coverage3.py) ----------


class _FakeLLM:
    """Doble del cliente LLM: respuestas programadas y contador de llamadas.

    `chat_reply` es lo que devuelve la selección de herramienta en texto
    (`_elegir_herramienta_por_texto`): "NINGUNA" evita recuperaciones.
    """

    def __init__(self, respond, stream_tokens=None, chat_reply="NINGUNA"):
        self._respond = respond
        self._stream = stream_tokens
        self._chat_reply = chat_reply
        self.calls = []

    async def chat_with_tools(self, messages, tools, max_tokens=512):
        self.calls.append({"messages": messages, "tools": tools})
        return await self._respond(messages, tools)

    async def chat(self, messages, max_tokens=512, **kwargs):
        return self._chat_reply

    async def chat_stream_tokens(self, messages, max_tokens=180, repeat_penalty=None):
        if isinstance(self._stream, Exception):
            raise self._stream
        for token in self._stream or ["respuesta"]:
            yield token


def _patch_common(monkeypatch, history=None):
    """Aísla el orquestador de BD, selección semántica y ranking."""

    async def save(*args, **kwargs):
        return None

    async def get_history(*args, **kwargs):
        return history or []

    async def select_tools(text):
        return []

    async def best_tools(text, k=3):
        return [], 0.0

    monkeypatch.setattr(orch.db, "save_chat_message", save)
    monkeypatch.setattr(orch.db, "get_chat_history", get_history)
    monkeypatch.setattr(orch, "select_tools_semantic", select_tools)
    monkeypatch.setattr(orch, "best_tools_for_message", best_tools)


def _plain(content, tool_calls=None):
    async def respond(messages, tools):
        return content, tool_calls

    return respond


def _enospc():
    """OSError como el que levanta SQLite/OS cuando se llena el disco."""
    return OSError(errno.ENOSPC, "No space left on device")


# ---------- (a) caída a mitad de la respuesta ----------


async def test_ia_cae_en_segunda_llamada_usa_fallback_honesto(monkeypatch):
    """2ª llamada (redacción tras las tools) lanza excepción: el código REAL
    responde «Consulta completada...»; no inventa ninguna acción."""
    _patch_common(monkeypatch)
    tool_call = {
        "id": "t1",
        "function": {"name": "manage_google_tasks", "arguments": '{"action": "create"}'},
    }
    llamadas = {"n": 0}

    async def respond(messages, tools):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            return "", [tool_call]
        raise OllamaClientError("conexion perdida a mitad de la respuesta")

    ejecutadas = []

    async def fake_exec(chat_id, func_name, args):
        ejecutadas.append(func_name)
        return {"success": True, "message": "Tarea creada: Comprar pilas"}

    monkeypatch.setattr("src.handlers.chat._execute_tool", fake_exec)
    monkeypatch.setattr(orch, "llm", _FakeLLM(respond))

    reply = await orch.generate_response("apunta la tarea comprar pilas", 1)

    # La herramienta SÍ se ejecutó antes de caerse la 2ª llamada...
    assert llamadas["n"] == 2
    assert ejecutadas == ["manage_google_tasks"]
    # ...pero la respuesta final es el fallback real, sin exito inventado.
    assert reply == "Consulta completada. Revisa el resultado de las herramientas."
    assert "He guardado" not in reply
    assert not orch._hallucination_risk(reply, "apunta la tarea comprar pilas")


# ---------- (b) sin respuesta en la primera llamada ----------


async def test_primera_llamada_con_timeout_degrada_con_mensaje_real(monkeypatch):
    """TimeoutError en la 1ª llamada → mensaje literal de `_prepare_tool_phase`."""
    _patch_common(monkeypatch)

    async def respond(messages, tools):
        raise TimeoutError("backend sin responder")

    monkeypatch.setattr(orch, "llm", _FakeLLM(respond))
    reply = await orch.generate_response("hola", 1)
    assert reply == (
        "Lo siento, el modelo tardo demasiado en responder. Intenta con un mensaje mas corto."
    )


async def test_primera_llamada_con_connection_error_degrada_con_mensaje_real(monkeypatch):
    """ConnectionError (nodo caído) → «Error interno al procesar la respuesta.»."""
    _patch_common(monkeypatch)

    async def respond(messages, tools):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(orch, "llm", _FakeLLM(respond))
    reply = await orch.generate_response("hola", 1)
    assert reply == "Error interno al procesar la respuesta."


async def test_primera_llamada_con_error_de_cliente_usa_mensaje_real(monkeypatch):
    """OllamaClientError (p. ej. «no responde tras 3 intentos») → «Error del
    modelo: <motivo real>», tal cual lo compone `_prepare_tool_phase`."""
    _patch_common(monkeypatch)

    async def respond(messages, tools):
        raise OllamaClientError("Ollama no responde tras 3 intentos: ConnectionResetError")

    monkeypatch.setattr(orch, "llm", _FakeLLM(respond))
    reply = await orch.generate_response("hola", 1)
    assert reply == ("Error del modelo: Ollama no responde tras 3 intentos: ConnectionResetError")


# ---------- (c) herramienta con error OAuth ----------


async def test_tool_con_error_oauth_no_permite_afirmar_exito(monkeypatch):
    """TODAS las tools devuelven success=false (OAuth revocado): la guardia
    `todas_fallidas` reemplaza el «He guardado...» del modelo por el error real."""
    _patch_common(monkeypatch)
    tool_call = {
        "id": "t1",
        "function": {"name": "manage_google_tasks", "arguments": '{"action": "create"}'},
    }
    llamadas = {"n": 0}

    async def respond(messages, tools):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            return "He guardado la tarea comprar pilas.", [tool_call]
        return "He guardado la tarea comprar pilas en tu lista.", None

    async def fake_exec(chat_id, func_name, args):
        return {"success": False, "error": "OAuth revocado (invalid_grant)"}

    monkeypatch.setattr("src.handlers.chat._execute_tool", fake_exec)
    monkeypatch.setattr(orch, "llm", _FakeLLM(respond))

    reply = await orch.generate_response("apunta la tarea comprar pilas", 1)

    assert "No he podido completar la acción" in reply
    assert "OAuth revocado (invalid_grant)" in reply
    assert "He guardado" not in reply
    assert not orch._hallucination_risk(reply, "apunta la tarea comprar pilas")


# ---------- (d) disco lleno (ENOSPC) ----------


async def test_disco_lleno_al_guardar_historial_no_revienta_la_peticion(monkeypatch, caplog):
    """ENOSPC guardando el historial del usuario: `_process_ai_message` no tiene
    guard, pero el wrapper del bot SÍ lo tiene: se loguea y se responde con el
    mensaje de error real, sin propagar (la petición no mata el proceso)."""
    from src.bot import RafitaBot
    from src.handlers import chat as chat_mod

    async def guardar(*args, **kwargs):
        raise _enospc()

    monkeypatch.setattr(chat_mod.db, "save_chat_message", guardar)
    monkeypatch.setattr(settings, "admin_ids", [1])

    respuestas = []

    class _Mensaje:
        text = "hola rafita"

        async def reply_text(self, texto, **kwargs):
            respuestas.append(texto)

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_message=_Mensaje(),
    )

    bot = RafitaBot()
    wrapped = bot._wrap(chat_mod.handle_message)
    with caplog.at_level(logging.ERROR):
        await wrapped(update, None)  # no debe lanzar

    assert respuestas == ["Ocurrió un error interno. El equipo ha sido notificado."]
    assert "Handler error for user 1" in caplog.text
    assert "No space left on device" in caplog.text


async def test_disco_lleno_en_escritura_de_estado_se_degrada_en_silencio(monkeypatch):
    """La escritura de estado/diario (`_save_diary_entry`) va en background y
    traga el ENOSPC: la respuesta ya está enviada y la petición continúa."""
    from src.handlers import chat as chat_mod
    from src.utils import obsidian_manager

    async def lleno(*args, **kwargs):
        raise _enospc()

    monkeypatch.setattr(settings, "persist_to_brain", True)
    monkeypatch.setattr(obsidian_manager, "create_or_append_note", lleno)

    # No lanza: el `except Exception` interno lo absorbe.
    assert await chat_mod._save_diary_entry(1, "hola", "respuesta") is None


async def test_orquestador_propaga_enospc_y_lo_contiene_el_wrapper(monkeypatch):
    """Seam real documentado: `generate_response` NO guarda el ENOSPC (los dos
    `db.save_chat_message` van sin try); la excepción sube hasta el wrapper
    del bot, que es quien la convierte en respuesta degradada."""
    from src.bot import RafitaBot

    _patch_common(monkeypatch)

    async def guardar(*args, **kwargs):
        raise _enospc()

    monkeypatch.setattr(orch.db, "save_chat_message", guardar)
    monkeypatch.setattr(orch, "llm", _FakeLLM(_plain("respuesta generada")))

    with pytest.raises(OSError):
        await orch.generate_response("hola", 1)

    # Mismo error, ya dentro del wrapper: degradación en vez de excepción.
    async def handler(update, context):
        raise _enospc()

    respuestas = []

    class _Mensaje:
        async def reply_text(self, texto, **kwargs):
            respuestas.append(texto)

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_message=_Mensaje(),
    )
    await RafitaBot()._wrap(handler)(update, None)
    assert respuestas == ["Ocurrió un error interno. El equipo ha sido notificado."]


# ---------- (e) infraestructura sin backend de IA ----------


def _checks_neutros(monkeypatch):
    """Neutraliza el resto de checks (sin red, disco ni base vectorial reales)."""

    def _sync(nombre):
        def _fn():
            return {"name": nombre, "ok": True, "severity": "info", "detail": "ok"}

        return _fn

    def _async(nombre):
        async def _fn():
            return {"name": nombre, "ok": True, "severity": "info", "detail": "ok"}

        return _fn

    from src.utils import infra_monitor

    monkeypatch.setattr(infra_monitor, "check_disk", _sync("disco"))
    monkeypatch.setattr(infra_monitor, "check_backup", _sync("backup"))
    monkeypatch.setattr(infra_monitor, "check_restore_drill", _sync("restore-drill"))
    monkeypatch.setattr(infra_monitor, "check_docker_services", _sync("docker"))
    monkeypatch.setattr(infra_monitor, "check_vector_db", _async("rag"))
    monkeypatch.setattr(infra_monitor, "check_whisper_remote", _async("stt"))
    monkeypatch.setattr(infra_monitor, "check_connectivity", _async("conectividad"))
    monkeypatch.setattr(infra_monitor, "check_certificates", _async("certificados"))


async def test_check_de_ia_sin_backend_se_marca_fallido(monkeypatch):
    """Seam real de `infra_monitor.check_llm`: `llm.check_health()` devuelve
    status unhealthy → check ok=False, severity crítico y detalle real."""
    from src.ollama_client import llm
    from src.utils import infra_monitor

    async def salud():
        return {"status": "unhealthy", "detail": "AI backend unreachable: connection refused"}

    monkeypatch.setattr(llm, "check_health", salud)
    check = await infra_monitor.check_llm()
    assert check["name"] == "ia"
    assert check["ok"] is False
    assert check["severity"] == "critical"
    assert "AI backend unreachable" in check["detail"]


async def test_check_de_ia_con_excepcion_se_degrada(monkeypatch):
    """Si el cliente revienta (nodo apagado), el check sigue devolviendo un
    dict de fallo en vez de lanzar: «IA inaccesible (<motivo>)»."""
    from src.ollama_client import llm
    from src.utils import infra_monitor

    async def salud():
        raise ConnectionError("sin backend local")

    monkeypatch.setattr(llm, "check_health", salud)
    check = await infra_monitor.check_llm()
    assert check["ok"] is False
    assert check["severity"] == "critical"
    assert check["detail"].startswith("IA inaccesible")
    assert "sin backend local" in check["detail"]


async def test_gauge_de_infraestructura_a_cero_sin_ia(monkeypatch):
    """`run_infra_checks` deja `infra_checks_ok` en 0.0 cuando la IA no está."""
    from src.ollama_client import llm
    from src.utils import infra_monitor
    from src.utils.telemetry import metrics

    _checks_neutros(monkeypatch)

    async def salud():
        return {"status": "unhealthy", "detail": "AI backend unreachable"}

    monkeypatch.setattr(llm, "check_health", salud)

    checks = await infra_monitor.run_infra_checks()
    ia = next(c for c in checks if c["name"] == "ia")
    assert ia["ok"] is False
    assert all(c["ok"] for c in checks if c["name"] != "ia")
    assert metrics.get_gauges()["infra_checks_ok"] == 0.0


async def test_gauge_de_infraestructura_a_uno_con_ia(monkeypatch):
    """Control: con la IA sana (y el resto neutralizado) el gauge sube a 1.0."""
    from src.ollama_client import llm
    from src.utils import infra_monitor
    from src.utils.telemetry import metrics

    _checks_neutros(monkeypatch)

    async def salud():
        return {"status": "ok", "model": "modelo-local"}

    monkeypatch.setattr(llm, "check_health", salud)

    checks = await infra_monitor.run_infra_checks()
    ia = next(c for c in checks if c["name"] == "ia")
    assert ia["ok"] is True
    assert metrics.get_gauges()["infra_checks_ok"] == 1.0
