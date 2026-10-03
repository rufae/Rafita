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


async def test_hallucination_risk_cubre_verbos_que_faltaban():
    # El 2026-09-30 afirmo tareas borradas/completadas y correos programados
    # sin llamar a ninguna herramienta: eran falsos negativos de la guardia.
    assert orch._hallucination_risk("He eliminado la tarea.", "borra la tarea")
    assert orch._hallucination_risk("He borrado el evento.", "borra el evento")
    assert orch._hallucination_risk("He marcan como hecha la tarea.", "completa la tarea")
    assert orch._hallucination_risk("Ya he programado el envio.", "envia un correo")
    assert orch._hallucination_risk("He movido la cita.", "mueve la cita")
    assert orch._hallucination_risk("He actualizado el evento.", "cambia el evento")


async def test_hallucination_risk_no_confunde_negaciones_ni_negativas():
    # Falsos positivos del 2026-09-30: respuestas honestas castigadas.
    assert not orch._hallucination_risk("No he encontrado nada.", "busca en mis notas")
    assert not orch._hallucination_risk("No encontre resultados.", "busca en mis notas")
    assert not orch._hallucination_risk("Sin resultados en tus notas.", "busca en mis notas")
    assert not orch._hallucination_risk(
        "No tengo nada apuntado sobre eso.", "tengo algo apuntado sobre eso"
    )
    assert not orch._hallucination_risk(
        "No he podido comprobar esa informacion.", "que tiempo hace"
    )
    assert not orch._hallucination_risk("No puedo buscar en internet.", "busca noticias")
    # Sin negacion, sigue avisando:
    assert orch._hallucination_risk("Encontre dos notas tuyas.", "busca en mis notas")
    assert orch._hallucination_risk("Aqui tienes la lista.", "que tareas tengo")
    # "Busqueda realizada..." sin acento cazado (2026-09-30: el modelo
    # invento un diagrama mermaid de los pinguinos del Sahara).
    assert orch._hallucination_risk("Busqueda realizada en tus notas:", "busca notas")
    assert orch._hallucination_risk("Búsqueda realizada en tus notas:", "busca notas")


async def test_prepare_tool_phase_fallback_honesto_si_reintento_tambien_inventa(monkeypatch):
    _patch_common(monkeypatch)

    async def respond(messages, tools):
        return "El correo de anabel@inventado.com es importante.", None

    monkeypatch.setattr(orch, "llm", _FakeLLM(respond))
    _, content, tool_calls, _ = await orch._prepare_tool_phase("busca un correo sobre anabel", 1)
    assert tool_calls == []
    assert content == orch.HONEST_FALLBACK


async def test_prepare_tool_phase_fuerza_top_tool_si_el_modelo_no_llama(monkeypatch):
    """El mismo mensaje funcionaba o no segun la ejecucion (2026-09-30): si el
    modelo no llama ni con el aviso, se le ofrece UNA sola herramienta."""
    _patch_common(monkeypatch)
    fake_tool = {
        "type": "function",
        "function": {"name": "manage_google_tasks", "description": "tareas"},
    }

    async def best_tools(text, k=3):
        return [fake_tool], 0.8

    monkeypatch.setattr(orch, "best_tools_for_message", best_tools)

    respuestas = [
        ("He borrado la tarea.", None),
        ("No he podido comprobar esa informacion.", None),
        (
            None,
            [
                {
                    "id": "1",
                    "function": {"name": "manage_google_tasks", "arguments": "{}"},
                }
            ],
        ),
    ]

    class _Respuestas:
        def __init__(self):
            self.calls = []

        async def chat_with_tools(self, messages, tools, max_tokens=512):
            self.calls.append(tools)
            return respuestas[len(self.calls) - 1]

    fake = _Respuestas()
    monkeypatch.setattr(orch, "llm", fake)

    ejecutadas = []

    async def fake_exec(chat_id, name, args):
        ejecutadas.append(name)
        return {"success": True, "message": "borrada"}

    from src.handlers import chat as chat_mod

    monkeypatch.setattr(chat_mod, "_execute_tool", fake_exec)

    _, _content, tool_calls, _ = await orch._prepare_tool_phase("borra la tarea", 1)
    assert tool_calls and tool_calls[0]["function"]["name"] == "manage_google_tasks"
    assert ejecutadas == ["manage_google_tasks"]
    assert len(fake.calls) == 3
    assert len(fake.calls[2]) == 1  # el reintento forzado ofrece una sola tool


