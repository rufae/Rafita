#!/usr/bin/env bash
# Nodo Dell — red segura del LLM (tareas 3.0c/3.1). Idempotente.
#
# Local:  sudo HP_TS_IP=100.121.77.29 bash 02-network.sh
# Remoto: ssh server@192.168.1.201 'sudo HP_TS_IP=100.121.77.29 bash -s' < 02-network.sh
#
# Efectos:
#   1. Instala Tailscale si falta (la autenticacion es interactiva, ver abajo).
#   2. Activa ufw: SSH desde la LAN, 11434 solo desde la IP Tailscale del HP.
#   3. Rebinda Ollama a 0.0.0.0:11434 (la restriccion real la hace ufw; asi
#      127.0.0.1 sigue funcionando para benchmarks locales).
set -euo pipefail

HP_TS_IP="${HP_TS_IP:?Define HP_TS_IP con la IP Tailscale del nodo HP (ej: 100.121.77.29)}"

if ! command -v tailscale >/dev/null 2>&1; then
    echo "[1/4] Instalando Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh
else
    echo "[1/4] Tailscale ya instalado: $(tailscale version | head -1)"
fi

echo "[2/4] Comprobando autenticacion en la tailnet..."
if ! tailscale status >/dev/null 2>&1; then
    echo "  ACCION REQUERIDA: ejecuta 'sudo tailscale up' en este nodo y abre la URL"
    echo "  que imprima para autenticarlo en la tailnet. Despues repite este script."
    exit 2
fi
TS_IP="$(tailscale ip -4 | head -1)"
echo "  IP Tailscale del Dell: ${TS_IP}"

echo "[3/4] Configurando ufw (SSH LAN + 11434 solo desde ${HP_TS_IP})..."
ufw allow from 192.168.1.0/24 to any port 22 proto tcp >/dev/null
ufw allow in on tailscale0 to any port 22 proto tcp >/dev/null
ufw allow in on tailscale0 from "${HP_TS_IP}" to any port 11434 proto tcp >/dev/null
ufw --force enable >/dev/null
ufw status | head -12

echo "[4/4] Rebind de Ollama a 0.0.0.0:11434 (filtrado por ufw)..."
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/override.conf <<EOF
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_MAX_LOADED_MODELS=3"
Environment="OLLAMA_NUM_PARALLEL=1"
EOF
systemctl daemon-reload
systemctl restart ollama
sleep 3
curl -sf http://127.0.0.1:11434/api/version >/dev/null && echo "  Ollama local: OK"
echo "  Verificacion desde el HP pendiente (curl a ${TS_IP}:11434)."
