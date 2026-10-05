#!/usr/bin/env python3
"""Importa los flujos de `n8n/workflows/` en una instancia de n8n (API v1).

Idempotente: crea los flujos que no existen y actualiza los que ya estan
(emprejados por nombre) y asigna la etiqueta de su categoria.

D13: los flujos no hornean secretos; usan $env con TELEGRAM_TOKEN,
WEBHOOK_SECRET, RAFITA_CHAT_ID y RAFITA_URL, que inyecta
deploy/hp/docker-compose.n8n.yml en el contenedor n8n. Este script solo
valida que el .env local tenga esas claves.

D14: los padres referencian sub-workflows por marcador `__SUB__:<nombre>`
(workflowId de los nodos Execute Workflow y `settings.errorWorkflow`).
El script resuelve los marcadores con los ids reales tras crearlos y
**activa primero los subs** (n8n rechaza publicar un padre si su sub
todavia no esta publicado). Todo por API (deactivate -> PUT/POST ->
activate), asi que funciona tambien sin docker en la maquina:

    python scripts/n8n_import_flows.py --activate

Sin `--activate` los flujos referenciados quedan desactivados tras
importarlos (n8n no permite editar referencias de publicacion estando
activos); vuelve a lanzar con `--activate` para reactivarlos.

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

# fichero -> etiqueta de categoria (solo flujos principales)
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
# Orden de creacion/activacion de los subs: los que dependen de otros
# (Sub · Errores llama a Sub · Logs) van detras de sus dependencias y
# todos antes que los padres.
SUBS_PRIMERO = [
    "90-sub-logs.json",
    "90-sub-telegram.json",
    "90-sub-autenticacion.json",
    "90-sub-validacion.json",
    "90-sub-ia.json",
    "90-sub-errores.json",
]
MARCADOR = "__SUB__:"


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
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            raise RuntimeError("%s %s: %s" % (e.code, ruta, e.read().decode()[:200])) from e


def obtener_o_crear_etiqueta(api: Api, nombre: str) -> str:
    existentes = api("GET", "/tags").get("data", [])
    for tag in existentes:
        if tag.get("name") == nombre:
            return tag["id"]
    return api("POST", "/tags", {"name": nombre})["id"]


def orden_importacion() -> list[Path]:
    subs = [FLUJOS_DIR / n for n in SUBS_PRIMERO]
    padres = sorted(f for f in FLUJOS_DIR.glob("*.json") if not f.name.startswith("90-"))
    faltan = [s.name for s in subs if not s.exists()]
    if faltan:
        raise SystemExit("ERROR: faltan sub-workflows: %s" % ", ".join(faltan))
    return subs + padres


def resolver_marcadores(texto: str, ids: dict[str, str]) -> str:
    """Sustituye __SUB__:<nombre> por el id real; falla si falta alguno."""
    if MARCADOR not in texto:
        return texto
    for nombre, fid in ids.items():
        texto = texto.replace(MARCADOR + nombre, fid)
    if MARCADOR in texto:
        pendiente = texto[texto.index(MARCADOR) :][:80]
        raise RuntimeError("referencia sin resolver: %s" % pendiente)
    return texto


def desactivar(api: Api, existentes: dict[str, dict], nombres: set[str]) -> None:
    """Desactiva en orden seguro: padres antes que subs (y best-effort)."""
    for nombre in sorted(nombres, key=lambda n: n.startswith("Rafita · Sub")):
        info = existentes.get(nombre)
        if not info or not info.get("active"):
            continue
        try:
            api("POST", "/workflows/%s/deactivate" % info["id"])
            print("desactivado", nombre)
        except RuntimeError as e:
            print("AVISO: no se pudo desactivar %s (%s)" % (nombre, str(e)[:80]))


def activar(api: Api, lista: list[tuple[str, str]]) -> None:
    for nombre, flujo_id in lista:
        try:
            api("POST", "/workflows/%s/activate" % flujo_id)
            print("activado", nombre)
        except RuntimeError as e:
            print("ERROR: no se pudo activar %s (%s)" % (nombre, str(e)[:160]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5678", help="URL de n8n")
    parser.add_argument("--api-key", default="", help="API key (por defecto N8N_API_KEY del .env)")
    parser.add_argument("--env-file", default=str(RAIZ / ".env"), help="ruta del .env")
    parser.add_argument("--activate", action="store_true", help="activar por API tras importar")
    args = parser.parse_args()

    env = cargar_env(Path(args.env_file))
    api_key = args.api_key or env.get("N8N_API_KEY", "")
    if not api_key:
        print("ERROR: falta N8N_API_KEY (en el .env o con --api-key)")
        return 1
    faltan = [
        clave for clave in ("WEBHOOK_SECRET", "TELEGRAM_TOKEN", "ADMIN_IDS") if not env.get(clave)
    ]
    if faltan:
        print(
            "AVISO: faltan en el .env: %s (el contenedor n8n los necesita como "
            "WEBHOOK_SECRET, TELEGRAM_TOKEN y RAFITA_CHAT_ID)" % ", ".join(faltan)
        )

    api = Api(args.url, api_key)
    try:
        crudos = api("GET", "/workflows")
    except RuntimeError as e:
        print("ERROR: no se pudo conectar con n8n (%s)" % e)
        return 1
    existentes = {
        w["name"]: {"id": w["id"], "active": w.get("active", False)} for w in crudos.get("data", [])
    }

    rutas = orden_importacion()
    nombres_wf = {json.loads(ruta.read_text(encoding="utf-8"))["name"] for ruta in rutas}
    # n8n valida las referencias de publicacion al activar; para poder
    # reimportar sobre una instancia viva, desactivamos primero.
    desactivar(api, existentes, nombres_wf)

    ids: dict[str, str] = {}
    activables: list[tuple[str, str]] = []
    for ruta in rutas:
        texto = ruta.read_text(encoding="utf-8")
        if "PEGA_AQUI" in texto:
            print("ERROR: %s todavia lleva placeholders PEGA_AQUI_* (D13)" % ruta.name)
            return 1
        flujo = json.loads(texto)
        payload = {
            "name": flujo["name"],
            "nodes": flujo["nodes"],
            "connections": flujo["connections"],
            "settings": flujo.get("settings", {}),
        }
        # subs ya creados en esta vuelta -> resolvemos contra ids reales
        # (ensure_ascii=False: los nombres llevan «·» y se escaparian)
        payload = json.loads(
            resolver_marcadores(json.dumps(payload, ensure_ascii=False), ids)
        )
        info = existentes.get(flujo["name"])
        if info:
            flujo_id = info["id"]
            api("PUT", "/workflows/" + flujo_id, payload)
            accion = "actualizado"
        else:
            flujo_id = api("POST", "/workflows", payload)["id"]
            existentes[flujo["name"]] = {"id": flujo_id, "active": False}
            accion = "creado"
        ids[flujo["name"]] = flujo_id
        etiqueta = ETIQUETAS.get(ruta.name)
        if etiqueta:
            tag_id = obtener_o_crear_etiqueta(api, etiqueta)
            api("PUT", "/workflows/%s/tags" % flujo_id, [{"id": tag_id}])
        if ruta.name not in NO_ACTIVAR:
            activables.append((flujo["name"], flujo_id))
        print("%-10s %-52s %s" % (accion, flujo["name"], flujo_id))

    print("\nImportados %d flujos." % len(rutas))
    if args.activate:
        subs = [(n, i) for n, i in activables if n.startswith("Rafita · Sub")]
        padres = [(n, i) for n, i in activables if not n.startswith("Rafita · Sub")]
        # n8n exige que los subs esten publicados antes que sus padres
        activar(api, subs)
        activar(api, padres)
        subprocess.run(["docker", "restart", "n8n"], capture_output=True, timeout=120)
        print("n8n reiniciado")
    else:
        print("Flujos referenciados importados DESACTIVADOS (n8n exige este")
        print("orden con sub-workflows). Para activar:")
        print("  python scripts/n8n_import_flows.py --activate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
