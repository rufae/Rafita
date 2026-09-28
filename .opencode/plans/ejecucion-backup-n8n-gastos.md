# Plan de ejecución — reparación backup + n8n + mejora 3 (gastos por foto)

Aprobado por el propietario (A-E, sin mejora 4). Pendiente de salir del modo
plan para ejecutar los cambios.

## A. Reparación de la migración de backups (usuario, con sudo)
El bug: `migrate-v2.sh` buscó el repo restic en `/mnt/rafael` (ya desmontado)
en vez de dentro del propio USB; no lo movió, por eso `restic check` falló, no
hubo backup y por eso no se sincronizó nada a Drive. `sqlite3` tampoco está
instalado (copia horaria fallaba).

**Decisión:** arreglar el script (B) y que el usuario **re-ejecute
`migrate-v2.sh`** (quedará idempotente y moverá los datos desde
`/mnt/backup/Servidor/...`). Comandos de verificación después:

```bash
sudo du -sh /mnt/backup/Backups-Rafita-AVP/restic      # <13 GB para Drive
sudo restic -r /mnt/backup/Backups-Rafita-AVP/restic snapshots   # 3 snapshots
sudo systemctl start rafita-backup.service
sudo tail -20 /var/log/rafita-backup.log               # snapshot + rclone OK
~/.local/bin/rclone lsd gdrive:Rafita-AVP-Backups
```

## B. Correcciones de código (agente)
1. `deploy/hp/backup/hourly-db.sh`: usar `python3` (`VACUUM INTO`) en vez del
   binario `sqlite3` → sin dependencias nuevas.
2. `deploy/hp/backup/migrate-v2.sh`:
   - mover restic/logs/snapshot-dell también desde `$USB_MOUNT/Servidor/...`
     (bug real), con destino idempotente (no pisa lo existente);
   - mensaje final correcto (la copia a Drive ya está activa);
   - re-ejecutable sin efectos secundarios.
3. `bash -n` + rsync al HP.

## C. n8n conectado a Rafita (mejora 1 completa)
1. Copiar `N8N_API_KEY` desde el `.env` local al `.env` del HP (sin imprimirla)
   y añadir `N8N_WEBHOOKS={"ejemplo": "http://n8n.home/webhook/ejemplo-rafita"}`.
2. Recrear el contenedor del agente (`up -d`, cambios de `.env`).
3. Con `scripts/n8n_manage.py`: `list`, `import` de los 2 flujos, `activate`.
4. Probar `run ejemplo-rafita` y, desde el agente, `_trigger_n8n` con el flujo
   «ejemplo» (verificación real).

## D. Mejora 3 — Gastos por foto (con botones de confirmación)
1. Prompt de visión: si la imagen es ticket/factura, añadir al final una línea
   estructurada `GASTO: {"importe":…, "comercio":…, "fecha":…, "categoria":…}`.
2. En `_process_vision_image` (files.py): parsear esa línea; si existe, enviar
   mensaje con **botones inline «Registrar / Descartar»** (CallbackQueryHandler
   nuevo en `bot.py`), guardando el gasto pendiente en `context.user_data`.
3. «Registrar» → `save_expense` (categorías existentes) + editar mensaje a
   confirmación; «Descartar» → editar a descartado. La imagen ya se guarda en
   el vault.
4. Si el caption menciona gasto/ticket y llava no emite GASTO, reintento con
   prompt específico de ticket.
5. Tests: parseo de GASTO, callback registrar/descartar, caption forzado.
   Verificación real: el usuario manda una foto de ticket.

## E. Cierre
- `plan.md` (local) actualizado; `CHANGELOG` genérico si aplica; tests + gate
  (ruff/mypy/pytest 1.169+) y commit/push de lo genérico.
- Recordatorio al usuario: re-ejecutar `migrate-v2.sh` y probar el ticket.
