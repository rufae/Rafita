"""Las plantillas n8n del repo no deben contener secretos ni datos personales.

Se ejecuta en el gate: si alguien exporta un flujo con su token, chat id o IP
privada, el test falla y no se sube.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
FLUJOS = sorted((RAIZ / "n8n" / "workflows").glob("*.json"))

# Token de bot de Telegram (123456789:AA...).
TOKEN_RE = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b")
# chat_id numerico en JSON o dentro de codigo JS: chat_id: 123456789 / Number('123456789')
CHAT_ID_RE = re.compile(r"chat_?id[^\n]{0,30}?(\d{6,})", re.IGNORECASE)
# IPs privadas (RFC1918 + rango Tailscale 100.64.0.0/10).
IP_RE = re.compile(
    r"\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3})\b"
)
# Valores largos en claves sensibles (los placeholders y expresiones se permiten).
CLAVE_RE = re.compile(
    r'["\']?(?:secret|token|password|api_?key)["\']?\s*[:=]\s*["\']([^"\']{20,})["\']',
    re.IGNORECASE,
)
PERMITIDOS = ("{{", "$env", "process.env")


def _textos() -> list[tuple[str, str]]:
    return [(f.name, f.read_text(encoding="utf-8")) for f in FLUJOS]


def test_hay_plantillas_en_el_repo():
    assert len(FLUJOS) >= 8, "faltan plantillas n8n en n8n/workflows/"


def test_sin_secretos_ni_datos_personales():
    problemas: list[tuple[str, str, str]] = []
    for nombre, texto in _textos():
        for m in TOKEN_RE.finditer(texto):
            problemas.append((nombre, "bot_token", m.group()[:18] + "..."))
        for m in CHAT_ID_RE.finditer(texto):
            problemas.append((nombre, "chat_id", m.group(1)))
        for m in IP_RE.finditer(texto):
            problemas.append((nombre, "ip_privada", m.group()))
        for m in CLAVE_RE.finditer(texto):
            if not m.group(1).startswith(PERMITIDOS):
                problemas.append((nombre, "clave_sensible", m.group(1)[:16] + "..."))
    assert problemas == [], "Posibles secretos/datos en plantillas: %s" % problemas


def test_config_por_entorno_sin_placeholders():
    # D13 (2026-10-05): los 33 placeholders PEGA_AQUI_* horneados se
    # sustituyeron por $env (HTTP) y process.env (Code); el importador deja
    # de horneear y valida. Nada de secretos horneables en las plantillas.
    for nombre, texto in _textos():
        assert "PEGA_AQUI" not in texto, "%s todavia lleva placeholders" % nombre
    briefing = (RAIZ / "n8n" / "workflows" / "01-briefing-contextual.json").read_text(
        encoding="utf-8"
    )
    assert "$env.TELEGRAM_TOKEN" in briefing
    assert "$env.WEBHOOK_SECRET" in briefing
    assert "$env.RAFITA_CHAT_ID" in briefing
    assert "$env.RAFITA_URL" in briefing


def test_flujos_solo_en_n8n_workflows():
    # D9 (2026-10-05): deploy/hp/n8n-flows/ era una copia desincronizada de
    # n8n/workflows/ (8/8 distintos, faltaban 09 y 10) y deploy/ nunca se
    # trackea. Se elimino la copia: fuente unica = n8n/workflows/. Si este
    # test falla, alguien ha vuelto a duplicar un flujo fuera de esa ruta.
    nombres = {f.name for f in FLUJOS}
    duplicados = [
        str(f.relative_to(RAIZ))
        for f in RAIZ.rglob("*.json")
        if f.name in nombres and f.parent != RAIZ / "n8n" / "workflows" and ".git" not in f.parts
    ]
    assert duplicados == [], "flujos duplicados fuera de n8n/workflows: %s" % duplicados
