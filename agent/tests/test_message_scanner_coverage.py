"""Cobertura de src/utils/message_scanner.py: escaneo batch de mensajes."""

from src.utils import message_scanner as ms


class _FakeLLM:
    def __init__(self, content="extraido", error=None):
        self.content = content
        self.error = error
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.content


def _patch_db(monkeypatch, history=None, kv=None):
    stored = {}

    async def fake_kv_get(key):
        return (kv or {}).get(key)

    async def fake_kv_set(key, value, expires_at=None):
        stored[key] = value

    async def fake_history(chat_id, limit=50):
        return history or []

    monkeypatch.setattr(ms.db, "kv_get", fake_kv_get)
    monkeypatch.setattr(ms.db, "kv_set", fake_kv_set)
    monkeypatch.setattr(ms.db, "get_chat_history", fake_history)
    return stored


# ---------- _extract_batch_with_llm ----------


async def test_extract_batch_empty_messages_skips_llm(monkeypatch):
    fake = _FakeLLM()
    monkeypatch.setattr("src.ollama_client.llm.chat", fake.chat)
    assert await ms._extract_batch_with_llm(1, []) == []
    assert fake.calls == []


async def test_extract_batch_returns_llm_response(monkeypatch):
    fake = _FakeLLM(content="guardado en Obsidian")
    monkeypatch.setattr("src.ollama_client.llm.chat", fake.chat)
    result = await ms._extract_batch_with_llm(1, ["hola", "guardar esto"])
    assert result == [{"type": "llm_response", "content": "guardado en Obsidian"}]
    assert len(fake.calls) == 1
    system_prompt = fake.calls[0]["messages"][0]["content"]
    assert "[0] hola" in system_prompt
    assert "[1] guardar esto" in system_prompt
    assert fake.calls[0]["temperature"] == 0.3


async def test_extract_batch_truncates_long_messages(monkeypatch):
    fake = _FakeLLM()
    monkeypatch.setattr("src.ollama_client.llm.chat", fake.chat)
    await ms._extract_batch_with_llm(1, ["x" * 800])
    system_prompt = fake.calls[0]["messages"][0]["content"]
    assert "x" * 501 not in system_prompt
    assert "x" * 500 in system_prompt


async def test_extract_batch_failure_returns_empty(monkeypatch):
    fake = _FakeLLM(error=RuntimeError("llm caido"))
    monkeypatch.setattr("src.ollama_client.llm.chat", fake.chat)
    assert await ms._extract_batch_with_llm(1, ["hola"]) == []


# ---------- scan_messages ----------


async def test_scan_messages_rejects_bad_date_format(monkeypatch):
    stored = _patch_db(monkeypatch)
    result = await ms.scan_messages(1, since="27-09-2026")
    assert result["success"] is False
    assert "YYYY-MM-DD" in result["message"]
    assert stored == {}


async def test_scan_messages_filters_by_since_and_limits(monkeypatch):
    history = [
        {"role": "user", "content": "viejo", "created_at": "2026-09-20 09:00:00"},
        {"role": "assistant", "content": "respuesta", "created_at": "2026-09-27 09:30:00"},
        {"role": "user", "content": "nuevo1", "created_at": "2026-09-27 10:00:00"},
        {"role": "user", "content": "nuevo2", "created_at": "2026-09-27 11:00:00"},
        {"role": "user", "content": "nuevo3", "created_at": "2026-09-27 12:00:00"},
    ]
    stored = _patch_db(monkeypatch, history=history)
    seen = {}

    async def fake_extract(chat_id, messages):
        seen["messages"] = messages
        return [{"type": "llm_response", "content": "ok"}]

    monkeypatch.setattr(ms, "_extract_batch_with_llm", fake_extract)
    result = await ms.scan_messages(1, since="2026-09-27", limit=2)

    assert result["success"] is True
    assert result["messages_scanned"] == 2
    assert result["messages_scanned"] == len(seen["messages"])
    assert seen["messages"] == ["nuevo2", "nuevo3"]
    assert result["extracted"] == [{"type": "llm_response", "content": "ok"}]
    assert "last_scan_timestamp" in stored
    assert result["scan_until"] == stored["last_scan_timestamp"]


async def test_scan_messages_without_recent_returns_early(monkeypatch):
    history = [{"role": "user", "content": "viejo", "created_at": "2026-01-01 00:00:00"}]
    stored = _patch_db(monkeypatch, history=history)

    async def fake_extract(chat_id, messages):
        raise AssertionError("no debe extraer sin mensajes")

    monkeypatch.setattr(ms, "_extract_batch_with_llm", fake_extract)
    result = await ms.scan_messages(1, since="2026-09-27")

    assert result["success"] is True
    assert result["messages_scanned"] == 0
    assert result["extracted"] == []
    assert stored == {}


async def test_scan_messages_uses_last_scan_timestamp(monkeypatch):
    history = [{"role": "user", "content": "reciente", "created_at": "2026-09-27 10:00:00"}]
    _patch_db(monkeypatch, kv={"last_scan_timestamp": "2026-09-27 00:00:00"}, history=history)
    seen = {}

    async def fake_extract(chat_id, messages):
        seen["messages"] = messages
        return []

    monkeypatch.setattr(ms, "_extract_batch_with_llm", fake_extract)
    result = await ms.scan_messages(1)
    assert seen["messages"] == ["reciente"]
    assert result["success"] is True


async def test_scan_messages_bad_last_scan_falls_back_to_24h(monkeypatch):
    history = [{"role": "user", "content": "reciente", "created_at": "2100-01-01 00:00:00"}]
    _patch_db(monkeypatch, kv={"last_scan_timestamp": "no-es-fecha"}, history=history)
    seen = {}

    async def fake_extract(chat_id, messages):
        seen["messages"] = messages
        return []

    monkeypatch.setattr(ms, "_extract_batch_with_llm", fake_extract)
    result = await ms.scan_messages(1)
    assert result["success"] is True
    assert seen["messages"] == ["reciente"]


async def test_scan_messages_without_last_scan_falls_back_to_24h(monkeypatch):
    history = [{"role": "user", "content": "reciente", "created_at": "2100-01-01 00:00:00"}]
    _patch_db(monkeypatch, kv={}, history=history)
    seen = {}

    async def fake_extract(chat_id, messages):
        seen["messages"] = messages
        return []

    monkeypatch.setattr(ms, "_extract_batch_with_llm", fake_extract)
    result = await ms.scan_messages(1)
    assert result["success"] is True
    assert seen["messages"] == ["reciente"]
