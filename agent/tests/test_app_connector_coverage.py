"""Cobertura de src/utils/app_connector.py: conectores Gmail/Home Assistant."""

import json

from src.utils import app_connector as ac


class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class _FakeHTTP:
    def __init__(self, get_handler=None, post_handler=None):
        self.gets = []
        self.posts = []
        self.closed = False
        self._get_handler = get_handler
        self._post_handler = post_handler

    async def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        if self._get_handler:
            return self._get_handler(url, kwargs)
        return _FakeResp()

    async def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if self._post_handler:
            return self._post_handler(url, kwargs)
        return _FakeResp()

    async def aclose(self):
        self.closed = True


def _patch_crypto(monkeypatch):
    monkeypatch.setattr(ac, "encrypt_value", lambda v: "ENC(%s)" % v)
    monkeypatch.setattr(ac, "decrypt_value", lambda v: v[4:-1] if v.startswith("ENC(") else v)


def _patch_db(monkeypatch, rows=None, fetchone=None, insert_id=7):
    calls = {"execute": [], "insert": []}

    async def fake_fetchall(sql, params=()):
        return rows or []

    async def fake_fetchone(sql, params=()):
        return fetchone

    async def fake_execute(sql, params=()):
        calls["execute"].append((sql, params))
        return None

    async def fake_insert(sql, params=()):
        calls["insert"].append((sql, params))
        return insert_id

    monkeypatch.setattr(ac.db, "execute_fetchall", fake_fetchall)
    monkeypatch.setattr(ac.db, "execute_fetchone", fake_fetchone)
    monkeypatch.setattr(ac.db, "execute", fake_execute)
    monkeypatch.setattr(ac.db, "execute_insert", fake_insert)
    return calls


def _connector() -> ac.AppConnector:
    conn = ac.AppConnector()
    conn._http_client = _FakeHTTP()
    return conn


def _gmail_connector(conn, active=True):
    conn._connectors["gmail"] = {
        "type": "gmail",
        "credentials": {"access_token": "tok"},
        "config": {},
        "is_active": active,
    }


def _ha_connector(conn, active=True):
    conn._connectors["homeassistant"] = {
        "type": "homeassistant",
        "credentials": {"token": "ha-tok"},
        "config": {"url": "http://ha.local:8123/"},
        "is_active": active,
    }


# ---------- initialize / close / _load_connectors ----------


async def test_initialize_loads_connectors(monkeypatch):
    _patch_crypto(monkeypatch)
    rows = [
        {
            "name": "gmail",
            "connector_type": "gmail",
            "credentials_enc": "ENC(%s)" % json.dumps({"access_token": "t"}),
            "config_json": json.dumps({"label": "inbox"}),
            "is_active": 1,
        },
        {
            "name": "ha",
            "connector_type": "homeassistant",
            "credentials_enc": "",
            "config_json": None,
            "is_active": 0,
        },
    ]
    _patch_db(monkeypatch, rows=rows)
    fake_http = _FakeHTTP()
    monkeypatch.setattr(ac.httpx, "AsyncClient", lambda **kwargs: fake_http)

    conn = ac.AppConnector()
    await conn.initialize()

    assert fake_http is conn._http_client
    loaded = conn.list_connectors()
    assert {c["name"] for c in loaded} == {"gmail", "ha"}
    gmail = conn.get_connector("gmail")
    assert gmail["credentials"] == {"access_token": "t"}
    assert gmail["config"] == {"label": "inbox"}
    assert gmail["is_active"] is True
    ha = conn.get_connector("ha")
    assert ha["credentials"] == {}
    assert ha["config"] == {}
    assert ha["is_active"] is False


async def test_close_closes_http_client():
    conn = _connector()
    await conn.close()
    assert conn._http_client.closed is True


async def test_close_without_client_is_noop():
    conn = ac.AppConnector()
    await conn.close()


# ---------- register / remove / list / get ----------


async def test_register_connector_inserts_new(monkeypatch):
    _patch_crypto(monkeypatch)
    calls = _patch_db(monkeypatch, fetchone=None, insert_id=77)
    conn = _connector()

    record_id = await conn.register_connector("n8n", "http", {"key": "k"}, {"url": "http://x"})
    assert record_id == 77
    assert calls["insert"], "debe insertar"
    assert conn.get_connector("n8n")["credentials"] == {"key": "k"}
    assert conn.list_connectors()[0]["name"] == "n8n"


