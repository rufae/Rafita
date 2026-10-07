"""Tests de las mejoras de voz V1-V4 (2026-10-07).

- V1: clarify con STT vacio, glosario STT, gate por pico de voz.
- V2: streaming directo (sin tools), presupuesto de reintentos en llamada.
- V3: historial por turnos y resumen rodante (memory_summary).
"""

from src.config import Settings, settings
from src.core import memory_summary as ms
from src.core import orchestrator as orch

# ---------- helpers ----------


class _FakeTime:
    """Primera llamada = t0; el resto salta pasado el presupuesto de voz."""

    def __init__(self, t0: float = 100.0, t1: float = 200.0):
        self._t0 = t0
        self._t1 = t1
        self._n = 0

    def time(self) -> float:
        self._n += 1
        return self._t0 if self._n == 1 else self._t1


async def _patch_stream_common(monkeypatch, guardados, history=None):
    async def save(chat_id, role, content):
        guardados.append((role, content))

    async def get_history(*args, **kwargs):
        return history or []

    async def sin_tools(text, k=3):
        return [], 0.0

    monkeypatch.setattr(orch.db, "save_chat_message", save)
    monkeypatch.setattr(orch.db, "get_chat_history", get_history)
    monkeypatch.setattr(orch, "best_tools_for_message", sin_tools)


# ---------- V2: elegibilidad del streaming directo ----------


async def test_voice_stream_eligible_segun_score(monkeypatch):
    async def sin_tools(text, k=3):
        return [], 0.0

    async def accion(text, k=3):
        return [{"function": {"name": "search_gmail"}}], 0.62

    monkeypatch.setattr(settings, "voice_tool_min_score", 0.45)
    monkeypatch.setattr(orch, "best_tools_for_message", sin_tools)
    assert await orch._voice_stream_eligible("hola que tal") is True

    monkeypatch.setattr(orch, "best_tools_for_message", accion)
    assert await orch._voice_stream_eligible("busca un correo") is False

    # Umbral en cero: streaming directo desactivado.
    monkeypatch.setattr(settings, "voice_tool_min_score", 0.0)
    monkeypatch.setattr(orch, "best_tools_for_message", sin_tools)
    assert await orch._voice_stream_eligible("hola que tal") is False


async def test_voice_stream_eligible_embedding_cae(monkeypatch):
    async def boom(text, k=3):
        raise RuntimeError("embed caido")

    monkeypatch.setattr(settings, "voice_tool_min_score", 0.45)
    monkeypatch.setattr(orch, "best_tools_for_message", boom)
    assert await orch._voice_stream_eligible("hola") is False


async def test_streaming_directo_emite_sin_tools(monkeypatch):
    guardados: list = []
    await _patch_stream_common(monkeypatch, guardados)

    class _Llamada:
        async def chat_with_tools(self, *args, **kwargs):
            raise AssertionError("el flujo lento no deberia usarse")

        async def chat_stream_tokens(self, messages, max_tokens=180, repeat_penalty=None):
            for tok in ["Hola", ",", " ¿en", " qué", " puedo", " ayudarte", "?"]:
                yield tok

    monkeypatch.setattr(orch, "llm", _Llamada())
    tokens = [t async for t in orch.generate_response_stream("hola", 5, voice=True)]
    assert "".join(tokens) == "Hola, ¿en qué puedo ayudarte?"
    assert guardados[0] == ("user", "hola")
    assert guardados[-1] == ("assistant", "Hola, ¿en qué puedo ayudarte?")


async def test_streaming_directo_riesgo_cae_al_flujo_lento(monkeypatch):
    guardados: list = []
    await _patch_stream_common(monkeypatch, guardados)
    llamadas_prepare: list = []

    async def fake_prepare(text, chat_id, voice=False, save_message=True):
        llamadas_prepare.append(save_message)
        return [{"role": "system", "content": "x"}], "Hecho.", [], []

    class _Stream:
        async def chat_stream_tokens(self, messages, max_tokens=180, repeat_penalty=None):
            # Primera frase afirma una accion sin herramienta → riesgo.
            for tok in ["He", " guardado", " la", " tarea", "."]:
                yield tok

    monkeypatch.setattr(orch, "llm", _Stream())
    monkeypatch.setattr(orch, "_prepare_tool_phase", fake_prepare)
    tokens = [t async for t in orch.generate_response_stream("apunta tarea", 5, voice=True)]
    # Nada del primer intento se emitio; la respuesta sale del flujo lento.
    assert llamadas_prepare == [False]
    assert "".join(tokens) == "Hecho."
    assert guardados == [("user", "apunta tarea"), ("assistant", "Hecho.")]