async def test_prepare_tool_phase_forzado_sin_confianza_ofrece_top3(monkeypatch):
    """Si el top-1 no es fiable (0.50-0.60) se ofrecen el top-3 con aviso
    duro: 'borra el evento' daba create_google_calendar_event 0.55."""
    _patch_common(monkeypatch)
    fake_tool = {
        "type": "function",
        "function": {"name": "create_google_calendar_event", "description": "crear"},
    }
    manage_tool = {"type": "function", "function": {"name": "manage_google_calendar"}}

    async def best_tools(text, k=3):
        return [fake_tool, manage_tool], 0.55

    monkeypatch.setattr(orch, "best_tools_for_message", best_tools)

    async def select_tools(text):
        return [fake_tool, manage_tool]

    monkeypatch.setattr(orch, "select_tools_semantic", select_tools)

    class _Respuestas:
        def __init__(self):
            self.calls = []

        async def chat_with_tools(self, messages, tools, max_tokens=512):
            self.calls.append(tools)
            if len(self.calls) < 3:
                return "He borrado el evento.", None
            return None, [
                {
                    "id": "1",
                    "function": {"name": "manage_google_calendar", "arguments": "{}"},
                }
            ]

    fake = _Respuestas()
    monkeypatch.setattr(orch, "llm", fake)

    async def fake_exec(chat_id, name, args):
        return {"success": True, "message": "borrado"}

    from src.handlers import chat as chat_mod

    monkeypatch.setattr(chat_mod, "_execute_tool", fake_exec)

    _, _content, tool_calls, _ = await orch._prepare_tool_phase("borra el evento", 1)
    assert tool_calls and tool_calls[0]["function"]["name"] == "manage_google_calendar"
    assert len(fake.calls[2]) == 2  # todas las seleccionadas, no solo el top-1


async def test_prepare_tool_phase_fuerza_y_si_falla_responde_honesto(monkeypatch):
    _patch_common(monkeypatch)
    fake_tool = {
        "type": "function",
        "function": {"name": "save_expense", "description": "gastos"},
    }

    async def best_tools(text, k=3):
        return [fake_tool], 0.7

    monkeypatch.setattr(orch, "best_tools_for_message", best_tools)

    async def respond(messages, tools):
        return "He registrado el gasto.", None

    monkeypatch.setattr(orch, "llm", _FakeLLM(respond))
    _, content, tool_calls, _ = await orch._prepare_tool_phase("registra 5 euros", 1)
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


async def test_guardia_caza_he_anotado(monkeypatch):
    """Regresion: 'He anotado la tarea' sin herramienta debe reintentarse."""
    _patch_common(monkeypatch)
    llamadas = []

    async def respond(messages, tools):
        llamadas.append(1)
        if len(llamadas) == 1:
            return 'He anotado la tarea "comprar pilas".', None
        return "", [
            {
                "id": "c1",
                "function": {
                    "name": "manage_google_tasks",
                    "arguments": '{"action": "create", "title": "comprar pilas"}',
                },
            }
        ]

    monkeypatch.setattr(orch, "llm", _FakeLLM(respond))

    async def fake_execute(chat_id, func_name, args):
        return {"success": True, "message": "ok"}

    monkeypatch.setattr("src.handlers.chat._execute_tool", fake_execute)
    _, _content, tool_calls, _ = await orch._prepare_tool_phase("apunta comprar pilas", 1)
    assert len(llamadas) == 2
    assert tool_calls and tool_calls[0]["function"]["name"] == "manage_google_tasks"


