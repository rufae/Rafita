"""D14 (2026-10-05): sub-workflows reutilizables (tareas.md «Evitar logica
duplicada» + «Crear sub-workflows»).

Invariantes del reparto:
- 6 subs en n8n/workflows/90-sub-*.json (Telegram, Autenticación, Logs,
  Errores, Validación, IA); Reintentos = retryOnFail por nodo y Errores
  estandarizados dentro de Sub · Logs (lo encadena Sub · Errores).
- Los padres referencian subs por marcador `__SUB__:<nombre>` que resuelve
  scripts/n8n_import_flows.py al importar, activando subs primero (n8n
  rechaza publicar padres con subs sin publicar).
- createHmac solo vive en Sub · Autenticación y Sub · Logs.
"""

import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
DIR = RAIZ / "n8n" / "workflows"
MARCADOR = "__SUB__:"

ESPERADOS = {
    "90-sub-logs.json": "Rafita · Sub · Logs",
    "90-sub-telegram.json": "Rafita · Sub · Telegram",
    "90-sub-autenticacion.json": "Rafita · Sub · Autenticación",
    "90-sub-errores.json": "Rafita · Sub · Errores",
    "90-sub-validacion.json": "Rafita · Sub · Validación",
    "90-sub-ia.json": "Rafita · Sub · IA",
}


def _cargar(nombre: str) -> dict:
    return json.loads((DIR / nombre).read_text(encoding="utf-8"))


def _padres() -> list[tuple[str, dict]]:
    out = []
    for f in sorted(DIR.glob("*.json")):
        if f.name.startswith("90-"):
            continue
        out.append((f.name, json.loads(f.read_text(encoding="utf-8"))))
    return out


def test_seis_sub_workflows_existent():
    for fname, nombre in ESPERADOS.items():
        w = _cargar(fname)
        assert w["name"] == nombre, "%s: nombre inesperado" % fname
        tipos = [n["type"] for n in w["nodes"]]
        assert "n8n-nodes-base.executeWorkflowTrigger" in tipos or (
            "n8n-nodes-base.errorTrigger" in tipos
        ), "%s sin trigger" % fname


def test_padres_referencian_subs_por_marcador():
    subs = set(ESPERADOS.values())
    for fname, w in _padres():
        assert w["settings"].get("errorWorkflow") == MARCADOR + "Rafita · Sub · Errores", (
            "%s sin errorWorkflow -> Sub · Errores" % fname
        )
        ejecs = [n for n in w["nodes"] if n["type"] == "n8n-nodes-base.executeWorkflow"]
        assert ejecs, "%s sin nodos executeWorkflow" % fname
        for n in ejecs:
            ref = n["parameters"]["workflowId"]["value"]
            assert ref.startswith(MARCADOR), "%s/%s sin marcador" % (fname, n["name"])
            assert ref[len(MARCADOR) :] in subs, "%s/%s -> sub desconocido %s" % (
                fname,
                n["name"],
                ref,
            )


def test_create_hmac_solo_en_subs():
    con_hmac = {}
    for f in sorted(DIR.glob("*.json")):
        w = json.loads(f.read_text(encoding="utf-8"))
        copias = sum(
            1 for n in w["nodes"] if "createHmac" in (n.get("parameters") or {}).get("jsCode", "")
        )
        if copias:
            con_hmac[f.name] = copias
    assert con_hmac == {"90-sub-autenticacion.json": 1, "90-sub-logs.json": 1}, con_hmac
    # los padres no firman nada: ni crypto ni secretos
    for fname, w in _padres():
        for n in w["nodes"]:
            js = (n.get("parameters") or {}).get("jsCode", "")
            assert "require('crypto')" not in js, "%s/%s firma en el padre" % (fname, n["name"])


