"""Tareas.md Fase 1: los workflows n8n deben ser robustos y observables.

Valida en cada gate las invariantes del endurecimiento de 2026-10-04:
reintentos con backoff, timeouts, fallo controlado (sin tumbar ni spamear)
e informe de cada ejecución a /api/n8n/run.
"""

import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
FLUJOS = sorted((RAIZ / "n8n" / "workflows").glob("*.json"))


def _cargar(nombre: str) -> dict:
    return json.loads((RAIZ / "n8n" / "workflows" / nombre).read_text(encoding="utf-8"))


def _http(workflow: dict) -> list[dict]:
    return [n for n in workflow["nodes"] if n.get("type", "").endswith("httpRequest")]


def _rafita(workflow: dict) -> list[dict]:
    return [
        n for n in _http(workflow) if "rafita-agent-core" in n.get("parameters", {}).get("url", "")
    ]


def test_hay_flujos_y_casi_todos_hablan_con_rafita():
    assert len(FLUJOS) >= 8
    con_rafita = [f for f in FLUJOS if _rafita(json.loads(f.read_text(encoding="utf-8")))]
    assert len(con_rafita) >= 8


def test_todo_http_tiene_reintentos_backoff_y_timeout():
    for f in FLUJOS:
        for n in _http(json.loads(f.read_text(encoding="utf-8"))):
            assert n.get("retryOnFail") is True, "%s/%s sin retry" % (f.name, n["name"])
            assert int(n.get("maxTries", 0)) >= 3, "%s/%s con menos de 3 intentos" % (
                f.name,
                n["name"],
            )
            assert int(n.get("waitBetweenTries", 0)) >= 1000, "%s/%s sin backoff" % (
                f.name,
                n["name"],
            )
            assert n.get("parameters", {}).get("options", {}).get("timeout"), (
                "%s/%s sin timeout" % (f.name, n["name"])
            )


def test_fallo_controlado_en_llamadas_a_rafita_y_telegram():
    # onError=continueRegularOutput: un 500 de Rafita o de Telegram degrada
    # el flujo en vez de tumbarlo (los filtros posteriores no avisan con basura).
    for f in FLUJOS:
        w = json.loads(f.read_text(encoding="utf-8"))
        for n in _rafita(w):
            assert n.get("onError") == "continueRegularOutput", "%s/%s" % (f.name, n["name"])
        for n in _http(w):
            if "telegram.org" in n.get("parameters", {}).get("url", ""):
                assert n.get("onError") == "continueRegularOutput", "%s/%s" % (f.name, n["name"])


def test_flujos_con_rafita_reportan_sus_ejecuciones():
    for f in FLUJOS:
        w = json.loads(f.read_text(encoding="utf-8"))
        llamadas = _rafita(w)
        if not llamadas:
            continue  # plantilla sin llamada (07): no hay ejecucion que informar
        nombres = {n["name"] for n in w["nodes"]}
        assert "Firmar informe" in nombres, "%s sin nodo de firma de informe" % f.name
        assert "Reportar a Rafita" in nombres, "%s sin nodo de informe" % f.name
        # La rama de informe nace del primer nodo critico Rafita.
        criticos = llamadas[0]["name"]
        salida = w["connections"].get(criticos, {}).get("main", [[]])[0]
        assert any(c["node"] == "Firmar informe" for c in salida), (
            "%s: informe no conectado a %s" % (f.name, criticos)
        )
        reporte = next(n for n in w["nodes"] if n["name"] == "Reportar a Rafita")
        assert reporte["parameters"]["url"].endswith("/api/n8n/run")
        headers = reporte["parameters"]["headerParameters"]["parameters"]
        assert any(h["name"] == "X-Webhook-Signature" for h in headers)
        assert reporte.get("onError") == "continueRegularOutput"


def test_informe_firma_hmac_y_usa_execution_id():
    w = _cargar("01-briefing-contextual.json")
    firmar = next(n for n in w["nodes"] if n["name"] == "Firmar informe")
    code = firmar["parameters"]["jsCode"]
    assert "PEGA_AQUI_TU_WEBHOOK_SECRET" in code
    assert "$execution.id" in code
    assert "$workflow.name" in code
    assert "'error'" in code and "'ok'" in code


def test_mensaje_telegram_sin_texto_no_dispara():
    # Fallo controlado: si Rafita devuelve error, no se manda 'undefined'.
    w = _cargar("01-briefing-contextual.json")
    nodo = next(n for n in w["nodes"] if n["name"] == "Mensaje Telegram")
    assert "if (!r || !r.text)" in nodo["parameters"]["jsCode"]


def test_radar_sin_texto_no_dispara_telegram():
    # 06-radar-ia no tenia filtro y mandaba text: undefined en fallo (calidad 2026-10-04).
    w = _cargar("06-radar-ia.json")
    filtro = next(n for n in w["nodes"] if n["name"] == "Solo si hay radar")
    code = filtro["parameters"]["jsCode"]
    assert "r.success === false || !r.text" in code
    assert w["connections"]["Rafita radar"]["main"][0][0]["node"] == "Solo si hay radar"
    assert (
        w["connections"]["Solo si hay radar"]["main"][0][0]["node"] == "Enviar Telegram"
    )
    # el informe de ejecución sigue conectado en paralelo
    assert any(
        c["node"] == "Firmar informe" for c in w["connections"]["Rafita radar"]["main"][0]
    )
