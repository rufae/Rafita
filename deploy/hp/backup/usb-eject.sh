#!/usr/bin/env bash
# Expulsion segura del USB RAFAEL (tarea 3.6).
# Uso: sudo usb-eject
set -euo pipefail

if mountpoint -q /mnt/rafael; then
    sync
    umount /mnt/rafael
    echo "USB RAFAEL desmontado. Ya puedes retirarlo con seguridad."
else
    echo "El USB RAFAEL no esta montado."
fi
