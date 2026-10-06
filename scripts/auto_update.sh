#!/usr/bin/env bash
# Autoactualización automática del nodo HP (runbook §7 automatizado).
#
# Multi-proyecto: actualiza Rafita (flujo completo con snapshot y rollback)
# y, según `scripts/auto-update/projects.conf`, otros repos del nodo
# (p. ej. BuenaTierra). Rafita usa el remoto público por HTTPS anónimo;
# los repos PRIVADOS necesitan credencial (recomendado: deploy key SSH de
# solo lectura). Flujo Rafita:
#   fetch → ¿commits nuevos? → snapshot (patch de tracked + untracked +
#   archivos solo-HP que el nuevo commit no trackea, p. ej. deploy/) →
#   git reset --hard → restaurar solo-HP → up -d --build con
#   measure-readiness → health gate de /ready → si no vuelve en 3 min:
#   ROLLBACK completo (código + árbol previo + recreate).
# Proyectos genéricos: fetch → gate de CI verde → si hay commits nuevos →
# si hay cambios locales tracked: stash → reset --hard → reaplicar stash
# (pop) → hook opcional. Si el pop choca, árbol limpio en el nuevo commit y
# el stash se conserva (aviso por Telegram). Los untracked no se tocan.
#
# Gate de CI: cada repo sólo se despliega si su último run del workflow
# ci.yml en la rama está en success (red de seguridad del timer diario de
# las 05:30; el CD normal ya sólo se dispara con workflow_run en success).
#
# Uso:  bash scripts/auto_update.sh [--force]
#   --force  actualiza Rafita aunque HEAD ya esté en el remoto (pruebas)
#   FORCE_DEPLOY=1  salta el gate de CI (sólo despliegues manuales)
#
# Estado: ~/.local/state/rafita/auto-update/  (snapshots + auto-update.log)
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="${HOME}/.local/state/rafita/auto-update"
LOG="$STATE/auto-update.log"
REMOTE_URL="${RAFITA_UPDATE_URL:-https://github.com/rufae/Rafita.git}"
READY_URL="http://127.0.0.1:8010/ready"
READY_TIMEOUT="${RAFITA_READY_TIMEOUT:-240}" # segundos esperando /ready 200
CONF="$REPO/scripts/auto-update/projects.conf"
mkdir -p "$STATE"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >>"$LOG"; }

# Un solo proceso a la vez (timer diario + runner de GitHub Actions +
# ejecuciones manuales): si otro está corriendo, esta instancia sale.
LOCK="$STATE/auto-update.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] otra ejecución en curso (lock); se omite" >>"$LOG"
    exit 0
fi

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

# ------------------------------------------------------------ gate CI ------
# slug_github: "git@github.com:rufae/X.git" | "https://github.com/rufae/X.git"
# → "rufae/X" (si el remoto no es de GitHub, se devuelve tal cual).
slug_github() {
    printf '%s' "$1" | sed -E 's#^(git@|https://)github\.com[:/]##; s#\.git$##'
}

# ci_verde <owner/repo> <rama>: 0 sólo si el ÚLTIMO run del workflow ci.yml
# de esa rama terminó en success. Un run fallido o en curso → no se
# despliega (fail-closed). FORCE_DEPLOY=1 lo salta (workflow_dispatch).
# Si la API no se puede consultar (sin jq, sin red, repo no accesible),
# se continúa con aviso: el fetch de git fallaría igualmente en el peor
# caso y así no se bloquea un despliegue legítimo para siempre.
ci_verde() {
    local repo="$1" branch="$2" runs concl token=""
    [ -n "${FORCE_DEPLOY:-}" ] && return 0
    if ! command -v jq >/dev/null 2>&1; then
        log "AVISO: jq ausente; no se verifica CI de $repo@$branch (se continúa)"
        return 0
    fi
    # Token del .git-credentials si existe (repos privados, p. ej. BuenaTierra).
    if [ -f "$HOME/.git-credentials" ]; then
        token="$(grep -oE 'https://[^:]+:[^@]+@github\.com' "$HOME/.git-credentials" 2>/dev/null \
            | head -1 | sed -E 's#^https://[^:]+:##; s#@github\.com$##')" || true
    fi
    local args=(-s -f -m 15 -H "Accept: application/vnd.github+json")
    [ -n "$token" ] && args+=(-H "Authorization: Bearer $token")
    if ! runs="$(curl "${args[@]}" \
        "https://api.github.com/repos/$repo/actions/workflows/ci.yml/runs?branch=$branch&per_page=1" \
        2>>"$LOG")"; then
        log "AVISO: API de GitHub no consultable; no se verifica CI de $repo@$branch (se continúa)"
        return 0
    fi
    if [ "$(printf '%s' "$runs" | jq '.workflow_runs | length' 2>>"$LOG")" = "0" ]; then
        log "AVISO: sin runs de CI para $repo@$branch; se continúa"
        return 0
    fi
    concl="$(printf '%s' "$runs" | jq -r '.workflow_runs[0].conclusion // ""' 2>>"$LOG")"
    [ "$concl" = "success" ] && return 0
    if [ -z "$concl" ]; then
        log "gate CI: run de $repo@$branch aún en curso — despliegue omitido"
    else
        log "gate CI: $repo@$branch en '$concl' — despliegue omitido"
    fi
    return 1
}

