"""Extras de automatizaciones: CAP, briefing adaptativo, tokens y callbacks."""

import io
import json
import tarfile
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from test_automations import _FakeGS, _FakeLLM, _install_kv

from src.config import settings
from src.handlers import automation_callbacks as callbacks
from src.services import automation_service as auto

# ---------------- AEMET CAP ----------------


def _cap_targz(severity="Severe", event="Lluvias intensas") -> bytes:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>TEST-1</identifier>
  <info>
    <event>%s</event>
    <severity>%s</severity>
    <areaDesc>Madrid</areaDesc>
    <expires>2026-09-28T20:00:00+02:00</expires>
  </info>
</alert>""" % (event, severity)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = xml.encode("utf-8")
        info = tarfile.TarInfo("aviso.xml")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_parse_cap_alerts_extracts_active_alerts():
    alerts = auto._parse_cap_alerts(_cap_targz())
    assert len(alerts) == 1
    assert "🟠" in alerts[0]
    assert "Lluvias intensas" in alerts[0]
    assert "Madrid" in alerts[0]
    assert "20:00" in alerts[0]


def test_parse_cap_ignores_minor_and_broken_blobs():
    assert auto._parse_cap_alerts(_cap_targz(severity="Minor")) == []
    assert auto._parse_cap_alerts(b"no es un tar.gz") == []


# ---------------- briefing adaptativo ----------------


def test_is_weekend():
    tz = ZoneInfo("Europe/Madrid")
    assert auto._is_weekend(datetime(2026, 9, 26, 10, 0, tzinfo=tz)) is True  # sabado
    assert auto._is_weekend(datetime(2026, 9, 27, 10, 0, tzinfo=tz)) is True  # domingo
    assert auto._is_weekend(datetime(2026, 9, 30, 10, 0, tzinfo=tz)) is False  # miercoles


def test_urgent_line_flags_event_within_two_hours():
    tz = ZoneInfo("Europe/Madrid")
    now = datetime(2026, 9, 28, 10, 0, tzinfo=tz)
    soon = (now + timedelta(minutes=90)).isoformat()
    later = (now + timedelta(hours=5)).isoformat()
    line = auto._urgent_line(
        [{"title": "Dentista", "start": soon}, {"title": "Cena", "start": later}], now
    )
    assert line.startswith("⏰ En 90 min: Dentista")


async def test_build_briefing_includes_alerts_and_saves_copy(monkeypatch):
    monkeypatch.setattr("src.services.google_services_manager.google_services", _FakeGS())

    async def fake_weather():
        return ""

    async def fake_server():
        return {}

    async def fake_alerts():
        return ["🟠 Severe — Lluvias — Madrid (hasta 20:00)"]

    saved = {}

    async def fake_save(text):
        saved["text"] = text

    monkeypatch.setattr(auto, "_weather", fake_weather)
    monkeypatch.setattr(auto, "_server_status", fake_server)
    monkeypatch.setattr(auto, "_aemet_alerts", fake_alerts)
    monkeypatch.setattr(auto, "_save_briefing_copy", fake_save)
    monkeypatch.setattr("src.ollama_client.llm", _FakeLLM("☀️ Briefing breve"))

    result = await auto.build_briefing()
    assert "🚨 *Avisos AEMET:*" in result["text"]
    assert result["counts"]["avisos"] == 1
    assert saved.get("text") == result["text"]


# ---------------- token del borrador ----------------


async def test_scan_inbox_stores_draft_with_token(monkeypatch):
    stored = _install_kv(monkeypatch)
    monkeypatch.setattr(
        "src.services.google_services_manager.google_services",
        _FakeGS(mails=[{"id": "m1", "from": "jefe", "subject": "Urgente", "snippet": "x"}]),
    )
    monkeypatch.setattr(
        "src.ollama_client.llm",
        _FakeLLM('[{"id": 0, "categoria": "urgente", "resumen": "r", "borrador": "Confirmo."}]'),
    )
    result = await auto.scan_inbox()
    item = result["items"][0]
    assert item["token"]
    raw = stored.get("inbox:draft:%s" % item["token"])
    assert raw and json.loads(raw)["borrador"] == "Confirmo."


# ---------------- callbacks ----------------


def _callback_update(data: str):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock()
    query.message.chat_id = 12345
    query.message.reply_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    return update, query


async def test_inbox_send_callback_sends_email(monkeypatch):
    sent = {}

    class _FakeGS:
        async def send_email(self, to, subject, body):
            sent.update({"to": to, "subject": subject, "body": body})
            return {"success": True, "message": "ok"}

    async def fake_kv_get(key, default=None):
        return json.dumps(
            {"from": "Jefe <jefe@empresa.com>", "subject": "Reunion", "borrador": "Confirmo."}
        )

    monkeypatch.setattr("src.services.google_services_manager.google_services", _FakeGS())
    monkeypatch.setattr("src.database.db.kv_get", fake_kv_get)

    update, query = _callback_update("inbox_send:tok1")
    await callbacks.inbox_send_callback(update, MagicMock())

    assert sent["to"] == "jefe@empresa.com"
    assert sent["subject"] == "Re: Reunion"
    assert sent["body"] == "Confirmo."
    assert "Respuesta enviada" in query.edit_message_text.call_args[0][0]


async def test_inbox_send_callback_missing_draft(monkeypatch):
    async def fake_kv_get(key, default=None):
        return None

    monkeypatch.setattr("src.database.db.kv_get", fake_kv_get)
    update, query = _callback_update("inbox_send:viejo")
    await callbacks.inbox_send_callback(update, MagicMock())
    assert "ya no está disponible" in query.edit_message_text.call_args[0][0]


async def test_brief_reagendar_shows_agenda(monkeypatch):
    async def fake_execute(chat_id, name, args):
        return {"success": True, "message": "📅 Próximos eventos: Dentista 10:00"}

    monkeypatch.setattr("src.handlers.chat._execute_tool", fake_execute)
    update, query = _callback_update("brief_reagendar")
    await callbacks.brief_reagendar_callback(update, MagicMock())

    text = query.message.reply_text.call_args[0][0]
    assert "Dentista" in text
    assert "mueve" in text.lower()
    assert settings.assistant_name  # settings cargado


# ---------------- radar ----------------


def test_parse_rss_titles_rss_and_atom():
    rss = b"""<rss><channel><item><title>Novedad 1</title><link>https://x/1</link></item>
    <item><title>Novedad 2</title><link>https://x/2</link></item></channel></rss>"""
    assert auto._parse_rss_titles(rss) == [
        ("Novedad 1", "https://x/1"),
        ("Novedad 2", "https://x/2"),
    ]
    atom = b"""<feed><entry><title>Atom 1</title><link href="https://y/1"/></entry></feed>"""
    assert auto._parse_rss_titles(atom) == [("Atom 1", "https://y/1")]
    assert auto._parse_rss_titles(b"no xml") == []