async def test_generate_response_composicion_vacia_reintenta_sin_tools(monkeypatch):
    """Visto 2026-09-30: tras usar una herramienta el modelo devolvio texto
    vacio ("No pude generar una respuesta"). Se reintenta sin herramientas."""
    _patch_common(monkeypatch)

    class _FakeComposicion:
        def __init__(self):
            self.llamadas = 0

        async def chat_with_tools(self, messages, tools, max_tokens=512):
            self.llamadas += 1
            if self.llamadas == 1:
                return None, [{"id": "1", "function": {"name": "get_weather", "arguments": "{}"}}]
            return None, None

        async def chat(self, messages, max_tokens=512):
            return "Hoy hace sol en Sevilla."

    monkeypatch.setattr(orch, "llm", _FakeComposicion())

    async def fake_exec(chat_id, name, args):
        return {"success": True, "message": "sol"}

    from src.handlers import chat as chat_mod

    monkeypatch.setattr(chat_mod, "_execute_tool", fake_exec)

    reply = await orch.generate_response("que tiempo hace", 1)
    assert reply == "Hoy hace sol en Sevilla."


async def test_generate_response_composicion_vacia_total_responde_honesto(monkeypatch):
    _patch_common(monkeypatch)

    class _FakeVacio:
        def __init__(self):
            self.llamadas = 0

        async def chat_with_tools(self, messages, tools, max_tokens=512):
            self.llamadas += 1
            if self.llamadas == 1:
                return None, [{"id": "1", "function": {"name": "get_weather", "arguments": "{}"}}]
            return None, None

        async def chat(self, messages, max_tokens=512):
            return ""

    monkeypatch.setattr(orch, "llm", _FakeVacio())

    async def fake_exec(chat_id, name, args):
        return {"success": True, "message": "sol"}

    from src.handlers import chat as chat_mod

    monkeypatch.setattr(chat_mod, "_execute_tool", fake_exec)

    reply = await orch.generate_response("que tiempo hace", 1)
    assert "no he podido redactar" in reply


async def test_prepare_tool_phase_forzado_elige_por_texto(monkeypatch):
    """Sin confianza en el ranking (0.50-0.60), el modelo elige la herramienta
    en texto y se fuerza esa (el ranking por embeddings es ruidoso)."""
    _patch_common(monkeypatch)
    tool_a = {"type": "function", "function": {"name": "create_event", "description": "citas"}}
    tool_b = {"type": "function", "function": {"name": "manage_google_tasks"}}

    async def best_tools(text, k=3):
        return [tool_a], 0.52

    monkeypatch.setattr(orch, "best_tools_for_message", best_tools)

    async def select_tools(text):
        return [tool_a, tool_b]

    monkeypatch.setattr(orch, "select_tools_semantic", select_tools)

    class _FakeSelector:
        def __init__(self):
            self.tools_calls = []

        async def chat_with_tools(self, messages, tools, max_tokens=512):
            self.tools_calls.append(tools)
            if len(self.tools_calls) < 3:
                return "He apuntado la cita.", None
            return None, [
                {
                    "id": "1",
                    "function": {"name": "manage_google_tasks", "arguments": "{}"},
                }
            ]

        async def chat(self, messages, max_tokens=24):
            return "manage_google_tasks"

    fake = _FakeSelector()
    monkeypatch.setattr(orch, "llm", fake)

    async def fake_exec(chat_id, name, args):
        return {"success": True, "message": "tarea"}

    from src.handlers import chat as chat_mod

    monkeypatch.setattr(chat_mod, "_execute_tool", fake_exec)

    _, _content, tool_calls, _ = await orch._prepare_tool_phase(
        "apunta que tengo que comprar pilas", 1
    )
    assert tool_calls and tool_calls[0]["function"]["name"] == "manage_google_tasks"
    assert fake.tools_calls[2] == [tool_b]