# ---------------------------------------------------------------- Rafita ---
# Flujo completo: snapshot, build, health gate y rollback (runbook §7).
actualizar_rafita() {
    local OLD_SHA NEW_SHA SNAP
    cd "$REPO" || return 1
    if ! OLD_SHA="$(git rev-parse HEAD 2>/dev/null)"; then
        log "ERROR: no es un repo git: $REPO"
        return 1
    fi

    if ! git fetch --quiet "$REMOTE_URL" master 2>>"$LOG"; then
        log "rafita: fetch sin éxito (¿sin red?); nada que actualizar"
        return 0
    fi
    NEW_SHA="$(git rev-parse FETCH_HEAD)"

    if [ "$NEW_SHA" = "$OLD_SHA" ] && [ "${1:-}" != "--force" ]; then
        log "rafita: sin cambios: ya en $OLD_SHA"
        return 0
    fi

    # Gate: no se despliega si el CI de master no está en success.
    if ! ci_verde "rufae/Rafita" "master"; then
        log "rafita: gate CI — se mantiene $OLD_SHA (el remoto está en $NEW_SHA)"
        return 0
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

    log "rafita: actualizando $OLD_SHA -> $NEW_SHA (solo-HP: $(wc -l <"$SNAP/solo-hp.txt" | tr -d ' '))"
    if ! git reset --hard "$NEW_SHA" >>"$LOG" 2>&1; then
        log "ERROR: reset a $NEW_SHA falló; se mantiene $OLD_SHA"
        return 1
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
        log "rafita: OK, actualizado y ready en $NEW_SHA"
        ls -1dt "$STATE"/snap-* 2>/dev/null | tail -n +4 | xargs -r rm -rf
        return 0
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
        log "rafita: rollback OK: sistema de nuevo en $OLD_SHA"
        notify "⚠️ Rafita auto-update: la actualización a $(echo "$NEW_SHA" | cut -c1-8) falló y se revirtió. Log: ~/.local/state/rafita/auto-update/auto-update.log"
    else
        log "CRÍTICO: rollback tampoco dejó /ready 200 — intervención manual"
        notify "🔴 Rafita auto-update: la actualización Y el rollback fallaron. Intervención manual necesaria."
    fi
    return 1
}

