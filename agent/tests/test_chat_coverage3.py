"""Cobertura extra de handlers/chat.py: comandos, _process_ai_message y helpers."""

from types import SimpleNamespace

import pytest

from src.config import settings
from src.handlers import chat as chat_mod
from src.ollama_client import OllamaClientError
from src.utils.access_control import SlidingWindowLimiter


class _Msg:
    def __init__(self, text=None):
        self.text = text
        self.replies = []
        self.voices = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))

    async def reply_voice(self, voice, **kwargs):
        self.voices.append(voice)

    async def reply_chat_action(self, *args, **kwargs):
        return None


def _update(text="hola", user_id=1, with_user=True):
    user = SimpleNamespace(id=user_id, first_name="Ana") if with_user else None
    return SimpleNamespace(effective_message=_Msg(text), effective_user=user)


def _ctx(args=None, user_data=None):
    return SimpleNamespace(args=args or [], user_data=user_data or {})


async def _afn(result=None, exc=None):
    if exc is not None:
        raise exc
    return result


# ---------- comandos ----------


async def test_start_command_welcomes_user():
    update = _update()
    await chat_mod.start_command(update, None)
    assert "Ana" in update.effective_message.replies[0][0]
    assert "ayuda" in update.effective_message.replies[0][0]


async def test_start_command_without_user_is_noop():
    update = _update(with_user=False)
    await chat_mod.start_command(update, None)
    assert not update.effective_message.replies


async def test_ayuda_command_lists_registry():
    update = _update()
    await chat_mod.ayuda_command(update, None)
    text, kwargs = update.effective_message.replies[0]
    assert "Comandos disponibles" in text
    assert kwargs.get("parse_mode") == "Markdown"


async def test_chat_command_without_args_shows_usage():
    update = _update()
    await chat_mod.chat_command(update, _ctx(args=[]))
    assert "Usa: /chat" in update.effective_message.replies[0][0]


async def test_chat_command_without_message_is_noop():
    update = SimpleNamespace(effective_message=None, effective_user=SimpleNamespace(id=1))
    await chat_mod.chat_command(update, _ctx(args=["hola"]))
    assert update.effective_message is None


async def test_chat_command_dispatches_to_ai(monkeypatch):
    seen = {}

    async def fake_process(update, text, context, from_voice=False):
        seen["text"] = text

    monkeypatch.setattr(chat_mod, "_process_ai_message", fake_process)
    update = _update()
    await chat_mod.chat_command(update, _ctx(args=["hola", "que", "tal"]))
    assert seen["text"] == "hola que tal"


async def test_limpiar_command_clears_history(monkeypatch):
    calls = []

    async def fake_clear(user_id):
        calls.append(user_id)

    monkeypatch.setattr(chat_mod.db, "clear_chat_history", fake_clear)
    update = _update(user_id=42)
    await chat_mod.limpiar_command(update, None)
    assert calls == [42]
    assert "eliminado" in update.effective_message.replies[0][0]


async def test_limpiar_command_without_user_is_noop():
    update = _update(with_user=False)
    await chat_mod.limpiar_command(update, None)
    assert not update.effective_message.replies


# ---------- handle_message ----------


async def test_handle_message_without_user_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", [])
    update = _update(text="hola", with_user=False)
    await chat_mod.handle_message(update, _ctx())
    assert not update.effective_message.replies


async def test_handle_message_blank_text_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", [])
    update = _update(text="   ")
    await chat_mod.handle_message(update, _ctx())
    assert not update.effective_message.replies