async def test_generate_response_tool_fallida_no_permite_afirmar_exito(monkeypatch):
    """Visto 2026-09-30: manage_google_calendar create fallo (sin fecha) y el
    modelo respondio 'He anadido una cita...'. Si TODAS las tools fallan y el
    texto afirma exito, se responde con el error real."""
    _patch_common(monkeypatch)

    class _FakeFallo:
        def __init__(self):
            self.llamadas = 0

        async def chat_with_tools(self, messages, tools, max_tokens=512):
            self.llamadas += 1
            if self.llamadas == 1:
                return None, [
                    {"id": "1", "function": {"name": "manage_google_calendar", "arguments": "{}"}}
                ]
            return "He anadido una cita para manana a las 10.", None

    monkeypatch.setattr(orch, "llm", _FakeFallo())

    async def fake_exec(chat_id, name, args):
        return {
            "success": False,
            "message": "Título y fecha/hora son obligatorios para crear un evento.",
        }

    from src.handlers import chat as chat_mod

    monkeypatch.setattr(chat_mod, "_execute_tool", fake_exec)

    reply = await orch.generate_response("apunta una cita manana", 1)
    assert "No he podido completar la acción" in reply
    assert "obligatorios" in reply


async def test_prepare_tool_phase_recuperacion_si_todas_fallan(monkeypatch):
    """Si todas las tools fallan, el modelo elige otra en texto (catalogo
    completo) y se fuerza; si acierta, se usan esos resultados."""
    _patch_common(monkeypatch)
    tool_mala = {"type": "function", "function": {"name": "create_event", "description": "eventos"}}

    async def select_tools(text):
        return [tool_mala]

    monkeypatch.setattr(orch, "select_tools_semantic", select_tools)

    class _FakeRecuperacion:
        def __init__(self):
            self.llamadas = []

        async def chat_with_tools(self, messages, tools, max_tokens=512):
            self.llamadas.append(tools)
            if len(self.llamadas) == 1:
                return None, [{"id": "1", "function": {"name": "create_event", "arguments": "{}"}}]
            return None, [
                {"id": "2", "function": {"name": "manage_google_tasks", "arguments": "{}"}}
            ]

        async def chat(self, messages, max_tokens=24):
            return "manage_google_tasks"

    fake = _FakeRecuperacion()
    monkeypatch.setattr(orch, "llm", fake)

    ejecutadas = []

    async def fake_exec(chat_id, name, args):
        ejecutadas.append(name)
        if name == "create_event":
            return {"success": False, "message": "fecha no valida"}
        return {"success": True, "message": "tarea borrada"}

    from src.handlers import chat as chat_mod

    monkeypatch.setattr(chat_mod, "_execute_tool", fake_exec)

    _, _content, tool_calls, _ = await orch._prepare_tool_phase("borra la tarea X", 1)
    assert ejecutadas == ["create_event", "manage_google_tasks"]
    assert tool_calls and tool_calls[0]["function"]["name"] == "manage_google_tasks"


def test_es_negacion_falsa_frases_reales():
    assert orch._es_negacion_falsa("No tengo acceso a tu Drive.")
    assert orch._es_negacion_falsa(
        "No he podido obtener la lista porque no se han proporcionado resultados "
        "a través de la herramienta correspondiente."
    )
    assert orch._es_negacion_falsa("No me ha devuelto resultados.")
    assert not orch._es_negacion_falsa("En tu Drive tienes 3 carpetas.")
    assert orch._es_negacion_falsa("No hay elementos en tu Drive.")
    assert orch._es_negacion_falsa("No hay archivos ni carpetas disponibles.")
    assert orch._es_negacion_falsa(
        "Necesito consultar tu cuenta. Por favor, un momento mientras accedo."
    )


