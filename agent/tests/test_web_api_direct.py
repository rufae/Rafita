"""Tests directos de web_api (gate de cobertura90%, auditoría 2026-10).

Igual que test_gateway_endpoints: el TestClient corre en el hilo de AnyIO
que coverage no traza, así que estos tests invocan los handlers como
corrutinas (los `Depends(require_user)` se resuelven pasando `user=` a mano).
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.config import settings
from src.utils import web_api
from src.utils.web_auth import hash_password


class _Req:
    def __init__(self, payload=None, json_malo=False):
        self._payload = payload if payload is not None else {}
        self._malo = json_malo

    async def json(self):
        if self._malo:
            raise ValueError("JSON invalido")
        return self._payload


def _usuario(id_=5, email="ana@x.es", admin=False):
    return {"id": id_, "email": email, "is_admin": admin, "password_hash": hash_password("secret123")}


# ---------- auth ----------


async def test_login_flujos(monkeypatch):
    u = _usuario()
    monkeypatch.setattr(settings, "web_auth_secret", "s3cret-de-pruebas")

    # usuario inexistente ->401
    async def _no_existe(email):
        return None

    monkeypatch.setattr(web_api.db, "get_web_user_by_email", _no_existe)
    with pytest.raises(HTTPException) as e:
        await web_api.auth_login(_Req({"email": "no@no.es", "password": "secret123"}))
    assert e.value.status_code == 401

    # contraseña mala ->401
    async def _existe(email):
        return u

    monkeypatch.setattr(web_api.db, "get_web_user_by_email", _existe)
    with pytest.raises(HTTPException) as e:
        await web_api.auth_login(_Req({"email": u["email"], "password": "mala"}))
    assert e.value.status_code == 401

    # sin WEB_AUTH_SECRET ->503 (login deshabilitado)
    monkeypatch.setattr(settings, "web_auth_secret", "")
    with pytest.raises(HTTPException) as e:
        await web_api.auth_login(_Req({"email": u["email"], "password": "secret123"}))
    assert e.value.status_code == 503

    # ok -> token
    monkeypatch.setattr(settings, "web_auth_secret", "s3cret-de-pruebas")
    resp = await web_api.auth_login(_Req({"email": u["email"], "password": "secret123"}))
    assert resp["token"]
    assert resp["user"]["email"] == u["email"]
    assert resp["user"]["is_admin"] is False


async def test_register_flujos(monkeypatch):
    # deshabilitado ->403
    monkeypatch.setattr(settings, "web_allow_registration", False)
    with pytest.raises(HTTPException) as e:
        await web_api.auth_register(_Req({"email": "a@b.es", "password": "12345678"}))
    assert e.value.status_code == 403

    monkeypatch.setattr(settings, "web_allow_registration", True)

    # email invalido ->400
    with pytest.raises(HTTPException) as e:
        await web_api.auth_register(_Req({"email": "noemail", "password": "12345678"}))
    assert e.value.status_code == 400

    # contraseña corta ->400
    with pytest.raises(HTTPException) as e:
        await web_api.auth_register(_Req({"email": "a@b.es", "password": "corta"}))
    assert e.value.status_code == 400

    # duplicado ->409
    async def _dup(email):
        return {"id": 1}

    monkeypatch.setattr(web_api.db, "get_web_user_by_email", _dup)
    with pytest.raises(HTTPException) as e:
        await web_api.auth_register(_Req({"email": "a@b.es", "password": "12345678"}))
    assert e.value.status_code == 409

    # alta ok
    llamado = {}

    async def _create(email, password_hash, is_admin=False):
        llamado.update(email=email, hash=password_hash, admin=is_admin)
        return 9

    async def _get(uid):
        return {"id": uid, "email": llamado["email"], "is_admin": False}

    async def _nuevo(email):
        return None

    monkeypatch.setattr(web_api.db, "get_web_user_by_email", _nuevo)
    monkeypatch.setattr(web_api.db, "create_web_user", _create)
    monkeypatch.setattr(web_api.db, "get_web_user", _get)
    monkeypatch.setattr(settings, "web_auth_secret", "s3cret")
    resp = await web_api.auth_register(_Req({"email": "NUEVO@X.es", "password": "12345678"}))
    assert resp["user"]["id"] == 9
    assert llamado["email"] == "nuevo@x.es"  # normalizado a minusculas


async def test_auth_me_y_google_status(monkeypatch):
    resp = await web_api.auth_me(user={"id": 7, "email": "b@x.es", "is_admin": 1})
    assert resp["user"] == {"id": 7, "email": "b@x.es", "is_admin": True}

    monkeypatch.setattr(settings, "web_auth_secret", "sec")
    resp = await web_api.auth_google_status()
    assert resp["login_ready"] is True
    assert "configured" in resp


# ---------- meetings ----------


class _File:
    def __init__(self, datos=b"audio", filename="a.webm"):
        self._datos = datos
        self.filename = filename

    async def read(self, n=-1):
        if n and n > 0:
            return self._datos[:n]
        return self._datos


async def test_meetings_crud_completo(monkeypatch):
    from src.services import meeting_service

    user = {"id": 5}

    # audio vacio ->400
    with pytest.raises(HTTPException) as e:
        await web_api.meetings_create(file=_File(b""), title="x", user=user)
    assert e.value.status_code == 400

    # audio gigante ->413 por limite
    grande = b"x" * (meeting_service.MAX_AUDIO_BYTES + 1)
    with pytest.raises(HTTPException) as e:
        await web_api.meetings_create(file=_File(grande), title="x", user=user)
    assert e.value.status_code == 413

    # ValueError del servicio ->413
    async def _crea_malo(uid, title, nombre, datos):
        raise ValueError("formato no soportado")

    monkeypatch.setattr(meeting_service, "crear_desde_subida", _crea_malo)
    with pytest.raises(HTTPException) as e:
        await web_api.meetings_create(file=_File(b"abc"), title="t", user=user)
    assert e.value.status_code == 413

    # alta ok
    llamado = {}

    async def _crea(uid, title, nombre, datos):
        llamado.update(uid=uid, title=title, nombre=nombre, n=len(datos))
        return {"id": 11, "title": title}

    monkeypatch.setattr(meeting_service, "crear_desde_subida", _crea)
    resp = await web_api.meetings_create(file=_File(b"abc", "v.webm"), title="Demo", user=user)
    assert resp["id"] == 11
    assert llamado["uid"] == 5 and llamado["nombre"] == "v.webm"

    # listado
    async def _lista(uid):
        return [{"id": 11, "title": "Demo"}]

    monkeypatch.setattr(meeting_service, "listar", _lista)
    resp = await web_api.meetings_list(user=user)
    assert len(resp["meetings"]) == 1

    # detalle:404 y ok
    async def _det(mid, uid):
        return {"id": mid, "title": "Demo"} if mid == 11 else None

    monkeypatch.setattr(meeting_service, "detalle", _det)
    assert (await web_api.meetings_detail(11, user=user))["id"] == 11
    with pytest.raises(HTTPException) as e:
        await web_api.meetings_detail(99, user=user)
    assert e.value.status_code == 404

    # patch: ok y404
    async def _edita(mid, uid, titulo=None, transcripcion=None):
        if mid != 11:
            return None
        return {"id": mid, "title": titulo or "sin", "transcript": transcripcion or ""}

    monkeypatch.setattr(meeting_service, "editar", _edita)
    resp = await web_api.meetings_update(11, _Req({"title": "Nuevo", "transcript": "acta"}), user)
    assert resp["success"] is True and resp["meeting"]["title"] == "Nuevo"
    with pytest.raises(HTTPException) as e:
        await web_api.meetings_update(99, _Req({"title": "x"}), user)
    assert e.value.status_code == 404

    # delete: ok y404
    async def _borra(mid, uid, borrar_nota=True):
        return mid == 11

    monkeypatch.setattr(meeting_service, "borrar", _borra)
    assert await web_api.meetings_delete(11, user=user) == {"success": True}
    with pytest.raises(HTTPException) as e:
        await web_api.meetings_delete(99, user=user)
    assert e.value.status_code == 404


# ---------- RGPD ----------


async def test_gdpr_export_y_delete(monkeypatch):
    user = {"id": 5}
    llamado = {}

    async def _export(chat_id, uid):
        llamado.update(chat=chat_id, uid=uid)
        return {"user": {"id": uid}, "messages": []}

    async def _borrar(chat_id, uid):
        llamado.update(dchat=chat_id, duid=uid)
        return {"messages": 3, "meetings": 1}

    monkeypatch.setattr(web_api.db, "export_user_data", _export)
    monkeypatch.setattr(web_api.db, "delete_user_data", _borrar)

    resp = await web_api.gdpr_export(user=user)
    assert resp["user"]["id"] == 5
    assert llamado["chat"] == web_api.WEB_CHAT_BASE + 5  # espacio de chat web

    # sin confirm ->400
    with pytest.raises(HTTPException) as e:
        await web_api.gdpr_delete(_Req({"confirm": False}), user)
    assert e.value.status_code == 400

    # confirm -> ok (y json roto tambien cae a400)
    resp = await web_api.gdpr_delete(_Req({"confirm": True}), user)
    assert resp["deleted"]["messages"] == 3
    with pytest.raises(HTTPException) as e:
        await web_api.gdpr_delete(_Req(json_malo=True), user)
    assert e.value.status_code == 400


# ---------- push ----------


async def test_push_config_subscribe_unsubscribe(monkeypatch):
    from src.services import push_service

    async def _cfg():
        return {"publicKey": "vapid-pub", "enabled": True}

    async def _sub(payload, uid):
        return {"success": True, "uid": uid, "endpoint": str(payload.get("endpoint"))[:20]}

    async def _unsub(payload):
        return {"success": True}

    monkeypatch.setattr(push_service, "push_config", _cfg)
    monkeypatch.setattr(push_service, "subscribe", _sub)
    monkeypatch.setattr(push_service, "unsubscribe", _unsub)

    resp = await web_api.push_public_config()
    assert resp["publicKey"] == "vapid-pub"

    resp = await web_api.push_subscribe(_Req({"endpoint": "https://push.example/abc"}), user={"id": 7})
    assert resp["uid"] == 7

    with pytest.raises(HTTPException) as e:
        await web_api.push_subscribe(_Req(json_malo=True), user={"id": 7})
    assert e.value.status_code == 400

    resp = await web_api.push_unsubscribe(_Req({"endpoint": "x"}))
    assert resp["success"] is True
    with pytest.raises(HTTPException) as e:
        await web_api.push_unsubscribe(_Req(json_malo=True))
    assert e.value.status_code == 400
