# Backup del homelab al USB (tarea 3.6)

Diseno completo y decisiones en `plan.md` (tarea 3.6). Resumen operativo:

## Estructura en el USB

```
<USB_LABEL>/Servidor/
├── server-<hostname>/
│   ├── restic/     # repositorio restic (cifrado, incremental, dedup)
│   └── logs/       # salida de cada ejecucion
└── server-dell/    # snapshot de configuracion del Dell (via stage de restic)
```

## Instalacion (HP, como root)

```bash
# El UUID del USB y el host del Dell son datos de cada instalacion:
sudo USB_UUID=<uuid> DELL_HOST=usuario@host bash deploy/hp/backup/install.sh
sudo cat /root/.restic-password   # guardar en el gestor de contrasenas
```

Los valores quedan en `/etc/rafita-backup.env` (root, no versionado).

### Migración desde una instalación anterior (mnt-rafael)

Si ya tenías el backup instalado con `mnt-rafael.*` y `/mnt/rafael`, los datos
del USB no cambian; solo se renombra el punto de montaje y se sacan del repo
los valores personales. Como root en el HP:

```bash
sudo USB_UUID="$(blkid -s UUID -o value /dev/sda1)" \
     DELL_HOST=usuario@host \
     USB_LABEL=RAFAEL RESTIC_DIR_NAME=server-<hostname> \
     bash deploy/hp/backup/install.sh
# Verifica que ve el historial y que el servicio arranca:
sudo restic -r /mnt/backup/Servidor/server-<hostname>/restic snapshots | tail -3
sudo systemctl start rafita-backup.service && systemctl status rafita-backup.service
```

Instala restic, monta el USB por UUID (`<UUID_USB>`), crea la estructura,
inicializa el repositorio y activa el timer diario (03:30, solo si el USB
esta presente). Incluye `usb-eject` para retirarlo con seguridad.

## Operacion

- Backup manual: `sudo systemctl start rafita-backup.service`
- Estado: `systemctl status rafita-backup.timer` y `/var/log/rafita-backup.log`
- Retirar el USB: `sudo usb-eject`
- Snapshots: `sudo restic -r /mnt/backup/Servidor/server-<hostname>/restic snapshots`
- Notificacion por Telegram al terminar (exito/fallo).

## Que se copia

| Servicio | Estrategia |
|---|---|
| BuenaTierra | `pg_dump -Fc` + volumenes (logs/reports/uploads/dist) + bind dir |
| Nextcloud | `occ maintenance:mode` + ficheros `/opt/homelab/nextcloud/app` + `pg_dump` |
| Rafita | SQLite `.backup` + vector_db + vault + `.env` (clave Fernet) + codigo |
| NPM | SQLite `.backup` + `data/` + `letsencrypt/` |
| AdGuard | `/data/compose/4` (conf+work) |
| WireGuard | `/home/server/wireguard/etc_wireguard` |
| Portainer | volumen BoltDB (parada breve ~5 s) |
| Configs | composes, `/etc/netplan`, `/etc/ufw`, `/etc/docker/daemon.json`, `/etc/fstab` |
| Dell | snapshot de config (netplan, ufw, ollama, tailscale, servicios) |

## Restauracion

Procedimiento completo en `docs/runbook.md` (seccion "Restauracion desde el
USB"). Verificacion no destructiva: restaurar a un directorio temporal y
comprobar integridad SQLite, chunks de Chroma, descifrado Fernet y arranque
del agente contra el Dell.