def test_es_saludo_generico_y_ultimo_mensaje_tool():
    assert orch._es_saludo_generico("Hola, soy Rafita. ¿En qué puedo ayudarte hoy?")
    assert orch._es_saludo_generico("¡Hola! ¿En qué puedo ayudarte?")
    assert not orch._es_saludo_generico("En tu Drive tienes 3 carpetas: ...")
    assert not orch._es_saludo_generico("")
    mensajes = [
        {"role": "tool", "content": '{"success": true, "message": "lista de Drive"}'},
        {"role": "tool", "content": "no-json"},
    ]
    assert orch._ultimo_mensaje_tool(mensajes) == "lista de Drive"
    assert orch._ultimo_mensaje_tool([{"role": "user", "content": "x"}]) == ""


async def test_generate_response_saludo_generico_tras_tools_reintenta(monkeypatch):
    """Bug 2026-09-30: tras list_google_drive respondio "Hola, soy Rafita..."."""
    _patch_common(monkeypatch)

    class _FakeSaludo:
        def __init__(self):
            self.n = 0

        async def chat_with_tools(self, messages, tools, max_tokens=512):
            self.n += 1
            if self.n == 1:
                return None, [
                    {"id": "1", "function": {"name": "list_google_drive", "arguments": "{}"}}
                ]
            return "Hola, soy Rafita. ¿En qué puedo ayudarte hoy?", None

        async def chat(self, messages, max_tokens=512):
            return "En tu Drive tienes la carpeta Proyectos y 2 archivos."

    monkeypatch.setattr(orch, "llm", _FakeSaludo())

    async def fake_exec(chat_id, name, args):
        return {"success": True, "message": "📂 Contenido de tu Google Drive: ..."}

    from src.handlers import chat as chat_mod

    monkeypatch.setattr(chat_mod, "_execute_tool", fake_exec)

    reply = await orch.generate_response("que carpetas tengo en mi drive", 1)
    assert "Drive" in reply
    assert not orch._es_saludo_generico(reply)


async def test_generate_response_saludo_generico_usa_mensaje_tool(monkeypatch):
    _patch_common(monkeypatch)

    class _FakeSaludoSiempre:
        def __init__(self):
            self.n = 0

        async def chat_with_tools(self, messages, tools, max_tokens=512):
            self.n += 1
            if self.n == 1:
                return None, [
                    {"id": "1", "function": {"name": "list_google_drive", "arguments": "{}"}}
                ]
            return "Hola, soy Rafita. ¿En qué puedo ayudarte hoy?", None

        async def chat(self, messages, max_tokens=512):
            return "Hola, soy Rafita. ¿En qué puedo ayudarte hoy?"

    monkeypatch.setattr(orch, "llm", _FakeSaludoSiempre())

    async def fake_exec(chat_id, name, args):
        return {
            "success": True,
            "message": "📂 Contenido de tu Drive: carpetas Proyectos, Fotos y 3 archivos",
        }

    from src.handlers import chat as chat_mod

    monkeypatch.setattr(chat_mod, "_execute_tool", fake_exec)

    reply = await orch.generate_response("que carpetas tengo en mi drive", 1)
    assert reply == "📂 Contenido de tu Drive: carpetas Proyectos, Fotos y 3 archivos"


async def test_generate_response_stream_saludo_generico_usa_mensaje_tool(monkeypatch):
    _patch_common(monkeypatch)

    class _FakeStream:
        def __init__(self):
            self.n = 0

        async def chat_with_tools(self, messages, tools, max_tokens=512):
            self.n += 1
            return None, [{"id": "1", "function": {"name": "list_google_drive", "arguments": "{}"}}]

        async def chat_stream_tokens(self, messages, max_tokens=180, repeat_penalty=None):
            for tok in ["Hola", ",", " soy", " Rafita", ".", " ¿En qué puedo ayudarte?"]:
                yield tok

    monkeypatch.setattr(orch, "llm", _FakeStream())

    async def fake_exec(chat_id, name, args):
        return {
            "success": True,
            "message": "📂 Contenido de tu Google Drive: carpetas Proyectos y Fotos",
        }

    from src.handlers import chat as chat_mod

    monkeypatch.setattr(chat_mod, "_execute_tool", fake_exec)

    salida = ""
    async for chunk in orch.generate_response_stream("que carpetas tengo", 1, voice=True):
        salida += chunk
    assert "Proyectos" in salida
    assert "Hola" not in salida