async def test_streaming_directo_fallo_de_stream_cae_al_flujo_lento(monkeypatch):
    guardados: list = []
    await _patch_stream_common(monkeypatch, guardados)
    llamadas_prepare: list = []

    async def fake_prepare(text, chat_id, voice=False, save_message=True):
        llamadas_prepare.append(save_message)
        return [{"role": "system", "content": "x"}], "Ok.", [], []

    class _Roto:
        async def chat_stream_tokens(self, messages, max_tokens=180, repeat_penalty=None):
            raise RuntimeError("stream caido")
            yield  # pragma: no cover

    monkeypatch.setattr(orch, "llm", _Roto())
    monkeypatch.setattr(orch, "_prepare_tool_phase", fake_prepare)
    tokens = [t async for t in orch.generate_response_stream("hola", 5, voice=True)]
    assert llamadas_prepare == [False]
    assert "".join(tokens) == "Ok."


# ---------- V2: presupuesto de herramientas en llamada ----------


async def test_presupuesto_de_tools_agotado_en_voz(monkeypatch):
    async def save(*args, **kwargs):
        return None

    async def get_history(*args, **kwargs):
        return []

    async def select_tools(text):
        return []

    llamadas: list = []

    async def respond(messages, tools, max_tokens=512):
        llamadas.append(1)
        return "He guardado la tarea pendiente.", None

    class _SoloChat:
        async def chat_with_tools(self, messages, tools, max_tokens=512):
            return await respond(messages, tools)

    monkeypatch.setattr(orch.db, "save_chat_message", save)
    monkeypatch.setattr(orch.db, "get_chat_history", get_history)
    monkeypatch.setattr(orch, "select_tools_semantic", select_tools)
    monkeypatch.setattr(orch, "llm", _SoloChat())
    monkeypatch.setattr(orch, "_time", _FakeTime())

    _, content, tool_calls, _ = await orch._prepare_tool_phase("apunta tarea", 1, voice=True)
    # Un solo call: sin presupuesto para reintentos/forzados...
    assert len(llamadas) == 1
    assert tool_calls == []
    # ...pero la afirmacion de accion no se devuelve: honestidad primero.
    assert content == orch.HONEST_FALLBACK


async def test_presupuesto_intacto_en_voz_normal(monkeypatch):
    """Fuera de presupuesto (o sin llamada) el reintento sigue existiendo."""

    async def save(*args, **kwargs):
        return None

    async def get_history(*args, **kwargs):
        return []

    async def select_tools(text):
        return []

    async def best_tools(text, k=3):
        return [{"function": {"name": "manage_google_tasks"}}], 0.6

    llamadas: list = []

    async def respond(messages, tools, max_tokens=512):
        llamadas.append(1)
        if len(llamadas) == 1:
            return "He guardado la tarea.", None
        return "", [{"id": "c1", "function": {"name": "manage_google_tasks", "arguments": "{}"}}]

    class _Fake:
        async def chat_with_tools(self, messages, tools, max_tokens=512):
            return await respond(messages, tools)

    executed = []

    async def fake_execute(chat_id, func_name, args):
        executed.append(func_name)
        return {"success": True, "message": "ok"}

    from src.handlers import chat as chat_mod

    monkeypatch.setattr(orch.db, "save_chat_message", save)
    monkeypatch.setattr(orch.db, "get_chat_history", get_history)
    monkeypatch.setattr(orch, "select_tools_semantic", select_tools)
    monkeypatch.setattr(orch, "best_tools_for_message", best_tools)
    monkeypatch.setattr(orch, "llm", _Fake())
    monkeypatch.setattr(chat_mod, "_execute_tool", fake_execute)
    monkeypatch.setattr(orch, "_time", _FakeTime(t0=100.0, t1=101.0))

    _, _, tool_calls, _ = await orch._prepare_tool_phase("apunta tarea", 1, voice=True)
    assert len(llamadas) == 2
    assert executed == ["manage_google_tasks"]
    assert tool_calls


# ---------- V3: resumen rodante ----------


async def test_resumener_y_poda_guarda_resumen_y_poda(monkeypatch):
    llamadas: dict = {}

    async def stale(chat_id, hours=2, limit=40):
        return [
            {"role": "user", "content": "quiero mudarme a Cancun"},
            {"role": "assistant", "content": "vale"},
        ]

    async def get_sum(chat_id):
        return ""

    async def upsert(chat_id, summary):
        llamadas["resumen"] = summary

    async def delete(chat_id, hours=2):
        llamadas["poda"] = hours
        return 2

    class _LLM:
        async def chat(self, messages, temperature=None, max_tokens=None):
            return "El usuario quiere mudarse a Cancun."

    monkeypatch.setattr(ms.db, "get_stale_chat_messages", stale)
    monkeypatch.setattr(ms.db, "get_chat_summary", get_sum)
    monkeypatch.setattr(ms.db, "upsert_chat_summary", upsert)
    monkeypatch.setattr(ms.db, "delete_stale_chat_history", delete)
    monkeypatch.setattr(ms, "llm", _LLM())

    ok = await ms.resumener_y_poda(0, hours=2)
    assert ok is True
    assert llamadas["resumen"].startswith("El usuario")
    assert llamadas["poda"] == 2


