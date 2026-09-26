# Backup del homelab al USB (tarea 3.6)

Diseno completo y decisiones en `plan.md` (tarea 3.6). Resumen operativo:

## Estructura en el USB

```
RAFAEL/Servidor/
├── server-nodochicohp/
│   ├── restic/     # repositorio restic (cifrado, incremental, dedup)
│   └── logs/       # salida de cada ejecucion
└── server-dell/    # snapshot de configuracion del Dell (via stage de restic)
```

## Instalacion (HP, como root)

```bash
sudo bash deploy/hp/backup/install.sh
sudo cat /root/.restic-password   # guardar en el gestor de contrasenas
```

Instala restic, monta el USB por UUID (`1916-3621`), crea la estructura,
inicializa el repositorio y activa el timer diario (03:30, solo si el USB
esta presente). Incluye `usb-eject` para retirarlo con seguridad.

## Operacion

- Backup manual: `sudo systemctl start rafita-backup.service`
- Estado: `systemctl status rafita-backup.timer` y `/var/log/rafita-backup.log`
- Retirar el USB: `sudo usb-eject`
- Snapshots: `sudo restic -r /mnt/rafael/Servidor/server-nodochicohp/restic snapshots`
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