async def test_generate_response_negacion_falsa_usa_mensaje_tool(monkeypatch):
    """2026-09-30: list_google_drive devolvio la lista y el modelo dijo que no
    tenia acceso; se reintenta y si insiste se usa el mensaje de la tool."""
    _patch_common(monkeypatch)

    class _FakeNegacion:
        def __init__(self):
            self.n = 0

        async def chat_with_tools(self, messages, tools, max_tokens=512):
            self.n += 1
            if self.n == 1:
                return None, [
                    {"id": "1", "function": {"name": "list_google_drive", "arguments": "{}"}}
                ]
            return "No tengo acceso a tu Google Drive en este momento.", None

        async def chat(self, messages, max_tokens=512):
            return "No puedo acceder a esa informacion."

    monkeypatch.setattr(orch, "llm", _FakeNegacion())

    async def fake_exec(chat_id, name, args):
        return {
            "success": True,
            "message": "📂 Contenido de tu Google Drive: carpetas Proyectos y Fotos",
        }

    from src.handlers import chat as chat_mod

    monkeypatch.setattr(chat_mod, "_execute_tool", fake_exec)

    reply = await orch.generate_response("que carpetas tengo en mi drive", 1)
    assert reply == "📂 Contenido de tu Google Drive: carpetas Proyectos y Fotos"


async def test_recuperacion_reintenta_la_misma_tool_con_args_malos(monkeypatch):
    """gemma manda action vacia; la recuperacion puede reintentar la MISMA
    herramienta (antes se saltaba por estar ya probada)."""
    _patch_common(monkeypatch)
    tool = {"type": "function", "function": {"name": "manage_google_tasks", "description": "t"}}

    async def select_tools(text):
        return [tool]

    monkeypatch.setattr(orch, "select_tools_semantic", select_tools)

    async def best_tools(text, k=3):
        return [tool], 0.9

    monkeypatch.setattr(orch, "best_tools_for_message", best_tools)

    class _Fake:
        def __init__(self):
            self.n = 0

        async def chat_with_tools(self, messages, tools, max_tokens=512):
            self.n += 1
            if self.n == 1:
                # Primer intento: action vacia (falla).
                return None, [
                    {"id": "1", "function": {"name": "manage_google_tasks", "arguments": "{}"}}
                ]
            # Recuperacion forzada: ahora si rellena los args.
            return None, [
                {
                    "id": "3",
                    "function": {
                        "name": "manage_google_tasks",
                        "arguments": '{"action": "create", "title": "Comprar pilas"}',
                    },
                }
            ]

        async def chat(self, messages, max_tokens=24):
            return "manage_google_tasks"

    monkeypatch.setattr(orch, "llm", _Fake())

    ejecutadas = []

    async def fake_exec(chat_id, name, args):
        ejecutadas.append(args)
        if not args.get("action"):
            return {"success": False, "message": "Acción no válida o vacía"}
        return {"success": True, "message": "Tarea creada"}

    from src.handlers import chat as chat_mod

    monkeypatch.setattr(chat_mod, "_execute_tool", fake_exec)

    _, _content, tool_calls, _ = await orch._prepare_tool_phase("apunta comprar pilas", 1)
    assert ejecutadas[-1].get("action") == "create"
    assert tool_calls and tool_calls[0]["function"]["name"] == "manage_google_tasks"


