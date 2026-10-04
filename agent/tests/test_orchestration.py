"""Orquestador: catalogo, permisos y ejecucion (tareas.md 22 / item 13)."""

from src.core import orchestration as orch
from src.handlers import chat as chat_mod


def test_catalogo_cubre_los_10_flujos():
    assert len(orch.AUTOMATIONS) == 10
    webhooks = [s.webhook for s in orch.AUTOMATIONS.values()]
    assert len(webhooks) == len(set(webhooks))
    for spec in orch.AUTOMATIONS.values():
        assert spec.webhook.startswith("manual-")
        assert spec.mode in ("read", "write", "execute")
        assert spec.schedule
        assert spec.flow.startswith("Rafita ·")


def test_resolve_por_clave_nombre_y_flujo():
    assert orch.resolve("briefing").key == "briefing"
    assert orch.resolve("BRIEFING").key == "briefing"
    assert orch.resolve("radar-ia").key == "radar-ia"
    assert orch.resolve("Secuencias de email").key == "secuencias-email"
    assert orch.resolve("Rafita · 9 Clientes · Seguimiento CRM (lunes 09:00)").key == (
        "crm-seguimiento"
    )
    assert orch.resolve("no-existe") is None
    assert orch.resolve("") is None


def test_autoriza_read_y_write_automaticos():
    for spec in orch.AUTOMATIONS.values():
        if spec.mode in ("read", "write"):
            dec = orch.authorize(spec, confirm=False)
            assert dec["allowed"] is True
            assert dec["mode"] == spec.mode


def test_autoriza_execute_requiere_confirmacion():
    spec = orch.AUTOMATIONS["secuencias-email"]
    dec = orch.authorize(spec, confirm=False)
    assert dec["allowed"] is False
    assert dec["needs_confirmation"] is True
    assert orch.authorize(spec, confirm=True)["allowed"] is True


async def test_run_automation_feliz_lanza_webhook(monkeypatch):
    llamadas = []

    async def post_falso(url, payload):
        llamadas.append((url, payload))
        return True, "HTTP 200: Workflow was started"

    monkeypatch.setattr("src.core.orchestration._post", post_falso)

    res = await orch.run_automation("radar-ia", {"origen": "test"})

    assert res["success"] is True
    assert res["mode"] == "read"
    assert "Radar" in res["message"]
    url, payload = llamadas[0]
    assert url.endswith("/webhook/manual-radar-ia")
    assert payload == {"origen": "test"}


async def test_run_automation_execute_sin_confirm_no_lanza(monkeypatch):
    lanzado = []

    async def post_falso(url, payload):
        lanzado.append(url)
        return True, "nunca deberia llegar"

    monkeypatch.setattr("src.core.orchestration._post", post_falso)

    res = await orch.run_automation("secuencias-email")

    assert res["success"] is False
    assert res["needs_confirmation"] is True
    assert "confirm=true" in res["message"]
    assert lanzado == []


async def test_run_automation_con_confirm_si_lanza(monkeypatch):
    async def post_falso(url, payload):
        return True, "HTTP 200"

    monkeypatch.setattr("src.core.orchestration._post", post_falso)

    res = await orch.run_automation("ejecutable", confirm=True)

    assert res["success"] is True
    assert res["mode"] == "execute"


async def test_run_automation_clave_desconocida_es_honesta(monkeypatch):
    async def post_falso(url, payload):  # pragma: no cover
        raise AssertionError("no debe lanzar nada")

    monkeypatch.setattr("src.core.orchestration._post", post_falso)

    res = await orch.run_automation("facturas-del-mes")

    assert res["success"] is False
    assert "facturas-del-mes" in res["message"]
    assert "briefing" in res["disponibles"]


async def test_run_automation_params_no_dict_usa_vacio(monkeypatch):
    capturado = {}

    async def post_falso(url, payload):
        capturado.update(payload)
        return True, "HTTP 200"

    monkeypatch.setattr("src.core.orchestration._post", post_falso)

    res = await orch.run_automation("briefing", params="hola")  # type: ignore[arg-type]

    assert res["success"] is True
    assert capturado == {}


async def test_run_automation_error_n8n_lo_dice(monkeypatch):
    async def post_falso(url, payload):
        return False, "n8n respondio HTTP 404: Cannot POST /webhook/manual-plantilla-aviso"

    monkeypatch.setattr("src.core.orchestration._post", post_falso)

    res = await orch.run_automation("plantilla-aviso")

    assert res["success"] is False
    assert "404" in res["message"]
    assert "plantilla" in res["message"].lower()


def test_describe_automations():
    res = orch.describe_automations()
    assert res["success"] is True
    assert len(res["automations"]) == 10
    claves = {a["key"] for a in res["automations"]}
    assert claves == set(orch.AUTOMATIONS)
    modos = {a["permiso"] for a in res["automations"]}
    assert "accion externa" in modos


async def test_trigger_n8n_del_catalogo_pasa_por_permisos(monkeypatch):
    """El tool legado trigger_n8n no bypasa la capa (tarea 13)."""
    llamadas = []

    async def post_falso(url, payload):
        llamadas.append(url)
        return True, "HTTP 200"

    monkeypatch.setattr("src.core.orchestration._post", post_falso)

    res = await chat_mod._trigger_n8n({"workflow": "radar-ia"})

    assert res["success"] is True
    assert llamadas and llamadas[0].endswith("/webhook/manual-radar-ia")

    # ...pero una accion externa exige confirm por esa misma via.
    res2 = await chat_mod._trigger_n8n({"workflow": "secuencias-email"})
    assert res2["success"] is False
    assert res2.get("needs_confirmation") is True
    assert len(llamadas) == 1
