#!/usr/bin/env python3
"""Importa los flujos de `n8n/workflows/` en una instancia de n8n (API v1).

Idempotente: crea los flujos que no existen y actualiza los que ya estan
(emparejados por nombre). Sustituye los placeholders de las plantillas por los
valores del `.env` del proyecto y asigna la etiqueta de su categoria.

La activacion no esta disponible en la API publica; usa `--activate` para que
el script la aplique por CLI (requiere ejecutarse en la maquina del contenedor
n8n) o ejecuta a mano:

    docker exec n8n n8n update:workflow --id=<id> --active=true && docker restart n8n

Uso:
    python scripts/n8n_import_flows.py
    python scripts/n8n_import_flows.py --activate
    python scripts/n8n_import_flows.py --url http://127.0.0.1:5678 --env-file .env
"""

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
FLUJOS_DIR = RAIZ / "n8n" / "workflows"

# fichero -> etiqueta de categoria
ETIQUETAS = {
    "01-briefing-contextual.json": "Briefing",
    "02-inbox-zero.json": "Correo",
    "03-captura-vault.json": "Boveda",
    "04-sync-google-vault.json": "Google",
    "05-informe-semanal.json": "Infra",
    "06-radar-ia.json": "Radar",
    "07-ejecutable-chat-voz.json": "Automatizacion",
    "08-plantilla-aviso-programado.json": "Plantillas",
    "09-crm-seguimiento.json": "Clientes",
    "10-secuencias-email.json": "Secuencias",
}
# Las plantillas de ejemplo no se activan nunca.
NO_ACTIVAR = {"08-plantilla-aviso-programado.json"}


def cargar_env(ruta: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not ruta.exists():
        return env
    for line in ruta.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        clave, valor = line.split("=", 1)
        env[clave.strip()] = valor.strip().strip('"').strip("'")
    return env


def primer_admin(env: dict[str, str]) -> str:
    bruto = env.get("ADMIN_IDS", "").strip().strip("[]")
    for trozo in bruto.replace(" ", "").split(","):
        if trozo.strip():
            return trozo.strip()
    return ""


def sustituir(plantilla: str, env: dict[str, str]) -> str:
    chat_id = primer_admin(env)
    reemplazos = {
        "PEGA_AQUI_TU_WEBHOOK_SECRET": env.get("WEBHOOK_SECRET", ""),
        "PEGA_AQUI_TU_BOT_TOKEN": env.get("TELEGRAM_TOKEN", ""),
        "PEGA_AQUI_TU_CHAT_ID": chat_id,
    }
    for clave, valor in reemplazos.items():
        plantilla = plantilla.replace(clave, valor)
    return plantilla


class Api:
    def __init__(self, url: str, api_key: str):
        self.url = url.rstrip("/")
        self.api_key = api_key

    def __call__(self, metodo: str, ruta: str, payload=None):
        datos = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.url + "/api/v1" + ruta, data=datos, method=metodo)
        req.add_header("X-N8N-API-KEY", self.api_key)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            raise RuntimeError("%s %s: %s" % (e.code, ruta, e.read().decode()[:160])) from e


def obtener_o_crear_etiqueta(api: Api, nombre: str) -> str:
    existentes = api("GET", "/tags").get("data", [])
    for tag in existentes:
        if tag.get("name") == nombre:
            return tag["id"]
    return api("POST", "/tags", {"name": nombre})["id"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5678", help="URL de n8n")
    parser.add_argument("--api-key", default="", help="API key (por defecto N8N_API_KEY del .env)")
    parser.add_argument("--env-file", default=str(RAIZ / ".env"), help="ruta del .env")
    parser.add_argument("--activate", action="store_true", help="activar por CLI tras importar")
    args = parser.parse_args()

    env = cargar_env(Path(args.env_file))
    api_key = args.api_key or env.get("N8N_API_KEY", "")
    if not api_key:
        print("ERROR: falta N8N_API_KEY (en el .env o con --api-key)")
        return 1
    if not env.get("WEBHOOK_SECRET") or not env.get("TELEGRAM_TOKEN"):
        print(
            "AVISO: WEBHOOK_SECRET o TELEGRAM_TOKEN vacios en el .env; "
            "los flujos quedaran con placeholders sin sustituir"
        )

    api = Api(args.url, api_key)
    try:
        existentes = {w["name"]: w["id"] for w in api("GET", "/workflows").get("data", [])}
    except RuntimeError as e:
        print("ERROR: no se pudo conectar con n8n (%s)" % e)
        return 1

    importados: list[tuple[str, str]] = []
    activables: list[tuple[str, str]] = []
    for ruta in sorted(FLUJOS_DIR.glob("*.json")):
        flujo = json.loads(sustituir(ruta.read_text(encoding="utf-8"), env))
        payload = {
            "name": flujo["name"],
            "nodes": flujo["nodes"],
            "connections": flujo["connections"],
            "settings": flujo.get("settings", {}),
        }
        flujo_id = existentes.get(flujo["name"])
        if flujo_id:
            api("PUT", "/workflows/" + flujo_id, payload)
            accion = "actualizado"
        else:
            flujo_id = api("POST", "/workflows", payload)["id"]
            accion = "creado"
        etiqueta = ETIQUETAS.get(ruta.name)
        if etiqueta:
            tag_id = obtener_o_crear_etiqueta(api, etiqueta)
            api("PUT", "/workflows/%s/tags" % flujo_id, [{"id": tag_id}])
        importados.append((flujo["name"], flujo_id))
        if ruta.name not in NO_ACTIVAR:
            activables.append((flujo["name"], flujo_id))
        print("%-10s %-52s %s" % (accion, flujo["name"], flujo_id))

    print("\nImportados %d flujos." % len(importados))
    ids = " ".join(fid for _, fid in activables)
    if args.activate:
        for nombre, flujo_id in activables:
            try:
                subprocess.run(
                    [
                        "docker",
                        "exec",
                        "n8n",
                        "n8n",
                        "update:workflow",
                        "--id=%s" % flujo_id,
                        "--active=true",
                    ],
                    check=True,
                    capture_output=True,
                    timeout=60,
                )
                print("activado", nombre)
            except Exception as e:
                print("no se pudo activar %s por CLI (%s)" % (flujo_id, str(e)[:80]))
        subprocess.run(["docker", "restart", "n8n"], capture_output=True, timeout=120)
    else:
        print("Para activarlos (la API publica no lo permite; la plantilla queda inactiva):")
        print(
            "  for id in %s; do docker exec n8n n8n update:workflow --id=$id --active=true; done"
            % ids
        )
        print("  docker restart n8n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
