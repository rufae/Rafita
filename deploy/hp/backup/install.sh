#!/usr/bin/env bash
# Instalador del sistema de backup al USB (tarea 3.6). Ejecutar como root en el HP.
#
# Uso:
#   sudo USB_UUID=<uuid> DELL_HOST=usuario@host ./install.sh
#   (o USB_DEV=/dev/sdX para detectar el UUID automaticamente con blkid)
#
# Los datos de cada instalacion (UUID del USB, host del Dell, etiqueta) no se
# versionan: se pasan por entorno o quedan en /etc/rafita-backup.env.
set -euo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"
USB_MOUNT="${USB_MOUNT:-/mnt/backup}"
USB_LABEL="${USB_LABEL:-BACKUP}"
RESTIC_DIR_NAME="${RESTIC_DIR_NAME:-server-$(hostname -s)}"
DELL_HOST="${DELL_HOST:-}"

echo "[1/7] restic..."
if ! command -v restic >/dev/null 2>&1; then
    apt-get update -qq
    apt-get install -y -qq restic
fi
restic version

echo "[2/7] UUID del USB..."
if [ -z "${USB_UUID:-}" ] && [ -n "${USB_DEV:-}" ]; then
    USB_UUID="$(blkid -s UUID -o value "$USB_DEV")"
fi
if [ -z "${USB_UUID:-}" ]; then
    echo "ERROR: define USB_UUID (o USB_DEV) del USB de backup."
    echo "  lsblk -o NAME,LABEL,UUID   # localiza el USB"
    exit 1
fi

echo "[3/7] unidades systemd..."
sed "s/__USB_UUID__/$USB_UUID/" "$BASE/mnt-backup.mount" > /etc/systemd/system/mnt-backup.mount
install -m 644 "$BASE/mnt-backup.automount" /etc/systemd/system/mnt-backup.automount
install -m 644 "$BASE/rafita-backup.service" /etc/systemd/system/rafita-backup.service
install -m 644 "$BASE/rafita-backup.timer" /etc/systemd/system/rafita-backup.timer
# Limpiar unidades antiguas de instalaciones previas (mnt-backup.*)
systemctl disable --now mnt-backup.automount 2>/dev/null || true
rm -f /etc/systemd/system/mnt-backup.automount /etc/systemd/system/mnt-backup.mount
systemctl daemon-reload
systemctl enable --now mnt-backup.automount
systemctl enable --now rafita-backup.timer

echo "[4/7] valores de la instalacion (/etc/rafita-backup.env)..."
if [ ! -f /etc/rafita-backup.env ]; then
    {
        echo "# Valores especificos de esta instalacion (no se versionan)."
        echo "DELL_HOST=${DELL_HOST:-usuario@host}"
        echo "USB_LABEL=$USB_LABEL"
        echo "RESTIC_DIR_NAME=$RESTIC_DIR_NAME"
        echo "USB_MOUNT=$USB_MOUNT"
    } > /etc/rafita-backup.env
    chmod 600 /etc/rafita-backup.env
fi
if grep -q '^DELL_HOST=usuario@host' /etc/rafita-backup.env 2>/dev/null; then
    echo "AVISO: edita /etc/rafita-backup.env y pon el DELL_HOST real (ssh usuario@host)."
fi

echo "[5/7] helper usb-eject..."
install -m 755 "$BASE/usb-eject.sh" /usr/local/bin/usb-eject

echo "[6/7] contrasena restic (root-only)..."
if [ ! -s /root/.restic-password ]; then
    openssl rand -base64 32 > /root/.restic-password
    chmod 600 /root/.restic-password
fi

echo "[7/7] montando USB y estructura..."
mkdir -p "$USB_MOUNT"
systemctl start mnt-backup.mount 2>/dev/null || true
if ! mountpoint -q "$USB_MOUNT"; then
    echo "ERROR: el USB no esta montado. Conectalo y repite el instalador."
    exit 1
fi

mkdir -p "$USB_MOUNT/Servidor/$RESTIC_DIR_NAME/restic" \
         "$USB_MOUNT/Servidor/$RESTIC_DIR_NAME/logs" \
         "$USB_MOUNT/Servidor/server-dell"
export RESTIC_REPOSITORY="$USB_MOUNT/Servidor/$RESTIC_DIR_NAME/restic"
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
