# Guía de re-auditoría independiente

**Fecha:** 2026-09-26
**Para qué:** una sesión externa, **sin acceso a la narrativa de `plan.md`**,
puede reproducir los puntos críticos usando solo el repositorio y los dos
nodos reales. Cada bloque incluye comandos exactos y el resultado esperado.

> Contexto mínimo: el despliegue es LLM en el Dell (`100.83.40.103`, Ollama) y
> agente en el HP (`100.121.77.29`, contenedor `rafita-agent-core`), unidos por
> Tailscale. El acceso estable entre nodos es la IP de Tailscale.

---

## 1. Tool-calling: reproducir el 46/46

**Por qué se duda:** el salto 29/46 (Fase 1, GPU) → 46/46 (Fase 3) es grande y
merece re-verificación con el mismo instrumento.

**Instrumento (sin cambios desde la Fase 1):**

```bash
sha256sum agent/scripts/tool_calling_eval.py
# Esperado: fd2f1cda0b8d197eede04c92a7ef0184980501994dfd9e16ccb7b1eb8e9d1a74
git log -1 --format='%H %ad %s' -- agent/scripts/tool_calling_eval.py
# Esperado: 6e83088a9564b55ae03c21bf769f2d5fb9fe920a ... (tarea 1.7)
```

Dataset: 21 herramientas de producción + 2 controles negativos, 2 intentos
por caso (23 filas en la tabla). El propio script imprime
`tools=21 tools_json_chars=14161`.

**Ejecución (en el HP):**

```bash
docker exec rafita-agent-core python /workspace/agent/scripts/tool_calling_eval.py --attempts 2
# Esperado: tabla con 2/2 en las 23 filas y "OVERALL: 46/46 (100%) failure_modes={}"
```

**Qué cambió respecto a 1.7 (explicación precisa, no vaga):**

- `gemma4:12b` expone capacidad `thinking` con `default=true` (verificable:
  `ollama show gemma4:12b`).
- Por el endpoint que usa la app (`/v1/chat/completions` de Ollama):
  `think:false` **se ignora**; `reasoning_effort:"none"` **sí lo desactiva**
  (verificado con `curl`; evidencia en `plan.md` 3.1/3.2, commit `5698227`).
- Sin ese ajuste, el razonamiento consumía el presupuesto de tokens antes de
  emitir la herramienta: de ahí los 17 `no_tool` de 1.7.

**Baseline honesto:** la medición de 1.7 (29/46) se hizo en GPU **sin** ese
ajuste; el 46/46 es en CPU (Dell) **con** el ajuste. No se está comparando
hardware, se está midiendo el efecto del ajuste.

---

## 2. Nextcloud: estado real de la contraseña

**Por qué se duda:** el compose de producción tenía un valor de ejemplo
distinto del real; hay precedente de secretos filtrados en este proyecto.

**Comprobaciones (todas ejecutables sin imprimir el secreto):**

```bash
# 1. La contraseña ya no está en el compose
grep POSTGRES_PASSWORD /opt/homelab/nextcloud/docker-compose.yml
# Esperado: POSTGRES_PASSWORD=${POSTGRES_PASSWORD} (dos servicios)

# 2. Está en un .env con permisos 600 y se incluye en el backup
ls -la /opt/homelab/nextcloud/.env
# Esperado: -rw------- server server

# 3. Coherencia .env vs contenedor (comparar hashes, no valores)
cd /opt/homelab/nextcloud
grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2- | tr -d '\n' | sha256sum
docker exec nextcloud_app printenv POSTGRES_PASSWORD | tr -d '\n' | sha256sum
# Esperado: el mismo hash en ambos

# 4. La app funciona con la contraseña nueva
docker exec nextcloud_app php occ status | head -3
# Esperado: installed: true, version 34.x

# 5. Barrido de filtraciones (resultados esperados: vacío / 0)
git log --all -S 'ClaveSeguraNextcloud' --oneline      # vacío
grep -c 'ClaveSeguraNextcloud' ~/.bash_history          # 0
docker logs nextcloud_db 2>&1 | grep -c 'ClaveSeguraNextcloud'   # 0
grep -rc 'ClaveSeguraNextcloud' ~/proyectos/rafita/data/logs/*.log # 0
```

**Estado:** contraseña **rotada** el 2026-09-26 (ALTER USER + `.env` + app
recreada). El valor antiguo ya no es válido.

---

## 3. Integridad tras el renombrado de usuario (`rafa` → `server`)

**Por qué se duda:** el renombrado tocó la máquina con 11 servicios de
producción; "recreado y verificado" es demasiado genérico.

**Comprobaciones de rutas (esperado: 0 coincidencias):**

```bash
grep -rl '/home/rafa' ~/proyectos/*/docker-compose*.yml ~/npm ~/wireguard ~/glances 2>/dev/null | wc -l
for c in $(docker ps --format '{{.Names}}'); do docker inspect $c --format '{{range .Mounts}}{{.Source}} {{end}}'; done | tr ' ' '\n' | grep -c '/home/rafa'
grep -rl '/home/rafa' /etc/cron.d/ 2>/dev/null | wc -l
grep -rl '/home/rafa' /etc/systemd/system/ 2>/dev/null | wc -l
ls /etc/sudoers.d/    # solo README (sin entradas rafa)
```

**Comprobaciones funcionales (resultado observado el 2026-09-26):**

```
nginx-proxy-manager (81):  200
portainer (9443):          200
glances (61208):           200
wg-easy (51821):           200
nextcloud:                 occ status -> installed: true (version 34.0.3.2)
buenatierra_nginx:         HTTP OK interno
adguardhome:               resolución DNS real OK (nslookup)
rafita-agent-core:         healthy, /ready ready
```

12 contenedores arriba (11 de producción + el agente).

---

## 4. Estado general (repetir el proceso de la auditoría original)

```bash
# Tests y estáticos (contenedor de auditoría)
docker run --rm -e TELEGRAM_TOKEN=dummy -e CI=true -v "$PWD:/workspace" -w /workspace \
  rafita-audit sh -lc "export PYTHONPATH=/tmp/auditdev:/workspace/agent/src; \
    python -m pytest agent/tests -q"

# Readiness y versión
curl -s http://127.0.0.1:8010/ready
curl -s http://127.0.0.1:8010/health

# Backup (requiere USB montado y sudo en el HP)
sudo bash -c 'export RESTIC_REPOSITORY=/mnt/rafael/Servidor/server-nodochicohp/restic \
  RESTIC_PASSWORD_FILE=/root/.restic-password; restic snapshots; restic check'
```

---

## 5. Cómo verificar la evidencia de `plan.md`

- Cada tarea de las Fases 0–4 tiene, debajo, un bloque
  `**Evidencia [fecha] [commit]**` con el comando exacto y su salida real.
- Los commits citados existen en el repositorio:
  `git log --oneline --all | grep <hash>`.
- Si una tarea no tuviera evidencia verificable, la regla del propio plan es
  que no se marque `[x]`; cualquier incumplimiento debe reportarse como
  hallazgo.

**Recomendación de cierre:** ejecutar esta guía en una sesión fresca y
contrastar los tres puntos críticos antes de dar el proyecto por cerrado.
