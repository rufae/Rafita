#!/usr/bin/env bash
# Backup diario del homelab al USB RAFAEL con restic (tarea 3.6).
#
# Se ejecuta como root (systemd rafita-backup.service). Si el USB no esta
# presente, registra "USB no presente" y sale con 0 (no es un fallo).
#
# Estrategia por servicio:
#   - Postgres (BuenaTierra, Nextcloud): pg_dump en vivo (consistente).
#   - SQLite (Rafita, NPM): API .backup de Python (consistente en vivo).
#   - Portainer: parada breve (~5 s) y copia del volumen BoltDB.
#   - Nextcloud ficheros: modo mantenimiento durante el restic.
#   - AdGuard: lectura directa como root (stack Portainer, /data/compose/4).
#   - Volumenes pequenos de BuenaTierra: tar al stage.
#   - Configs del sistema + snapshot del Dell.
set -euo pipefail

HOME_DIR="$(getent passwd server | cut -d: -f6 || true)"
HOME_DIR="${HOME_DIR:-/home/server}"
REPO_DIR="$HOME_DIR/proyectos/rafita"
ENV_FILE="$REPO_DIR/.env"
USB_MOUNT="/mnt/rafael"
USB_LABEL="RAFAEL"
RESTIC_REPO="$USB_MOUNT/Servidor/server-nodochicohp/restic"
RESTIC_PASSWORD_FILE="/root/.restic-password"
STAGE="/var/lib/rafita-backup/stage"
LOG="/var/log/rafita-backup.log"
LOCK="/var/lock/rafita-backup.lock"
DELL_HOST="server@100.83.40.103"
DELL_SNAPSHOT="$STAGE/dell-config-latest.tar.gz"

mkdir -p /var/lib/rafita-backup
if [ "${1:-}" != "--notify-test" ]; then
    exec >>"$LOG" 2>&1
fi
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

notify() {
    local text="$1"
    [ -f "$ENV_FILE" ] || return 0
    local token admin resp
    # ADMIN_IDS admite CSV ("1,2") o JSON ("[1,2]"): limpiar corchetes,
    # comillas, espacios y CR antes de quedarse con el primer id.
    token="$(grep -E '^TELEGRAM_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '\r\n ' || true)"
    admin="$(grep -E '^ADMIN_IDS=' "$ENV_FILE" | head -1 | cut -d= -f2- \
        | tr -d '\r\n "[]' | cut -d, -f1 || true)"
    [ -n "$token" ] && [ -n "$admin" ] || return 0
    resp="$(curl -s -m 20 \
        "https://api.telegram.org/bot${token}/sendMessage" \
        --data-urlencode "chat_id=${admin}" \
        --data-urlencode "text=${text}" || true)"
    case "$resp" in
        *'"ok":true'*) ;;
        *) log "WARN: notificacion Telegram fallida: $(printf '%s' "$resp" | head -c 200)" ;;
    esac
}

if [ "${1:-}" = "--notify-test" ]; then
    notify "🔔 Prueba de notificacion del backup del homelab (--notify-test)"
    echo "notify-test ejecutado (revisa Telegram)"
    exit 0
fi

trap 'notify "❌ Backup del homelab FALLIDO. Revisa /var/log/rafita-backup.log en el HP."' ERR

exec 9>"$LOCK"
if ! flock -n 9; then
    log "Otro backup en curso; se omite esta ejecucion."
    exit 0
fi

log "=== Inicio backup ==="

# --- 0. USB presente? -------------------------------------------------------
if ! lsblk -o LABEL | grep -qx "$USB_LABEL"; then
    log "USB '$USB_LABEL' no presente; backup omitido."
    exit 0
fi
if ! mountpoint -q "$USB_MOUNT"; then
    systemctl start mnt-rafael.mount 2>/dev/null || mount "$USB_MOUNT" 2>/dev/null || true
fi
if ! mountpoint -q "$USB_MOUNT"; then
    log "USB presente pero no se pudo montar; backup omitido."
    notify "⚠️ Backup omitido: el USB $USB_LABEL está conectado pero no se pudo montar."
    exit 1
fi
log "USB montado en $USB_MOUNT."

export RESTIC_REPOSITORY="$RESTIC_REPO"
export RESTIC_PASSWORD_FILE
export XDG_CACHE_HOME="/var/cache/restic"
mkdir -p "$XDG_CACHE_HOME"
if ! restic snapshots >/dev/null 2>&1; then
    log "Repositorio restic no inicializado; ejecuta deploy/hp/backup/install.sh"
    exit 1
fi

NC_MAINTENANCE=0
PORTAINER_STOPPED=0
USB_LOG_DIR="$USB_MOUNT/Servidor/server-nodochicohp/logs"
USB_LOG="$USB_LOG_DIR/backup-$(date +%Y%m%d-%H%M%S).log"
mkdir -p "$USB_LOG_DIR"

copy_log_to_usb() {
    cp -f "$LOG" "$USB_LOG" 2>/dev/null || true
    ls -1t "$USB_LOG_DIR"/backup-*.log 2>/dev/null | tail -n +31 | xargs -r rm -f
}

