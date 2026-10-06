# ADR-006: Disparo del backup — fichero-trigger + systemd path unit (además del timer 03:30)

**Estado**: Aceptada
**Fecha**: 2026-10-06
**Decisión**: La copia completa bajo demanda se pide escribiendo `data/backup.trigger`; el host la consume con `rafita-backup-now.path`. El timer diario 03:30 se mantiene como disparo programado.

---

## Contexto

El backup completo del homelab (repositorio **restic** cifrado hacia el USB,
más las copias de servicios vecinos) se ejecuta en el **host**, no en el
contenedor del agente: necesita privilegios, montar el USB, ejecutar
`restic`, `pg_dump`, `docker` y avisar por Telegram.

El agente, en cambio, corre **aislado en su contenedor**: no tiene `docker.sock`,
ni SSH, ni credenciales del host. La interfaz disponible con el host es el
bind mount del repositorio (`/data` ↔ `data/`).

Hasta ahora la única vía era el **timer programado**. El usuario necesita además
pedir una copia **en el momento** desde Telegram (`/backup`), desde la web o
diciéndole «hazme un backup ahora» (tool `run_backup`).

Verificable en: `agent/src/utils/backup.py`, `deploy/hp/backup/`
(`rafita-backup-now.path`, `rafita-backup.service`, `rafita-backup.timer`,
`backup.sh`, `README.md`) y `agent/tests/test_backup_ondemand.py`.

## Alternativas consideradas

### Opción elegida: fichero-trigger + unidad systemd `path`

- El agente escribe `data/backup.trigger` (JSON con marca de tiempo y origen:
  `telegram`, `web` o `tool`).
- La unidad `rafita-backup-now.path` (`PathExists` sobre ese fichero) arranca
  `rafita-backup.service`.
- El servicio **borra el trigger al empezar** (`ExecStartPre`), de modo que la
  siguiente petición vuelve a disparar; `flock` en `backup.sh` evita dobles
  ejecuciones.
- Deduplicación en el agente (no reenvía si hace menos de 5 min) y detección
  de petición estancada (>10 min → «¿está instalada la unidad
  `rafita-backup-now.path`?»).
- Estado devuelto al usuario por `data/backup-status.json`, que el propio
  `backup.sh` escribe al terminar (mismo canal en dirección contraria).

**Ventaja**: el contenedor no necesita privilegios ni canal de control nuevo;
sólo toca su propio volumen de datos.

### `docker.sock` montado en el agente

- Permitiría `docker exec` / `systemctl` desde el código del agente.
- **Se descarta**: `docker.sock` equivale a root en el host. Cualquier fallo o
  inyección en el proceso (que recibe texto de documentos y de la web) pasaría
  a control total de la máquina.

### SSH desde el contenedor hacia el host

- **Se descarta**: expondría una clave privada dentro del contenedor (y en su
  imagen/volumen), ampliando el impacto de cualquier lectura de ficheros.

### Petición HTTP a un servidor de control en el host

- **Se descarta**: hay que instalar, autenticar y mantener un servicio nuevo
  escuchando en el host; el mecanismo de fichero ya existe por el bind mount y
  es auditable (`ls data/backup.trigger`).

### Sólo timer programado (sin bajo demanda)

- **Se descarta como única vía**: no cubre «backup ahora antes de un cambio
  importante». El timer **se mantiene** como garantía diaria.

## Decisión

Mantener **dos disparos** con el mismo `rafita-backup.service`:

| Disparo | Mecanismo | Cuándo |
|---|---|---|
| Programado | `rafita-backup.timer` — `OnCalendar=*-*-* 03:30:00`, `Persistent=true`, `RandomizedDelaySec=300` | Diario, con arranque atrasado si el host estaba apagado |
| Bajo demanda | `rafita-backup-now.path` — `PathExists=data/backup.trigger` → `rafita-backup.service` | `/backup` en Telegram, `/backup` en la web, tool `run_backup`, o escritura directa del fichero |

Instalación (una vez, con privilegios): copiar la unidad `path` a
`/etc/systemd/system/` y `systemctl enable --now rafita-backup-now.path`.

## Consecuencias

- **Positivas**:
  - Cero privilegios nuevos para el contenedor (sin `docker.sock`, sin SSH).
  - Los tres canales (Telegram, web, voz/herramienta) usan el mismo código:
    `trigger_system_backup(source)`.
  - El host conserva el control: deduplicación, `flock`, avisos por Telegram
    si el USB falta o si algo falla, y timeout de ejecución (7200 s).
  - Cobertura de diagnóstico: si el trigger envejece sin ejecutarse, el
    estado del backup lo señala explícitamente.
- **Negativas / límites conocidos**:
  - Requiere una **instalación manual** con sudo de la unidad `path`; sin ella
    el bajo demanda falla en silencio (mitigado por el aviso de estado).
  - Cualquier escritura de ficheros en `data/` podría disparar el servicio: es
    un vector de coste de recursos (no de confidencialidad) anotado en el
    modelo de amenazas.
  - El orden host↔contenedor depende de la consistencia del bind mount; un
    fichero perdido se traduce en una petición no atendida, nunca en datos
    corruptos.
  - El disparo y el estado viajan en claro dentro de `data/` (no contienen
    secretos: origen, marca de tiempo y resumen del resultado).

---

*ADR revisable si el backup pasa a ejecutarse dentro de un contenedor con
privilegios mínimos o si aparece un canal de control dedicado.*
