"""Tests del pipeline de OpenShorts (comando, servicio de subida y endpoints).

Sin red: httpx se sustituye por un cliente falso con respuestas guionizadas.
Cubre `/openshorts`, `clip_upload_service` (descarga, YouTube, Instagram,
TikTok) y los endpoints del gateway `/automation/clip-upload|clip-delete`.
"""

import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from telegram import Bot, Chat, Message, Update, User

from src.config import settings
from src.handlers import openshorts
from src.services import clip_upload_service as svc
from src.utils import webhook_server

# ---------------------------------------------------------------------------
# helpers comunes
# ---------------------------------------------------------------------------

SECRET = "s3cret-openshorts"


@pytest.fixture
def secreto(monkeypatch):
    monkeypatch.setattr(webhook_server, "_webhook_secret", SECRET)
    monkeypatch.setattr(webhook_server, "_bot_ref", None)
    return SECRET


class _Req:
    def __init__(self, body: bytes = b"", headers: dict | None = None):
        self._body = body
        self.headers = headers or {}
        self.query_params = {}
        self.client = SimpleNamespace(host="testclient")
        self.url = SimpleNamespace(path="/test")

    async def body(self) -> bytes:
        return self._body


def _firmar(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _req(payload) -> _Req:
    body = json.dumps(payload).encode()
    return _Req(body=body, headers={"X-Webhook-Signature": _firmar(body)})


class _Resp:
    def __init__(self, status_code=200, json_data=None, headers=None, text="", content=b""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.headers = headers or {}
        self.text = text
        self.content = content

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    """Cliente httpx falso: consume respuestas guionizadas por (metodo, frag)."""

    def __init__(self, script=None, **kw):
        self.script = list(script or [])
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def _next(self, method, url):
        self.calls.append((method, url))
        for i, (m, frag, resp) in enumerate(self.script):
            if m == method and frag in url:
                self.script.pop(i)
                return resp
        return _Resp()

    async def get(self, url, **kw):
        return self._next("GET", url)

    async def post(self, url, **kw):
        return self._next("POST", url)

    async def put(self, url, **kw):
        return self._next("PUT", url)


def _cliente(monkeypatch, script):
    fake = _FakeClient(script)
    monkeypatch.setattr(svc.httpx, "AsyncClient", lambda **kw: fake)
    return fake


def make_update(text="hola", user_id=12345):
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


def sent_text(bot):
    return bot.send_message.call_args.kwargs["text"]


# ---------------------------------------------------------------------------
# comando /openshorts
# ---------------------------------------------------------------------------


async def test_openshorts_sin_args_muestra_uso():
    update, bot = make_update("/openshorts")
    await openshorts.openshorts_command(update, make_context([]))
    assert "Usa: /openshorts" in sent_text(bot)


async def test_openshorts_url_invalida_muestra_uso():
    update, bot = make_update("/openshorts esto-no-es-una-url")
    await openshorts.openshorts_command(update, make_context(["esto-no-es-una-url"]))
    assert "Usa: /openshorts" in sent_text(bot)


async def test_openshorts_sin_mensaje_noop():
    update = MagicMock()
    update.effective_message = None
    await openshorts.openshorts_command(update, make_context(["https://a.b/c"]))


async def test_openshorts_envia_a_n8n(monkeypatch):
    script = [("POST", "/webhook/openshorts", _Resp(200))]
    fake = _FakeClient(script)
    monkeypatch.setattr(openshorts.httpx, "AsyncClient", lambda **kw: fake)
    monkeypatch.setattr(settings, "openshorts_n8n_webhook", "http://n8n:5678/webhook/openshorts")
    monkeypatch.setattr(settings, "openshorts_webhook_secret", "s")

    update, bot = make_update("/openshorts https://youtu.be/x")
    await openshorts.openshorts_command(update, make_context(["https://youtu.be/x"]))

    assert fake.calls and fake.calls[0][0] == "POST"
    assert "Trabajo de clips creado" in sent_text(bot)


async def test_openshorts_fallo_n8n_avisa(monkeypatch):
    fake = _FakeClient([("POST", "/webhook/openshorts", _Resp(500))])
    monkeypatch.setattr(openshorts.httpx, "AsyncClient", lambda **kw: fake)

    update, bot = make_update("/openshorts https://youtu.be/x")
    await openshorts.openshorts_command(update, make_context(["https://youtu.be/x"]))
    assert "No pude encolar" in sent_text(bot)


def test_webhook_url_por_defecto(monkeypatch):
    monkeypatch.setattr(settings, "openshorts_n8n_webhook", "")
    monkeypatch.setattr(settings, "n8n_base_url", "http://n8n:5678")
    assert openshorts._webhook_url() == "http://n8n:5678/webhook/openshorts"


# ---------------------------------------------------------------------------
# clip_upload_service: descarga y borrado
# ---------------------------------------------------------------------------


async def test_download_clip_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "clips_dir", str(tmp_path))
    _cliente(monkeypatch, [("GET", "/videos/j1/clip.mp4", _Resp(200, content=b"RIFFdata"))])
    ruta = await svc.download_clip("j1", "clip.mp4")
    assert ruta is not None and ruta.read_bytes() == b"RIFFdata"


async def test_download_clip_cacheado(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "clips_dir", str(tmp_path))
    destino = svc.clip_path("j1", "clip.mp4")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(b"ya")
    ruta = await svc.download_clip("j1", "clip.mp4")
    assert ruta == destino


async def test_download_clip_sin_api(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "clips_dir", str(tmp_path))
    monkeypatch.setattr(settings, "openshorts_api_url", "")
    assert await svc.download_clip("j1", "c.mp4") is None


async def test_download_clip_error_http(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "clips_dir", str(tmp_path))
    _cliente(monkeypatch, [("GET", "/videos/", _Resp(404))])
    assert await svc.download_clip("j1", "clip.mp4") is None


def test_delete_clip(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "clips_dir", str(tmp_path))
    destino = svc.clip_path("j2", "clip.mp4")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(b"x")
    assert svc.delete_clip("j2", "clip.mp4") is True
    assert not destino.exists()
    assert not destino.parent.exists()


# ---------------------------------------------------------------------------
# clip_upload_service: YouTube
# ---------------------------------------------------------------------------


def _yt_script(init_status=200, put_status=200, token_ok=True):
    token = _Resp(200, {"access_token": "tok"}) if token_ok else _Resp(400, text="bad")
    return [
        ("POST", "oauth2.googleapis.com/token", token),
        (
            "POST",
            "upload/youtube/v3/videos",
            _Resp(init_status, headers={"Location": "https://upload.example/session"}),
        ),
        ("PUT", "upload.example/session", _Resp(put_status, {"id": "vid123"})),
    ]


async def test_upload_youtube_ok(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0" * 100)
    _cliente(monkeypatch, _yt_script())
    url, err = await svc.upload_youtube(video, "Titulo", "Desc", ["#tag"])
    assert err == ""
    assert url == "https://youtube.com/shorts/vid123"


async def test_upload_youtube_token_falla(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0" * 10)
    _cliente(monkeypatch, _yt_script(token_ok=False))
    url, err = await svc.upload_youtube(video, "T", "D", [])
    assert url == "" and err.startswith("YouTube")


async def test_upload_youtube_init_sin_location(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0" * 10)
    _cliente(
        monkeypatch,
        [
            ("POST", "oauth2.googleapis.com/token", _Resp(200, {"access_token": "t"})),
            ("POST", "upload/youtube/v3/videos", _Resp(200, {})),
        ],
    )
    url, err = await svc.upload_youtube(video, "T", "D", [])
    assert url == "" and "Location" in err


async def test_upload_youtube_put_error(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0" * 10)
    _cliente(monkeypatch, _yt_script(put_status=500))
    url, err = await svc.upload_youtube(video, "T", "D", [])
    assert url == "" and err.startswith("YT PUT")


# ---------------------------------------------------------------------------
# clip_upload_service: Instagram
# ---------------------------------------------------------------------------


async def test_upload_instagram_no_configurado(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ig_access_token", "")
    monkeypatch.setattr(settings, "ig_user_id", "")
    url, err = await svc.upload_instagram(tmp_path / "x.mp4", "cap")
    assert url == "" and "no configurado" in err


def _ig_script(status="FINISHED", publish_ok=True):
    return [
        ("POST", "/media", _Resp(200, {"id": "cid1", "uri": "https://rupload.example/u1"})),
        ("POST", "rupload.example/u1", _Resp(201, {})),
        ("GET", "cid1", _Resp(200, {"status_code": status})),
        (
            "POST",
            "media_publish",
            _Resp(200 if publish_ok else 500, {"id": "mid1"}),
        ),
        ("GET", "mid1", _Resp(200, {"permalink": "https://instagram.com/p/abc"})),
    ]


async def test_upload_instagram_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ig_access_token", "tok")
    monkeypatch.setattr(settings, "ig_user_id", "uid")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0" * 50)
    _cliente(monkeypatch, _ig_script())
    url, err = await svc.upload_instagram(video, "caption")
    assert err == ""
    assert url == "https://instagram.com/p/abc"


async def test_upload_instagram_contenedor_error(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ig_access_token", "tok")
    monkeypatch.setattr(settings, "ig_user_id", "uid")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0" * 10)
    _cliente(monkeypatch, _ig_script(status="ERROR"))
    url, err = await svc.upload_instagram(video, "cap")
    assert url == "" and "ERROR" in err


async def test_upload_instagram_publish_falla(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ig_access_token", "tok")
    monkeypatch.setattr(settings, "ig_user_id", "uid")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0" * 10)
    _cliente(monkeypatch, _ig_script(publish_ok=False))
    url, err = await svc.upload_instagram(video, "cap")
    assert url == "" and err.startswith("IG publish")


# ---------------------------------------------------------------------------
# clip_upload_service: TikTok + orquestador
# ---------------------------------------------------------------------------


async def test_upload_tiktok_no_configurado(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "tiktok_access_token", "")
    url, err = await svc.upload_tiktok(tmp_path / "x.mp4", "t")
    assert url == "" and "TikTok no configurado" in err


async def test_upload_tiktok_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "tiktok_access_token", "tok")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0" * 20)
    _cliente(
        monkeypatch,
        [
            (
                "POST",
                "video/init",
                _Resp(200, {"data": {"publish_id": "p1", "upload_url": "https://up.tt/x"}}),
            ),
            ("PUT", "up.tt/x", _Resp(201, {})),
        ],
    )
    url, err = await svc.upload_tiktok(video, "Titulo")
    assert err == "" and "p1" in url


async def test_upload_clip_descarga_falla(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "clips_dir", str(tmp_path))
    monkeypatch.setattr(settings, "openshorts_api_url", "")
    res = await svc.upload_clip({"job_id": "j", "archivo": "a.mp4"})
    assert res["success"] is False and res["errores"]


async def test_upload_clip_orquestador(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "clips_dir", str(tmp_path))
    monkeypatch.setattr(svc, "download_clip", AsyncMock(return_value=tmp_path / "a.mp4"))
    monkeypatch.setattr(
        svc, "upload_youtube", AsyncMock(return_value=("https://youtube.com/shorts/x", ""))
    )
    monkeypatch.setattr(
        svc, "upload_instagram", AsyncMock(return_value=("https://instagram.com/p/x", ""))
    )
    monkeypatch.setattr(svc, "upload_tiktok", AsyncMock(return_value=("tt", "")))
    res = await svc.upload_clip(
        {
            "job_id": "j1",
            "archivo": "a.mp4",
            "titulo": "T",
            "plataformas": ["youtube", "instagram", "tiktok"],
        }
    )
    assert res["success"] is True
    assert set(res["links"]) == {"youtube", "instagram", "tiktok"}


async def test_upload_clip_con_errores(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "clips_dir", str(tmp_path))
    monkeypatch.setattr(svc, "download_clip", AsyncMock(return_value=tmp_path / "a.mp4"))
    monkeypatch.setattr(svc, "upload_youtube", AsyncMock(return_value=("", "fallo yt")))
    res = await svc.upload_clip({"job_id": "j1", "archivo": "a.mp4", "plataformas": ["youtube"]})
    assert res["success"] is False and "fallo yt" in res["errores"]


# ---------------------------------------------------------------------------
# endpoints del gateway
# ---------------------------------------------------------------------------


async def test_clip_upload_sin_campos(secreto):
    with pytest.raises(HTTPException) as exc:
        await webhook_server.automation_clip_upload(_req({"job_id": "", "archivo": ""}))
    assert exc.value.status_code == 400


async def test_clip_upload_json_invalido(secreto):
    body = b"{no-json"
    req = _Req(body=body, headers={"X-Webhook-Signature": _firmar(body)})
    with pytest.raises(HTTPException) as exc:
        await webhook_server.automation_clip_upload(req)
    assert exc.value.status_code == 400


async def test_clip_upload_ok(secreto, monkeypatch):
    monkeypatch.setattr(
        svc,
        "upload_clip",
        AsyncMock(return_value={"success": True, "links": {"youtube": "u"}, "errores": []}),
    )
    resp = await webhook_server.automation_clip_upload(_req({"job_id": "j1", "archivo": "a.mp4"}))
    assert resp.get("success") is True


async def test_clip_upload_falla_devuelve_502(secreto, monkeypatch):
    monkeypatch.setattr(
        svc,
        "upload_clip",
        AsyncMock(return_value={"success": False, "links": {}, "errores": ["fallo"]}),
    )
    resp = await webhook_server.automation_clip_upload(_req({"job_id": "j1", "archivo": "a.mp4"}))
    assert getattr(resp, "status_code", 200) == 502


async def test_clip_upload_firma_invalida(secreto):
    body = json.dumps({"job_id": "j", "archivo": "a"}).encode()
    req = _Req(body=body, headers={"X-Webhook-Signature": "mala"})
    with pytest.raises(HTTPException):
        await webhook_server.automation_clip_upload(req)


async def test_clip_delete_ok(secreto, monkeypatch):
    monkeypatch.setattr(svc, "delete_clip", MagicMock(return_value=True))
    resp = await webhook_server.automation_clip_delete(_req({"job_id": "j1", "archivo": "a.mp4"}))
    assert resp.get("success") is True


async def test_clip_delete_sin_campos(secreto):
    with pytest.raises(HTTPException) as exc:
        await webhook_server.automation_clip_delete(_req({}))
    assert exc.value.status_code == 400
