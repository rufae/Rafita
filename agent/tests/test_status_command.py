"""Regresión del panel /status: HTML y escapado (bug de Markdown, tarea 4.1)."""

from types import SimpleNamespace

from src.handlers import dashboard


class _FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_chat_action(self, *_args, **_kwargs):
        return None

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


async def _no_chats():
    return []


async def _vstats():
    return {"total_chunks": 0, "total_documents": 0}


async def _system_health():
    return {"logs": {"recent_errors": []}}


async def test_status_command_uses_html_and_keeps_underscores(monkeypatch):
    message = _FakeMessage()
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=1),
    )

    async def fake_check_health():
        return {
            "status": "ok",
            "provider": "ollama",
            "model": "gemma4:12b",
            "latency_ms": 7,
        }

    monkeypatch.setattr(dashboard.llm, "check_health", fake_check_health)
    monkeypatch.setattr(dashboard.db, "get_all_chat_ids", _no_chats)
    monkeypatch.setattr(dashboard.vector_db, "get_stats", _vstats)
    monkeypatch.setattr(dashboard.wm, "get_system_health", _system_health)

    await dashboard.status_command(update, None)

    assert message.replies, "el panel no envió ningún mensaje"
    text, kwargs = message.replies[0]
    assert kwargs.get("parse_mode") == "HTML"
    assert "<b>" in text
    # Los guiones bajos de las tools ya no rompen el formato
    assert "ask_deep_knowledge_base" in text
    assert "*PANEL" not in text


def test_esc_escapes_html():
    assert dashboard._esc("<b>&</b>") == "&lt;b&gt;&amp;&lt;/b&gt;"
