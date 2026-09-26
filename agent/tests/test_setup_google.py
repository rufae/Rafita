"""Flujo determinista de /setup_google (enlace y código), sin depender del modelo."""

from types import SimpleNamespace

from src.handlers import admin as admin_module


class _FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **_kwargs):
        self.replies.append(text)


def _update(user_id: int = 1):
    return SimpleNamespace(
        effective_message=_FakeMessage(),
        effective_user=SimpleNamespace(id=user_id),
    )


async def test_existing_credentials_generates_auth_link(monkeypatch, tmp_path):
    monkeypatch.setattr(admin_module, "CREDENTIALS_DIR", tmp_path)
    (tmp_path / "credentials.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(admin_module.settings, "admin_ids", [1])

    async def init_false():
        return False

    async def gen_url():
        return {"success": True, "auth_url": "https://accounts.google.com/o/oauth2/auth?x=1"}

    monkeypatch.setattr(admin_module.google_service, "initialize", init_false)
    monkeypatch.setattr(admin_module.google_service, "generate_auth_url", gen_url)

    update = _update()
    await admin_module.setup_google_command(update, SimpleNamespace(args=[]))

    reply = update.effective_message.replies[-1]
    assert "accounts.google.com" in reply
    assert "/setup_google <codigo>" in reply


async def test_code_argument_exchanges_token(monkeypatch, tmp_path):
    monkeypatch.setattr(admin_module, "CREDENTIALS_DIR", tmp_path)
    (tmp_path / "credentials.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(admin_module.settings, "admin_ids", [1])
    called = {}

    async def exchange(code):
        called["code"] = code
        return {"success": True, "message": "Token guardado correctamente."}

    monkeypatch.setattr(admin_module.google_service, "exchange_code", exchange)

    update = _update()
    await admin_module.setup_google_command(update, SimpleNamespace(args=["4/0Aabc-123"]))

    assert called["code"] == "4/0Aabc-123"
    assert "Token guardado" in update.effective_message.replies[-1]


async def test_already_connected(monkeypatch, tmp_path):
    monkeypatch.setattr(admin_module, "CREDENTIALS_DIR", tmp_path)
    (tmp_path / "credentials.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(admin_module.settings, "admin_ids", [1])

    async def init_true():
        return True

    monkeypatch.setattr(admin_module.google_service, "initialize", init_true)

    update = _update()
    await admin_module.setup_google_command(update, SimpleNamespace(args=[]))
    assert "ya está conectado" in update.effective_message.replies[-1]


async def test_without_credentials_sends_instructions(monkeypatch, tmp_path):
    monkeypatch.setattr(admin_module, "CREDENTIALS_DIR", tmp_path)
    monkeypatch.setattr(admin_module.settings, "admin_ids", [1])

    update = _update()
    await admin_module.setup_google_command(update, SimpleNamespace(args=[]))
    assert "Google Cloud Console" in update.effective_message.replies[-1]


async def test_non_admin_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(admin_module, "CREDENTIALS_DIR", tmp_path)
    monkeypatch.setattr(admin_module.settings, "admin_ids", [999])

    update = _update(user_id=1)
    await admin_module.setup_google_command(update, SimpleNamespace(args=[]))
    assert "administradores" in update.effective_message.replies[-1]