async def test_handle_message_rejects_unauthorized_user(monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", [999])
    update = _update(text="hola", user_id=1)
    await chat_mod.handle_message(update, _ctx())
    assert "no está autorizado" in update.effective_message.replies[0][0]


async def test_handle_message_rate_limited(monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", [1])
    monkeypatch.setattr(
        "src.utils.access_control.chat_limiter",
        SimpleNamespace(allow=lambda key: False),
    )
    update = _update(text="hola", user_id=1)
    await chat_mod.handle_message(update, _ctx())
    assert "Vas muy rápido" in update.effective_message.replies[0][0]


async def test_handle_message_allowed_user_reaches_ai(monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", [1])
    monkeypatch.setattr(
        "src.utils.access_control.chat_limiter",
        SlidingWindowLimiter(max_events=5, window_seconds=60.0),
    )
    seen = {}

    async def fake_process(update, text, context, from_voice=False):
        seen["text"] = text

    monkeypatch.setattr(chat_mod, "_process_ai_message", fake_process)
    update = _update(text="  hola  ", user_id=1)
    await chat_mod.handle_message(update, _ctx())
    assert seen["text"] == "hola"


# ---------- _detect_tool_intent ----------


def test_detect_tool_intent_short_text_is_false():
    assert not chat_mod._detect_tool_intent("hi")


def test_detect_tool_intent_multiword_keyword_matches():
    assert chat_mod._detect_tool_intent("un resumen financiero del mes")
    assert not chat_mod._detect_tool_intent("que bonito esta el dia")


# ---------- helpers puros ----------


def test_md_to_html_full_syntax():
    out = chat_mod._md_to_html("# Titulo\n**negrita** `codigo` [link](https://a.io)\n- item *cur*")
    assert "<b>Titulo</b>" in out
    assert "<b>negrita</b>" in out
    assert "<code>codigo</code>" in out
    assert '<a href="https://a.io">link</a>' in out
    assert "• item" in out
    assert "<i>cur</i>" in out


def test_format_telegram_response_closes_trailing_table():
    formatted, mode = chat_mod.format_telegram_response("| a | b |\n|---|---|\n| 1 | 2 |")
    assert mode == "HTML"
    assert formatted.rstrip().endswith("</pre>")


async def test_reply_formatted_chunks_long_text():
    message = _Msg()
    long_text = "\n".join("linea %d con contenido" % i for i in range(400))
    await chat_mod._reply_formatted(message, long_text)
    assert len(message.replies) > 1
    assert all(len(text) <= 4000 for text, _ in message.replies)


async def test_send_response_without_message_is_noop():
    update = SimpleNamespace(effective_message=None)
    await chat_mod._send_response_with_audio_interceptor(update, None, "hola")


async def test_audio_interceptor_sends_voice(monkeypatch, tmp_path):
    async def fake_tts(text):
        return tmp_path / "out.wav"

    async def fake_ogg(path):
        out = tmp_path / "out.ogg"
        out.write_bytes(b"OGG")
        return out

    monkeypatch.setattr("src.utils.tts_manager.text_to_speech", fake_tts)
    monkeypatch.setattr("src.utils.tts_manager.convert_to_ogg", fake_ogg)
    message = _Msg()
    await chat_mod._send_response_with_audio_interceptor(
        SimpleNamespace(effective_message=message), None, "[Audio]hola[/Audio]\nresumen"
    )
    assert message.voices
    assert "resumen" in message.replies[0][0]


async def test_audio_interceptor_tts_failure_falls_back(monkeypatch):
    async def fake_tts(text):
        return None

    monkeypatch.setattr("src.utils.tts_manager.text_to_speech", fake_tts)
    message = _Msg()
    await chat_mod._send_response_with_audio_interceptor(
        SimpleNamespace(effective_message=message), None, "[Audio]hola[/Audio]"
    )
    assert "hola" in message.replies[0][0]


async def test_audio_interceptor_ogg_failure_and_exception(monkeypatch, tmp_path):
    async def fake_tts(text):
        return tmp_path / "out.wav"

    async def fake_ogg_none(path):
        return None

    monkeypatch.setattr("src.utils.tts_manager.text_to_speech", fake_tts)
    monkeypatch.setattr("src.utils.tts_manager.convert_to_ogg", fake_ogg_none)
    message = _Msg()
    await chat_mod._send_response_with_audio_interceptor(
        SimpleNamespace(effective_message=message), None, "[Audio]hola[/Audio]"
    )
    assert message.replies

    async def fake_tts_boom(text):
        raise RuntimeError("tts caido")

    monkeypatch.setattr("src.utils.tts_manager.text_to_speech", fake_tts_boom)
    message2 = _Msg()
    await chat_mod._send_response_with_audio_interceptor(
        SimpleNamespace(effective_message=message2), None, "[Audio]adios[/Audio]"
    )
    assert "adios" in message2.replies[0][0]


async def test_audio_interceptor_skips_empty_audio_tags():
    message = _Msg()
    await chat_mod._send_response_with_audio_interceptor(
        SimpleNamespace(effective_message=message), None, "[Audio]   [/Audio]\nsolo texto"
    )
    assert message.replies and "solo texto" in message.replies[0][0]


# ---------- _save_diary_entry ----------


async def test_save_diary_entry_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "persist_to_brain", False)
    called = []

    async def fake_create(**kwargs):
        called.append(kwargs)

    monkeypatch.setattr("src.utils.obsidian_manager.create_or_append_note", fake_create)
    await chat_mod._save_diary_entry(1, "hola", "adios")
    assert not called


async def test_save_diary_entry_writes_note(monkeypatch):
    monkeypatch.setattr(settings, "persist_to_brain", True)
    called = []

    async def fake_create(**kwargs):
        called.append(kwargs)
        return {"success": True}

    monkeypatch.setattr("src.utils.obsidian_manager.create_or_append_note", fake_create)
    await chat_mod._save_diary_entry(1, "pregunta", "respuesta")
    assert called and "Conversacion" in called[0]["content"]


async def test_save_diary_entry_swallows_errors(monkeypatch):
    monkeypatch.setattr(settings, "persist_to_brain", True)

    async def fake_create(**kwargs):
        raise RuntimeError("vault roto")

    monkeypatch.setattr("src.utils.obsidian_manager.create_or_append_note", fake_create)
    await chat_mod._save_diary_entry(1, "pregunta", "respuesta")


# ---------- n8n ----------


class _FakeResp:
    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text


class _FakeAsyncClient:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None):
        self.posts.append((url, json))
        if self._exc is not None:
            raise self._exc
        return self._resp


async def test_post_n8n_webhook_success(monkeypatch):
    fake = _FakeAsyncClient(resp=_FakeResp(200))
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: fake)
    ok, detail = await chat_mod._post_n8n_webhook("http://n8n/x", {"a": 1})
    assert ok and detail == "HTTP 200"
    assert fake.posts == [("http://n8n/x", {"a": 1})]


