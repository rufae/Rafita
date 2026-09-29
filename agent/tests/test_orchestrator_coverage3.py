"""Cobertura de core/orchestrator.py: _prepare_tool_phase, generate_response y stream."""

from src.config import settings
from src.core import orchestrator as orch
from src.ollama_client import OllamaClientError


class _FakeLLM:
    def __init__(self, respond, stream_tokens=None):
        self._respond = respond
        self._stream = stream_tokens
        self.calls = []

    async def chat_with_tools(self, messages, tools, max_tokens=512):
        self.calls.append({"messages": messages, "tools": tools})
        return await self._respond(messages, tools)

    async def chat_stream_tokens(self, messages, max_tokens=180, repeat_penalty=None):
        if isinstance(self._stream, Exception):
            raise self._stream
        for token in self._stream or ["respuesta"]:
            yield token


def _patch_common(monkeypatch, history=None):
    async def save(*args, **kwargs):
        return None

    async def get_history(*args, **kwargs):
        return history or []

    async def select_tools(text):
        return []

    monkeypatch.setattr(orch.db, "save_chat_message", save)
    monkeypatch.setattr(orch.db, "get_chat_history", get_history)
    monkeypatch.setattr(orch, "select_tools_semantic", select_tools)


def _plain(content, tool_calls=None):
    async def respond(messages, tools):
        return content, tool_calls

    return respond


# ---------- prompt / fecha ----------


def test_date_context_line_falls_back_on_bad_timezone(monkeypatch):
    monkeypatch.setattr(settings, "timezone", "Zona/Invalida")
    line = orch.date_context_line()
    assert line.startswith("[Contexto: hoy es")
    assert "Zona/Invalida" in line


def test_build_system_prompt_voice_and_text_survive_bad_timezone(monkeypatch):
    monkeypatch.setattr(settings, "timezone", "Zona/Invalida")
    assert "VOICE_RULE" in orch.build_system_prompt(voice=True)
    assert "FORMAT_RULE" in orch.build_system_prompt(voice=False)


# ---------- _prepare_tool_phase ----------