cleanup() {
    if [ "$NC_MAINTENANCE" = "1" ]; then
        docker exec -u www-data nextcloud_app php occ maintenance:mode --off >/dev/null 2>&1 || true
        log "Nextcloud: modo mantenimiento desactivado."
    fi
    if [ "$PORTAINER_STOPPED" = "1" ]; then
        docker start portainer >/dev/null 2>&1 || true
        log "Portainer reiniciado."
    fi
    copy_log_to_usb
}
trap cleanup EXIT

restic unlock >/dev/null 2>&1 || true

rm -rf "$STAGE"
mkdir -p "$STAGE"

# --- 1. Dumps de Postgres (en vivo) -----------------------------------------
log "Postgres: dump BuenaTierra..."
docker exec buenatierra_db pg_dump -U buenatierra_admin -d buenatierra -Fc \
    > "$STAGE/buenatierra.dump"
log "Postgres: dump Nextcloud..."
docker exec nextcloud_db pg_dump -U nextcloud -d nextcloud -Fc \
    > "$STAGE/nextcloud.dump"

# --- 2. SQLite (Rafita y NPM) con la API .backup ----------------------------
log "SQLite: rafita.db..."
docker exec -i rafita-agent-core python - <<'PY'
import sqlite3
src = sqlite3.connect("/data/db/rafita.db")
dst = sqlite3.connect("/tmp/rafita.db.snapshot")
with dst:
    src.backup(dst)
src.close(); dst.close()
print("rafita.db snapshot OK")
PY
docker cp rafita-agent-core:/tmp/rafita.db.snapshot "$STAGE/rafita.db" >/dev/null
docker exec rafita-agent-core rm -f /tmp/rafita.db.snapshot >/dev/null 2>&1 || true

log "SQLite: nginx-proxy-manager..."
docker run --rm -i -u 0 \
    -v "$HOME_DIR/npm/data:/src:ro" \
    -v "$STAGE:/dst" \
    rafita-rafita-agent-core python - <<'PY'
import sqlite3, glob
for db in glob.glob("/src/*.sqlite"):
    name = db.rsplit("/", 1)[-1]
    src = sqlite3.connect(db)
    dst = sqlite3.connect("/dst/npm-" + name)
    with dst:
        src.backup(dst)
    src.close(); dst.close()
    print("snapshot:", name)
PY

# --- 3. Portainer (BoltDB) con parada breve ---------------------------------
log "Portainer: parada breve para copia consistente..."
docker stop portainer >/dev/null
PORTAINER_STOPPED=1
tar czf "$STAGE/portainer_data.tgz" -C /var/lib/docker/volumes/portainer_data _data
docker start portainer >/dev/null
PORTAINER_STOPPED=0

# --- 4. Volumenes pequenos de BuenaTierra -----------------------------------
log "Volumenes BuenaTierra..."
for vol in buenatierra_api_logs buenatierra_api_reports buenatierra_api_uploads buenatierra_frontend_dist; do
    tar czf "$STAGE/${vol}.tgz" -C "/var/lib/docker/volumes/$vol" _data 2>/dev/null || true
done

# --- 5. Snapshot de configuracion del Dell ----------------------------------
log "Snapshot de configuracion del Dell..."
if scp -q -o BatchMode=yes -o ConnectTimeout=10 \
    -i /home/server/.ssh/id_ed25519 \
    -o UserKnownHostsFile=/home/server/.ssh/known_hosts \
    -o StrictHostKeyChecking=accept-new \
    "$DELL_HOST:/home/server/backups/dell-config-latest.tar.gz" "$DELL_SNAPSHOT" 2>/dev/null; then
    log "  snapshot del Dell descargado."
else
    log "  WARN: no se pudo descargar el snapshot del Dell (se continua)."
fi

# --- 6. Nextcloud: modo mantenimiento y restic ------------------------------
log "Nextcloud: activando modo mantenimiento..."
if docker exec -u www-data nextcloud_app php occ maintenance:mode --on >/dev/null 2>&1; then
    NC_MAINTENANCE=1
else
    log "  WARN: no se pudo activar el modo mantenimiento; se copia igualmente."
fi

log "restic backup..."
restic backup \
    --tag auto \
    --exclude-file="$REPO_DIR/deploy/hp/backup/restic-excludes.txt" \
    "$STAGE" \
    "$REPO_DIR" \
    "$HOME_DIR/proyectos/BuenaTierra" \
    "$HOME_DIR/npm" \
    "$HOME_DIR/wireguard" \
    "$HOME_DIR/glances" \
    /opt/homelab/nextcloud/app \
    /opt/homelab/nextcloud/docker-compose.yml \
    /data/compose/4 \
    /etc/netplan /etc/ufw /etc/docker/daemon.json /etc/fstab

log "restic forget/prune (7 diarios, 4 semanales, 6 mensuales)..."
restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune

log "restic check..."
restic check

SNAP="$(restic snapshots --latest 1 --json 2>/dev/null \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d[-1]["short_id"] if d else "?")' \
    || echo '?')"
SIZE="$(restic stats --mode raw-data 2>/dev/null | grep -i 'Total Size' || true)"
log "=== Backup completado: snapshot $SNAP ==="
notify "✅ Backup diario completado en el USB RAFAEL (snapshot $SNAP). $SIZE"
