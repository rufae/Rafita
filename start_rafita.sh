#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================"
echo "  Rafita Agent Core v5.0-Vision-Calendar-RAG"
echo "  Iniciando sistema..."
echo "============================================"
echo

# Verificar que Docker esta instalado
if ! command -v docker &>/dev/null; then
    echo "[ERROR] Docker no encontrado. Instala Docker Desktop desde:"
    echo "  https://www.docker.com/products/docker-desktop/"
    exit 1
fi

# Verificar que Docker esta corriendo
if ! docker info &>/dev/null; then
    echo "[ERROR] Docker no esta corriendo. Abre Docker Desktop e intenta de nuevo."
    exit 1
fi

# Levantar contenedores
echo "[Rafita] Levantando contenedores..."
docker compose up -d

# Esperar a que Ollama este saludable
echo "[Rafita] Esperando a que Ollama este listo..."
until docker inspect -f "{{.State.Health.Status}}" ollama-service 2>/dev/null | grep -q "healthy"; do
    sleep 5
done
echo "[Rafita] Ollama listo!"

# Verificar que el agente esta corriendo
echo "[Rafita] Verificando agente..."
if ! docker inspect -f "{{.State.Status}}" rafita-agent-core 2>/dev/null | grep -q "running"; then
    echo "[ERROR] El agente no esta corriendo. Revisa los logs con:"
    echo "  docker compose logs rafita-agent-core"
    exit 1
fi

echo "============================================"
echo "  Sistema listo! Busca a @${BOT_USERNAME:-RafitaBot} en Telegram"
echo "  Logs en pantalla (cierra con Ctrl+C):"
echo "============================================"
echo

docker compose logs -f rafita-agent-core