async def test_prepare_tool_phase_plain_response(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(orch, "llm", _FakeLLM(_plain("hola!")))
    messages, content, tool_calls, tools = await orch._prepare_tool_phase("hola", 1)
    assert content == "hola!"
    assert tool_calls == []
    assert messages[0]["role"] == "system"
    assert messages[-1]["role"] == "user"
    assert "[Contexto:" in messages[-1]["content"]


async def test_prepare_tool_phase_appends_user_message_when_missing(monkeypatch):
    _patch_common(monkeypatch, history=[{"role": "assistant", "content": "previo" + "x" * 600}])
    monkeypatch.setattr(orch, "llm", _FakeLLM(_plain("ok")))
    messages, *_ = await orch._prepare_tool_phase("pregunta", 1)
    roles = [m["role"] for m in messages]
    assert roles.count("user") == 1
    assert messages[-1]["content"].endswith("pregunta")
    assert len(messages[1]["content"]) <= 503


async def test_prepare_tool_phase_injects_date_into_history_user(monkeypatch):
    _patch_common(monkeypatch, history=[{"role": "user", "content": "pregunta previa"}])
    monkeypatch.setattr(orch, "llm", _FakeLLM(_plain("ok")))
    messages, *_ = await orch._prepare_tool_phase("ahora", 1)
    assert "[Contexto:" in messages[-1]["content"]
    assert messages[-1]["content"].endswith("pregunta previa")


async def test_prepare_tool_phase_runs_tools_and_appends_results(monkeypatch):
    _patch_common(monkeypatch)
    tool_call = {
        "id": "t1",
        "function": {"name": "save_expense", "arguments": '{"amount": 1}'},
    }
    monkeypatch.setattr(orch, "llm", _FakeLLM(_plain("", [tool_call])))
    executed = []

    async def fake_execute(chat_id, func_name, args):
        executed.append((func_name, args))
        return {"success": True, "message": "ok"}

    monkeypatch.setattr("src.handlers.chat._execute_tool", fake_execute)
    messages, content, tool_calls, _ = await orch._prepare_tool_phase("gasto", 1)
    assert executed == [("save_expense", {"amount": 1})]
    assert tool_calls == [tool_call]
    assert messages[-1]["role"] == "tool"
    assert messages[-2]["role"] == "assistant"


async def test_prepare_tool_phase_tolerates_bad_tool_args(monkeypatch):
    _patch_common(monkeypatch)
    tool_call = {"id": "t1", "function": {"name": "save_expense", "arguments": "{no json}"}}
    monkeypatch.setattr(orch, "llm", _FakeLLM(_plain("", [tool_call])))
    executed = []

    async def fake_execute(chat_id, func_name, args):
        executed.append(args)
        return {"success": True, "message": "ok"}

    monkeypatch.setattr("src.handlers.chat._execute_tool", fake_execute)
    await orch._prepare_tool_phase("gasto", 1)
    assert executed == [{}]


async def test_prepare_tool_phase_model_timeouts(monkeypatch):
    _patch_common(monkeypatch)

    async def respond_timeout(messages, tools):
        raise TimeoutError

    monkeypatch.setattr(orch, "llm", _FakeLLM(respond_timeout))
    messages, content, tool_calls, tools = await orch._prepare_tool_phase("hola", 1)
    assert "tardo demasiado" in content
    assert tool_calls == [] and tools == []

    async def respond_client_error(messages, tools):
        raise OllamaClientError("sin servidor")

    monkeypatch.setattr(orch, "llm", _FakeLLM(respond_client_error))
    _, content, _, _ = await orch._prepare_tool_phase("hola", 1)
    assert "Error del modelo: sin servidor" in content

    async def respond_boom(messages, tools):
        raise RuntimeError("fallo interno")

    monkeypatch.setattr(orch, "llm", _FakeLLM(respond_boom))
    _, content, _, _ = await orch._prepare_tool_phase("hola", 1)
    assert "Error interno" in content


# ---------- generate_response ----------


async def test_generate_response_saves_plain_answer(monkeypatch):
    _patch_common(monkeypatch)
    saved = []

    async def fake_save(*args, **kwargs):
        saved.append(args)

    monkeypatch.setattr(orch, "llm", _FakeLLM(_plain("hola humano")))
    monkeypatch.setattr(orch.db, "save_chat_message", fake_save)
    result = await orch.generate_response("hola", 1)
    assert result == "hola humano"
    assert saved


async def test_generate_response_second_call_with_tools(monkeypatch):
    _patch_common(monkeypatch)
    tool_call = {"id": "t1", "function": {"name": "search_knowledge", "arguments": "{}"}}
    calls = {"n": 0}

    async def respond(messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return "", [tool_call]
        return "resumen final", None

    async def fake_execute(chat_id, func_name, args):
        return {"success": True, "message": "ok"}

    monkeypatch.setattr("src.handlers.chat._execute_tool", fake_execute)
    monkeypatch.setattr(orch, "llm", _FakeLLM(respond))
    result = await orch.generate_response("que sabes", 1)
    assert result == "resumen final"
    assert calls["n"] == 2


async def test_generate_response_second_call_failure_falls_back(monkeypatch):
    _patch_common(monkeypatch)
    tool_call = {"id": "t1", "function": {"name": "search_knowledge", "arguments": "{}"}}
    calls = {"n": 0}

    async def respond(messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return "", [tool_call]
        raise RuntimeError("llm caido")

    async def fake_execute(chat_id, func_name, args):
        return {"success": True, "message": "ok"}

    monkeypatch.setattr("src.handlers.chat._execute_tool", fake_execute)
    monkeypatch.setattr(orch, "llm", _FakeLLM(respond))
    result = await orch.generate_response("que sabes", 1)
    assert "Consulta completada" in result


async def test_generate_response_empty_content(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(orch, "llm", _FakeLLM(_plain("")))
    result = await orch.generate_response("hola", 1)
    assert "No pude generar una respuesta" in result


# ---------- generate_response_stream ----------


async def test_generate_response_stream_with_tools(monkeypatch):
    _patch_common(monkeypatch)
    tool_call = {"id": "t1", "function": {"name": "search_knowledge", "arguments": "{}"}}
    calls = {"n": 0}

    async def respond(messages, tools):
        calls["n"] += 1
        return ("", [tool_call]) if calls["n"] == 1 else ("", None)

    async def fake_execute(chat_id, func_name, args):
        return {"success": True, "message": "ok"}

    monkeypatch.setattr("src.handlers.chat._execute_tool", fake_execute)
    monkeypatch.setattr(orch, "llm", _FakeLLM(respond, stream_tokens=["Hola", " ", "mundo"]))
    tokens = [t async for t in orch.generate_response_stream("hola", 1)]
    assert "".join(tokens) == "Hola mundo"


async def test_generate_response_stream_falls_back_on_stream_error(monkeypatch):
    _patch_common(monkeypatch)
    tool_call = {"id": "t1", "function": {"name": "search_knowledge", "arguments": "{}"}}
    calls = {"n": 0}

    async def respond(messages, tools):
        calls["n"] += 1
        return ("", [tool_call]) if calls["n"] == 1 else ("", None)

    async def fake_execute(chat_id, func_name, args):
        return {"success": True, "message": "ok"}

    monkeypatch.setattr("src.handlers.chat._execute_tool", fake_execute)
    monkeypatch.setattr(orch, "llm", _FakeLLM(respond, stream_tokens=RuntimeError("stream caido")))
    tokens = [t async for t in orch.generate_response_stream("hola", 1)]
    assert "".join(tokens) == "Consulta completada. Revisa el resultado."


async def test_generate_response_stream_word_chunks_without_tools(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(orch, "llm", _FakeLLM(_plain("hola mundo")))
    tokens = [t async for t in orch.generate_response_stream("hola", 1)]
    assert "".join(tokens) == "hola mundo"
    assert tokens[-1] == "mundo"


async def test_generate_response_stream_empty_content(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(orch, "llm", _FakeLLM(_plain("")))
    tokens = [t async for t in orch.generate_response_stream("hola", 1)]
    assert "".join(tokens) == "No pude generar una respuesta."


def test_chunk_words_keeps_spacing():
    assert list(orch._chunk_words("un dos tres")) == ["un ", "dos ", "tres"]


# ---------- guardia de honestidad (2026-09-29) ----------


async def test_prepare_tool_phase_reintenta_accion_sin_herramienta(monkeypatch):
    """Si afirma una accion sin tool_calls, reintenta y ejecuta la herramienta."""
    _patch_common(monkeypatch)
    llamadas = []

    async def respond(messages, tools):
        llamadas.append(1)
        if len(llamadas) == 1:
            return "Buscando un correo sobre Anabel. Un momento por favor.", None
        return "", [
            {"id": "c1", "function": {"name": "search_gmail", "arguments": '{"query": "anabel"}'}}
        ]

    monkeypatch.setattr(orch, "llm", _FakeLLM(respond))
    ejecutados = []

    async def fake_execute(chat_id, func_name, args):
        ejecutados.append((func_name, args))
        return {"success": True, "message": "ok"}

    monkeypatch.setattr("src.handlers.chat._execute_tool", fake_execute)
    _, _content, tool_calls, _ = await orch._prepare_tool_phase("busca un correo sobre anabel", 1)
    assert len(llamadas) == 2
    assert ejecutados == [("search_gmail", {"query": "anabel"})]
    assert tool_calls and tool_calls[0]["function"]["name"] == "search_gmail"


async def test_prepare_tool_phase_sin_reintento_si_no_afirma_accion(monkeypatch):
    _patch_common(monkeypatch)
    llamadas = []

    async def respond(messages, tools):
        llamadas.append(1)
        return "No encuentro ningun correo de Anabel.", None

    monkeypatch.setattr(orch, "llm", _FakeLLM(respond))
    _, content, tool_calls, _ = await orch._prepare_tool_phase("busca un correo", 1)
    assert len(llamadas) == 1
    assert tool_calls == []
    assert "No encuentro" in content


async def test_prepare_tool_phase_reintento_fallido_se_tolera(monkeypatch):
    _patch_common(monkeypatch)

    async def respond(messages, tools):
        ultimo = messages[-1]
        if ultimo.get("role") == "system" and "AVISO" in (ultimo.get("content") or ""):
            raise RuntimeError("boom")
        return "He guardado la tarea.", None

    monkeypatch.setattr(orch, "llm", _FakeLLM(respond))
    _, content, tool_calls, _ = await orch._prepare_tool_phase("guarda esta tarea", 1)
    assert tool_calls == []
    assert "He guardado" in content


async def test_hallucination_risk_detecta_emails_y_afirmaciones():
    assert orch._hallucination_risk("He guardado la tarea.", "guarda una tarea")
    assert orch._hallucination_risk("Te escribio anabel@x.com", "busca un correo")
    assert orch._hallucination_risk("Searchando en tus correos...", "busca un correo")
    assert not orch._hallucination_risk("Tu correo es yo@x.com", "mi correo es yo@x.com")
    assert not orch._hallucination_risk("No encuentro nada.", "busca un correo")


async def test_prepare_tool_phase_fallback_honesto_si_reintento_tambien_inventa(monkeypatch):
    _patch_common(monkeypatch)

    async def respond(messages, tools):
        return "El correo de anabel@inventado.com es importante.", None

    monkeypatch.setattr(orch, "llm", _FakeLLM(respond))
    _, content, tool_calls, _ = await orch._prepare_tool_phase("busca un correo sobre anabel", 1)
    assert tool_calls == []
    assert content == orch.HONEST_FALLBACK


async def test_prepare_tool_phase_reintenta_por_email_inventado(monkeypatch):
    _patch_common(monkeypatch)
    llamadas = []

    async def respond(messages, tools):
        llamadas.append(1)
        if len(llamadas) == 1:
            return "He encontrado un correo de anabel@x.com", None
        return "", [
            {"id": "c1", "function": {"name": "search_gmail", "arguments": '{"query": "anabel"}'}}
        ]

    monkeypatch.setattr(orch, "llm", _FakeLLM(respond))

    async def fake_execute(chat_id, func_name, args):
        return {"success": True, "message": "sin resultados"}

    monkeypatch.setattr("src.handlers.chat._execute_tool", fake_execute)
    _, _content, tool_calls, _ = await orch._prepare_tool_phase("busca un correo sobre anabel", 1)
    assert len(llamadas) == 2
    assert tool_calls and tool_calls[0]["function"]["name"] == "search_gmail"