async def test_resumener_y_poda_sin_llm_no_poda(monkeypatch):
    async def stale(chat_id, hours=2, limit=40):
        return [{"role": "user", "content": "algo"}]

    class _LLM:
        async def chat(self, messages, temperature=None, max_tokens=None):
            raise RuntimeError("llm caido")

    async def delete(chat_id, hours=2):
        raise AssertionError("no debe podar sin resumen")

    monkeypatch.setattr(ms.db, "get_stale_chat_messages", stale)
    monkeypatch.setattr(ms.db, "delete_stale_chat_history", delete)
    monkeypatch.setattr(ms, "llm", _LLM())

    assert await ms.resumener_y_poda(0) is False


async def test_resumener_y_poda_sin_mensajes_viejos(monkeypatch):
    async def stale(chat_id, hours=2, limit=40):
        return []

    monkeypatch.setattr(ms.db, "get_stale_chat_messages", stale)
    assert await ms.resumener_y_poda(0) is False


async def test_get_summary_block_acorta_y_vacio(monkeypatch):
    async def mucho(chat_id):
        return ("resumen viejo. " * 200).strip()

    async def fallo(chat_id):
        raise RuntimeError("db caida")

    monkeypatch.setattr(ms.db, "get_chat_summary", mucho)
    bloque = await ms.get_summary_block(1)
    assert "Resumen de lo hablado" in bloque
    assert len(bloque) <= ms.SUMMARY_MAX_CHARS + 200

    monkeypatch.setattr(ms.db, "get_chat_summary", fallo)
    assert await ms.get_summary_block(1) == ""

    async def vacio(chat_id):
        return ""

    monkeypatch.setattr(ms.db, "get_chat_summary", vacio)
    assert await ms.get_summary_block(1) == ""


async def test_build_messages_resumen_solo_en_voz(monkeypatch):
    limites: list = []

    async def get_history(chat_id, limit):
        limites.append(limit)
        return []

    async def save(*args, **kwargs):
        return None

    async def resumen(chat_id):
        return "\n\nResumen de lo hablado antes de esta sesion: hablamos de la mudanza."

    monkeypatch.setattr(orch.db, "get_chat_history", get_history)
    monkeypatch.setattr(orch.db, "save_chat_message", save)
    monkeypatch.setattr(ms, "get_summary_block", resumen)

    mensajes = await orch._build_messages("hola", 9, voice=True)
    assert limites == [16]
    assert mensajes[1]["role"] == "system"
    assert "mudanza" in mensajes[1]["content"]

    limites.clear()
    mensajes = await orch._build_messages("hola", 9, voice=False)
    assert limites == [12]
    assert all("mudanza" not in (m.get("content") or "") for m in mensajes)


# ---------- V1: glosario STT y defaults de config ----------


def test_stt_prompt_incluye_glosario_de_nombres(monkeypatch):
    from src import i18n

    monkeypatch.setattr(settings, "language", "es")
    monkeypatch.setattr(settings, "stt_prompt_extra", "Soraya, Moonlight")
    prompt = i18n.stt_prompt()
    assert "Nombres propios" in prompt
    assert "Soraya" in prompt and "Moonlight" in prompt

    monkeypatch.setattr(settings, "stt_prompt_extra", "")
    assert "Nombres propios" not in i18n.stt_prompt()