async def test_radar_filters_with_llm(monkeypatch):
    class _Resp:
        def __init__(self, content=b"", payload=None):
            self.content = content
            self._payload = payload

        def json(self):
            return self._payload or {}

    class _Client:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def get(self, url, **_kw):
            if "api.github.com" in url:
                return _Resp(
                    payload={
                        "items": [
                            {
                                "full_name": "acme/rag-tool",
                                "stargazers_count": 120,
                                "description": "RAG local",
                                "html_url": "https://github.com/acme/rag-tool",
                            }
                        ]
                    }
                )
            return _Resp(
                content=b"<rss><channel><item><title>Post IA</title><link>https://b/1</link></item></channel></rss>"
            )

    monkeypatch.setattr("httpx.AsyncClient", _Client)
    saved = {}

    async def fake_note(title, text, folder):
        saved["title"] = title

    monkeypatch.setattr(auto, "_save_vault_note", fake_note)
    monkeypatch.setattr(
        "src.ollama_client.llm",
        _FakeLLM(
            '[{"titulo": "rag-tool", "por_que": "RAG local en Python", "enlace": "https://github.com/acme/rag-tool"}]'
        ),
    )

    result = await auto.radar()
    assert result["success"]
    assert "rag-tool" in result["text"]
    assert saved.get("title", "").startswith("Radar IA")


# ---------------- informe de infraestructura ----------------


async def test_infra_report_reads_status_files(tmp_path, monkeypatch):
    (tmp_path / "backup-status.json").write_text(
        '{"timestamp": "2026-09-28T03:30:00", "snapshot": "abc123", "rclone": "OK"}',
        encoding="utf-8",
    )
    (tmp_path / "restore-drill-status.json").write_text(
        '{"timestamp": "2026-09-01T04:10:00", "integrity": "ok"}', encoding="utf-8"
    )
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))

    async def fake_server():
        return {"ia": "ok", "rag": "ok", "google": "conectado"}

    monkeypatch.setattr(auto, "_server_status", fake_server)
    monkeypatch.setattr("src.ollama_client.llm", _FakeLLM("🖥 Informe semanal OK"))

    result = await auto.infra_report()
    assert result["success"]
    assert result["backup"]["snapshot"] == "abc123"
    assert result["drill"]["integrity"] == "ok"
    assert "Informe" in result["text"]


