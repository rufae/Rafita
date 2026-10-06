"""Tests de autenticacion web (Fase 3): scrypt, JWT y utilidades Google."""

import time

import pytest

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


# ---------- ramas de error y bootstrap (cobertura 2026-10-06) ----------


def test_verify_password_algoritmo_desconocido():
    stored = "md5$1$1$1$c2FsdA==$ZGlnZXN0"
    assert wa.verify_password("clave", stored) is False


def test_create_token_sin_secreto_lanza(monkeypatch):
    monkeypatch.setattr(settings, "web_auth_secret", "")
    with pytest.raises(RuntimeError, match="WEB_AUTH_SECRET"):
        wa.create_token({"id": 1, "email": "x@y.com"})


def test_decode_token_malformado_devuelve_none(monkeypatch):
    monkeypatch.setattr(settings, "web_auth_secret", "secreto-test")
    assert wa.decode_token("solo-dos-partes") is None
    assert wa.decode_token("a.b.c") is None


class _DbFalso:
    def __init__(self, usuarios=0, fallo=False):
        self.usuarios = usuarios
        self.fallo = fallo
        self.creados: list = []

    async def count_web_users(self) -> int:
        return self.usuarios

    async def create_web_user(self, email, password_hash, is_admin=False) -> int:
        if self.fallo:
            raise RuntimeError("db rota")
        self.creados.append((email, password_hash, is_admin))
        return 1


async def test_bootstrap_admin_crea_al_primer_usuario(monkeypatch):
    monkeypatch.setattr(settings, "web_admin_email", " Admin@Casa.Local ")
    monkeypatch.setattr(settings, "web_admin_password", "clave-larga")
    db_falso = _DbFalso(usuarios=0)
    monkeypatch.setattr("src.database.db", db_falso)
    await wa.bootstrap_admin()
    assert len(db_falso.creados) == 1
    email, hash_, es_admin = db_falso.creados[0]
    assert email == "admin@casa.local"
    assert es_admin is True and hash_.startswith("scrypt$")


async def test_bootstrap_admin_sin_config_y_con_usuarios(monkeypatch):
    db_falso = _DbFalso(usuarios=0)
    monkeypatch.setattr("src.database.db", db_falso)
    monkeypatch.setattr(settings, "web_admin_email", "")
    monkeypatch.setattr(settings, "web_admin_password", "clave")
    await wa.bootstrap_admin()
    assert db_falso.creados == []

    monkeypatch.setattr(settings, "web_admin_email", "a@b.c")
    db_falso.usuarios = 3
    await wa.bootstrap_admin()
    assert db_falso.creados == []


async def test_bootstrap_admin_falla_db_es_honesta(monkeypatch):
    monkeypatch.setattr(settings, "web_admin_email", "a@b.c")
    monkeypatch.setattr(settings, "web_admin_password", "clave")
    monkeypatch.setattr("src.database.db", _DbFalso(usuarios=0, fallo=True))
    await wa.bootstrap_admin()  # no propaga: loguea y sigue


async def test_exchange_google_code_ok_y_sin_red(monkeypatch):
    import sys
    from types import SimpleNamespace

    _configurar_google(monkeypatch)
    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(
            AsyncClient=lambda **k: _FakeClient(
                [{"access_token": "tok"}],
                get_payload={"email": "x@y.com", "sub": "9", "name": "X"},
            )
        ),
    )
    datos = await wa.exchange_google_code("codigo", "http://rafita.local/")
    assert datos is not None and datos["email"] == "x@y.com"

    class _SinRed(_FakeClient):
        async def post(self, url, data=None):
            raise ConnectionError("sin red")

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(AsyncClient=lambda **k: _SinRed([])))
    assert await wa.exchange_google_code("codigo") is None


async def test_google_device_start_sin_config_y_con_fallo(monkeypatch):
    import sys
    from types import SimpleNamespace

    monkeypatch.setattr(settings, "google_web_client_id", "")
    monkeypatch.setattr(settings, "google_web_client_secret", "")
    assert await wa.google_device_start() is None

    _configurar_google(monkeypatch)

    class _SinRed(_FakeClient):
        async def post(self, url, data=None):
            raise ConnectionError("sin red")

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(AsyncClient=lambda **k: _SinRed([])))
    assert await wa.google_device_start() is None


async def test_google_device_poll_error_con_descripcion(monkeypatch):
    import sys
    from types import SimpleNamespace

    _configurar_google(monkeypatch)
    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(
            AsyncClient=lambda **k: _FakeClient(
                [{"error": "invalid_grant", "error_description": "Codigo caducado"}]
            )
        ),
    )
    res = await wa.google_device_poll("d1")
    assert res == {"status": "error", "detail": "Codigo caducado"}


async def test_google_device_poll_excepcion_devuelve_error(monkeypatch):
    import sys
    from types import SimpleNamespace

    _configurar_google(monkeypatch)

    class _SinRed(_FakeClient):
        async def post(self, url, data=None):
            raise ConnectionError("sin red")

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(AsyncClient=lambda **k: _SinRed([])))
    res = await wa.google_device_poll("d1")
    assert res["status"] == "error" and "sin red" in res["detail"]
