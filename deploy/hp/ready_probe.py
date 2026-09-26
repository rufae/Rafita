#!/usr/bin/env python3
"""Sonda de readiness para evidencias de 3.3/3.4 (nodo HP).

Ejecuta los checks reales del gateway (`webhook_server.app`) sin arrancar el
bot de Telegram, y permite simular fallos por variables de entorno:

  PROBE_SKIP_VECTOR=1   no inicializa Chroma  -> vector_db unhealthy
  PROBE_UNLOAD=1        descarga el modelo de chat en el LLM -> degraded
  OLLAMA_HOST=...       apuntar a un backend inexistente -> unhealthy
  OBSIDIAN_VAULT_DIR=.. vault inexistente -> unhealthy

Uso (en el HP, desde la raiz del repo):
  docker run --rm --env-file .env -e PYTHONPATH=/app \
    -v "$PWD:/workspace" -v "$PWD/agent/src:/app/src" \
    -v "$PWD/data:/data" -v "$PWD/mi_boveda_obsidian:/data/obsidian_vault" \
    rafita-rafita-agent-core python /workspace/deploy/hp/ready_probe.py
"""

import asyncio
import json
import os
import sys

import httpx

from src.ollama_client import llm
from src.utils import webhook_server as ws
from src.utils.vector_manager import vector_db


class _FakeBot:
    def polling_status(self) -> dict:
        return {"status": "ok"}


async def main() -> int:
    if os.environ.get("PROBE_NO_BOT") == "1":
        ws.configure_gateway("probe-secret", bot_ref=None)
    else:
        ws.configure_gateway("probe-secret", bot_ref=_FakeBot())

    try:
        await llm.initialize()
    except Exception as exc:  # LLM caido: seguir para poder reportarlo
        print("llm.initialize failed: %s" % exc)

    if os.environ.get("PROBE_SKIP_VECTOR") == "1":
        print("PROBE_SKIP_VECTOR=1: no se inicializa Chroma")
    else:
        await vector_db.initialize()

    if os.environ.get("PROBE_UNLOAD") == "1":
        await llm.unload_model(llm.model)
        await asyncio.sleep(2)

    transport = httpx.ASGITransport(app=ws.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://probe") as client:
        response = await client.get("/ready")
        print("HTTP %d" % response.status_code)
        print(json.dumps(response.json(), indent=2, ensure_ascii=False))
        print(
            "SUMMARY http=%d overall=%s checks=%s"
            % (
                response.status_code,
                response.json()["status"],
                {k: v.get("status") for k, v in response.json()["checks"].items()},
            )
        )

    if not os.environ.get("PROBE_SKIP_VECTOR"):
        await vector_db.close()
    await llm.close()
    return 0 if response.status_code in (200, 503) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
