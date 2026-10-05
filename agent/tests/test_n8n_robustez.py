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
        if f.name.startswith("90-"):
            continue  # subs: no son flujos con informe propio
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
        # D14: el POST/HMAC vive en Sub · Logs; el padre solo lo invoca.
        assert reporte["type"] == "n8n-nodes-base.executeWorkflow", f.name
        assert reporte["parameters"]["workflowId"]["value"] == "__SUB__:Rafita · Sub · Logs"
        assert reporte.get("onError") == "continueRegularOutput"
    # y el sub es el que postea a /api/n8n/run firmado
    logs = _cargar("90-sub-logs.json")
    http = next(n for n in logs["nodes"] if n["type"].endswith("httpRequest"))
    assert http["parameters"]["url"].endswith("'/api/n8n/run' }}")
    assert "$env.RAFITA_URL" in http["parameters"]["url"]
    headers = http["parameters"]["headerParameters"]["parameters"]
    assert any(h["name"] == "X-Webhook-Signature" for h in headers)
    assert http.get("onError") == "continueRegularOutput"


def test_informe_firma_hmac_y_usa_execution_id():
    w = _cargar("01-briefing-contextual.json")
    firmar = next(n for n in w["nodes"] if n["name"] == "Firmar informe")
    code = firmar["parameters"]["jsCode"]
    # D14: el padre solo monta el report con SU contexto de ejecucion...
    assert "$execution.id" in code
    assert "$workflow.name" in code
    assert "'error'" in code and "'ok'" in code
    assert "require('crypto')" not in code
    # ...y la firma HMAC (createHmac) vive en Sub · Logs.
    logs = _cargar("90-sub-logs.json")
    firmador = next(n for n in logs["nodes"] if n["type"].endswith("code"))
    assert "$env.WEBHOOK_SECRET" in firmador["parameters"]["jsCode"]
    assert "createHmac" in firmador["parameters"]["jsCode"]


def test_mensaje_telegram_sin_texto_no_dispara():
    # Fallo controlado + flag generico de silencio: sin notify del backend
    # (que solo se enciende con texto real) no se manda 'undefined'.
    w = _cargar("01-briefing-contextual.json")
    nodo = next(n for n in w["nodes"] if n["name"] == "Mensaje Telegram")
    assert "if (!r || r.notify !== true)" in nodo["parameters"]["jsCode"]


def test_radar_sin_texto_no_dispara_telegram():
    # 06-radar-ia no tenia filtro y mandaba text: undefined en fallo (calidad 2026-10-04).
    w = _cargar("06-radar-ia.json")
    filtro = next(n for n in w["nodes"] if n["name"] == "Solo si hay radar")
    code = filtro["parameters"]["jsCode"]
    assert "r.notify !== true" in code
    assert w["connections"]["Rafita radar"]["main"][0][0]["node"] == "Solo si hay radar"
    assert w["connections"]["Solo si hay radar"]["main"][0][0]["node"] == "Enviar Telegram"
    # el informe de ejecución sigue conectado en paralelo
    assert any(c["node"] == "Firmar informe" for c in w["connections"]["Rafita radar"]["main"][0])


def _destinos(w: dict, origen: str) -> list[str]:
    salida = w["connections"].get(origen, {}).get("main", [[]])[0]
    return [c["node"] for c in salida]


def test_02_guarda_solo_si_urgente():
    w = _cargar("02-inbox-zero.json")
    guard = next(n for n in w["nodes"] if n["name"] == "Solo si urgente")
    assert "if (!r || r.notify !== true) { return []; }" in guard["parameters"]["jsCode"]
    assert "Solo si urgente" in _destinos(w, "Rafita inbox-scan")
    assert "Firmar informe" in _destinos(w, "Rafita inbox-scan")
    assert _destinos(w, "Solo si urgente") == ["Enviar Telegram"]


def test_04_guarda_solo_si_cambios_sync():
    w = _cargar("04-sync-google-vault.json")
    guard = next(n for n in w["nodes"] if n["name"] == "Solo si cambios")
    assert "if (!r || r.notify !== true) { return []; }" in guard["parameters"]["jsCode"]
    assert "Solo si cambios" in _destinos(w, "Rafita sync")
    assert _destinos(w, "Solo si cambios") == ["Enviar Telegram"]


def test_05_guarda_solo_si_hay_informe():
    # Regresión D13: esta guarda quedó desconectada y el informe semanal no
    # enviaba Telegram pese a reportar success.
    w = _cargar("05-informe-semanal.json")
    guard = next(n for n in w["nodes"] if n["name"] == "Solo si hay informe")
    assert "if (!r || r.notify !== true) { return []; }" in guard["parameters"]["jsCode"]
    assert "Solo si hay informe" in _destinos(w, "Rafita informe")
    assert "Firmar informe" in _destinos(w, "Rafita informe")
    assert _destinos(w, "Solo si hay informe") == ["Enviar Telegram"]


