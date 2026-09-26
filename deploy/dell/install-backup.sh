#!/usr/bin/env bash
# Instalador del snapshot de configuracion del Dell (tarea 3.6). Como root.
set -euo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"

install -m 755 "$BASE/dell-config-snapshot.sh" /usr/local/bin/dell-config-snapshot.sh
cat > /etc/cron.d/dell-config-snapshot <<'EOF'
# Snapshot diario de configuracion del Dell (tarea 3.6)
0 3 * * * root /usr/local/bin/dell-config-snapshot.sh >/var/log/dell-config-snapshot.log 2>&1
EOF
chmod 644 /etc/cron.d/dell-config-snapshot

/usr/local/bin/dell-config-snapshot.sh
echo "Instalado. Snapshot diario a las 03:00 en /home/server/backups/"