async def test_post_n8n_webhook_http_error(monkeypatch):
    fake = _FakeAsyncClient(resp=_FakeResp(500, "boom"))
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: fake)
    ok, detail = await chat_mod._post_n8n_webhook("http://n8n/x", None)
    assert not ok
    assert "500" in detail


async def test_post_n8n_webhook_connection_error(monkeypatch):
    fake = _FakeAsyncClient(exc=RuntimeError("sin red"))
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: fake)
    ok, detail = await chat_mod._post_n8n_webhook("http://n8n/x", {})
    assert not ok
    assert "No pude contactar" in detail


async def test_trigger_n8n_wraps_non_dict_payload(monkeypatch):
    calls = {}

    async def fake_post(url, payload):
        calls["payload"] = payload
        return True, "HTTP 200"

    monkeypatch.setattr(chat_mod, "_post_n8n_webhook", fake_post)
    result = await chat_mod._trigger_n8n({"workflow": "http://n8n/x", "payload": "sueldo"})
    assert result["success"]
    assert calls["payload"] == {"value": "sueldo"}


async def test_trigger_n8n_invalid_mapping_json(monkeypatch):
    monkeypatch.setattr(settings, "n8n_webhooks", "{no es json")
    result = await chat_mod._trigger_n8n({"workflow": "facturas"})
    assert not result["success"]
    assert "N8N_WEBHOOKS" in result["message"]


async def test_trigger_n8n_case_insensitive_lookup(monkeypatch):
    monkeypatch.setattr(settings, "n8n_webhooks", '{"facturas": "http://n8n/y"}')
    calls = {}

    async def fake_post(url, payload):
        calls["url"] = url
        return True, "HTTP 200"

    monkeypatch.setattr(chat_mod, "_post_n8n_webhook", fake_post)
    result = await chat_mod._trigger_n8n({"workflow": "Facturas"})
    assert result["success"]
    assert calls["url"] == "http://n8n/y"


# ---------- _resolve_contact_alias ----------


async def test_resolve_contact_alias_uses_canon_keys(monkeypatch):
    seen = []

    async def fake_search(scope, key):
        seen.append(key)
        return [{"value": "Aa Mama"}] if key == "madre" else []

    monkeypatch.setattr(chat_mod.db, "search_personal_knowledge", fake_search)
    alias = await chat_mod._resolve_contact_alias(1, "el telefono de mi mama")
    assert alias == "Aa Mama"
    assert "mama" in seen and "madre" in seen


async def test_resolve_contact_alias_error_returns_empty(monkeypatch):
    async def fake_search(scope, key):
        raise RuntimeError("db caida")

    monkeypatch.setattr(chat_mod.db, "search_personal_knowledge", fake_search)
    assert await chat_mod._resolve_contact_alias(1, "mi madre") == ""


async def test_resolve_contact_alias_without_kinship_is_empty(monkeypatch):
    async def fake_search(scope, key):
        pytest.fail("no debe buscarse alias sin parentesco")

    monkeypatch.setattr(chat_mod.db, "search_personal_knowledge", fake_search)
    assert await chat_mod._resolve_contact_alias(1, "el clima de hoy") == ""


# ---------- _process_ai_message ----------