async def test_register_connector_updates_existing(monkeypatch):
    _patch_crypto(monkeypatch)
    calls = _patch_db(monkeypatch, fetchone={"id": 5}, insert_id=99)
    conn = _connector()

    record_id = await conn.register_connector("n8n", "http", {"key": "k2"}, {"url": "http://y"})
    assert record_id == 5
    assert calls["insert"] == []
    assert any("UPDATE app_connectors" in sql for sql, _ in calls["execute"])


async def test_remove_connector_known_and_unknown(monkeypatch):
    _patch_crypto(monkeypatch)
    _patch_db(monkeypatch)
    conn = _connector()
    _gmail_connector(conn)

    assert await conn.remove_connector("gmail") is True
    assert conn.get_connector("gmail") is None
    assert await conn.remove_connector("inventado") is False


# ---------- test_gmail ----------


async def test_test_gmail_success():
    conn = _connector()
    conn._http_client = _FakeHTTP(
        get_handler=lambda url, kwargs: _FakeResp(200, {"emailAddress": "yo@correo.com"})
    )
    result = await conn.test_gmail({"access_token": "tok"})
    assert result == {"success": True, "email": "yo@correo.com"}
    assert conn._http_client.gets[0][1]["headers"]["Authorization"] == "Bearer tok"


async def test_test_gmail_http_error():
    conn = _connector()
    conn._http_client = _FakeHTTP(get_handler=lambda url, kwargs: _FakeResp(401))
    result = await conn.test_gmail({})
    assert result["success"] is False
    assert "401" in result["error"]


async def test_test_gmail_exception():
    conn = _connector()

    def boom(url, kwargs):
        raise RuntimeError("sin red")

    conn._http_client = _FakeHTTP(get_handler=boom)
    result = await conn.test_gmail({})
    assert result["success"] is False
    assert "sin red" in result["error"]


# ---------- fetch_urgent_emails ----------


async def test_fetch_urgent_emails_guards():
    conn = _connector()
    assert await conn.fetch_urgent_emails("inexistente") == []
    _gmail_connector(conn, active=False)
    assert await conn.fetch_urgent_emails() == []
    conn._connectors["otro"] = {"type": "http", "credentials": {}, "config": {}, "is_active": True}
    assert await conn.fetch_urgent_emails("otro") == []


async def test_fetch_urgent_emails_parses_messages():
    conn = _connector()
    _gmail_connector(conn)

    def handler(url, kwargs):
        if url.endswith("/messages"):
            return _FakeResp(200, {"messages": [{"id": "m1"}, {"id": "m2"}]})
        msg_id = url.rsplit("/", 1)[-1]
        return _FakeResp(
            200,
            {
                "snippet": "resumen %s" % msg_id,
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Asunto %s" % msg_id},
                        {"name": "From", "value": "emisor@correo.com"},
                    ]
                },
            },
        )

    conn._http_client = _FakeHTTP(get_handler=handler)
    results = await conn.fetch_urgent_emails()
    assert [r["id"] for r in results] == ["m1", "m2"]
    assert results[0]["subject"] == "Asunto m1"
    assert results[0]["from"] == "emisor@correo.com"
    assert results[0]["snippet"] == "resumen m1"


async def test_fetch_urgent_emails_list_failure_returns_empty():
    conn = _connector()
    _gmail_connector(conn)
    conn._http_client = _FakeHTTP(get_handler=lambda url, kwargs: _FakeResp(500))
    assert await conn.fetch_urgent_emails() == []


async def test_fetch_urgent_emails_detail_failure_skips_message():
    conn = _connector()
    _gmail_connector(conn)

    def handler(url, kwargs):
        if url.endswith("/messages"):
            return _FakeResp(200, {"messages": [{"id": "m1"}]})
        return _FakeResp(500)

    conn._http_client = _FakeHTTP(get_handler=handler)
    assert await conn.fetch_urgent_emails() == []


async def test_fetch_urgent_emails_exception_returns_empty():
    conn = _connector()
    _gmail_connector(conn)

    def boom(url, kwargs):
        raise RuntimeError("corte")

    conn._http_client = _FakeHTTP(get_handler=boom)
    assert await conn.fetch_urgent_emails() == []


