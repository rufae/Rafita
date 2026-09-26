#!/usr/bin/env bash
# Instalador del sistema de backup al USB (tarea 3.6). Ejecutar como root en el HP.
set -euo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"

echo "[1/6] restic..."
if ! command -v restic >/dev/null 2>&1; then
    apt-get update -qq
    apt-get install -y -qq restic
fi
restic version

echo "[2/6] unidades systemd..."
install -m 644 "$BASE/mnt-rafael.mount" /etc/systemd/system/mnt-rafael.mount
install -m 644 "$BASE/mnt-rafael.automount" /etc/systemd/system/mnt-rafael.automount
install -m 644 "$BASE/rafita-backup.service" /etc/systemd/system/rafita-backup.service
install -m 644 "$BASE/rafita-backup.timer" /etc/systemd/system/rafita-backup.timer
systemctl daemon-reload
systemctl enable --now mnt-rafael.automount
systemctl enable --now rafita-backup.timer

echo "[3/6] helper usb-eject..."
install -m 755 "$BASE/usb-eject.sh" /usr/local/bin/usb-eject

echo "[4/6] contrasena restic (root-only)..."
if [ ! -s /root/.restic-password ]; then
    openssl rand -base64 32 > /root/.restic-password
    chmod 600 /root/.restic-password
fi

echo "[5/6] montando USB..."
mkdir -p /mnt/rafael
systemctl start mnt-rafael.mount 2>/dev/null || true
if ! mountpoint -q /mnt/rafael; then
    echo "ERROR: el USB no esta montado. Conectalo y repite el instalador."
    exit 1
fi

echo "[6/6] estructura en el USB y repositorio restic..."
mkdir -p /mnt/rafael/Servidor/server-nodochicohp/restic \
         /mnt/rafael/Servidor/server-nodochicohp/logs \
         /mnt/rafael/Servidor/server-dell
export RESTIC_REPOSITORY=/mnt/rafael/Servidor/server-nodochicohp/restic
export RESTIC_PASSWORD_FILE=/root/.restic-password
if ! restic snapshots >/dev/null 2>&1; then
    restic init
    echo "Repositorio restic inicializado."
fi

echo
echo "Instalacion completada. IMPORTANTE: guarda la contrasena de restic en tu"
echo "gestor de contrasenas (sin ella los backups no se pueden restaurar):"
echo "  sudo cat /root/.restic-password"
echo
echo "Prueba manual del backup:  sudo systemctl start rafita-backup.service"
echo "Expulsion segura del USB:  sudo usb-eject"