async def test_infra_report_alerts_without_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))

    async def fake_server():
        return {}

    monkeypatch.setattr(auto, "_server_status", fake_server)
    monkeypatch.setattr("src.ollama_client.llm", _FakeLLM(""))
    result = await auto.infra_report()
    assert "ALERTAS" in result["text"]
    assert "backup" in result["text"].lower()


# ---------------- endpoint send-voice ----------------


async def test_send_voice_endpoint(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from src.utils import webhook_server

    ogg = tmp_path / "voz.ogg"
    ogg.write_bytes(b"OggSfake")

    class _FakeBot:
        def __init__(self):
            self.sent = []

        async def send_voice(self, chat_id, path):
            self.sent.append((chat_id, path))
            return True

    async def fake_tts(text):
        wav = tmp_path / "voz.wav"
        wav.write_bytes(b"RIFF")
        return wav

    async def fake_ogg(wav):
        return ogg

    monkeypatch.setattr("src.utils.tts_manager.text_to_speech", fake_tts)
    monkeypatch.setattr("src.utils.tts_manager.convert_to_ogg", fake_ogg)
    monkeypatch.setattr(settings, "admin_ids", [777])

    bot = _FakeBot()
    webhook_server.configure_gateway("secreto-voz", bot_ref=bot)
    client = TestClient(webhook_server.app)

    import hashlib
    import hmac as _hmac

    body = json.dumps({"text": "Hola, esto es el briefing en audio"}).encode()
    sig = _hmac.new(b"secreto-voz", body, hashlib.sha256).hexdigest()
    resp = client.post("/automation/send-voice", content=body, headers={"X-Webhook-Signature": sig})
    assert resp.status_code == 200
    assert bot.sent and bot.sent[0][0] == 777

    bad = client.post(
        "/automation/send-voice", content=body, headers={"X-Webhook-Signature": "malo"}
    )
    assert bad.status_code == 401


# ---------- tiempo para el chat (get_weather) ----------


def _limpiar_cache_tiempo():
    auto._WEATHER_CACHE.clear()


async def test_weather_report_usa_aemet_para_ciudad_conocida(monkeypatch):
    _limpiar_cache_tiempo()
    monkeypatch.setattr(settings, "aemet_api_key", "clave")
    llamadas = []

    async def fake_aemet(code, day_index=0):
        llamadas.append((code, day_index))
        return "🌡 12-24 °C, 10% de lluvia"

    monkeypatch.setattr(auto, "_aemet_forecast", fake_aemet)
    result = await auto.weather_report(ciudad="Sevilla", dia="mañana")
    assert result["success"]
    assert llamadas == [("41091", 1)]
    assert "Sevilla" in result["message"] and "mañana" in result["message"]
    # Segunda llamada: cache (no repite AEMET).
    result2 = await auto.weather_report(ciudad="Sevilla", dia="mañana")
    assert result2["message"] == result["message"]
    assert len(llamadas) == 1


async def test_weather_report_ciudad_desconocida_usa_openmeteo(monkeypatch):
    _limpiar_cache_tiempo()
    monkeypatch.setattr(settings, "aemet_api_key", "clave")

    async def fake_geo(nombre):
        return (40.4, -3.7, "Torrelodones")

    async def fake_openmeteo(lat, lon, day_index):
        assert (lat, lon, day_index) == (40.4, -3.7, 0)
        return "🌡 10-20 °C, despejado"

    monkeypatch.setattr(auto, "_geocode", fake_geo)
    monkeypatch.setattr(auto, "_openmeteo_forecast", fake_openmeteo)
    result = await auto.weather_report(ciudad="Torrelodones", dia="hoy")
    assert result["success"]
    assert "Torrelodones" in result["message"] and "despejado" in result["message"]


async def test_weather_report_sin_datos_responde_honesto(monkeypatch):
    _limpiar_cache_tiempo()
    monkeypatch.setattr(settings, "aemet_api_key", "")
    monkeypatch.setattr(settings, "briefing_lat", "")
    monkeypatch.setattr(settings, "briefing_lon", "")

    async def fake_location():
        return ("", "", "tu zona")

    async def fake_geo(nombre):
        return None

    async def fake_openmeteo(lat, lon, day_index):
        return ""

    monkeypatch.setattr(auto, "_location", fake_location)
    monkeypatch.setattr(auto, "_geocode", fake_geo)
    monkeypatch.setattr(auto, "_openmeteo_forecast", fake_openmeteo)
    result = await auto.weather_report()
    assert result["success"] is False
    assert "No he podido consultar" in result["message"]
