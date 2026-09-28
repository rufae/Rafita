"""Tests del mini-CRM (Fase 1): servicio en la boveda y endpoint de n8n."""

import hashlib
import hmac
import json
from datetime import date, timedelta

from src.config import settings
from src.services import crm_service as crm


def _vault(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "obsidian_vault_dir", str(tmp_path))
    return tmp_path / "CRM"


# ---------- funciones puras ----------


def test_slug_y_estado():
    assert crm._slug("María García") == "maria-garcia"
    assert crm._slug("  ACME S.L.  ") == "acme-s-l"
    assert crm._slug("") == "cliente"
    assert crm._normalizar_estado("Propuesta") == "propuesta"
    assert crm._normalizar_estado("ganado") == "cerrado"
    assert crm._normalizar_estado("lo-que-sea") == ""


def test_parse_frontmatter_y_render():
    texto = "---\nnombre: Ana\nvalor: 100\netiquetas: [cliente, web]\n---\n\n# Ana\n"
    datos, cuerpo = crm._parse_frontmatter(texto)
    assert datos["nombre"] == "Ana"
    assert datos["etiquetas"] == ["cliente", "web"]
    assert cuerpo.startswith("# Ana")
    vuelta = crm._render_nota(datos, cuerpo)
    datos2, _ = crm._parse_frontmatter(vuelta)
    assert datos2["nombre"] == "Ana"


def test_formatear_valor_con_alias_de_moneda():
    assert "€" in crm._formatear_valor(1200, "EUR")
    assert "€" in crm._formatear_valor(1200, "EURO")
    assert "1200" in crm._formatear_valor(1200, "EUR")


# ---------- servicio ----------


async def test_crear_cliente_con_defaults(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    result = await crm.crear_o_actualizar("María García", valor=1500, email="m@x.com")
    assert result["success"] is True
    assert result["cliente"]["estado"] == "lead"
    assert result["cliente"]["valor"] == 1500
    assert (tmp_path / "CRM" / "maria-garcia.md").exists()


async def test_obtener_por_nombre_con_acentos(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    await crm.crear_o_actualizar("María García")
    cliente = await crm.obtener("Maria Garcia")
    assert cliente is not None
    assert cliente["nombre"] == "María García"


async def test_estado_invalido(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    result = await crm.crear_o_actualizar("Ana", estado="inventado")
    assert result["success"] is False
    assert "Estado no valido" in result["message"]


async def test_anadir_nota_actualiza_contacto(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    await crm.crear_o_actualizar("Juan")
    result = await crm.anadir_nota("Juan", "Quiere presupuesto de web")
    assert result["success"] is True
    cliente = await crm.obtener("Juan")
    assert "Quiere presupuesto de web" in cliente["cuerpo"]
    assert cliente["ultimo_contacto"] == date.today().isoformat()


async def test_registrar_contacto(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    await crm.crear_o_actualizar("Luis")
    result = await crm.registrar_contacto("Luis", "2026-09-01")
    assert result["success"] is True
    cliente = await crm.obtener("Luis")
    assert cliente["ultimo_contacto"] == "2026-09-01"
    fallo = await crm.registrar_contacto("Nadie")
    assert fallo["success"] is False


async def test_resumen_pipeline(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    await crm.crear_o_actualizar("Ana", estado="propuesta", valor=1000)
    await crm.crear_o_actualizar("Luis", estado="cerrado", valor=2000)
    await crm.crear_o_actualizar("Sara", valor=500)
    resumen = await crm.resumen_pipeline()
    assert resumen["total_clientes"] == 3
    assert resumen["por_estado"]["propuesta"]["clientes"] == 1
    assert resumen["ganado"] == 2000
    assert resumen["valor_activo"] == 1500
    mensaje = (await crm.handle("summary", {}))["message"]
    assert "Pipeline de clientes" in mensaje
    assert "cerrado" in mensaje


async def test_handle_list_y_acciones(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    vacio = await crm.handle("list", {})
    assert vacio["success"] is True
    await crm.handle("create", {"nombre": "Ana", "estado": "contactado", "valor": 300})
    listado = await crm.handle("list", {})
    assert "Ana" in listado["message"] and "contactado" in listado["message"]
    nota = await crm.handle("note", {"nombre": "Ana", "nota": "Llamada ok"})
    assert nota["success"] is True
    estado = await crm.handle("estado", {"nombre": "Ana", "estado": "cerrado"})
    assert estado["cliente"]["estado"] == "cerrado"
    malo = await crm.handle("loquesea", {})
    assert malo["success"] is False


async def test_seguimientos_pendientes(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    antiguo = (date.today() - timedelta(days=10)).isoformat()
    reciente = date.today().isoformat()
    await crm.crear_o_actualizar("Viejo", valor=100)
    await crm.registrar_contacto("Viejo", antiguo)
    await crm.crear_o_actualizar("Nuevo", valor=200)
    await crm.registrar_contacto("Nuevo", reciente)
    await crm.crear_o_actualizar("Ganado", estado="cerrado", valor=300)
    await crm.registrar_contacto("Ganado", antiguo)
    await crm.crear_o_actualizar(
        "Cita", valor=50, proximo_seguimiento=(date.today() - timedelta(days=1)).isoformat()
    )
    pendientes = await crm.seguimientos_pendientes(dias=7)
    nombres = [c["nombre"] for c in pendientes]
    assert "Viejo" in nombres
    assert "Cita" in nombres
    assert "Nuevo" not in nombres
    assert "Ganado" not in nombres
    # ordenados por valor descendente
    assert nombres.index("Viejo") < nombres.index("Cita")


# ---------- endpoint n8n ----------


def test_crm_remind_requires_signature():
    from fastapi.testclient import TestClient

    from src.utils import webhook_server

    webhook_server.configure_gateway("secreto-crm")
    client = TestClient(webhook_server.app)
    resp = client.post("/automation/crm-remind", json={})
    assert resp.status_code in (401, 503)


def test_crm_remind_firmado(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from src.utils import webhook_server

    monkeypatch.setattr(settings, "obsidian_vault_dir", str(tmp_path))
    webhook_server.configure_gateway("secreto-crm")
    client = TestClient(webhook_server.app)
    body = json.dumps({"dias": 7}).encode()
    firma = hmac.new(b"secreto-crm", body, hashlib.sha256).hexdigest()
    resp = client.post(
        "/automation/crm-remind",
        content=body,
        headers={"X-Webhook-Signature": firma, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["changes"] == 0