# ---------- call_home_assistant ----------


async def test_call_home_assistant_not_configured():
    conn = _connector()
    result = await conn.call_home_assistant("light.salon")
    assert result["success"] is False
    assert "not configured" in result["error"]


async def test_call_home_assistant_success_normalizes_service():
    conn = _connector()
    _ha_connector(conn)
    conn._http_client = _FakeHTTP(post_handler=lambda url, kwargs: _FakeResp(200, []))
    result = await conn.call_home_assistant("light.salon", action="turn_on")
    assert result == {"success": True, "entity": "light.salon", "action": "turn_on"}
    url, kwargs = conn._http_client.posts[0]
    assert url == "http://ha.local:8123/api/services/light/turn_on"
    assert kwargs["json"] == {"entity_id": "light.salon"}
    assert kwargs["headers"]["Authorization"] == "Bearer ha-tok"


async def test_call_home_assistant_invalid_action_defaults_toggle():
    conn = _connector()
    _ha_connector(conn)
    conn._http_client = _FakeHTTP(post_handler=lambda url, kwargs: _FakeResp(201, []))
    result = await conn.call_home_assistant("switch.caldera", action="explode")
    assert result["action"] == "toggle"
    url, _ = conn._http_client.posts[0]
    assert url.endswith("/api/services/switch/toggle")


async def test_call_home_assistant_entity_without_domain():
    conn = _connector()
    _ha_connector(conn)
    conn._http_client = _FakeHTTP(post_handler=lambda url, kwargs: _FakeResp(200, []))
    await conn.call_home_assistant("sinpunto")
    url, _ = conn._http_client.posts[0]
    assert "/api/services/light/" in url


async def test_call_home_assistant_http_error():
    conn = _connector()
    _ha_connector(conn)
    conn._http_client = _FakeHTTP(post_handler=lambda url, kwargs: _FakeResp(500))
    result = await conn.call_home_assistant("light.salon")
    assert result["success"] is False
    assert "500" in result["error"]


async def test_call_home_assistant_exception():
    conn = _connector()
    _ha_connector(conn)

    def boom(url, kwargs):
        raise RuntimeError("ha caido")

    conn._http_client = _FakeHTTP(post_handler=boom)
    result = await conn.call_home_assistant("light.salon")
    assert result["success"] is False
    assert "ha caido" in result["error"]


# ---------- get_home_assistant_state ----------


async def test_get_home_assistant_state_not_configured():
    conn = _connector()
    result = await conn.get_home_assistant_state()
    assert result["success"] is False


async def test_get_home_assistant_state_single_entity():
    conn = _connector()
    _ha_connector(conn)
    payload = {"entity_id": "light.salon", "state": "on"}
    conn._http_client = _FakeHTTP(get_handler=lambda url, kwargs: _FakeResp(200, payload))
    result = await conn.get_home_assistant_state("light.salon")
    assert result == {"success": True, "data": payload}
    url, kwargs = conn._http_client.gets[0]
    assert url == "http://ha.local:8123/api/states/light.salon"
    assert kwargs["headers"] == {"Authorization": "Bearer ha-tok"}


async def test_get_home_assistant_state_all_entities():
    conn = _connector()
    _ha_connector(conn)
    payload = [{"entity_id": "light.salon"}, {"entity_id": "sensor.temp"}]
    conn._http_client = _FakeHTTP(get_handler=lambda url, kwargs: _FakeResp(200, payload))
    result = await conn.get_home_assistant_state()
    assert result["data"] == payload
    url, _ = conn._http_client.gets[0]
    assert url == "http://ha.local:8123/api/states"


async def test_get_home_assistant_state_http_error():
    conn = _connector()
    _ha_connector(conn)
    conn._http_client = _FakeHTTP(get_handler=lambda url, kwargs: _FakeResp(404))
    result = await conn.get_home_assistant_state("light.nada")
    assert result["success"] is False
    assert "404" in result["error"]


async def test_get_home_assistant_state_exception():
    conn = _connector()
    _ha_connector(conn)

    def boom(url, kwargs):
        raise RuntimeError("timeout")

    conn._http_client = _FakeHTTP(get_handler=boom)
    result = await conn.get_home_assistant_state()
    assert result["success"] is False
    assert "timeout" in result["error"]
