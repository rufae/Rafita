#!/usr/bin/env python3
"""Gestion de workflows de n8n por su API publica.

Variables de entorno:
    N8N_URL      URL base de n8n (ej: http://localhost:5678)
    N8N_API_KEY  API key creada en n8n (Settings -> n8n API)

Uso:
    python scripts/n8n_manage.py list
    python scripts/n8n_manage.py import ruta/flujo1.json ruta/flujo2.json
    python scripts/n8n_manage.py activate <workflow_id>
    python scripts/n8n_manage.py deactivate <workflow_id>
    python scripts/n8n_manage.py run <ruta-webhook> '{"clave": "valor"}'
"""

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("N8N_URL", "http://localhost:5678").rstrip("/")
KEY = os.environ.get("N8N_API_KEY", "")


def api(method: str, path: str, payload: dict | None = None) -> dict:
    url = BASE + "/api/v1" + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-N8N-API-KEY", KEY)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        return {"error": e.code, "detail": e.read().decode()[:300]}
    except Exception as e:
        return {"error": "conexion", "detail": str(e)[:200]}


def cmd_list() -> int:
    result = api("GET", "/workflows")
    if "error" in result:
        print("ERROR:", result)
        return 1
    for wf in result.get("data", []):
        print("%-22s %-40s active=%s" % (wf.get("id"), wf.get("name"), wf.get("active")))
    return 0


def cmd_import(paths: list[str]) -> int:
    rc = 0
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            wf = json.load(fh)
        payload = {
            "name": wf.get("name", os.path.basename(path)),
            "nodes": wf.get("nodes", []),
            "connections": wf.get("connections", {}),
            "settings": wf.get("settings", {"executionOrder": "v1"}),
        }
        result = api("POST", "/workflows", payload)
        if "error" in result:
            print("ERROR importando %s: %s" % (path, result))
            rc = 1
        else:
            print("Importado '%s' -> id=%s" % (payload["name"], result.get("id")))
    return rc


def cmd_set_active(workflow_id: str, active: bool) -> int:
    result = api("PATCH", "/workflows/" + workflow_id, {"active": active})
    if "error" in result:
        print("ERROR:", result)
        return 1
    print("Workflow %s active=%s" % (workflow_id, result.get("active")))
    return 0


def cmd_run(webhook_path: str, payload_json: str) -> int:
    payload = json.loads(payload_json) if payload_json else {}
    url = BASE + "/webhook/" + webhook_path.lstrip("/")
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            print("HTTP %s: %s" % (resp.status, resp.read().decode()[:500]))
        return 0
    except urllib.error.HTTPError as e:
        print("HTTP %s: %s" % (e.code, e.read().decode()[:300]))
        return 1
    except Exception as e:
        print("ERROR:", e)
        return 1


def main(argv: list[str]) -> int:
    if not KEY and argv[:1] != ["run"]:
        print("Falta N8N_API_KEY (Settings -> n8n API en la interfaz de n8n).")
        return 2
    if not argv:
        print(__doc__)
        return 2
    command = argv[0]
    if command == "list":
        return cmd_list()
    if command == "import":
        return cmd_import(argv[1:])
    if command in ("activate", "deactivate") and len(argv) == 2:
        return cmd_set_active(argv[1], command == "activate")
    if command == "run" and len(argv) >= 2:
        return cmd_run(argv[1], argv[2] if len(argv) > 2 else "")
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