# ------------------------------------------------------- otros proyectos ---
# Un proyecto genérico: fetch → commits nuevos → (si hay cambios locales
# tracked: stash) → reset --hard → reaplicar el stash → hook opcional
# (p. ej. "docker compose up -d --build"). Los cambios locales tracked no
# se pierden nunca: se guardan en stash y se reaplican tras el pull; si
# reaplicar choca, se deja el árbol limpio en el nuevo commit y el stash
# se conserva para resolverlo a mano (aviso por Telegram). Los archivos
# untracked no se tocan (reset --hard solo falla si el commit entrante
# añade un nombre que ya existe sin trackear; eso se trata como error).
# Los fallos notifican; lo normal (sin cambios) solo se registra en el log.
actualizar_generico() {
    local nom="$1" dir="$2" branch="$3" remote="$4" hook="$5"
    local OLD_SHA NEW_SHA STASHED=0 slug
    if [ ! -d "$dir/.git" ]; then
        log "$nom: no es un repo git ($dir); se omite"
        return 0
    fi
    if ! git -C "$dir" fetch --quiet "$remote" "$branch" 2>>"$LOG"; then
        log "$nom: fetch sin éxito de $remote (¿repo privado sin credencial o sin red?)"
        notify "⚠️ Auto-update $nom: no se pudo consultar $remote (¿repo privado sin credencial en el HP?). Se omite."
        return 0
    fi
    NEW_SHA="$(git -C "$dir" rev-parse FETCH_HEAD)"
    OLD_SHA="$(git -C "$dir" rev-parse HEAD)"
    if [ "$NEW_SHA" = "$OLD_SHA" ]; then
        log "$nom: sin cambios: ya en $OLD_SHA"
        return 0
    fi
    # Gate: no se despliega si el CI de su rama no está en success
    # (sólo aplica a remotos de GitHub; el resto se omite con nota arriba).
    slug="$(slug_github "$remote")"
    if [ "$slug" != "$remote" ] && ! ci_verde "$slug" "$branch"; then
        log "$nom: gate CI — se mantiene $OLD_SHA (el remoto está en $NEW_SHA)"
        return 0
    fi
    if [ -n "$(git -C "$dir" status --porcelain 2>/dev/null | grep -v '^??')" ]; then
        # Hay cambios tracked: stash previo (los untracked quedan donde están).
        if ! git -C "$dir" stash push -m "auto-update $nom $(date +%Y%m%d-%H%M%S)" >>"$LOG" 2>&1; then
            log "$nom: hay cambios locales y el stash falló en $dir; se salta $OLD_SHA..$NEW_SHA"
            notify "⚠️ Auto-update $nom: cambios locales en $dir y el stash falló; no se actualiza a $(echo "$NEW_SHA" | cut -c1-8)."
            return 0
        fi
        STASHED=1
        log "$nom: cambios locales en stash (previo a $OLD_SHA..$NEW_SHA)"
    fi
    if ! git -C "$dir" reset --hard "$NEW_SHA" >>"$LOG" 2>&1; then
        # Devolver los cambios stashados y volver al estado previo.
        if [ "$STASHED" = 1 ]; then
            git -C "$dir" stash pop >>"$LOG" 2>&1 || true
        fi
        log "$nom: ERROR reset a $NEW_SHA falló; se mantiene $OLD_SHA"
        notify "🔴 Auto-update $nom: git reset falló; se mantiene $(echo "$OLD_SHA" | cut -c1-8)."
        return 1
    fi
    if [ "$STASHED" = 1 ]; then
        if git -C "$dir" stash pop >>"$LOG" 2>&1; then
            log "$nom: cambios locales reaplicados tras el pull"
        else
            # Conflicto al reaplicar: árbol limpio en NEW, stash intacto.
            git -C "$dir" reset --hard "$NEW_SHA" >>"$LOG" 2>&1 || true
            log "$nom: AVISO: cambios locales no reaplicables (conflicto); se conservan en stash de $dir (git stash list)"
            notify "⚠️ Auto-update $nom: actualizado a $(echo "$NEW_SHA" | cut -c1-8), pero los cambios locales chocaron al reaplicarse; están en 'git stash list' de $dir — resuelve y haz pop a mano."
            return 1
        fi
    fi
    if [ -n "$hook" ]; then
        if ! (cd "$dir" && eval "$hook") >>"$LOG" 2>&1; then
            log "$nom: el hook posterior devolvió error ($hook)"
            notify "🔴 Auto-update $nom: código en $(echo "$NEW_SHA" | cut -c1-8) pero el hook falló: $hook"
            return 1
        fi
    fi
    log "$nom: actualizado $OLD_SHA -> $NEW_SHA"
    return 0
}

# ------------------------------------------------------------------ main ---
RC=0
cd "$REPO" || exit 1
actualizar_rafita "${1:-}" || RC=1

if [ -f "$CONF" ]; then
    while IFS='|' read -r nom dir branch remote activado hook || [ -n "${nom:-}" ]; do
        case "$nom" in ''|\#*) continue ;; esac
        if [ "$activado" != "si" ]; then
            log "$nom: desactivado en projects.conf (activado=$activado)"
            continue
        fi
        dir="${dir/#\~/$HOME}"
        actualizar_generico "$nom" "$dir" "$branch" "$remote" "${hook:-}" || RC=1
    done <"$CONF"
else
    log "AVISO: no existe $CONF; solo se actualiza Rafita"
fi

# Estado de contenedores para el check "docker" del agente (best-effort;
# también lo genera el cron de usuario: deploy/hp/infra/docker_status.sh).
if [ -f "$REPO/deploy/hp/infra/docker_status.sh" ]; then
    bash "$REPO/deploy/hp/infra/docker_status.sh" >>"$LOG" 2>&1 \
        || log "AVISO: docker_status.sh devolvió error"
fi

exit "$RC"