def test_09_guarda_solo_si_pendientes():
    w = _cargar("09-crm-seguimiento.json")
    guard = next(n for n in w["nodes"] if n["name"] == "Solo si pendientes")
    assert "if (!r || r.notify !== true) { return []; }" in guard["parameters"]["jsCode"]
    assert "Solo si pendientes" in _destinos(w, "Rafita crm-remind")
    assert "Firmar informe" in _destinos(w, "Rafita crm-remind")
    assert _destinos(w, "Solo si pendientes") == ["Enviar Telegram"]


def test_10_guarda_solo_si_cambios():
    w = _cargar("10-secuencias-email.json")
    guard = next(n for n in w["nodes"] if n["name"] == "Solo si cambios")
    assert "if (!r || r.notify !== true) { return []; }" in guard["parameters"]["jsCode"]
    assert "Solo si cambios" in _destinos(w, "Rafita secuencias")
    assert "Firmar informe" in _destinos(w, "Rafita secuencias")
    assert _destinos(w, "Solo si cambios") == ["Enviar Telegram"]


def test_03_captura_sin_telegram_y_siempre_reporta():
    # La captura nunca spamea Telegram (la notificación la decide el agente);
    # sus dos triggers comparten payload y siempre se reporta la ejecución.
    w = _cargar("03-captura-vault.json")
    nombres = {n["name"] for n in w["nodes"]}
    assert not any("Telegram" in n for n in nombres)
    assert _destinos(w, "Webhook") == ["Payload captura"]
    assert _destinos(w, "Manual (chat)") == ["Payload captura"]
    assert "Firmar informe" in _destinos(w, "Rafita captura")


def test_08_plantilla_aviso_sin_telegram():
    # Plantilla de ejemplo (pausada): entrega vía /webhook/n8n del agente,
    # sin Telegram directo, con doble trigger y reporte siempre conectado.
    w = _cargar("08-plantilla-aviso-programado.json")
    nombres = {n["name"] for n in w["nodes"]}
    assert not any("Telegram" in n for n in nombres)
    assert _destinos(w, "Cada dia 09:00") == ["Payload aviso"]
    assert _destinos(w, "Manual (chat)") == ["Payload aviso"]
    assert "Firmar informe" in _destinos(w, "Enviar a Rafita")


def test_guardas_de_todos_los_flujos_leen_flag_notify():
    # Línea 68: `notify` del backend es el único interruptor de silencio;
    # toda guarda de texto («Solo si…» o «Mensaje Telegram») lo exige
    # (fail-closed: sin la clave o en false, jamás se avisa).
    vistas = 0
    for f in FLUJOS:
        w = json.loads(f.read_text(encoding="utf-8"))
        for n in w["nodes"]:
            if not (n["name"].startswith("Solo si") or n["name"] == "Mensaje Telegram"):
                continue
            code = n.get("parameters", {}).get("jsCode", "")
            if not code:
                continue
            assert "r.notify !== true" in code, "%s/%s sin flag notify" % (f.name, n["name"])
            vistas += 1
    assert vistas >= 7


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
    # D11/D14 (2026-10-05): el HTTP de sendMessage vive UNA vez, dentro de
    # Sub · Telegram; los padres lo invocan con executeWorkflow y siguen
    # construyendo el payload {chat_id, text, parse_mode,
    # disable_web_page_preview, reply_markup?} en un nodo code aguas arriba.
    import json as _json

    sub = _cargar("90-sub-telegram.json")
    http = [n for n in sub["nodes"] if n["type"].endswith("httpRequest")]
    assert len(http) == 1, "Sub · Telegram debe tener un unico HTTP"
    assert "sendMessage" in http[0]["parameters"]["url"]
    assert "$env.TELEGRAM_TOKEN" in http[0]["parameters"]["url"]
    assert http[0]["parameters"]["jsonBody"] == "={{ JSON.stringify($json) }}"

    padres = 0
    for f in sorted((RAIZ / "n8n" / "workflows").glob("*.json")):
        if f.name.startswith("90-"):
            continue
        w = _json.loads(f.read_text(encoding="utf-8"))
        invocaciones = [
            n
            for n in w["nodes"]
            if n.get("type") == "n8n-nodes-base.executeWorkflow"
            and n["parameters"]["workflowId"]["value"] == "__SUB__:Rafita · Sub · Telegram"
        ]
        if not invocaciones:
            continue
        padres += 1
        codigos = [
            (n.get("parameters") or {}).get("jsCode", "")
            for n in w["nodes"]
            if n.get("type") == "n8n-nodes-base.code"
        ]
        assert any("chat_id:" in c for c in codigos), (
            "%s: sin nodo code que construya chat_id" % f.name
        )
        assert any("disable_web_page_preview: true" in c for c in codigos), (
            "%s: payload sin disable_web_page_preview" % f.name
        )
    assert padres >= 7, "solo %d padres usan Sub · Telegram" % padres


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
        if f.name.startswith("90-"):
            continue  # en Sub · Logs «Firmar informe» es la firma, no el contexto
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