def test_sub_errores_encadena_sub_logs():
    w = _cargar("90-sub-errores.json")
    tipos = [n["type"] for n in w["nodes"]]
    assert "n8n-nodes-base.errorTrigger" in tipos
    ejecs = [n for n in w["nodes"] if n["type"] == "n8n-nodes-base.executeWorkflow"]
    assert len(ejecs) == 1
    assert ejecs[0]["parameters"]["workflowId"]["value"] == MARCADOR + "Rafita · Sub · Logs"
    contexto = next(n for n in w["nodes"] if n["name"] == "Contexto error")
    js = contexto["parameters"]["jsCode"]
    assert "status: 'error'" in js and "severity: 'error'" in js
    # y la cadena de conexiones llega hasta el sub
    assert w["connections"]["Fallo"]["main"][0][0]["node"] == "Contexto error"
    assert w["connections"]["Contexto error"]["main"][0][0]["node"] == "Sub · Logs"


def test_execute_con_reintentos():
    # D14 «Reintentos»: todo nodo executeWorkflow reintenta como los HTTP.
    for f in sorted(DIR.glob("*.json")):
        w = json.loads(f.read_text(encoding="utf-8"))
        for n in w["nodes"]:
            if n.get("type") != "n8n-nodes-base.executeWorkflow":
                continue
            assert n.get("retryOnFail") is True, "%s/%s sin retry" % (f.name, n["name"])
            assert int(n.get("maxTries", 0)) >= 3, "%s/%s con <3 intentos" % (f.name, n["name"])
            assert int(n.get("waitBetweenTries", 0)) >= 1000, "%s/%s sin backoff" % (
                f.name,
                n["name"],
            )


def test_importador_resuelve_y_ordena_subs():
    src = (RAIZ / "scripts" / "n8n_import_flows.py").read_text(encoding="utf-8")
    assert '"__SUB__:"' in src, "el importador no resuelve marcadores __SUB__"
    assert "SUBS_PRIMERO" in src
    # Sub · Errores depende de Sub · Logs: debe declararse detras
    orden = src.index("SUBS_PRIMERO = [")
    bloque = src[orden : src.index("]", orden)]
    assert bloque.index("90-sub-logs.json") < bloque.index("90-sub-errores.json")
    # la activacion es subs primero y padres despues (API, no CLI)
    act_subs = src.index("activar(api, subs)")
    act_padres = src.index("activar(api, padres)")
    assert act_subs < act_padres, "los padres se activarian antes que los subs"


def test_flujo_07_valida_y_consulta_ia():
    w = _cargar("07-ejecutable-chat-voz.json")
    conns = w["connections"]
    assert conns["Webhook"]["main"][0][0]["node"] == "Validación"
    assert conns["Validación"]["main"][0][0]["node"] == "Procesar"
    assert conns["Procesar"]["main"][0][0]["node"] == "Sub · IA"
    assert conns["Sub · IA"]["main"][0][0]["node"] == "Respuesta"
    # el manual sigue entrando directo a Procesar (sin payload que validar)
    assert conns["Manual (chat)"]["main"][0][0]["node"] == "Procesar"
    ejecs = {
        n["name"]: n["parameters"]["workflowId"]["value"]
        for n in w["nodes"]
        if n["type"] == "n8n-nodes-base.executeWorkflow"
    }
    assert ejecs["Validación"] == MARCADOR + "Rafita · Sub · Validación"
    assert ejecs["Sub · IA"] == MARCADOR + "Rafita · Sub · IA"
    # respuesta final = esquema unico + resultado IA
    respuesta = next(n for n in w["nodes"] if n["name"] == "Respuesta")
    js = respuesta["parameters"]["jsCode"]
    assert "$('Procesar')" in js and "ia" in js


def test_subs_de_ia_y_validacion_usan_entorno():
    ia = _cargar("90-sub-ia.json")
    http = next(n for n in ia["nodes"] if n["type"].endswith("httpRequest"))
    assert "$env.OLLAMA_URL" in http["parameters"]["url"]
    assert "$env.OLLAMA_MODEL" in http["parameters"]["body"]
    assert http["parameters"].get("options", {}).get("timeout")
    val = _cargar("90-sub-validacion.json")
    js = next(n for n in val["nodes"] if n["type"].endswith("code"))["parameters"]["jsCode"]
    # sin texto valido no devuelve items: el flujo se detiene sin IFs
    assert "return [];" in js