def _patch_llm(monkeypatch, chat_with_tools=None, chat=None):
    fake = SimpleNamespace()
    fake.chat_with_tools = chat_with_tools or (lambda **kwargs: _afn(("hola", None)))
    fake.chat = chat or (lambda **kwargs: _afn("respuesta"))
    monkeypatch.setattr(chat_mod, "llm", fake)
    monkeypatch.setattr(chat_mod, "select_tools_semantic", lambda text: _afn([]))
    monkeypatch.setattr(settings, "persist_to_brain", False)


def _patch_db(monkeypatch, history=None):
    monkeypatch.setattr(chat_mod.db, "save_chat_message", lambda *a, **k: _afn(None))
    monkeypatch.setattr(chat_mod.db, "get_chat_history", lambda *a, **k: _afn(history or []))


async def test_process_ai_message_requires_message_and_user():
    no_message = SimpleNamespace(effective_message=None, effective_user=SimpleNamespace(id=1))
    assert await chat_mod._process_ai_message(no_message, "hola", None) is None
    no_user = SimpleNamespace(effective_message=_Msg("hola"), effective_user=None)
    assert await chat_mod._process_ai_message(no_user, "hola", None) is None


async def test_process_ai_message_timeout_text(monkeypatch):
    _patch_db(monkeypatch)

    async def boom(**kwargs):
        raise TimeoutError

    _patch_llm(monkeypatch, chat_with_tools=boom)
    update = _update()
    result = await chat_mod._process_ai_message(update, "hola", _ctx())
    assert result == "timeout"
    assert "tardando demasiado" in update.effective_message.replies[0][0]


async def test_process_ai_message_timeout_vision(monkeypatch):
    _patch_db(monkeypatch)

    async def boom(**kwargs):
        raise TimeoutError

    _patch_llm(monkeypatch, chat_with_tools=boom)
    update = _update()
    ctx = _ctx(user_data={"processing_image": True})
    result = await chat_mod._process_ai_message(update, "hola", ctx)
    assert result == "timeout"
    assert "imagen" in update.effective_message.replies[0][0]


async def test_process_ai_message_model_client_error(monkeypatch):
    _patch_db(monkeypatch)

    async def boom(**kwargs):
        raise OllamaClientError("ollama caido")

    _patch_llm(monkeypatch, chat_with_tools=boom)
    update = _update()
    result = await chat_mod._process_ai_message(update, "hola", _ctx())
    assert "ollama caido" in result
    assert "⚠️" in update.effective_message.replies[0][0]


async def test_process_ai_message_bad_tool_args_and_log_failure(monkeypatch):
    _patch_db(monkeypatch)

    async def fake_log(*args, **kwargs):
        raise RuntimeError("log caido")

    async def fake_vector(*args, **kwargs):
        raise RuntimeError("vector caido")

    monkeypatch.setattr(chat_mod.db, "log_second_brain_query", fake_log)
    monkeypatch.setattr(chat_mod.vector_db, "query", fake_vector)
    tool_call = {
        "id": "c1",
        "function": {"name": "search_second_brain", "arguments": "{no es json}"},
    }

    async def with_tools(**kwargs):
        return ("", [tool_call])

    async def second_call(**kwargs):
        return "redaccion final"

    _patch_llm(monkeypatch, chat_with_tools=with_tools, chat=second_call)
    monkeypatch.setattr(
        chat_mod, "_execute_tool", lambda *a, **k: _afn({"success": True, "message": "ok"})
    )
    update = _update()
    result = await chat_mod._process_ai_message(update, "hola", _ctx())
    assert "redaccion final" in result


async def test_process_ai_message_failed_tool_and_fallback_text(monkeypatch):
    _patch_db(monkeypatch)
    tool_call = {"id": "c1", "function": {"name": "save_expense", "arguments": "{}"}}

    async def with_tools(**kwargs):
        return ("contenido base", [tool_call])

    async def second_call_boom(**kwargs):
        raise RuntimeError("llm caido")

    _patch_llm(monkeypatch, chat_with_tools=with_tools, chat=second_call_boom)
    monkeypatch.setattr(
        chat_mod, "_execute_tool", lambda *a, **k: _afn({"success": False, "message": "denegado"})
    )
    update = _update()
    result = await chat_mod._process_ai_message(update, "hola", _ctx())
    assert "Error: denegado" in result
    assert "contenido base" in result


async def test_process_ai_message_long_response_skips_diary(monkeypatch):
    _patch_db(monkeypatch)
    long_text = "x" * 5000

    async def with_tools(**kwargs):
        return (long_text, None)

    _patch_llm(monkeypatch, chat_with_tools=with_tools)
    update = _update()
    result = await chat_mod._process_ai_message(update, "hola", _ctx())
    assert result == long_text
