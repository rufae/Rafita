"""Cobertura de handlers/admin.py (eventos, alertas, setup Google, auth code)."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Bot, Chat, Message, Update, User

from src.config import settings
from src.handlers import admin


class FakeDB:
    """Reemplaza src.database.db (la BD no esta inicializada en tests)."""

    def __init__(self, events=None, alerts=None):
        self.events = events or []
        self.alerts = alerts or []
        self.added_events = []
        self.added_alerts = []

    async def add_event(self, **kwargs):
        self.added_events.append(kwargs)
        return 11

    async def get_upcoming_events(self, chat_id, limit=10):
        return self.events

    async def add_alert(self, **kwargs):
        self.added_alerts.append(kwargs)
        return 22

    async def get_active_alerts(self, chat_id):
        return self.alerts


def make_update(text, user_id=12345):
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()
    bot.send_chat_action = AsyncMock()
    user = User(id=user_id, first_name="Test", is_bot=False, username="testuser")
    chat = Chat(id=user_id, type="private")
    message = Message(message_id=1, date=None, chat=chat, from_user=user, text=text)
    message._bot = bot
    return Update(update_id=1, message=message), bot


def make_context(args=None):
    context = MagicMock()
    context.args = args or []
    return context


def install_db(monkeypatch, **kwargs):
    fake_db = FakeDB(**kwargs)
    monkeypatch.setattr(admin, "db", fake_db)
    return fake_db


def sent_text(bot):
    return bot.send_message.call_args.kwargs["text"]


def install_admin(monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", [12345])


# ---------------------------------------------------------------------------
# evento_command
# ---------------------------------------------------------------------------


async def test_evento_without_message_is_noop(monkeypatch):
    fake_db = install_db(monkeypatch)
    update = MagicMock()
    update.effective_message = None
    update.effective_user = User(id=1, first_name="T", is_bot=False)

    await admin.evento_command(update, make_context(["2026-12-25", "18:00", "Cena"]))

    assert fake_db.added_events == []


@pytest.mark.parametrize(
    "command",
    [
        admin.evento_command,
        admin.eventos_command,
        admin.alerta_command,
        admin.alertas_command,
        admin.calendario_command,
        admin.sync_google_command,
        admin.setup_google_command,
    ],
)
async def test_commands_without_message_are_noop(monkeypatch, command):
    install_db(monkeypatch)
    update = MagicMock()
    update.effective_message = None
    update.effective_user = User(id=1, first_name="T", is_bot=False)

    await command(update, make_context(["x"]))
    # El guard "sin mensaje" sale sin tocar la API de Telegram


async def test_evento_requires_two_args(monkeypatch):
    fake_db = install_db(monkeypatch)
    update, bot = make_update("/evento 2026-12-25")

    await admin.evento_command(update, make_context(["2026-12-25"]))

    assert fake_db.added_events == []
    assert "Usa: /evento" in sent_text(bot)


async def test_evento_invalid_date_rejected(monkeypatch):
    fake_db = install_db(monkeypatch)
    update, bot = make_update("/evento 99-99-99 25:99 Cena")

    await admin.evento_command(update, make_context(["99-99-99", "25:99", "Cena"]))

    assert fake_db.added_events == []
    assert "Formato de fecha inválido" in sent_text(bot)


async def test_evento_full_datetime_with_description(monkeypatch):
    fake_db = install_db(monkeypatch)
    update, bot = make_update("/evento 2026-12-25 18:00 Cena navideña Con la familia")

    await admin.evento_command(
        update, make_context(["2026-12-25", "18:00", "Cena", "navideña", "Con", "la", "familia"])
    )

    assert fake_db.added_events == [
        {
            "chat_id": 12345,
            "title": "Cena",
            "event_datetime": "2026-12-25 18:00:00",
            "description": "navideña Con la familia",
        }
    ]
    text = sent_text(bot)
    assert "Evento creado" in text
    assert "Cena" in text
    assert "ID: 11" in text


async def test_evento_date_only_uses_end_of_day(monkeypatch):
    fake_db = install_db(monkeypatch)
    update, bot = make_update("/evento 2026-12-25 Cena")

    await admin.evento_command(update, make_context(["2026-12-25", "Cena"]))

    assert fake_db.added_events[0]["event_datetime"] == "2026-12-25 23:59:00"
    assert fake_db.added_events[0]["title"] == "Cena"
    assert fake_db.added_events[0]["description"] is None
    assert "Descripción" not in sent_text(bot)


async def test_evento_datetime_without_title_rejected(monkeypatch):
    fake_db = install_db(monkeypatch)
    update, bot = make_update("/evento 2026-12-25 18:00")

    await admin.evento_command(update, make_context(["2026-12-25", "18:00"]))

    assert fake_db.added_events == []
    assert "al menos un título" in sent_text(bot)


async def test_eventos_empty(monkeypatch):
    install_db(monkeypatch, events=[])
    update, bot = make_update("/eventos")

    await admin.eventos_command(update, make_context([]))

    assert "No tienes eventos próximos" in sent_text(bot)


async def test_eventos_lists_upcoming(monkeypatch):
    install_db(
        monkeypatch,
        events=[
            {
                "id": 3,
                "title": "Cena",
                "event_datetime": "2026-12-25 18:00:00",
                "description": "Con la familia",
            },
            {
                "id": 4,
                "title": "Reunión",
                "event_datetime": "2027-01-01 09:30:00",
                "description": "",
            },
        ],
    )
    update, bot = make_update("/eventos")

    await admin.eventos_command(update, make_context([]))

    text = sent_text(bot)
    assert "Eventos próximos" in text
    assert "Cena" in text
    assert "25/12/2026 18:00" in text
    assert "Con la familia" in text
    assert "Reunión" in text
    assert "01/01/2027 09:30" in text
    assert "🆔 3" in text
    assert "🆔 4" in text


# ---------------------------------------------------------------------------
# alerta_command / alertas_command
# ---------------------------------------------------------------------------


async def test_alerta_without_args_shows_usage(monkeypatch):
    fake_db = install_db(monkeypatch)
    update, bot = make_update("/alerta")

    await admin.alerta_command(update, make_context([]))

    assert fake_db.added_alerts == []
    assert "Usa: /alerta" in sent_text(bot)


async def test_alerta_invalid_expiry_rejected(monkeypatch):
    fake_db = install_db(monkeypatch)
    update, bot = make_update("/alerta Revisar expira:ayer")

    await admin.alerta_command(update, make_context(["Revisar", "expira:ayer"]))

    assert fake_db.added_alerts == []
    assert "expiración inválido" in sent_text(bot)


async def test_alerta_without_message_text_rejected(monkeypatch):
    fake_db = install_db(monkeypatch)
    update, bot = make_update("/alerta warning")

    await admin.alerta_command(update, make_context(["warning"]))

    assert fake_db.added_alerts == []
    assert "mensaje" in sent_text(bot)


async def test_alerta_creates_default_info_alert(monkeypatch):
    fake_db = install_db(monkeypatch)
    update, bot = make_update("/alerta Revisar presupuesto mensual")

    await admin.alerta_command(update, make_context(["Revisar", "presupuesto", "mensual"]))

    assert fake_db.added_alerts == [
        {
            "chat_id": 12345,
            "message": "Revisar presupuesto mensual",
            "alert_type": "info",
            "expires_at": None,
        }
    ]
    text = sent_text(bot)
    assert "Alerta creada" in text
    assert "ID: 22" in text
    assert "Expira" not in text


async def test_alerta_with_type_and_expiry(monkeypatch):
    fake_db = install_db(monkeypatch)
    update, bot = make_update("/alerta Revisar presupuesto warning expira:2026-07-01")

    await admin.alerta_command(
        update, make_context(["Revisar", "presupuesto", "warning", "expira:2026-07-01"])
    )

    assert fake_db.added_alerts == [
        {
            "chat_id": 12345,
            "message": "Revisar presupuesto",
            "alert_type": "warning",
            "expires_at": "2026-07-01 00:00:00",
        }
    ]
    text = sent_text(bot)
    assert "2026-07-01" in text
    assert "warning" in text


async def test_alerta_urgent_emoji(monkeypatch):
    install_db(monkeypatch)
    update, bot = make_update("/alerta Cita urgent")

    await admin.alerta_command(update, make_context(["Cita", "urgent"]))

    assert "🚨" in sent_text(bot)


async def test_alertas_empty(monkeypatch):
    install_db(monkeypatch, alerts=[])
    update, bot = make_update("/alertas")

    await admin.alertas_command(update, make_context([]))

    assert "No tienes alertas activas" in sent_text(bot)


async def test_alertas_lists_active(monkeypatch):
    install_db(
        monkeypatch,
        alerts=[
            {
                "id": 5,
                "message": "Revisar presupuesto",
                "alert_type": "warning",
                "created_at": "2026-09-01 10:00:00",
            },
            {
                "id": 6,
                "message": "Sin fecha",
                "alert_type": "otro",
                "created_at": "",
            },
        ],
    )
    update, bot = make_update("/alertas")

    await admin.alertas_command(update, make_context([]))

    text = sent_text(bot)
    assert "Alertas activas" in text
    assert "[WARNING]" in text
    assert "Revisar presupuesto" in text
    assert "Creada: 2026-09-01" in text
    assert "Sin fecha" in text
    assert "[OTRO]" in text
    assert "marcar como leída" in text


# ---------------------------------------------------------------------------
# extract_auth_code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("4/0AeanS-abc123", "4/0AeanS-abc123"),
        ("code=THECODE&scope=https://www.googleapis.com/auth/calendar", "THECODE"),
        ("http://localhost:8080/?code=THECODE&scope=x", "THECODE"),
        ("=LEFTOVER", "LEFTOVER"),
        ("code=ab%2Fcd", "ab/cd"),
        ("  spaced-code  ", "spaced-code"),
    ],
)
def test_extract_auth_code(raw, expected):
    assert admin.extract_auth_code(raw) == expected


# ---------------------------------------------------------------------------
# calendario_command
# ---------------------------------------------------------------------------


async def test_calendario_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", [999])
    update, bot = make_update("/calendario x@y.z", user_id=12345)

    await admin.calendario_command(update, make_context(["x@y.z"]))

    assert "Solo los administradores" in sent_text(bot)


async def test_calendario_shows_current(monkeypatch):
    install_admin(monkeypatch)
    monkeypatch.setattr(
        admin, "google_service", SimpleNamespace(calendar_id="primary@group.calendar.google.com")
    )
    monkeypatch.setattr(
        admin, "service_account_email", lambda: "sa@example.iam.gserviceaccount.com"
    )
    update, bot = make_update("/calendario")

    await admin.calendario_command(update, make_context([]))

    text = sent_text(bot)
    assert "primary@group.calendar.google.com" in text
    assert "sa@example.iam.gserviceaccount.com" in text


async def test_calendario_sets_new_calendar(monkeypatch):
    install_admin(monkeypatch)
    calls = []

    async def fake_set(cid):
        calls.append(cid)
        return {"success": True, "message": "Calendario fijado a %s" % cid}

    monkeypatch.setattr(admin, "google_service", SimpleNamespace(set_calendar_id=fake_set))
    update, bot = make_update("/calendario mi-cal@gmail.com")

    await admin.calendario_command(update, make_context(["mi-cal@gmail.com"]))

    assert calls == ["mi-cal@gmail.com"]
    assert "Calendario fijado" in sent_text(bot)


# ---------------------------------------------------------------------------
# sync_google_command
# ---------------------------------------------------------------------------


async def test_sync_google_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", [999])
    update, bot = make_update("/sync_google", user_id=12345)

    await admin.sync_google_command(update, make_context([]))

    assert "Solo los administradores" in sent_text(bot)


async def test_sync_google_runs_brain_sync(monkeypatch):
    install_admin(monkeypatch)
    called = {}

    async def fake_sync():
        called["done"] = True
        return {"success": True, "message": "Sincronizados 3 contactos"}

    import src.utils.google_brain_sync as brain_sync

    monkeypatch.setattr(brain_sync, "sync_google_to_vault", fake_sync)
    update, bot = make_update("/sync_google")

    await admin.sync_google_command(update, make_context([]))

    assert called["done"] is True
    texts = [c.kwargs["text"] for c in bot.send_message.call_args_list]
    assert any("Sincronizados 3 contactos" in t for t in texts)


# ---------------------------------------------------------------------------
# setup_google_command
# ---------------------------------------------------------------------------


async def test_setup_google_rejects_non_admin(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "admin_ids", [999])
    monkeypatch.setattr(admin, "CREDENTIALS_DIR", tmp_path)
    update, bot = make_update("/setup_google", user_id=12345)

    await admin.setup_google_command(update, make_context([]))

    assert "Solo los administradores" in sent_text(bot)


async def test_setup_google_without_credentials_sends_instructions(monkeypatch, tmp_path):
    install_admin(monkeypatch)
    monkeypatch.setattr(admin, "CREDENTIALS_DIR", tmp_path)
    update, bot = make_update("/setup_google")

    await admin.setup_google_command(update, make_context([]))

    text = sent_text(bot)
    assert "Configuracion de Google Calendar" in text
    assert "service_account.json" in text


async def test_setup_google_renames_service_account_upload(monkeypatch, tmp_path):
    install_admin(monkeypatch)
    monkeypatch.setattr(admin, "CREDENTIALS_DIR", tmp_path)
    (tmp_path / "credentials.json").write_text(
        json.dumps({"type": "service_account", "client_email": "sa@proj.iam.gserviceaccount.com"}),
        encoding="utf-8",
    )
    update, bot = make_update("/setup_google")

    await admin.setup_google_command(update, make_context([]))

    assert not (tmp_path / "credentials.json").exists()
    assert (tmp_path / "service_account.json").exists()
    text = sent_text(bot)
    assert "Cuenta de servicio detectada" in text
    assert "sa@proj.iam.gserviceaccount.com" in text


async def test_setup_google_service_account_unreadable_email(monkeypatch, tmp_path):
    install_admin(monkeypatch)
    monkeypatch.setattr(admin, "CREDENTIALS_DIR", tmp_path)
    (tmp_path / "service_account.json").write_text("not-json", encoding="utf-8")
    update, bot = make_update("/setup_google")

    await admin.setup_google_command(update, make_context([]))

    assert "(no legible)" in sent_text(bot)


async def test_setup_google_oauth_sends_auth_link_with_missing_scopes(monkeypatch, tmp_path):
    install_admin(monkeypatch)
    monkeypatch.setattr(admin, "CREDENTIALS_DIR", tmp_path)
    (tmp_path / "credentials.json").write_text(json.dumps({"installed": {}}), encoding="utf-8")

    async def fake_missing():
        return ["https://www.googleapis.com/auth/calendar"]

    async def fake_auth_url():
        return {"success": True, "auth_url": "https://accounts.google.com/o/oauth2/auth?x=1"}

    monkeypatch.setattr(
        admin,
        "google_services",
        SimpleNamespace(oauth_missing_scopes=fake_missing, initialize=AsyncMock()),
    )
    monkeypatch.setattr(admin, "google_service", SimpleNamespace(generate_auth_url=fake_auth_url))
    update, bot = make_update("/setup_google")

    await admin.setup_google_command(update, make_context([]))

    text = sent_text(bot)
    assert "https://accounts.google.com/o/oauth2/auth?x=1" in text
    assert "calendar" in text


async def test_setup_google_oauth_already_connected(monkeypatch, tmp_path):
    install_admin(monkeypatch)
    monkeypatch.setattr(admin, "CREDENTIALS_DIR", tmp_path)
    (tmp_path / "credentials.json").write_text(json.dumps({"web": {}}), encoding="utf-8")

    async def fake_missing():
        return []

    async def fake_initialize():
        return True

    monkeypatch.setattr(
        admin,
        "google_services",
        SimpleNamespace(
            oauth_missing_scopes=fake_missing,
            initialize=fake_initialize,
            auth_method="oauth",
        ),
    )
    update, bot = make_update("/setup_google")

    await admin.setup_google_command(update, make_context([]))

    assert "ya está conectado con OAuth" in sent_text(bot)


async def test_setup_google_oauth_auth_url_failure(monkeypatch, tmp_path):
    install_admin(monkeypatch)
    monkeypatch.setattr(admin, "CREDENTIALS_DIR", tmp_path)
    (tmp_path / "credentials.json").write_text(json.dumps({"installed": {}}), encoding="utf-8")

    async def fake_missing():
        return []

    async def fake_initialize():
        return False

    async def fake_auth_url():
        return {"success": False, "message": "flow no disponible"}

    monkeypatch.setattr(
        admin,
        "google_services",
        SimpleNamespace(
            oauth_missing_scopes=fake_missing,
            initialize=fake_initialize,
            auth_method=None,
        ),
    )
    monkeypatch.setattr(admin, "google_service", SimpleNamespace(generate_auth_url=fake_auth_url))
    update, bot = make_update("/setup_google")

    await admin.setup_google_command(update, make_context([]))

    assert "No se pudo generar el enlace" in sent_text(bot)


async def test_setup_google_exchanges_pasted_code(monkeypatch, tmp_path):
    install_admin(monkeypatch)
    monkeypatch.setattr(admin, "CREDENTIALS_DIR", tmp_path)
    (tmp_path / "credentials.json").write_text(json.dumps({"installed": {}}), encoding="utf-8")
    calls = []

    async def fake_exchange(code):
        calls.append(code)
        return {"success": True, "message": "Google conectado."}

    monkeypatch.setattr(admin, "google_service", SimpleNamespace(exchange_code=fake_exchange))
    update, bot = make_update("/setup_google code=THECODE&scope=x")

    await admin.setup_google_command(update, make_context(["code=THECODE&scope=x"]))

    assert calls == ["THECODE"]
    text = sent_text(bot)
    assert "Google conectado." in text
    assert "/sync_google" in text


async def test_setup_google_corrupt_credentials_json_falls_back(monkeypatch, tmp_path):
    install_admin(monkeypatch)
    monkeypatch.setattr(admin, "CREDENTIALS_DIR", tmp_path)
    (tmp_path / "credentials.json").write_text("esto no es json {{{", encoding="utf-8")

    async def fake_initialize():
        return True

    monkeypatch.setattr(admin, "google_service", SimpleNamespace(initialize=fake_initialize))
    update, bot = make_update("/setup_google")

    await admin.setup_google_command(update, make_context([]))

    assert "ya está conectado y funcionando" in sent_text(bot)


async def test_setup_google_legacy_file_already_connected(monkeypatch, tmp_path):
    install_admin(monkeypatch)
    monkeypatch.setattr(admin, "CREDENTIALS_DIR", tmp_path)
    (tmp_path / "credentials.json").write_text(json.dumps({"other": 1}), encoding="utf-8")

    async def fake_initialize():
        return True

    monkeypatch.setattr(admin, "google_service", SimpleNamespace(initialize=fake_initialize))
    update, bot = make_update("/setup_google")

    await admin.setup_google_command(update, make_context([]))

    assert "ya está conectado y funcionando" in sent_text(bot)


async def test_setup_google_legacy_file_sends_auth_link(monkeypatch, tmp_path):
    install_admin(monkeypatch)
    monkeypatch.setattr(admin, "CREDENTIALS_DIR", tmp_path)
    (tmp_path / "credentials.json").write_text(json.dumps({"other": 1}), encoding="utf-8")

    async def fake_initialize():
        return False

    async def fake_auth_url():
        return {"success": True, "auth_url": "https://accounts.google.com/o/oauth2/auth?y=2"}

    monkeypatch.setattr(
        admin,
        "google_service",
        SimpleNamespace(initialize=fake_initialize, generate_auth_url=fake_auth_url),
    )
    update, bot = make_update("/setup_google")

    await admin.setup_google_command(update, make_context([]))

    text = sent_text(bot)
    assert "https://accounts.google.com/o/oauth2/auth?y=2" in text
    assert "/setup_google <codigo>" in text


async def test_setup_google_legacy_file_auth_failure(monkeypatch, tmp_path):
    install_admin(monkeypatch)
    monkeypatch.setattr(admin, "CREDENTIALS_DIR", tmp_path)
    (tmp_path / "credentials.json").write_text(json.dumps({"other": 1}), encoding="utf-8")

    async def fake_initialize():
        return False

    async def fake_auth_url():
        return {"success": False, "message": "sin flow"}

    monkeypatch.setattr(
        admin,
        "google_service",
        SimpleNamespace(initialize=fake_initialize, generate_auth_url=fake_auth_url),
    )
    update, bot = make_update("/setup_google")

    await admin.setup_google_command(update, make_context([]))

    assert "No se pudo generar el enlace" in sent_text(bot)
