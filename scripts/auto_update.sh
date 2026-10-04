#!/usr/bin/env bash
# Autoactualización automática de Rafita (runbook §7 automatizado).
#
# Fuente: GitHub por HTTPS anónimo (repo público). Flujo:
#   fetch → ¿commits nuevos? → snapshot (patch de tracked + untracked +
#   archivos solo-HP que el nuevo commit no trackea, p. ej. deploy/) →
#   git reset --hard → restaurar solo-HP → up -d --build con
#   measure-readiness → health gate de /ready → si no vuelve en 3 min:
#   ROLLBACK completo (código + árbol previo + recreate).
#
# Uso:  bash scripts/auto_update.sh [--force]
#   --force  actualiza aunque HEAD ya esté en el remoto (pruebas)
#
# Estado: ~/.local/state/rafita/auto-update/  (snapshots + auto-update.log)
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="${HOME}/.local/state/rafita/auto-update"
LOG="$STATE/auto-update.log"
REMOTE_URL="${RAFITA_UPDATE_URL:-https://github.com/rufae/Rafita.git}"
READY_URL="http://127.0.0.1:8010/ready"
READY_TIMEOUT="${RAFITA_READY_TIMEOUT:-240}" # segundos esperando /ready 200
mkdir -p "$STATE"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >>"$LOG"; }

wait_ready() {
    local i
    for i in $(seq 1 "$READY_TIMEOUT"); do
        curl -sf -m 2 -o /dev/null "$READY_URL" 2>/dev/null && return 0
        sleep 1
    done
    return 1
}

notify() {
    # Aviso por Telegram (best-effort, patrón de deploy/hp/backup/backup.sh).
    local text="$1" env_file="$REPO/.env" token admin
    [ -f "$env_file" ] || return 0
    token="$(grep -E '^TELEGRAM_TOKEN=' "$env_file" | head -1 | cut -d= -f2- | tr -d '\r\n ' || true)"
    admin="$(grep -E '^ADMIN_IDS=' "$env_file" | head -1 | cut -d= -f2- \
        | tr -d '\r\n "[]' | cut -d, -f1 || true)"
    [ -n "$token" ] && [ -n "$admin" ] || return 0
    curl -s -m 20 "https://api.telegram.org/bot${token}/sendMessage" \
        --data-urlencode "chat_id=${admin}" \
        --data-urlencode "text=${text}" >/dev/null 2>&1 || true
}

cd "$REPO" || exit 1
if ! OLD_SHA="$(git rev-parse HEAD 2>/dev/null)"; then
    log "ERROR: no es un repo git: $REPO"
    exit 1
fi

if ! git fetch --quiet "$REMOTE_URL" master 2>>"$LOG"; then
    log "fetch sin éxito (¿sin red?); nada que actualizar"
    exit 0
fi
NEW_SHA="$(git rev-parse FETCH_HEAD)"

if [ "$NEW_SHA" = "$OLD_SHA" ] && [ "${1:-}" != "--force" ]; then
    log "sin cambios: ya en $OLD_SHA"
    exit 0
fi

# --- snapshot previo (rollback posible) ---
SNAP="$STATE/snap-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$SNAP"
printf '%s\n' "$OLD_SHA" >"$SNAP/sha"
git diff >"$SNAP/patch.diff"
git ls-files --others --exclude-standard 2>/dev/null \
    | tar -czf "$SNAP/untracked.tgz" -T - 2>/dev/null || true
# Archivos trackeados en OLD que el nuevo commit NO trackea (deploy/,
# plan.md, scripts heredados...): reset --hard los borraría → snapshot y
# se restauran tras el reset como.untracked.
LC_ALL=C comm -23 <(LC_ALL=C git ls-files | LC_ALL=C sort) \
    <(LC_ALL=C git ls-tree -r --name-only "$NEW_SHA" | LC_ALL=C sort) \
    >"$SNAP/solo-hp.txt"
if [ -s "$SNAP/solo-hp.txt" ]; then
    tar -czf "$SNAP/solo-hp.tgz" -T "$SNAP/solo-hp.txt" 2>/dev/null || true
fi

log "actualizando $OLD_SHA -> $NEW_SHA (solo-HP: $(wc -l <"$SNAP/solo-hp.txt" | tr -d ' '))"
if ! git reset --hard "$NEW_SHA" >>"$LOG" 2>&1; then
    log "ERROR: reset a $NEW_SHA falló; se mantiene $OLD_SHA"
    exit 1
fi
# Restaurar archivos solo-HP como no-trackeados (en NEW no existen).
if [ -f "$SNAP/solo-hp.tgz" ]; then
    tar -xzf "$SNAP/solo-hp.tgz" -C "$REPO" 2>/dev/null || true
fi

# --- despliegue (runbook §7) ---
bash "$REPO/deploy/hp/measure-readiness.sh" \
    docker compose -f docker-compose.yml -f deploy/hp/docker-compose.hp.yml \
    up -d --build >>"$LOG" 2>&1 || log "AVISO: up -d --build devolvió error (se verifica igualmente)"

if wait_ready; then
    log "OK: actualizado y ready en $NEW_SHA"
    ls -1dt "$STATE"/snap-* 2>/dev/null | tail -n +4 | xargs -r rm -rf
    exit 0
fi

# --- rollback ---
log "FALLO: /ready no volvió en ${READY_TIMEOUT}s; rollback a $OLD_SHA"
git reset --hard "$OLD_SHA" >>"$LOG" 2>&1 || true
if [ -s "$SNAP/patch.diff" ]; then
    git apply "$SNAP/patch.diff" >>"$LOG" 2>&1 || log "AVISO: no se pudo reaplicar el patch previo"
fi
if [ -f "$SNAP/untracked.tgz" ]; then
    tar -xzf "$SNAP/untracked.tgz" -C "$REPO" 2>/dev/null || true
fi
docker compose -f docker-compose.yml -f deploy/hp/docker-compose.hp.yml \
    up -d --force-recreate >>"$LOG" 2>&1 || true
if wait_ready; then
    log "rollback OK: sistema de nuevo en $OLD_SHA"
    notify "⚠️ Rafita auto-update: la actualización a $(echo "$NEW_SHA" | cut -c1-8) falló y se revirtió. Log: ~/.local/state/rafita/auto-update/auto-update.log"
else
    log "CRÍTICO: rollback tampoco dejó /ready 200 — intervención manual"
    notify "🔴 Rafita auto-update: la actualización Y el rollback fallaron. Intervención manual necesaria."
fi
exit 1
