#!/usr/bin/env bash
# Renombrado seguro del usuario rafa -> server en el nodo HP (tarea 3.6).
#
# Ejecutar como root y DESATENDIDO (via systemd-run): el script espera a que
# no queden procesos del usuario rafa (sesiones SSH cerradas) antes de tocar
# nada. Despues:
#   1. usermod -l/-m y groupmod (mismo UID/GID: permisos y docker intactos).
#   2. Crea el sudo temporal para el nuevo usuario server (se revierte al
#      terminar el despliegue de la tarea).
#   3. Recrea los proyectos con bind mounts dentro del home (rafita,
#      BuenaTierra, npm, wireguard, glances) para que las rutas nuevas sean
#      las activas. Nextcloud (/opt) y AdGuard (/data) no se ven afectados.
set -euo pipefail

LOG=/root/rename-user.log
exec >>"$LOG" 2>&1
echo "=== $(date -Is) inicio renombrado rafa -> server ==="

# El agente Rafita corre dentro de Docker con UID 1000 (el usuario `rafita` de
# la imagen), asi que `pgrep -u rafa` nunca queda vacio por si solo: se para el
# contenedor (se recrea al final), se terminan las sesiones del usuario y se
# limpian restos antes del renombrado.
docker stop rafita-agent-core >/dev/null 2>&1 || true
loginctl terminate-user rafa >/dev/null 2>&1 || true
systemctl stop "user@$(id -u rafa).service" >/dev/null 2>&1 || true
sleep 5
pkill -9 -u rafa >/dev/null 2>&1 || true
sleep 2
if pgrep -u rafa >/dev/null 2>&1; then
    echo "ABORTADO: siguen existiendo procesos del usuario rafa:"
    ps -u rafa -o pid,cmd || true
    exit 1
fi

usermod -l server -d /home/server -m rafa
if ! getent group server >/dev/null 2>&1; then
    groupmod -n server rafa
fi
chown -R server:server /home/server

TMP_SUDO="$(mktemp)"
echo "server ALL=(ALL) NOPASSWD:ALL" > "$TMP_SUDO"
visudo -cf "$TMP_SUDO"
chmod 440 "$TMP_SUDO"
mv "$TMP_SUDO" /etc/sudoers.d/rafita-deploy

recreate() {
    local dir="$1"
    shift
    if [ -f "$dir/docker-compose.yml" ]; then
        if ( cd "$dir" && docker compose "$@" up -d --force-recreate >/dev/null 2>&1 ); then
            echo "recreado: $dir"
        else
            echo "ERROR recreando: $dir"
        fi
    fi
}
recreate /home/server/proyectos/rafita -f docker-compose.yml -f deploy/hp/docker-compose.hp.yml
recreate /home/server/proyectos/BuenaTierra
recreate /home/server/npm
recreate /home/server/wireguard
recreate /home/server/glances

echo "=== $(date -Is) fin renombrado ==="
