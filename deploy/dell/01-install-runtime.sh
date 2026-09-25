#!/usr/bin/env bash
# Nodo Dell — runtime de IA (tarea 3.1). Idempotente.
#
# Local:  sudo bash 01-install-runtime.sh
# Remoto: ssh server@192.168.1.201 'sudo bash -s' < 01-install-runtime.sh
#
# Variables:
#   MODELS       modelos a descargar (default: "gemma4:12b bge-m3")
#   OLLAMA_BIND  interfaz inicial (default: 127.0.0.1; 02-network.sh la cambia)
set -euo pipefail

MODELS="${MODELS:-gemma4:12b bge-m3}"
OLLAMA_BIND="${OLLAMA_BIND:-127.0.0.1}"

if ! command -v ollama >/dev/null 2>&1; then
    echo "[1/4] Instalando Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
else
    echo "[1/4] Ollama ya instalado: $(ollama --version 2>/dev/null | head -1)"
fi

echo "[2/4] Configurando servicio (bind ${OLLAMA_BIND}:11434, keep_alive=-1, max_loaded=3)"
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/override.conf <<EOF
[Service]
Environment="OLLAMA_HOST=${OLLAMA_BIND}:11434"
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_MAX_LOADED_MODELS=3"
Environment="OLLAMA_NUM_PARALLEL=1"
EOF
systemctl daemon-reload
systemctl enable ollama >/dev/null 2>&1 || true
systemctl restart ollama

echo "[3/4] Esperando la API..."
for _ in $(seq 1 30); do
    if curl -sf http://127.0.0.1:11434/api/version >/dev/null; then
        break
    fi
    sleep 2
done
curl -s http://127.0.0.1:11434/api/version || {
    echo "ERROR: la API de Ollama no responde"
    exit 1
}
echo

echo "[4/4] Descargando modelos: ${MODELS}"
for model in ${MODELS}; do
    echo "  -> ${model}"
    ollama pull "${model}"
done
echo
ollama list
