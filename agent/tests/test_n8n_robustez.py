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
        # D13: la URL vive en una expresion con $env.RAFITA_URL + path fijo.
        assert reporte["parameters"]["url"].endswith("'/api/n8n/run' }}")
        assert "$env.RAFITA_URL" in reporte["parameters"]["url"]
        headers = reporte["parameters"]["headerParameters"]["parameters"]
        assert any(h["name"] == "X-Webhook-Signature" for h in headers)
        assert reporte.get("onError") == "continueRegularOutput"


def test_informe_firma_hmac_y_usa_execution_id():
    w = _cargar("01-briefing-contextual.json")
    firmar = next(n for n in w["nodes"] if n["name"] == "Firmar informe")
    code = firmar["parameters"]["jsCode"]
    assert "process.env.WEBHOOK_SECRET" in code
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
    assert w["connections"]["Solo si hay radar"]["main"][0][0]["node"] == "Enviar Telegram"
    # el informe de ejecución sigue conectado en paralelo
    assert any(c["node"] == "Firmar informe" for c in w["connections"]["Rafita radar"]["main"][0])


def test_readme_n8n_refleja_la_realidad():
    # D10 (2026-10-05): el README decia /automation/brief (el real es
    # /automation/briefing), un payload de captura con "message" (el real es
    # text/title/tags) y firmaba HMAC el webhook de 07 (n8n no verifica).
    readme = (RAIZ / "n8n" / "README.md").read_text(encoding="utf-8")
    assert "/automation/briefing" in readme
    assert "/automation/brief " not in readme and "automation/brief`" not in readme
    assert '"text": "..."' in readme
    assert "rafita-agent-core:8000/webhook/captura-vault" not in readme
    # cada flujo documentado y con su bloque Dependencias
    flujos = sorted((RAIZ / "n8n" / "workflows").glob("*.json"))
    assert len(flujos) >= 10
    for f in flujos:
        assert f.name in readme, "falta en README: %s" % f.name
    assert readme.count("**Dependencias**") == len(flujos)


def test_nodos_telegram_payload_canonico_unificado():
    # D11 (2026-10-05): los 7 nodos «Enviar Telegram» usan stringify($json);
    # el payload {chat_id, text, parse_mode, disable_web_page_preview,
    # reply_markup?} lo construye siempre un nodo code aguas arriba.
    import json as _json

    flujos = sorted((RAIZ / "n8n" / "workflows").glob("*.json"))
    vistos = 0
    for f in flujos:
        w = _json.loads(f.read_text(encoding="utf-8"))
        codigos = [
            (n.get("parameters") or {}).get("jsCode", "")
            for n in w["nodes"]
            if n.get("type") == "n8n-nodes-base.code"
        ]
        for n in w["nodes"]:
            params = n.get("parameters") or {}
            if n.get("type") != "n8n-nodes-base.httpRequest":
                continue
            if "sendMessage" not in str(params.get("url", "")):
                continue
            vistos += 1
            assert params.get("jsonBody") == "={{ JSON.stringify($json) }}", (
                "%s / %s sin payload unificado" % (f.name, n["name"])
            )
            assert any("chat_id:" in c for c in codigos), (
                "%s: sin nodo code que construya chat_id" % f.name
            )
            assert any("disable_web_page_preview: true" in c for c in codigos), (
                "%s: payload sin disable_web_page_preview" % f.name
            )
    assert vistos >= 7


def test_briefing_no_usa_r_antes_de_declararla():
    # Regresion real: TDZ «Cannot access 'r' before initialization» rompio el
    # flujo 01 cada dia (ejecucion 503, 2026-10-05); nadie lo veia porque el
    # ProactiveWorker mandaba el briefing por otro camino.
    import json as _json

    w = _json.loads(
        (RAIZ / "n8n" / "workflows" / "01-briefing-contextual.json").read_text(encoding="utf-8")
    )
    nodo = next(n for n in w["nodes"] if n["name"] == "Mensaje Telegram")
    code = nodo["parameters"]["jsCode"]
    assert code.index("const r =") < code.index("if (!r")


def test_ejecutable_responde_esquema_unico():
    # D11: el flujo 07 respondia {ok, mensaje}; el esquema unico es
    # {success, message} (igual que el resto de endpoints del gateway).
    import json as _json

    w = _json.loads(
        (RAIZ / "n8n" / "workflows" / "07-ejecutable-chat-voz.json").read_text(encoding="utf-8")
    )
    codigos = "\n".join(
        (n.get("parameters") or {}).get("jsCode", "")
        for n in w["nodes"]
        if n.get("type") == "n8n-nodes-base.code"
    )
    assert "success: true" in codigos
    assert "ok: true" not in codigos
    assert "mensaje:" not in codigos


def test_flujos_envian_severity_en_el_informe():
    # D12: taxonomía única; cada nodo «Firmar informe» lleva el nivel en el
    # body que postea a /api/n8n/run (el backend lo deriva si no viene).
    import json as _json

    flujos = sorted((RAIZ / "n8n" / "workflows").glob("*.json"))
    con_informe = 0
    for f in flujos:
        w = _json.loads(f.read_text(encoding="utf-8"))
        for n in w["nodes"]:
            if n.get("name") != "Firmar informe":
                continue
            con_informe += 1
            code = n["parameters"]["jsCode"]
            assert "severity: error ? 'error' : 'info'" in code, f.name
    assert con_informe == 9


def test_telegram_alcanzable_desde_las_fuentes():
    # Regresion real (D13, 2026-10-05): en el flujo 05 el nodo guard
    # «Solo si hay informe» quedo sin conectar (Rafita informe apuntaba solo
    # a Firmar informe) y el informe semanal no enviaba Telegram pese a
    # reportar success. Este test exige que Enviar Telegram sea alcanzable.
    import json as _json

    for f in sorted((RAIZ / "n8n" / "workflows").glob("*.json")):
        w = _json.loads(f.read_text(encoding="utf-8"))
        nombres = {n["name"] for n in w["nodes"]}
        if "Enviar Telegram" not in nombres:
            continue
        destinos = set()
        for origen in w["connections"].values():
            for arr in origen.get("main", []):
                for c in arr:
                    destinos.add(c["node"])
        fuentes = nombres - destinos
        alcanzables = set(fuentes)
        cola = list(fuentes)
        while cola:
            actual = cola.pop()
            for arr in w["connections"].get(actual, {}).get("main", []):
                for c in arr:
                    if c["node"] not in alcanzables:
                        alcanzables.add(c["node"])
                        cola.append(c["node"])
        assert "Enviar Telegram" in alcanzables, (
            "%s: Enviar Telegram no alcanzable (nodo huerfano)" % f.name
        )
