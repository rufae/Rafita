#!/usr/bin/env bash
# Expulsion segura del USB de backup (tarea 3.6).
# Uso: sudo usb-eject
set -euo pipefail

USB_MOUNT="${USB_MOUNT:-/mnt/backup}"
if mountpoint -q "$USB_MOUNT"; then
    sync
    umount "$USB_MOUNT"
    echo "USB de backup desmontado. Ya puedes retirarlo con seguridad."
else
    echo "El USB de backup no esta montado."
fi