def test_tiene_placeholders():
    assert orch._tiene_placeholders("| [Nombre de la carpeta 1] | [ID 1] |")
    assert orch._tiene_placeholders("fecha: [fecha]")
    assert not orch._tiene_placeholders("📂 Carpetas: Personal, Automatizaciones")


def test_tool_lista_ignorada():
    mensajes = [
        {
            "role": "tool",
            "content": '{"success": true, "message": "📂 Drive:\\n  • 📁 Personal (id: 1aZ)\\n  • 📄 Informe.pdf (id: 2b)"}',
        }
    ]
    assert orch._tool_lista_ignorada(mensajes, "No tengo acceso a tu Drive.")
    assert orch._tool_lista_ignorada(mensajes, "[Nombre de la carpeta 1] [ID 1]")
    assert not orch._tool_lista_ignorada(
        mensajes, "Tienes la carpeta Personal y el archivo Informe.pdf"
    )
    # Un resumen sin ningun nombre tambien se sustituye por la lista real.
    assert orch._tool_lista_ignorada(mensajes, "En tu Drive tienes 2 elementos.")
    # Sin lista no aplica
    sin_lista = [{"role": "tool", "content": '{"success": true, "message": "Hoy 20 grados"}'}]
    assert not orch._tool_lista_ignorada(sin_lista, "No tengo acceso.")


# ---------- citas [S#] y Fuentes (mejora 1) ----------


def test_grounding_rules_incluye_cite_rule():
    assert "CITE_RULE" in orch.GROUNDING_RULES
    assert "[S1]" in orch.GROUNDING_RULES


async def test_generate_response_anade_fuentes_de_citas(monkeypatch):
    _patch_common(monkeypatch)
    tool_call = {
        "id": "t1",
        "function": {"name": "search_second_brain", "arguments": '{"query": "Ana"}'},
    }

    async def respond(messages, tools):
        if not any(m.get("role") == "tool" for m in messages):
            return "", [tool_call]
        return "Ana trabaja en Babel [S1] y vive en Sevilla [S2].", None

    monkeypatch.setattr(orch, "llm", _FakeLLM(respond))
    executed = []

    async def fake_execute(chat_id, func_name, args):
        executed.append(func_name)
        from src.utils.citations import citations

        sid = citations.add("ana.md", "Trabajo", "obsidian://ana")
        return {"success": True, "message": "[%s] fragmento sobre Ana" % sid, "sources": []}

    monkeypatch.setattr("src.handlers.chat._execute_tool", fake_execute)
    out = await orch.generate_response("que dice Ana", 1)
    assert executed == ["search_second_brain"]
    assert "[S1]" in out
    assert "[S2]" not in out  # S2 nunca se registro: se elimina
    assert "Fuentes:" in out
    assert "ana.md" in out
    assert "[abrir](obsidian://ana)" in out


async def test_generate_response_stream_voz_quita_marcas(monkeypatch):
    _patch_common(monkeypatch)
    tool_call = {
        "id": "t1",
        "function": {"name": "search_second_brain", "arguments": '{"query": "Ana"}'},
    }

    async def respond(messages, tools):
        return "", [tool_call]

    async def stream(messages, max_tokens=180, repeat_penalty=None):
        for token in ["Ana", " trabaja", " en", " Babel", " [S1]", "."]:
            yield token

    fake = _FakeLLM(respond, stream_tokens=["Ana", " trabaja", " en", " Babel", " [S1]", "."])
    monkeypatch.setattr(orch, "llm", fake)

    async def fake_execute(chat_id, func_name, args):
        from src.utils.citations import citations

        citations.add("ana.md", "Trabajo", "obsidian://ana")
        return {"success": True, "message": "• Ana trabaja en Babel"}

    monkeypatch.setattr("src.handlers.chat._execute_tool", fake_execute)
    chunks = [c async for c in orch.generate_response_stream("que dice Ana", 1, voice=True)]
    full = "".join(chunks)
    assert "[S1]" not in full
    assert "Fuentes" not in full
    assert "Ana trabaja en Babel." in full
