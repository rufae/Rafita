#!/usr/bin/env bash
# Snapshot diario de configuracion del nodo Dell (tarea 3.6).
# No incluye los pesos de los modelos (re-descargables); solo configuracion.
# Se ejecuta como root (cron.d). Deja el tarball en /home/server/backups.
set -euo pipefail

OUT_DIR="/home/server/backups"
TS="$(date +%Y%m%d)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$OUT_DIR" "$TMP/config"

cp -a /etc/netplan "$TMP/config/" 2>/dev/null || true
ufw status numbered > "$TMP/config/ufw-status.txt" 2>/dev/null || true
cp -a /etc/systemd/system/ollama.service.d "$TMP/config/" 2>/dev/null || true
cp -a /etc/fstab "$TMP/config/" 2>/dev/null || true
cp -a /etc/docker/daemon.json "$TMP/config/" 2>/dev/null || true
ollama list > "$TMP/config/ollama-models.txt" 2>/dev/null || true
tailscale status > "$TMP/config/tailscale-status.txt" 2>/dev/null || true
ip -4 addr > "$TMP/config/ip-addr.txt" 2>/dev/null || true
hostnamectl > "$TMP/config/hostnamectl.txt" 2>/dev/null || true
uname -a > "$TMP/config/uname.txt" 2>/dev/null || true
systemctl list-units --type=service --state=running --no-pager \
    > "$TMP/config/services-running.txt" 2>/dev/null || true

tar czf "$OUT_DIR/dell-config-$TS.tar.gz" -C "$TMP" .
cp -f "$OUT_DIR/dell-config-$TS.tar.gz" "$OUT_DIR/dell-config-latest.tar.gz"

# Retencion local: 7 snapshots
ls -1t "$OUT_DIR"/dell-config-*.tar.gz 2>/dev/null | tail -n +8 | xargs -r rm -f

echo "snapshot: $OUT_DIR/dell-config-$TS.tar.gz"
