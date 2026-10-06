# ADR-005: Persistencia — SQLite (aiosqlite) vs PostgreSQL vs ficheros JSON

**Estado**: Aceptada
**Fecha**: 2026-10-06
**Decisión**: Mantener SQLite con `aiosqlite`, WAL y una única conexión por proceso

---

## Contexto

Rafita AVP necesita persistir la memoria operativa del asistente: historial de
chat, eventos, tareas, alertas, finanzas, contactos/conocimiento personal,
usuarios web, credenciales cifradas, reuniones, suscripciones push y el
registro de ejecuciones de las automatizaciones.

Restricciones del proyecto:

- **Un solo nodo y un solo usuario** (ADR-004): no hay réplicas ni lectores
  concurrentes de otras máquinas.
- **Un solo proceso Python** (`rafita-agent-core`) con varios workers de
  asyncio en el mismo event loop (`main.py`): `ProactiveWorker`,
  `BriefingWorker`, `InfraWorker`, `BrainMaintainer` y `VaultIndexer`, además
  de las peticiones entrantes del bot, del gateway y de la voz.
- **Instalación en un contenedor con poca RAM** y backup diario al USB.
- El almacén vectorial ya es un fichero aparte (ChromaDB, ADR-001): la BD sólo
  cubre datos relacionales.

## Alternativas consideradas

### SQLite (actual, `aiosqlite`)

Verificado en `agent/src/database.py`:

- **Archivo único** (`data/db/rafita.db`): sin servidor, sin puerto, sin
  proceso que supervisar. El backup consistente es una llamada a la API
  `backup()` (usada en `deploy/hp/backup/backup.sh`) o `VACUUM INTO`
  (usada en `hourly-db.sh`), no una copia en caliente del fichero.
- **WAL activado** (`PRAGMA journal_mode=WAL`), `foreign_keys=ON` y
  `busy_timeout=5000`: las escrituras no bloquean las lecturas y el event loop
  no se atasca ante un acceso concurrente breve.
- **Una sola conexión** (`DatabaseManager._conn`) compartida por todo el
  proceso, con un `transaction()` que hace commit/rollback: encaja con un
  proceso único asyncio, donde la concurrencia real es cooperativa.
- **Migraciones idempotentes** en el arranque (`CREATE TABLE IF NOT EXISTS` +
  `PRAGMA table_info` para añadir columnas que falten).
- **Mantenimiento**: `VACUUM` periódico desde la recolección de basura del
  `ProactiveWorker` (cada 7 ejecuciones).

Costes: sin cifrado de la base completa (sólo columnas puntuales con Fernet),
esquema gestionado a mano y un único escritor.

### PostgreSQL

- Requeriría un contenedor más, credenciales, volumen y su propio backup.
- ACID completo, réplicas y concurrencia multi-cliente que **no se usan** con
  un nodo y un usuario.
- Coste de RAM (~100 MB en reposo) que compite con los modelos de Ollama.
- **Se descarta**: contradice la instalación en un contenedor único y no
  aporta nada al caso de uso real. Se reconsideraría si Rafita pasara a
  varios procesos escritores o multiusuario (mismo criterio que ADR-001).

### Ficheros JSON / SQLite embebido alternativo

- Sin consultas ni índices: obligaría a cargar todo en memoria y a reimplementar
  filtros, ordenaciones y deduplicación que hoy son SQL.
- Escrituras no atómicas ante caída del proceso.
- **Se descarta**: pierde integridad y ya existe una capa de datos
  (ChromaDB) que cubre la parte de búsqueda.

### SQLite cifrado (SQLCipher)

- Protegería la BD sin depender del cifrado de disco del host.
- Añade una compilación/binario nativo y una clave más que gestionar y respaldar.
- **Se descarta por ahora**: el alcance actual (ver `docs/SECURITY.md`) delega
  el cifrado en reposo a disco completo; el cifrado por columna con Fernet ya
  cubre las credenciales. Se reconsideraría si la instalación corre en un
  host que no sea de confianza.

## Decisión

**Mantener SQLite con `aiosqlite`**, con estas invariantes:

1. Fichero único bajo `data/db/`, servido por `DatabaseManager` (singleton).
2. WAL + claves foráneas + `busy_timeout` en la inicialización.
3. Una conexión por proceso; nunca se abren conexiones desde workers sueltos.
4. Backup siempre por la API de SQLite (`backup()` / `VACUUM INTO`), nunca
   copiando el fichero en caliente (con WAL, la copia puede quedar incompleta).
5. Migraciones aditivas e idempotentes en `_create_tables()`.

## Consecuencias

- **Positivas**: cero servicios extra, backup/restauración triviales, arranque
  inmediato y tests sin dependencias externas (mismo argumento que ADR-001 y
  ADR-002).
- **Negativas / límites conocidos**:
  - Un solo escritor: si el proyecto se divide en varios procesos, hay que
    revisar esta decisión (WAL tolera lectores concurrentes, no varios
    escritores con esta arquitectura).
  - La base **no está cifrada** en reposo; el historial de chat queda en claro
    salvo cifrado de disco. Sólo `credentials.value_enc` y
    `app_connectors.credentials_enc` van con Fernet.
  - Las migraciones son manuales: añadir columnas exige editar el esquema con
    el patrón de `PRAGMA table_info` ya existente.
  - Al respaldar hay que incluir el `.env` (clave Fernet) junto con la BD o
    las credenciales quedan irrecoverables.

---

*ADR revisable si aparece un segundo proceso escritor, si se necesita cifrado
de la base completa o si Rafita pasa a multiusuario.*
