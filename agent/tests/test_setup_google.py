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


def test_extract_auth_code_variants():
    code = "4/0AXabc-def_ghi"
    assert admin_module.extract_auth_code(code) == code
    assert admin_module.extract_auth_code("code=%s&scope=x" % code) == code
    assert (
        admin_module.extract_auth_code(
            "http://localhost:8080/?state=abc&iss=https://accounts.google.com"
            "&code=%s&scope=https://www.googleapis.com/auth/drive" % code
        )
        == code
    )
    assert admin_module.extract_auth_code("=4/0AXabc%2Fdef&scope=y") == "4/0AXabc/def"


async def test_code_argument_with_url_params_is_cleaned(monkeypatch, tmp_path):
    monkeypatch.setattr(admin_module, "CREDENTIALS_DIR", tmp_path)
    (tmp_path / "credentials.json").write_text(
        '{"installed": {"client_id": "x"}}', encoding="utf-8"
    )
    monkeypatch.setattr(admin_module.settings, "admin_ids", [1])
    called = {}

    async def exchange(code):
        called["code"] = code
        return {"success": True, "message": "ok"}

    monkeypatch.setattr(admin_module.google_service, "exchange_code", exchange)
    update = _update()
    await admin_module.setup_google_command(
        update,
        SimpleNamespace(
            args=[
                "4/0AXabc&scope=https://www.googleapis.com/auth/drive.readonly",
                "https://www.googleapis.com/auth/calendar",
            ]
        ),
    )
    assert called["code"] == "4/0AXabc"


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


async def test_service_account_uploaded_as_oauth_is_renamed(monkeypatch, tmp_path):
    monkeypatch.setattr(admin_module, "CREDENTIALS_DIR", tmp_path)
    monkeypatch.setattr(admin_module.settings, "admin_ids", [1])
    (tmp_path / "credentials.json").write_text(
        '{"type": "service_account", "client_email": "rafita@proyecto.iam.gserviceaccount.com"}',
        encoding="utf-8",
    )

    update = _update()
    await admin_module.setup_google_command(update, SimpleNamespace(args=[]))

    assert (tmp_path / "service_account.json").exists()
    assert not (tmp_path / "credentials.json").exists()
    reply = update.effective_message.replies[-1]
    assert "rafita@proyecto.iam.gserviceaccount.com" in reply
    assert "Detectará tu calendario automáticamente" in reply


async def test_oauth_credentials_take_precedence_over_service_account(monkeypatch, tmp_path):
    """Con credentials.json OAuth y service_account.json, gana OAuth (bug 27/09)."""
    monkeypatch.setattr(admin_module, "CREDENTIALS_DIR", tmp_path)
    monkeypatch.setattr(admin_module.settings, "admin_ids", [1])
    (tmp_path / "credentials.json").write_text(
        '{"installed": {"client_id": "x", "client_secret": "y"}}', encoding="utf-8"
    )
    (tmp_path / "service_account.json").write_text(
        '{"type": "service_account", "client_email": "sa@x"}', encoding="utf-8"
    )

    async def init_false():
        return False

    async def gen_url():
        return {"success": True, "auth_url": "https://accounts.google.com/o/oauth2/auth?x=1"}

    monkeypatch.setattr(admin_module.google_services, "initialize", init_false)
    monkeypatch.setattr(admin_module.google_service, "generate_auth_url", gen_url)

    update = _update()
    await admin_module.setup_google_command(update, SimpleNamespace(args=[]))

    reply = update.effective_message.replies[-1]
    assert "accounts.google.com" in reply
    assert "Cuenta de servicio detectada" not in reply


async def test_oauth_already_connected_reports_it(monkeypatch, tmp_path):
    monkeypatch.setattr(admin_module, "CREDENTIALS_DIR", tmp_path)
    monkeypatch.setattr(admin_module.settings, "admin_ids", [1])
    (tmp_path / "credentials.json").write_text(
        '{"installed": {"client_id": "x"}}', encoding="utf-8"
    )

    async def init_true():
        return True

    async def no_missing():
        return []

    monkeypatch.setattr(admin_module.google_services, "initialize", init_true)
    monkeypatch.setattr(admin_module.google_services, "oauth_missing_scopes", no_missing)
    monkeypatch.setattr(
        type(admin_module.google_services), "auth_method", property(lambda self: "oauth")
    )

    update = _update()
    await admin_module.setup_google_command(update, SimpleNamespace(args=[]))
    assert "OAuth" in update.effective_message.replies[-1]


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
