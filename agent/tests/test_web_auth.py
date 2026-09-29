"""Tests de autenticacion web (Fase 3): scrypt, JWT y utilidades Google."""

import time

import src.utils.web_auth as wa
from src.config import settings


def test_hash_y_verificacion_de_password():
    stored = wa.hash_password("clave-segura-123")
    assert stored.startswith("scrypt$")
    assert wa.verify_password("clave-segura-123", stored) is True
    assert wa.verify_password("otra-clave", stored) is False
    assert wa.verify_password("x", "formato-roto") is False
    assert wa.verify_password("x", "") is False


def test_password_distinto_salt_cada_vez():
    a = wa.hash_password("misma")
    b = wa.hash_password("misma")
    assert a != b


def test_jwt_crear_y_decodificar(monkeypatch):
    monkeypatch.setattr(settings, "web_auth_secret", "secreto-test")
    user = {"id": 7, "email": "a@b.com", "is_admin": False}
    token = wa.create_token(user)
    payload = wa.decode_token(token)
    assert payload is not None
    assert payload["sub"] == 7
    assert payload["email"] == "a@b.com"
    assert payload["adm"] == 0


def test_jwt_caducado(monkeypatch):
    monkeypatch.setattr(settings, "web_auth_secret", "secreto-test")
    token = wa.create_token({"id": 1, "email": "x@y.com"}, ttl_s=-10)
    assert wa.decode_token(token) is None


def test_jwt_manipulado_o_secreto_distinto(monkeypatch):
    monkeypatch.setattr(settings, "web_auth_secret", "secreto-test")
    token = wa.create_token({"id": 1, "email": "x@y.com"})
    cabeza, cuerpo, firma = token.split(".")
    assert wa.decode_token(".".join([cabeza, cuerpo + "x", firma])) is None
    assert wa.decode_token(token[:-2] + "aa") is None
    monkeypatch.setattr(settings, "web_auth_secret", "otro-secreto")
    assert wa.decode_token(token) is None


def test_jwt_sin_secreto_falla_cerrado(monkeypatch):
    monkeypatch.setattr(settings, "web_auth_secret", "")
    assert wa.decode_token("cualquiera") is None


def test_google_configurado(monkeypatch):
    monkeypatch.setattr(settings, "google_web_client_id", "")
    monkeypatch.setattr(settings, "google_web_client_secret", "")
    monkeypatch.setattr(settings, "google_web_redirect_uri", "")
    assert wa.google_configured() is False
    monkeypatch.setattr(settings, "google_web_client_id", "id")
    monkeypatch.setattr(settings, "google_web_client_secret", "secreto")
    assert wa.google_configured() is True
    url = wa.google_auth_url("estado1", "http://rafita.local/")
    assert "accounts.google.com" in url
    assert "state=estado1" in url
    assert "rafita.local" in url


def test_token_ttl_por_defecto(monkeypatch):
    monkeypatch.setattr(settings, "web_auth_secret", "secreto-test")
    token = wa.create_token({"id": 1, "email": "x@y.com"})
    payload = wa.decode_token(token)
    assert payload["exp"] - payload["iat"] == wa.TOKEN_TTL_S
    assert payload["exp"] > int(time.time())


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, post_payloads, get_payload=None, **kwargs):
        self._post = list(post_payloads)
        self._get = get_payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, data=None):
        return _FakeResponse(self._post.pop(0))

    async def get(self, url, headers=None):
        return _FakeResponse(self._get or {})


def _configurar_google(monkeypatch):
    monkeypatch.setattr(settings, "google_web_client_id", "cliente-id")
    monkeypatch.setattr(settings, "google_web_client_secret", "cliente-secreto")


async def test_google_device_start(monkeypatch):
    import sys
    from types import SimpleNamespace

    _configurar_google(monkeypatch)
    inicio = {
        "device_code": "d1",
        "user_code": "ABCD-EFGH",
        "verification_url": "https://google.com/device",
        "expires_in": 1800,
        "interval": 5,
    }
    monkeypatch.setitem(
        sys.modules, "httpx", SimpleNamespace(AsyncClient=lambda **k: _FakeClient([inicio]))
    )
    datos = await wa.google_device_start()
    assert datos["user_code"] == "ABCD-EFGH"


async def test_google_device_poll_pending_y_ok(monkeypatch):
    import sys
    from types import SimpleNamespace

    _configurar_google(monkeypatch)
    # pending
    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(AsyncClient=lambda **k: _FakeClient([{"error": "authorization_pending"}])),
    )
    assert (await wa.google_device_poll("d1"))["status"] == "pending"
    # ok con perfil
    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(
            AsyncClient=lambda **k: _FakeClient(
                [{"access_token": "t"}], get_payload={"email": "Yo@X.com"}
            )
        ),
    )
    resultado = await wa.google_device_poll("d1")
    assert resultado["status"] == "ok"
    assert resultado["email"] == "yo@x.com"


async def test_google_device_poll_denegado(monkeypatch):
    import sys
    from types import SimpleNamespace

    _configurar_google(monkeypatch)
    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(AsyncClient=lambda **k: _FakeClient([{"error": "access_denied"}])),
    )
    resultado = await wa.google_device_poll("d1")
    assert resultado["status"] == "error"
    assert "access_denied" in resultado["detail"]