def test_settings_voz_v1234_defaults(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_TOKEN=dummy\n", encoding="utf-8")
    s = Settings(_env_file=env)
    assert s.voice_history_turns == 16
    assert s.voice_tool_min_score == 0.45
    assert s.voice_tool_budget_s == 45.0
    assert s.voice_filler_delay_s == 3.0
    assert s.stt_prompt_extra == ""
    # Overrides
    env2 = tmp_path / "override.env"
    env2.write_text(
        "TELEGRAM_TOKEN=dummy\nVOICE_HISTORY_TURNS=24\nVOICE_TOOL_MIN_SCORE=0.5\n"
        "VOICE_TOOL_BUDGET_S=60\nVOICE_FILLER_DELAY_S=5\nSTT_PROMPT_EXTRA=Soraya\n",
        encoding="utf-8",
    )
    s2 = Settings(_env_file=env2)
    assert s2.voice_history_turns == 24
    assert s2.voice_tool_min_score == 0.5
    assert s2.voice_tool_budget_s == 60.0
    assert s2.voice_filler_delay_s == 5.0
    assert s2.stt_prompt_extra == "Soraya"


async def test_streaming_directo_emite_tras_primera_frase(monkeypatch):
    guardados: list = []
    await _patch_stream_common(monkeypatch, guardados)

    class _Llamada:
        async def chat_with_tools(self, *args, **kwargs):
            raise AssertionError("el flujo lento no deberia usarse")

        async def chat_stream_tokens(self, messages, max_tokens=180, repeat_penalty=None):
            for tok in ["Hola", ".", " ¿Cómo", " estás", "?"]:
                yield tok

    monkeypatch.setattr(orch, "llm", _Llamada())
    tokens = [t async for t in orch.generate_response_stream("hola", 5, voice=True)]
    # La primera frase se valida ("Hola.") y el resto fluye token a token.
    assert "".join(tokens) == "Hola. ¿Cómo estás?"
    assert tokens[0] == "Hola." and tokens[1] == " ¿Cómo"


async def test_streaming_directo_sin_frase_final_emite_el_buffer(monkeypatch):
    guardados: list = []
    await _patch_stream_common(monkeypatch, guardados)

    class _Llamada:
        async def chat_with_tools(self, *args, **kwargs):
            raise AssertionError("el flujo lento no deberia usarse")

        async def chat_stream_tokens(self, messages, max_tokens=180, repeat_penalty=None):
            for tok in ["hola", " sin", " punto"]:
                yield tok

    monkeypatch.setattr(orch, "llm", _Llamada())
    tokens = [t async for t in orch.generate_response_stream("hola", 5, voice=True)]
    assert "".join(tokens) == "hola sin punto"


async def test_streaming_directo_riesgo_al_final_del_buffer(monkeypatch):
    guardados: list = []
    await _patch_stream_common(monkeypatch, guardados)
    llamadas_prepare: list = []

    async def fake_prepare(text, chat_id, voice=False, save_message=True):
        llamadas_prepare.append(save_message)
        return [], "Vale.", [], []

    class _Llamada:
        async def chat_with_tools(self, messages, tools, max_tokens=512):
            raise AssertionError("no deberia llegar aqui")

        async def chat_stream_tokens(self, messages, max_tokens=180, repeat_penalty=None):
            # Nunca cierra frase (<60 chars): la decision la toca el buffer final.
            for tok in ["he", " guardado", " la", " tarea"]:
                yield tok

    monkeypatch.setattr(orch, "llm", _Llamada())
    monkeypatch.setattr(orch, "_prepare_tool_phase", fake_prepare)
    tokens = [t async for t in orch.generate_response_stream("apunta tarea", 5, voice=True)]
    assert llamadas_prepare == [False]
    assert "".join(tokens) == "Vale."


# ---------- V3: bordes de resumener_y_poda ----------


async def test_resumener_y_poda_rechaza_texto_vacio(monkeypatch):
    async def stale(chat_id, hours=2, limit=40):
        return [{"role": "user", "content": "algo"}]

    async def delete(chat_id, hours=2):
        raise AssertionError("no debe podar con resumen vacio")

    class _LLM:
        async def chat(self, messages, temperature=None, max_tokens=None):
            return "   "

    monkeypatch.setattr(ms.db, "get_stale_chat_messages", stale)
    monkeypatch.setattr(ms.db, "delete_stale_chat_history", delete)
    monkeypatch.setattr(ms, "llm", _LLM())
    assert await ms.resumener_y_poda(0) is False


async def test_resumener_y_poda_acorta_resumen_largo(monkeypatch):
    llamadas: dict = {}

    async def stale(chat_id, hours=2, limit=40):
        return [{"role": "user", "content": "x"}]

    async def get_sum(chat_id):
        return "viejo. " * 500

    async def upsert(chat_id, summary):
        llamadas["resumen"] = summary

    async def delete(chat_id, hours=2):
        return 1

    class _LLM:
        async def chat(self, messages, temperature=None, max_tokens=None):
            return "nuevo. " * 500

    monkeypatch.setattr(ms.db, "get_stale_chat_messages", stale)
    monkeypatch.setattr(ms.db, "get_chat_summary", get_sum)
    monkeypatch.setattr(ms.db, "upsert_chat_summary", upsert)
    monkeypatch.setattr(ms.db, "delete_stale_chat_history", delete)
    monkeypatch.setattr(ms, "llm", _LLM())

    assert await ms.resumener_y_poda(0) is True
    assert len(llamadas["resumen"]) == ms.SUMMARY_MAX_CHARS * 2
    assert llamadas["resumen"].endswith("nuevo.")


async def test_resumener_y_poda_ignora_si_ya_hay_uno_en_vuelo():
    ms._inflight.add(42)
    try:
        assert await ms.resumener_y_poda(42) is False
    finally:
        ms._inflight.discard(42)
