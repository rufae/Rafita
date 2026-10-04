"""Tests de la API web (Fase 3): login, chat, boveda y llamada."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.database as database
import src.utils.web_api as web_api
from src.config import settings
from src.utils.web_auth import hash_password


class _FakeDB:
    """Base de datos web en memoria para los tests."""

    def __init__(self):
        self.users: dict[int, dict] = {}
        self._id = 0

    async def create_web_user(self, email, password_hash, is_admin=False):
        self._id += 1
        self.users[self._id] = {
            "id": self._id,
            "email": email.strip().lower(),
            "password_hash": password_hash,
            "is_admin": bool(is_admin),
        }
        return self._id

    async def get_web_user(self, user_id):
        return self.users.get(int(user_id))

    async def get_web_user_by_email(self, email):
        for user in self.users.values():
            if user["email"] == email.strip().lower():
                return user
        return None

    async def count_web_users(self):
        return len(self.users)

    async def list_web_users(self):
        return list(self.users.values())

    async def get_chat_history(self, chat_id, limit=30, offset=0):
        # Igual que la BD real: mas reciente primero.
        filas = [
            {
                "id": 2,
                "chat_id": chat_id,
                "role": "assistant",
                "content": "buenas",
                "created_at": "2026-09-29",
            },
            {
                "id": 1,
                "chat_id": chat_id,
                "role": "user",
                "content": "hola",
                "created_at": "2026-09-29",
            },
        ]
        return filas[offset : offset + limit]


def _cliente(monkeypatch, tmp_path):
    fake = _FakeDB()
    monkeypatch.setattr(settings, "web_auth_secret", "secreto-web-test")
    monkeypatch.setattr(settings, "web_allow_registration", False)
    monkeypatch.setattr(settings, "voice_call_token", "token-llamada")
    monkeypatch.setattr(settings, "obsidian_vault_dir", str(tmp_path / "vault"))
    (tmp_path / "vault").mkdir(parents=True, exist_ok=True)
    for nombre in (
        "create_web_user",
        "get_web_user",
        "get_web_user_by_email",
        "count_web_users",
        "list_web_users",
        "get_chat_history",
    ):
        monkeypatch.setattr(database.db, nombre, getattr(fake, nombre))
    from src.utils import webhook_server

    return TestClient(webhook_server.app), fake


async def _crear_usuario(fake, email="admin@x.com", clave="clave12345", admin=True):
    await fake.create_web_user(email, hash_password(clave), is_admin=admin)


def test_login_y_me(monkeypatch, tmp_path):
    client, fake = _cliente(monkeypatch, tmp_path)
    import asyncio

    asyncio.run(_crear_usuario(fake))

    malo = client.post("/api/auth/login", json={"email": "admin@x.com", "password": "nope"})
    assert malo.status_code == 401
    ok = client.post("/api/auth/login", json={"email": "admin@x.com", "password": "clave12345"})
    assert ok.status_code == 200
    token = ok.json()["token"]
    assert ok.json()["user"]["is_admin"] is True

    sin = client.get("/api/auth/me")
    assert sin.status_code == 401
    con = client.get("/api/auth/me", headers={"Authorization": "Bearer " + token})
    assert con.status_code == 200
    assert con.json()["user"]["email"] == "admin@x.com"


def test_registro_deshabilitado_y_habilitado(monkeypatch, tmp_path):
    client, fake = _cliente(monkeypatch, tmp_path)
    no = client.post("/api/auth/register", json={"email": "n@x.com", "password": "clave12345"})
    assert no.status_code == 403
    monkeypatch.setattr(settings, "web_allow_registration", True)
    si = client.post("/api/auth/register", json={"email": "n@x.com", "password": "clave12345"})
    assert si.status_code == 200
    corta = client.post("/api/auth/register", json={"email": "o@x.com", "password": "123"})
    assert corta.status_code == 400
    repetida = client.post(
        "/api/auth/register", json={"email": "n@x.com", "password": "clave12345"}
    )
    assert repetida.status_code == 409


def test_chat_requiere_token_y_responde(monkeypatch, tmp_path):
    client, fake = _cliente(monkeypatch, tmp_path)
    import asyncio

    asyncio.run(_crear_usuario(fake))
    token = client.post(
        "/api/auth/login", json={"email": "admin@x.com", "password": "clave12345"}
    ).json()["token"]

    assert client.post("/api/chat", json={"message": "hola"}).status_code == 401

    async def fake_generate(text, chat_id):
        assert text == "hola"
        assert chat_id == web_api.WEB_CHAT_BASE + 1
        return "buenas!"

    monkeypatch.setattr("src.core.generate_response", fake_generate)
    resp = client.post(
        "/api/chat",
        json={"message": "hola"},
        headers={"Authorization": "Bearer " + token},
    )
    assert resp.status_code == 200
    assert resp.json()["reply"] == "buenas!"

    hist = client.get("/api/chat/history", headers={"Authorization": "Bearer " + token})
    assert hist.status_code == 200
    assert hist.json()["messages"][0]["content"] == "hola"


def test_vault_crud_y_seguridad_de_rutas(monkeypatch, tmp_path):
    client, fake = _cliente(monkeypatch, tmp_path)
    import asyncio

    asyncio.run(_crear_usuario(fake))
    token = client.post(
        "/api/auth/login", json={"email": "admin@x.com", "password": "clave12345"}
    ).json()["token"]
    headers = {"Authorization": "Bearer " + token}

    vacio = client.get("/api/vault/notes", headers=headers)
    assert vacio.status_code == 200 and vacio.json()["notes"] == []

    creada = client.post(
        "/api/vault/note",
        json={"path": "00-Inbox/prueba.md", "content": "# Hola\ncontenido"},
        headers=headers,
    )
    assert creada.status_code == 200
    listado = client.get("/api/vault/notes?query=contenido", headers=headers)
    assert listado.status_code == 200
    assert listado.json()["notes"][0]["path"] == "00-Inbox/prueba.md"

    leida = client.get("/api/vault/note?path=00-Inbox/prueba.md", headers=headers)
    assert leida.status_code == 200 and "contenido" in leida.json()["content"]

    # Seguridad: traversal y extensiones no permitidas
    malo = client.get("/api/vault/note?path=../fuera.md", headers=headers)
    assert malo.status_code == 400
    no_md = client.post(
        "/api/vault/note", json={"path": "x.sh", "content": "echo"}, headers=headers
    )
    assert no_md.status_code == 400

    borrada = client.delete("/api/vault/note?path=00-Inbox/prueba.md", headers=headers)
    assert borrada.status_code == 200
    assert client.get("/api/vault/note?path=00-Inbox/prueba.md", headers=headers).status_code == 404


def test_call_token_y_spa(monkeypatch, tmp_path):
    client, fake = _cliente(monkeypatch, tmp_path)
    import asyncio

    asyncio.run(_crear_usuario(fake))
    token = client.post(
        "/api/auth/login", json={"email": "admin@x.com", "password": "clave12345"}
    ).json()["token"]
    resp = client.get("/api/call/token", headers={"Authorization": "Bearer " + token})
    assert resp.status_code == 200
    assert resp.json()["token"] == "token-llamada"

    # SPA y PWA servidas por el gateway
    assert client.get("/app/").status_code == 200
    assert "Rafita" in client.get("/app/").text
    assert client.get("/app/manifest.webmanifest").status_code == 200
    assert client.get("/app/sw.js").status_code == 200
    assert client.get("/").status_code in (200, 307)


def test_meetings_api(monkeypatch, tmp_path):
    client, fake = _cliente(monkeypatch, tmp_path)
    import asyncio

    asyncio.run(_crear_usuario(fake))
    token = client.post(
        "/api/auth/login", json={"email": "admin@x.com", "password": "clave12345"}
    ).json()["token"]
    headers = {"Authorization": "Bearer " + token}

    assert client.get("/api/meetings").status_code == 401

    llamadas = {}

    async def fake_crear(user_id, titulo, nombre, datos):
        llamadas["crear"] = (user_id, titulo, nombre, len(datos))
        return {"id": 5, "title": titulo or "Reunion", "status": "processing"}

    async def fake_listar(user_id):
        return [{"id": 5, "title": "Reunion", "status": "done", "duration_s": 60.0}]

    async def fake_detalle(meeting_id, user_id):
        if meeting_id != 5:
            return None
        return {"id": 5, "title": "Reunion", "status": "done", "tasks_list": ["t"]}

    async def fake_borrar(meeting_id, user_id, borrar_nota=True):
        return meeting_id == 5

    monkeypatch.setattr("src.services.meeting_service.crear_desde_subida", fake_crear)
    monkeypatch.setattr("src.services.meeting_service.listar", fake_listar)
    monkeypatch.setattr("src.services.meeting_service.detalle", fake_detalle)
    monkeypatch.setattr("src.services.meeting_service.borrar", fake_borrar)

    subida = client.post(
        "/api/meetings",
        files={"file": ("reunion.webm", b"audio-bytes", "audio/webm")},
        data={"title": "Daily"},
        headers=headers,
    )
    assert subida.status_code == 200
    assert subida.json()["id"] == 5
    assert llamadas["crear"][1] == "Daily"
    assert llamadas["crear"][3] == len(b"audio-bytes")

    listado = client.get("/api/meetings", headers=headers)
    assert listado.status_code == 200 and listado.json()["meetings"][0]["id"] == 5

    detalle = client.get("/api/meetings/5", headers=headers)
    assert detalle.status_code == 200 and detalle.json()["tasks_list"] == ["t"]
    assert client.get("/api/meetings/99", headers=headers).status_code == 404

    assert client.delete("/api/meetings/5", headers=headers).status_code == 200
    assert client.delete("/api/meetings/99", headers=headers).status_code == 404

    vacio = client.post(
        "/api/meetings",
        files={"file": ("vacio.webm", b"", "audio/webm")},
        headers=headers,
    )
    assert vacio.status_code == 400


def test_google_device_flow_api(monkeypatch, tmp_path):
    client, fake = _cliente(monkeypatch, tmp_path)
    # Hermetico: sin credenciales de Google (aunque el .env local las tenga).
    monkeypatch.setattr(settings, "google_web_client_id", "")
    monkeypatch.setattr(settings, "google_web_client_secret", "")
    monkeypatch.setattr(settings, "google_web_redirect_uri", "")

    # Sin configurar -> 503 con pistas
    no = client.post("/api/auth/google/device/start")
    assert no.status_code == 503
    assert "TVs and Limited Input" in no.json()["hint"]

    monkeypatch.setattr(settings, "google_web_client_id", "id")
    monkeypatch.setattr(settings, "google_web_client_secret", "secreto")

    async def fake_start():
        return {
            "device_code": "d1",
            "user_code": "ABCD-EFGH",
            "verification_url": "https://google.com/device",
            "interval": 5,
            "expires_in": 1800,
        }

    monkeypatch.setattr(web_api, "google_device_start", fake_start)
    inicio = client.post("/api/auth/google/device/start")
    assert inicio.status_code == 200
    assert inicio.json()["user_code"] == "ABCD-EFGH"
    state = inicio.json()["state"]

    # pendiente
    async def fake_pending(device_code):
        return {"status": "pending"}

    monkeypatch.setattr(web_api, "google_device_poll", fake_pending)
    pend = client.get("/api/auth/google/device/poll?state=" + state)
    assert pend.status_code == 200 and pend.json()["status"] == "pending"

    # autorizado -> crea usuario y devuelve token
    async def fake_ok(device_code):
        return {"status": "ok", "email": "nuevo@x.com"}

    monkeypatch.setattr(web_api, "google_device_poll", fake_ok)
    ok = client.get("/api/auth/google/device/poll?state=" + state)
    assert ok.status_code == 200
    assert ok.json()["status"] == "ok"
    assert ok.json()["user"]["email"] == "nuevo@x.com"
    token = ok.json()["token"]
    me = client.get("/api/auth/me", headers={"Authorization": "Bearer " + token})
    assert me.status_code == 200

    # el state ya no sirve dos veces
    repetido = client.get("/api/auth/google/device/poll?state=" + state)
    assert repetido.status_code == 404
    assert client.get("/api/auth/google/device/poll?state=inexistente").status_code == 404


def test_spa_security_headers(monkeypatch, tmp_path):
    client, fake = _cliente(monkeypatch, tmp_path)
    monkeypatch.setattr(
        settings, "web_allowed_origins", "http://rafita.home,http://voz.rafita.home"
    )
    resp = client.get("/app/")
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("referrer-policy") == "no-referrer"
    csp = resp.headers.get("content-security-policy", "")
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'self'" in csp
    assert "http://voz.rafita.home" in csp  # el iframe de llamada
    pp = resp.headers.get("permissions-policy", "")
    assert 'microphone=(self "http://rafita.home" "http://voz.rafita.home")' in pp
    assert "camera=()" in pp
    # /api no lleva CSP restrictiva de SPA
    api = client.get("/api/auth/me")
    assert "content-security-policy" not in api.headers


def test_call_page_security_headers(monkeypatch):
    from src.voice_stream import server as vs

    monkeypatch.setattr(vs.settings, "web_allowed_origins", "http://rafita.home")
    headers = vs._call_page_security_headers()
    assert "frame-ancestors 'self' http://rafita.home" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "camera=()" in headers["Permissions-Policy"]


def test_design_tokens_css_servido_por_el_servidor_de_voz():
    """El sistema de diseno unificado debe servirse en la pagina de llamada."""
    from fastapi.testclient import TestClient

    from src.voice_stream import server as vs

    client = TestClient(vs.app)
    resp = client.get("/design-tokens.css")
    assert resp.status_code == 200
    assert "--font-ui" in resp.text
    assert "--paper" in resp.text
    assert resp.headers.get("content-type", "").startswith("text/css")


def test_vault_notes_paginacion(monkeypatch, tmp_path):
    client, fake = _cliente(monkeypatch, tmp_path)
    import asyncio

    asyncio.run(_crear_usuario(fake))
    token = client.post(
        "/api/auth/login", json={"email": "admin@x.com", "password": "clave12345"}
    ).json()["token"]
    headers = {"Authorization": "Bearer " + token}
    for i in range(5):
        client.post(
            "/api/vault/note",
            json={"path": "00-Inbox/pag-%d.md" % i, "content": "cuerpo %d" % i},
            headers=headers,
        )
    p1 = client.get("/api/vault/notes?limit=2&offset=0", headers=headers).json()
    p2 = client.get("/api/vault/notes?limit=2&offset=2", headers=headers).json()
    p3 = client.get("/api/vault/notes?limit=2&offset=4", headers=headers).json()
    assert p1["total"] == 5 and len(p1["notes"]) == 2
    assert p2["total"] == 5 and len(p2["notes"]) == 2
    assert p3["total"] == 5 and len(p3["notes"]) == 1
    rutas1 = {n["path"] for n in p1["notes"]}
    rutas2 = {n["path"] for n in p2["notes"]}
    assert not rutas1 & rutas2  # paginas sin solaparse
    assert p1["offset"] == 0 and p2["offset"] == 2


def test_chat_history_offset(monkeypatch, tmp_path):
    client, fake = _cliente(monkeypatch, tmp_path)
    import asyncio

    asyncio.run(_crear_usuario(fake))
    token = client.post(
        "/api/auth/login", json={"email": "admin@x.com", "password": "clave12345"}
    ).json()["token"]
    headers = {"Authorization": "Bearer " + token}
    p1 = client.get("/api/chat/history?limit=1&offset=0", headers=headers).json()
    p2 = client.get("/api/chat/history?limit=1&offset=1", headers=headers).json()
    assert len(p1["messages"]) == 1 and p1["messages"][0]["content"] == "buenas"
    assert len(p2["messages"]) == 1 and p2["messages"][0]["content"] == "hola"


def test_build_web_minifica_y_hashea(tmp_path):
    """Build minimo (Fase 4.2): minifica, hashea y reescribe referencias."""
    import importlib.util

    pytest.importorskip("rjsmin")
    spec = importlib.util.spec_from_file_location(
        "build_web", str(Path(__file__).resolve().parents[2] / "scripts" / "build_web.py")
    )
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    salida = tmp_path / "app-dist"
    hasheados = modulo.build(salida)
    assert set(hasheados) == {"app.js", "styles.css", "design-tokens.css"}
    for nombre, destino in hasheados.items():
        assert (salida / destino).exists()
        assert destino != nombre  # lleva hash
        origen = (Path(__file__).resolve().parents[2] / "web" / "app" / nombre).stat().st_size
        assert (salida / destino).stat().st_size <= origen
    html = (salida / "index.html").read_text(encoding="utf-8")
    for destino in hasheados.values():
        assert destino in html
    assert 'src="config.js"' in html  # editable por instalacion, sin hashear
    sw = (salida / "sw.js").read_text(encoding="utf-8")
    assert "rafita-shell-" in sw
    for destino in hasheados.values():
        assert destino in sw
    assert (salida / "manifest.webmanifest").exists()
    assert (salida / "icons" / "icon-192.png").exists()


def test_google_status_y_errores_vuelven_a_la_spa(monkeypatch, tmp_path):
    """Google: estado consultable y fallos que vuelven a la SPA (no JSON)."""
    client, fake = _cliente(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "google_web_client_id", "")
    monkeypatch.setattr(settings, "google_web_client_secret", "")
    monkeypatch.setattr(settings, "google_web_redirect_uri", "")
    estado = client.get("/api/auth/google/status")
    assert estado.status_code == 200
    assert estado.json()["configured"] is False

    inicio = client.get("/api/auth/google/start", follow_redirects=False)
    assert inicio.status_code in (302, 307)
    assert "google-error=no_config" in inicio.headers["location"]

    cancelado = client.get("/api/auth/google/callback?error=access_denied", follow_redirects=False)
    assert cancelado.status_code in (302, 307)
    assert "google-error=denied" in cancelado.headers["location"]

    mal_estado = client.get(
        "/api/auth/google/callback?code=x&state=inventado", follow_redirects=False
    )
    assert "google-error=state" in mal_estado.headers["location"]

    monkeypatch.setattr(settings, "google_web_client_id", "id")
    monkeypatch.setattr(settings, "google_web_client_secret", "secreto")
    assert client.get("/api/auth/google/status").json()["configured"] is True


def test_meetings_editar_y_borrar(monkeypatch, tmp_path):
    client, fake = _cliente(monkeypatch, tmp_path)
    import asyncio

    asyncio.run(_crear_usuario(fake))
    token = client.post(
        "/api/auth/login", json={"email": "admin@x.com", "password": "clave12345"}
    ).json()["token"]
    headers = {"Authorization": "Bearer " + token}
    llamadas = {}

    async def fake_editar(meeting_id, user_id, titulo=None, transcripcion=None):
        llamadas["editar"] = (meeting_id, titulo, transcripcion)
        return {"id": meeting_id, "title": titulo, "transcript": transcripcion}

    async def fake_borrar(meeting_id, user_id, borrar_nota=True):
        llamadas["borrar"] = (meeting_id, borrar_nota)
        return meeting_id == 7

    monkeypatch.setattr("src.services.meeting_service.editar", fake_editar)
    monkeypatch.setattr("src.services.meeting_service.borrar", fake_borrar)

    r = client.patch(
        "/api/meetings/7",
        json={"title": "Nuevo título", "transcript": "texto"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["meeting"]["title"] == "Nuevo título"
    assert llamadas["editar"] == (7, "Nuevo título", "texto")

    sin_auth = client.patch("/api/meetings/7", json={"title": "x"})
    assert sin_auth.status_code == 401

    d = client.delete("/api/meetings/7?borrar_nota=false", headers=headers)
    assert d.status_code == 200
    assert llamadas["borrar"] == (7, False)
    assert client.delete("/api/meetings/99", headers=headers).status_code == 404


def test_gdpr_export_y_delete(monkeypatch, tmp_path):
    client, fake = _cliente(monkeypatch, tmp_path)
    import asyncio

    asyncio.run(_crear_usuario(fake))
    token = client.post(
        "/api/auth/login", json={"email": "admin@x.com", "password": "clave12345"}
    ).json()["token"]
    headers = {"Authorization": "Bearer " + token}

    async def fake_export(*ids):
        return {"chat_id": ids[0], "chat_history": [{"id": 1, "content": "hola"}]}

    async def fake_delete(*ids):
        return {"chat_history": 1}

    monkeypatch.setattr(database.db, "export_user_data", fake_export)
    monkeypatch.setattr(database.db, "delete_user_data", fake_delete)

    export = client.get("/api/gdpr/export", headers=headers)
    assert export.status_code == 200
    assert export.json()["chat_history"][0]["content"] == "hola"

    sin_confirmar = client.post("/api/gdpr/delete", json={}, headers=headers)
    assert sin_confirmar.status_code == 400

    borrado = client.post("/api/gdpr/delete", json={"confirm": True}, headers=headers)
    assert borrado.status_code == 200
    assert borrado.json()["deleted"]["chat_history"] == 1


def test_web_chat_comandos_demo_y_ayuda(monkeypatch, tmp_path):
    client, fake = _cliente(monkeypatch, tmp_path)
    import asyncio

    asyncio.run(_crear_usuario(fake))
    token = client.post(
        "/api/auth/login", json={"email": "admin@x.com", "password": "clave12345"}
    ).json()["token"]
    headers = {"Authorization": "Bearer " + token}

    async def fake_demo(chat_id):
        return "🎬 *Demo de Rafita* — secciones"

    async def fake_generate(text, chat_id):
        raise AssertionError("un comando no debe caer en el modelo")

    monkeypatch.setattr("src.handlers.demo.build_demo_text", fake_demo)
    monkeypatch.setattr("src.core.generate_response", fake_generate)

    demo = client.post("/api/chat", json={"message": "/demo"}, headers=headers)
    assert demo.status_code == 200
    assert "Demo de Rafita" in demo.json()["reply"]

    ayuda = client.post("/api/chat", json={"message": "/ayuda"}, headers=headers)
    assert ayuda.status_code == 200
    assert "Comandos disponibles" in ayuda.json()["reply"]


def test_web_chat_comando_backup_dispara_sistema(monkeypatch, tmp_path):
    client, fake = _cliente(monkeypatch, tmp_path)
    import asyncio

    asyncio.run(_crear_usuario(fake))
    token = client.post(
        "/api/auth/login", json={"email": "admin@x.com", "password": "clave12345"}
    ).json()["token"]
    headers = {"Authorization": "Bearer " + token}

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    async def fake_generate(text, chat_id):
        raise AssertionError("un comando no debe caer en el modelo")

    monkeypatch.setattr("src.core.generate_response", fake_generate)

    resp = client.post("/api/chat", json={"message": "/backup"}, headers=headers)
    assert resp.status_code == 200
    assert "Backup completo" in resp.json()["reply"]
    assert (data_dir / "backup.trigger").exists()

    resp_zip = client.post("/api/chat", json={"message": "/backup_zip"}, headers=headers)
    assert resp_zip.status_code == 200
    assert "/backup_zip" in resp_zip.json()["reply"]
