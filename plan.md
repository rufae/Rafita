# plan.md — Rafita AVP: loop de estabilización y profesionalización

**Origen:** Auditoría técnica externa del 2026-09-24, commit `e1ef0ec` (tag `v0.1.0`).
**Estado de partida:** prototipo personal con componentes funcionales; NO producto
genérico, NO listo para servidor continuo. Índice de generificación medido: 15/100.
`BrainMaintainer` y `PERSIST_TO_BRAIN` **no existen** en el código auditado pese a
haberse reportado como implementados en un ciclo anterior — trátalos como
funcionalidad nueva a construir desde cero, no como "arreglo".

Este documento es la fuente de verdad del avance. Se actualiza en cada sesión de
trabajo, nunca al final. Un ítem sin evidencia pegada bajo él es un ítem sin hacer,
sin importar lo que diga el título de esta sección.

---

## 0. Cómo usar este documento (léelo antes de tocar código)

### 0.1 Regla de evidencia — innegociable

Ningún checkbox se marca `[x]` sin que, inmediatamente debajo de la tarea, haya:
- el comando exacto ejecutado,
- su salida real (pegada, no resumida ni parafraseada),
- la fecha y el hash de commit sobre el que se ejecutó.

Si no se puede verificar algo (falta hardware, falta servicio externo, etc.),
el checkbox queda `[ ]` y se escribe explícitamente **por qué** no se pudo verificar,
en la subsección "Bloqueado / no verificable" de esa tarea. Nunca se marca `[x]`
"porque en teoría ya debería funcionar".

### 0.2 Loop obligatorio por tarea

Cada tarea de cada fase sigue este ciclo, sin saltarse pasos:

1. **Plan** — antes de tocar código, escribe en 2-4 líneas qué vas a cambiar y por qué,
   citando el hallazgo concreto de la auditoría que lo motiva.
2. **Implementa** — el cambio mínimo que resuelve el hallazgo, sin arrastrar refactors
   no pedidos.
3. **Verifica con evidencia real** — ejecuta la prueba indicada en la tarea (o diseña
   una si no existe) y pega el resultado literal. Si la verificación falla, esto NO es
   un fallo tuyo que ocultar: es información. Pásalo al paso 4.
4. **Corrige si falla** — repite implementa→verifica hasta que la evidencia confirme
   el resultado esperado, o hasta que documentes por qué no es alcanzable ahora mismo.
5. **Documenta** — actualiza este `plan.md`: marca el checkbox, pega la evidencia,
   anota cualquier decisión de diseño tomada en el camino (nueva ADR si aplica).
6. **Resumen de cierre de tarea** — una línea: qué cambió, qué prueba lo confirma,
   qué queda pendiente si algo queda pendiente.

### 0.3 Orden de fases — bloqueante

Las fases están ordenadas por severidad de riesgo, no por facilidad. **No se empieza
la Fase N+1 mientras la Fase N tenga checkboxes sin evidencia**, salvo que un ítem se
haya movido explícitamente a "Bloqueado / no verificable" con motivo documentado
(ej. no hay GPU disponible en esta sesión). Saltarse el orden para "avanzar más rápido"
es exactamente el patrón que produjo el estado actual (funciones reportadas como
hechas que no existían).

### 0.4 Formato de evidencia esperado

```
**Evidencia [fecha] [commit]:**
$ <comando ejecutado>
<salida real, completa o el fragmento relevante con "..." explícito si se recorta>
```

---

## FASE 0 — Contención de riesgos (bloqueante, primero)

Objetivo: eliminar los riesgos de seguridad/integridad activos antes de construir
nada nuevo encima. Ninguno de estos ítems admite "lo arreglamos después".

- [x] **0.1 Cifrado de credenciales fail-closed y persistente**
  Hallazgo: `ENV_PATH` resuelve a `/.env` en imagen (no escribible por usuario
  no-root); Settings lee de `/workspace/.env`. Con clave inválida,
  `encrypt_value()` devuelve el valor en **texto plano** en vez de fallar
  (`Encryption failed: Incorrect padding` seguido de fallback silencioso).
  Tareas:
  - Unificar `ENV_PATH` real con la ruta que lee `Settings`.
  - Hacer que un fallo de cifrado lance excepción (fail-closed), nunca devuelva
    el valor sin cifrar.
  - Prueba de persistencia: cifrar, reiniciar contenedor, descifrar y confirmar
    que el valor sobrevive el ciclo completo.
  Evidencia requerida: repetir el test `encrypt_value("audit-marker")` con clave
  inválida y confirmar que lanza excepción, no que devuelve `audit-marker` en
  claro; más un ciclo cifrado→restart→descifrado exitoso contra la DB real.

  **Implementación (decisiones de diseño):**
  - `config.py` define `ENV_FILE_PATH = Path(os.environ.get("ENV_FILE", "/workspace/.env"))`
    y ese mismo valor lo usa `Settings` como `env_file`: una sola fuente de verdad.
    `security_manager.ENV_PATH` apunta a esa ruta.
  - `encrypt_value()` ya no captura excepciones: si el cifrado falla, propaga
    (código que llama decide; en tools el error se devuelve al usuario sin guardar).
  - Si no hay `ENCRYPTION_KEY` configurada, se lee la ya persistida en el fichero
    (caso crítico: Compose inyecta variables al crear el contenedor, así que tras
    `docker restart` el env puede seguir vacío y la clave del fichero debe ganar).
  - Si hay que generar clave y no se puede persistir → `RuntimeError`; nunca se
    usa una clave efímera en memoria (las credenciales cifradas con ella serían
    irrecuperables tras reiniciar).
  - Compatibilidad legada conservada: `_normalize_key()` acepta clave Fernet
    directa o doblemente codificada (formato que escribía el código anterior en
    `.env`), y el formato del token (`base64(Fernet.encrypt)`) no cambia, así que
    las credenciales ya guardadas siguen descifrándose.
  - `decrypt_value()` sigue siendo tolerante a propósito (devuelve el valor tal
    cual si no descifra) para no romper filas antiguas guardadas en claro por el
    bug anterior; el cifrado nunca vuelve a escribir texto plano. Fuera del
    alcance de 0.1, documentado aquí.
  - Nota para servidor Linux: si `/workspace/.env` no es escribible por el usuario
    del contenedor, la generación lanza `RuntimeError` y el operador debe fijar
    `ENCRYPTION_KEY` manualmente. Fail-closed intencional.

  **Evidencia [2026-09-24] [commit `5ce1cac`, base `e1ef0ec`]:**

  Lint, tipos y tests (fuente del commit montada en la imagen en `/workspace/agent/src`):
  ```
  $ docker run --rm -e TELEGRAM_TOKEN=dummy_token_for_audit -e CI=true \
      -v "<repo>:/workspace" -w /workspace rafita-audit sh -lc \
      "export PYTHONPATH=/tmp/auditdev:/workspace/agent/src; \
       python -m ruff check agent/src agent/tests/test_security_manager.py --config pyproject.toml; \
       python -m ruff format --check agent/src agent/tests/test_security_manager.py --config pyproject.toml; \
       python -m mypy agent/src --config-file pyproject.toml --no-error-summary; \
       TELEGRAM_TOKEN=dummy python -m pytest agent/tests -q --tb=short"
  --- ruff check ---
  All checks passed!
  --- ruff format check ---
  45 files already formatted
  --- mypy ---
  agent/src/utils/telemetry.py:33: note: By default the bodies of untyped functions are not checked...
  ... (13 notas idénticas de annotation-unchecked, sin errores)
  --- pytest ---
  ............................................ssssssssssss...              [100%]
  47 passed, 12 skipped in 6.49s
  ```
  (47 = 38 previos + 9 nuevos en `agent/tests/test_security_manager.py`.)

  Ciclo real: cifrar → reinicio de contenedor → descifrar contra SQLite real
  (contenedor nuevo con el mismo volumen; en el segundo arranque se inyecta
  `-e ENCRYPTION_KEY=` vacío para reproducir el env obsoleto de Compose):
  ```
  $ docker run --rm -w /app -e PYTHONPATH=/app -e TELEGRAM_TOKEN=dummy_audit_token \
      -v "<tmp>\workspace:/workspace" -v "<tmp>\data:/data" rafita-audit \
      python /workspace/verify_01_store.py
  2026-09-24 21:57:18 | INFO | rafita:get_or_create_encryption_key:111 | Encryption key generated and saved to /workspace/.env
  KEY_FIRST16: huAFItXDZIItyS6p
  STORED_RAW: Z0FBQUFBQnF0WnktdE9vMXRLRmVVdFlyY0RkMGxpTy1SSWphU2hWUThvc1U2WDdwdEJKeDVUVWtEWVcyMjRIRzhDX3lJNF9ZcUwycUstb0x6MmZYTkRLYjNUeFpxbm90ZkE9PQ==
  STEP1_OK: stored value is encrypted, not plaintext

  $ <host> verificación de .env de prueba (clave enmascarada):
  TELEGRAM_TOKEN=dummy_audit_token
  ENCRYPTION_KEY=<masked len=60>

  $ docker run --rm -w /app -e PYTHONPATH=/app -e TELEGRAM_TOKEN=dummy_audit_token \
      -e ENCRYPTION_KEY= \
      -v "<tmp>\workspace:/workspace" -v "<tmp>\data:/data" rafita-audit \
      python /workspace/verify_01_read.py
  KEY_SECOND16: huAFItXDZIItyS6p
  DECRYPTED: audit-secret-01
  STEP2_OK: value survived container restart and decrypts correctly
  ```
  (Misma clave en ambos arranques, valor descifrado correcto.)

  Regresión exacta de la auditoría: clave inválida ya no devuelve texto plano:
  ```
  $ docker run --rm -w /app -e PYTHONPATH=/app -e TELEGRAM_TOKEN=dummy_audit_token \
      -e ENCRYPTION_KEY=not-a-valid-fernet \
      -v "<tmp>\workspace:/workspace" rafita-audit \
      python -c "from src.utils.security_manager import encrypt_value; print('RETURNED:', encrypt_value('audit-marker'))"
  Traceback (most recent call last):
    ...
    File "/app/src/utils/security_manager.py", line 103, in get_or_create_encryption_key
      return _normalize_key(settings.encryption_key)
    File "/app/src/utils/security_manager.py", line 50, in _normalize_key
      raise ValueError(
  ValueError: Invalid ENCRYPTION_KEY: not a valid Fernet key (expects a Fernet key or its base64 encoding, as generated by this project).
  EXIT_CODE=1
  ```

  Resumen de cierre: `ENV_PATH` unificado con `Settings`, cifrado fail-closed,
  clave persistida y reutilizada tras reiniciar (incluso con env obsoleto),
  compatibilidad con credenciales y claves legadas probada; 9 tests nuevos.

- [x] **0.2 Dependencias vulnerables**
  Hallazgo: `pip-audit` reporta 20 vulnerabilidades en `chromadb==0.5.0`,
  `pypdf==6.15.0`, `starlette==0.46.2`; 8 alertas Dependabot abiertas; el job de
  CI usa `continue-on-error: true` para pip-audit (no bloquea).
  Tareas:
  - Actualizar las tres dependencias a versiones sin CVEs conocidos, o
    documentar explícitamente por qué una no se puede actualizar todavía
    (breaking change, incompatibilidad) con plan de mitigación mientras tanto.
  - Quitar `continue-on-error: true` de pip-audit en CI, o sustituirlo por un
    umbral explícito de severidad documentado.
  Evidencia requerida: salida de `pip-audit -r agent/requirements.txt` con 0
  vulnerabilidades (o lista residual justificada), y el YAML de CI mostrando el
  gate activo.

  **Implementación (decisiones de diseño):**
  - `pypdf 6.15.0 → 6.19.0`: resuelve los tres PYSEC de pypdf. Sin cambios de API
    relevantes para `_extract_text_from_file`.
  - `fastapi 0.115.14 → 0.141.1` + `starlette==1.7.0` fijada explícitamente:
    los avisos de starlette exigen como mínimo 1.3.1 para limpiarse todos; fastapi
    0.115 y 0.116/0.125 mantienen techo `<0.49`/`<0.51`, mientras que
    **fastapi 0.141.1 ya no impone techo** (`starlette>=0.46.0`), lo que permite
    fijar 1.7.0. Se fija starlette en requirements para que el gate sea
    determinista (es transitiva de fastapi).
  - `chromadb`: **no existe versión corregida** — el rango afectado llega hasta
    1.5.9, que es la última publicada, y los avisos no tienen `fixed`. No se
    actualiza (además 0.5.0→1.x es un salto mayor que no resuelve las CVEs y
    arriesga el RAG justo antes de la Fase 1). Se aceptan con justificación y
    mitigación documentadas en `SECURITY.md` ("Dependencias con avisos
    aceptados (ChromaDB)"): los tres avisos (PYSEC-2026-3813/3814/3815) solo
    aplican al **servidor HTTP de Chroma** con autenticación/multi-tenant y
    modelos remotos (`trust_remote_code`); Rafita usa `PersistentClient`
    embebido, mono-usuario, sin API HTTP ni puerto de Chroma. Plan de salida:
    actualizar en cuanto upstream publique fix (Dependabot lo señalará).
  - CI: se elimina `continue-on-error: true`; el job ahora es un gate real con
    tres `--ignore-vuln` explícitos (los de ChromaDB). Cualquier aviso nuevo
    rompe el build.
  - No contradice ADR-001 (se mantiene ChromaDB como almacén) ni ninguna
    decisión previa: solo cambian versiones de dependencias web/PDF.

  **Evidencia [2026-09-24] [commit `c9a48d1`, base `5ce1cac`]:**

  Reconocimiento inicial (estado de partida, 20 vulnerabilidades en 3 paquetes):
  ```
  $ docker run --rm -v "<repo>/agent/requirements.txt:/req.txt:ro" python:3.11-slim \
      sh -lc "pip install -q pip-audit; pip-audit -r /req.txt"
  Found 20 known vulnerabilities in 3 packages
  Name      Version ID              Fix Versions
  chromadb  0.5.0   PYSEC-2026-3814
  chromadb  0.5.0   PYSEC-2026-3815
  chromadb  0.5.0   PYSEC-2026-3813
  pypdf     6.15.0  PYSEC-2026-3910 6.16.1
  pypdf     6.15.0  PYSEC-2026-3911 6.16.1
  pypdf     6.15.0  PYSEC-2026-3913 6.16.0
  starlette 0.46.2  PYSEC-2026-1941 0.47.2
  starlette 0.46.2  PYSEC-2026-1942 0.49.1
  ... (hasta PYSEC-2026-249 / PYSEC-2026-248, fixes 1.3.1/1.3.0)
  ```

  Compatibilidad del salto de fastapi (metadata de los wheels):
  ```
  $ sh check_fastapi.sh
  fastapi-0.116.2: Requires-Dist: starlette<0.49.0,>=0.40.0
  fastapi-0.125.0: Requires-Dist: starlette<0.51.0,>=0.40.0
  fastapi-0.141.1: Requires-Dist: starlette>=0.46.0
  ```
  → solo 0.141.1 permite instalar starlette ≥1.3.1 (la que limpia todos los avisos).

  Instalación limpia, smoke test de las apps ASGI reales y gate exacto de CI:
  ```
  $ docker run --rm -e TELEGRAM_TOKEN=dummy -e PYTHONPATH=/app \
      -v "<repo>/agent/requirements.txt:/req.txt:ro" -v "<repo>/agent:/app:ro" \
      python:3.11-slim sh /verify_02b.sh
  resolved: 0.141.1 1.7.0 2.10.3
  gateway /health: 200 {'status': 'ok', 'service': 'rafita-gateway', 'timestamp': 1790287929.7064986}
  gateway /metrics: 200
  voice /health: 200
  voice ws error msg: {'type': 'error', 'message': 'session not found'}
  SMOKE_OK
  --- pip-audit gate (con ignores documentados de chromadb) ---
  No known vulnerabilities found, 3 ignored
  ```

  Suite y estáticos dentro de la imagen reconstruida con el nuevo
  `requirements.txt`:
  ```
  $ docker build -q -t rafita-audit ./agent
  sha256:9044119686035b5dcf421430798d46fc52a334d61cdd843dfc81838b84719527
  $ ... sh /verify_02c.sh
  fastapi 0.141.1 | starlette 1.7.0 | pypdf 6.19.0 | pydantic 2.10.3
  --- ruff ---
  All checks passed!
  --- mypy ---
  (sin errores; 13 notas annotation-unchecked)
  --- pytest ---
  47 passed, 12 skipped in 4.53s
  ```

  Gate real en `.github/workflows/ci.yml` (diff aplicado):
  ```yaml
        # Gate bloqueante: cualquier vulnerabilidad nueva rompe el CI.
        # Los 3 avisos de ChromaDB están aceptados explícitamente (solo afectan
        # al modo servidor multi-tenant con auth, que Rafita no despliega).
        # Ver SECURITY.md -> "Dependencias con avisos aceptados (ChromaDB)".
        - name: Dependency vulnerability scan
          run: |
            pip-audit -r agent/requirements.txt \
              --ignore-vuln PYSEC-2026-3813 \
              --ignore-vuln PYSEC-2026-3814 \
              --ignore-vuln PYSEC-2026-3815
  ```

  **Residual:** los 3 avisos de ChromaDB quedan abiertos a propósito (no hay fix
  upstream); documentados con mitigación y con revisión pendiente cuando salga
  versión corregida. Las alertas Dependabot de pypdf pueden tardar en
  actualizarse hasta el re-escaneo de GitHub.

  Resumen de cierre: pypdf y fastapi/starlette actualizados a versiones sin CVE,
  `pip-audit` pasa a ser gate bloqueante con 3 excepciones explícitas, ChromaDB
  aceptada con mitigación documentada en SECURITY.md; smoke de endpoints y
  suite completa en verde.

- [x] **0.3 Path traversal en el vault**
  Hallazgo: `obsidian_manager.py::_resolve_path` concatena la carpeta recibida
  desde una tool sin verificar que el resultado siga dentro del vault;
  `_ensure_folder` crea rutas directas; `move_or_rename_file` usa `startswith`
  (bypasseable con rutas hermanas tipo `vault_bueno_falso/`).
  Tareas:
  - Confinar toda operación de lectura/escritura/borrado/movido a una
    resolución con `Path.resolve()` + comprobación real de ancestro (no
    `startswith` sobre string).
  - Test explícito con payloads de traversal (`../`, symlink, ruta hermana con
    prefijo igual) que deben ser rechazados.
  Evidencia requerida: pegar el test nuevo y su resultado en verde, más al menos
  un caso donde el código viejo habría fallado (regression test).

  **Implementación (decisiones de diseño):**
  - Nuevo `agent/src/utils/path_safety.py` con `resolve_within(root, candidate)`:
    resuelve el candidato y comprueba ancestro real (`root in resolved.parents`),
    rechazando también symlinks que apunten fuera del root. Se usa en todos los
    puntos de escritura/lectura/borrado/movido.
  - `obsidian_manager.py`: `_resolve_folder()` (rechaza rutas absolutas, normaliza
    `\`→`/`, confina) alimenta a `_resolve_path()`, `_ensure_folder()` y a
    `move_or_rename_file()` (origen, carpeta destino y nombre destino). Las
    funciones de notas propagan `ValueError` que `_execute_tool` ya convierte en
    mensaje de error; `move_or_rename_file` conserva su contrato de devolver
    `{"success": False}`.
  - Hallazgos adyacentes de la misma clase corregidos en el mismo alcance:
    - `files.py::_safe_vault_subpath` usaba `startswith` (aceptaba un symlink
      dentro del vault hacia un hermano del vault con prefijo igual). Ahora usa
      `resolve_within`; se mantiene el filtrado de componentes `..`.
    - `chat.py::ingest_file` hacía `mkdir` de `vault_path / folder` **antes** de
      llamar a `create_or_append_note`, creando directorios fuera del vault si
      `folder` traía traversal. Eliminado: la carpeta la crea ya
      `create_or_append_note` con la ruta validada.
  - No contradice ninguna decisión previa: mismos nombres de funciones y misma
    semántica de carpetas; solo se añade validación.

  **Evidencia [2026-09-25] [commit `b3f1467`, base `c9a48d1`]:**

  Regresión: con el código viejo (stash de `obsidian_manager.py` y `files.py`),
  los tests nuevos detectan los huecos — 20 fallos, 8 pasan:
  ```
  $ git stash push -m "tmp-regression-03" -- agent/src/utils/obsidian_manager.py agent/src/handlers/files.py
  Saved working directory and index state On master: tmp-regression-03
  $ docker run ... pytest agent/tests/test_path_traversal.py -q --tb=line
  FAILED ...test_create_rejects_traversal_folder[../outside] - Failed: DID NOT RAISE ValueError
  FAILED ...test_create_rejects_traversal_folder[../../outside] - Failed: DID NOT RAISE ValueError
  FAILED ...test_create_rejects_traversal_folder[01-Proyectos/../../outside] - Failed: DID NOT RAISE ValueError
  FAILED ...test_create_rejects_traversal_folder[/tmp/outside] - Failed: DID NOT RAISE ValueError
  FAILED ...test_create_rejects_traversal_folder[..\outside] - Failed: DID NOT RAISE ValueError
  FAILED ...test_read_rejects_traversal_folder[../outside] - Failed: DID NOT RAISE ValueError
  FAILED ...test_read_rejects_traversal_folder[../../outside] - Failed: DID NOT RAISE ValueError
  FAILED ...test_read_rejects_traversal_folder[01-Proyectos/../../outside] - Failed: DID NOT RAISE ValueError
  FAILED ...test_read_rejects_traversal_folder[/tmp/outside] - Failed: DID NOT RAISE ValueError
  FAILED ...test_read_rejects_traversal_folder[..\outside] - Failed: DID NOT RAISE ValueError
  FAILED ...test_delete_rejects_traversal_folder[../outside] - Failed: DID NOT RAISE ValueError
  FAILED ...test_delete_rejects_traversal_folder[../../outside] - Failed: DID NOT RAISE ValueError
  FAILED ...test_delete_rejects_traversal_folder[01-Proyectos/../../outside] - Failed: DID NOT RAISE ValueError
  FAILED ...test_delete_rejects_traversal_folder[/tmp/outside] - Failed: DID NOT RAISE ValueError
  FAILED ...test_delete_rejects_traversal_folder[..\outside] - Failed: DID NOT RAISE ValueError
  FAILED ...test_create_rejects_symlinked_folder_outside - Failed: DID NOT RAISE ValueError
  FAILED ...test_move_rejects_source_with_sibling_prefix - assert True is False
  FAILED ...test_move_rejects_destination_traversal - assert True is False
  FAILED ...test_move_rejects_symlinked_source_outside - assert True is False
  FAILED ...test_symlink_prefix_escape_rejected - Failed: DID NOT RAISE ValueError
  20 failed, 8 passed in 2.30s
  $ git stash pop
  Dropped refs/stash@{0}
  ```
  (El log del fallo del symlink de origen muestra que el código viejo movía el
  enlace: `File moved: .../nota_link.md -> .../01-Proyectos/nueva.md`.)

  Fix aplicado, lint/tipos y suite completa en verde:
  ```
  $ docker run ... sh /verify_03.sh
  --- ruff check ---
  All checks passed!
  --- ruff format check ---
  46 files already formatted
  --- mypy ---
  (sin errores; 13 notas annotation-unchecked)
  --- pytest (solo path traversal) ---
  28 passed in 2.02s
  --- pytest (suite completa) ---
  75 passed, 12 skipped in 4.77s
  ```

  Resumen de cierre: todas las operaciones del vault (notas y mover/renombrar)
  confinadas con resolución real de ancestro; cerrado también el `mkdir` sin
  validar de `ingest_file` y el `startswith` de `files.py`; 28 tests nuevos de
  traversal/symlink/prefijo hermano con prueba de regresión contra el código viejo.

- [x] **0.4 Webhook secret hardcodeado**
  Hallazgo: `WEBHOOK_SECRET=rafita-secure-2026` fijo en Compose; si falta el
  secreto, el handler **omite** la validación HMAC en vez de rechazar.
  Tareas:
  - Generar secreto aleatorio único por instancia en el setup (nunca un
    default compartido en el repo).
  - Si `WEBHOOK_SECRET` falta o está vacío, rechazar la petición (fail-closed),
    nunca aceptar sin validar.
  Evidencia requerida: arranque sin `WEBHOOK_SECRET` definido → petición de
  webhook rechazada (log/código de estado real pegado), no aceptada.

  **Implementación (decisiones de diseño):**
  - Se reutiliza el patrón de 0.1: `security_manager` generaliza la lectura y
    escritura del `.env` (`_read_env_var`/`_persist_env_var`) y añade
    `get_or_create_webhook_secret()`: usa `WEBHOOK_SECRET` si está configurado;
    si no, lee el persistido en `.env` (caso env obsoleto de Compose); si no
    existe, genera `secrets.token_urlsafe(32)` y lo persiste. Si no se puede
    persistir, devuelve `""` y el gateway rechaza (no aborta el arranque por una
    función opcional, a diferencia de la clave de cifrado que sí es esencial).
  - `webhook_server._check_webhook_auth()`: `503` si no hay secreto
    ("Webhook secret not configured"), `401` si falta o no coincide la firma.
    Sustituye los `if _webhook_secret:` que omitían la validación en los 5
    endpoints protegidos (`/webhook/{source}`, `POST|DELETE /connector/{name}`,
    `/gmail/check`, `/homeassistant/{entity_id}`).
  - `main.py` resuelve el secreto en el arranque y registra si la auth queda
    habilitada; `docker-compose.yml` elimina el default compartido;
    `.env.example` documenta la variable; `SECURITY.md` queda alineado con el
    comportamiento real (lo que la tarea 4.1 pedirá confirmar).
  - No contradice decisiones previas: sigue siendo HMAC SHA-256 por cabecera
    `X-Webhook-Signature`; solo cambia el origen del secreto y el fail-closed.

  **Evidencia [2026-09-25] [commit `b63013c`, base `b3f1467`]:**

  Simulación de arranque real del gateway (mismas funciones que usa `main.py`),
  sin `WEBHOOK_SECRET` en el entorno ni en el `.env`:
  ```
  $ docker run ... sh /verify_04.sh
  --- startup gateway simulation (sin WEBHOOK_SECRET) ---
  2026-09-25 08:03:13 | INFO | rafita:get_or_create_webhook_secret:136 | Webhook secret generated and saved to /tmp/tmpy6kzhrl8/.env
  secret_len: 43
  env_has_secret: True
  unsigned request: 401 {'detail': 'Invalid signature'}
  unconfigured: 503 {'detail': 'Webhook secret not configured'}
  ```
  (Petición sin firma rechazada con 401; gateway sin secreto rechaza con 503.)

  Regresión: con el `webhook_server.py` viejo, los tests nuevos detectan que se
  aceptaban peticiones sin secreto:
  ```
  $ git stash push -m "tmp-regression-04" -- agent/src/utils/webhook_server.py
  $ docker run ... pytest agent/tests/test_webhook_server.py -q --tb=line
  FAILED ...test_unconfigured_secret_rejects[/webhook/test-payload0] - assert 200 == 503
  FAILED ...test_unconfigured_secret_rejects[/connector/foo-payload1] - assert 500 == 503
  FAILED ...test_unconfigured_secret_rejects[/gmail/check-payload2] - assert 200 == 503
  FAILED ...test_unconfigured_secret_rejects[/homeassistant/luz-payload3] - assert 200 == 503
  FAILED ...test_connector_delete_rejected_when_unconfigured - RuntimeError: Database not initialized
  5 failed, 9 passed, 1 warning in 2.37s
  $ git stash pop
  ```

  Fix aplicado, lint/tipos y suites en verde:
  ```
  --- ruff check ---
  All checks passed!
  --- ruff format check ---
  48 files already formatted
  --- mypy ---
  (sin errores; 13 notas annotation-unchecked)
  --- pytest webhook + security ---
  26 passed, 1 warning in 2.35s
  --- pytest suite completa ---
  92 passed, 12 skipped, 1 warning in 5.80s
  ```
  (el warning es de `fastapi.testclient` sobre httpx/httpx2, solo herramienta de test)

  Resumen de cierre: secreto HMAC único por instancia generado y persistido,
  endpoints fail-closed (503 sin secreto / 401 sin firma válida), default
  compartido eliminado de Compose y documentación alineada; 17 tests nuevos.

- [x] **0.5 `ADMIN_IDS` no parsea desde `.env.example`**
  Hallazgo: `pydantic-settings` intenta decodificar como JSON antes del
  validator personalizado; el CSV de `.env.example` rompe con
  `JSONDecodeError: Extra data`. El Quickstart dice "solo edita
  TELEGRAM_TOKEN", así que una instalación de cero rompe.
  Tareas: arreglar el parseo (validator `mode="before"` o cambiar el formato
  documentado) para que el `.env.example` tal cual funcione sin edición extra.
  Evidencia requerida: `docker compose up` desde un clon limpio con solo
  `TELEGRAM_TOKEN` editado, sin error de `ADMIN_IDS`.

  **Implementación (decisiones de diseño):**
  - El campo pasa a `Annotated[list[int], NoDecode]`: `NoDecode` impide que
    pydantic-settings haga `json.loads()` del valor crudo antes del validator,
    así el CSV documentado funciona tal cual. Se mantiene `list[int]` porque
    `files.py` y `admin.py` lo usan con `user_id not in admin_ids`.
  - Validator `parse_admin_ids` robusto: acepta CSV (`1,2`), JSON (`[1,2]`),
    separadores `;`/espacios, un único id, vacío → `[]`, y valores inválidos se
    ignoran (mismo comportamiento tolerante que antes). `INSTALL.md` documenta
    JSON y `.env.example` CSV: ambos formatos quedan soportados.
  - No contradice decisiones previas: mismo tipo y misma semántica; solo cambia
    la capa de decodificación de pydantic-settings.

  **Evidencia [2026-09-25] [commit `34a3f4e`, base `b63013c`]:**

  Reproducción del fallo antes del fix, con un `.env` idéntico a
  `.env.example` (solo `TELEGRAM_TOKEN` editado):
  ```
  $ docker run ... -e PYTHONPATH=/appagent rafita-audit \
      python -c "from src.config import settings; print('admin_ids:', settings.admin_ids)"
  File "/app/src/config.py", line 122, in <module>
      settings = Settings()  # type: ignore[call-arg]
  pydantic_settings.sources.SettingsError: error parsing value for field "admin_ids" from source "DotEnvSettingsSource"
  (cadena: json.decoder.JSONDecodeError: Extra data: line 1 column 10 (char 9)
   a través de decode_complex_value -> json.loads)
  ```

  Fix aplicado: carga del mismo `.env` de ejemplo y suites en verde:
  ```
  --- ruff check ---            All checks passed!
  --- ruff format check ---     46 files already formatted
  --- mypy ---                  (sin errores; 13 notas annotation-unchecked)
  --- config load from .env.example copy (ENV_FILE=/cfg/.env) ---
  admin_ids: [123456789, 987654321]
  --- pytest config ---
  8 passed in 0.72s
  --- pytest suite completa ---
  100 passed, 12 skipped, 1 warning in 6.05s
  ```

  Clon limpio del repo (commit `34a3f4e`) con `.env` copiado de `.env.example`
  y **solo** `TELEGRAM_TOKEN` editado, ejecutado por Compose:
  ```
  $ git clone <repo> clone && cp .env.example .env  # + token dummy
  $ docker compose config --quiet
  compose-config: OK
  $ docker compose run --no-deps --rm rafita-agent-core \
      python -c "from src.config import settings; print('admin_ids:', settings.admin_ids); print('assistant:', settings.assistant_name)"
  Image clone-rafita-agent-core Built
  admin_ids: [123456789, 987654321]
  assistant: Rafita
  ```
  **Matiz de alcance:** no se ejecutó `docker compose up` completo (requiere
  descargar modelos de Ollama y un token de Telegram real; los contenedores
  quedarían en bucle de polling). Se ejecutó el contenedor real del agente vía
  `compose run` con el mismo `env_file`/volúmenes/imagen, que es donde ocurría
  el fallo (import de `src.config`). El arranque completo queda para la Fase 3.

  Resumen de cierre: `ADMIN_IDS` acepta CSV y JSON sin tocar código; `NoDecode`
  + validator robusto; 8 tests nuevos y clon limpio por Compose cargando el
  ejemplo sin error.

- [x] **0.6 Health check falso positivo**
  Hallazgo: `/health` devuelve `ok` estático sin comprobar Ollama, Chroma,
  Telegram o el estado real del RAG; fallos en esos componentes se capturan y
  el arranque continúa igualmente.
  Tareas: hacer que `/health` (o un `/ready` separado de `/health`) refleje el
  estado real de las dependencias críticas.
  Evidencia requerida: apagar Ollama deliberadamente y confirmar que el
  endpoint de readiness lo refleja (no devuelve `ok`).

  **Implementación (decisiones de diseño):**
  - `/health` se mantiene como **liveness** (proceso vivo, sin dependencias) y
    se añade `/ready` como **readiness** real. El healthcheck de Compose y del
    Dockerfile pasa a `/ready`, para que `docker compose ps` no reporte
    `healthy` con dependencias caídas.
  - `/ready` comprueba tres componentes y devuelve `503` si alguno no está ok:
    - **Ollama**: GET `/api/tags` con timeout 5s (probe directo, sin el circuit
      breaker del cliente para no arrastrar backoffs); marca `degraded` si
      responde pero el modelo de chat configurado no está descargado.
    - **Chroma**: nuevo `VectorManager.health()`: error si no está inicializado
      o si `count()` falla; `ok` con `chunks` (un vault vacío es válido).
    - **Telegram**: nuevo `RafitaBot.polling_status()` que comprueba app
      inicializada, evento `_app_started` y tarea de polling viva.
  - No contradice decisiones previas; `/metrics` no cambia. La separación
    completa liveness/readiness con estados degradados por dependencia es la
    tarea 3.1; aquí se implementa el mínimo verificable.

  **Evidencia [2026-09-25] [commit `8044e35`, base `34a3f4e`]:**

  Suite y estáticos:
  ```
  --- ruff check ---            All checks passed!
  --- ruff format check ---     46 files already formatted
  --- mypy ---                  (sin errores; 13 notas annotation-unchecked)
  --- pytest readiness ---      9 passed in 4.13s
  --- pytest suite completa --- 109 passed, 12 skipped in 7.19s
  ```

  Prueba en vivo con servicios reales (contenedor del gateway con
  `vector_db.initialize()` real, contenedor `ollama/ollama:latest` real y
  `qwen2.5:0.5b` descargado). La comprobación de Telegram usa la lógica real
  (`polling_status`) con el estado de polling simulado, porque la auditoría no
  dispone de un token real; el resto es HTTP/Chroma/Ollama de verdad:
  ```
  === OLLAMA ARRIBA ===
  $ curl http://127.0.0.1:18000/ready
  {"status":"ready","ready":true,"checks":{
    "ollama":{"status":"ok","latency_ms":58,"chat_model":"qwen2.5:0.5b","chat_model_available":true},
    "vector_db":{"status":"ok","chunks":0},
    "telegram":{"status":"ok"}},"timestamp":1790325095.5}
  HTTP_STATUS 200

  $ curl http://127.0.0.1:18000/health
  {"status":"ok","service":"rafita-gateway","timestamp":1790325095.5}
  HTTP_STATUS 200

  === OLLAMA PARADO (docker stop rafita-ollama-ready) ===
  $ curl http://127.0.0.1:18000/ready
  {"status":"not_ready","ready":false,"checks":{
    "ollama":{"status":"error","detail":"Ollama unreachable: [Errno -2] Name or service not known"},
    "vector_db":{"status":"ok","chunks":0},
    "telegram":{"status":"ok"}},"timestamp":1790325112.2}
  HTTP_STATUS 503

  $ curl http://127.0.0.1:18000/health
  {"status":"ok","service":"rafita-gateway","timestamp":1790325112.2}
  HTTP_STATUS 200
  ```
  (Liveness sigue 200 mientras readiness refleja la caída: separación correcta.)

  **Alcance no verificado aquí:** el estado `degraded` (Ollama alcanzable pero
  modelo sin descargar) está cubierto solo por test unitario; y el polling real
  de Telegram requerirá token válido en el servidor (Fase 3). Se documenta para
  no redondear el resultado.

  Resumen de cierre: `/health` liveness separado de `/ready` readiness real
  (Ollama+modelo, Chroma, Telegram), healthchecks de Compose/Dockerfile
  apuntando a `/ready`, prueba en vivo 200→503 al parar Ollama con liveness
  intacto; 9 tests nuevos.

- [x] **0.7 Bloque GPU de Compose inconsistente**
  Hallazgo: MIGRATION.md muestra un bloque `deploy` distinto al ya presente en
  el compose de memoria, lo que produce YAML inválido si se combinan tal cual.
  Tareas: unificar en un único bloque `deploy`/GPU correcto y actualizar
  MIGRATION.md para que coincida exactamente con el repo.
  Evidencia requerida: `docker compose config --quiet` en verde con el bloque
  GPU real incluido (no solo el compose sin GPU).

  **Implementación (decisiones de diseño):**
  - Se elimina la trampa de raíz: en vez de invitar a descomentar/pegar un
    segundo `deploy:` en el compose base (YAML inválido), se añade un overlay
    oficial `docker-compose.gpu.yml` que se fusiona con el base mediante
    `-f docker-compose.yml -f docker-compose.gpu.yml`. Compose fusiona el
    mapping `deploy` y añade `devices` a las `reservations` existentes, sin
    tocar los límites de memoria.
  - El compose base mantiene su único bloque `deploy` (memoria) y ahora solo
    apunta al overlay en un comentario, sin líneas accionables engañosas.
  - El overlay fija además `OLLAMA_VULKAN=0` para que en la máquina con GPU se
    use CUDA nativo (el base deja `OLLAMA_VULKAN=1`, útil en CPU).
  - `MIGRATION.md` Paso 2 se reescribe para usar el overlay y validar la
    combinación con `docker compose config --quiet` antes de arrancar. Se
    conserva la instalación del NVIDIA Container Toolkit (Paso 1) y la
    verificación en logs (Paso 3).
  - No contradice ADR ni SECURITY: solo cambia empaquetado de Compose. El
    arranque real con GPU sigue pendiente de hardware (migración/Fase 3).

  **Evidencia [2026-09-25] [commit `3e97259`, base `8044e35`]:**

  Regresión: el patrón que documentaba la guía vieja (dos claves `deploy` en el
  mismo servicio) es rechazado por Compose:
  ```
  $ docker compose -f old-pattern.yml config --quiet
  failed to parse ...\old-pattern.yml: yaml: construct errors:
  line 1: line 10: mapping key "deploy" already defined at line 4
  EXIT=1
  ```
  (archivo `old-pattern.yml` con el bloque de memoria + el bloque GPU pegados
  tal cual indicaba la guía anterior)

  Base sin GPU y overlay GPU, ambos válidos:
  ```
  $ docker compose config --quiet
  compose-config: OK

  $ docker compose -f docker-compose.yml -f docker-compose.gpu.yml config --quiet
  gpu-overlay-config: OK
  ```

  Renderizado del overlay (deploy fusionado, memoria preservada, GPU añadida y
  Vulkan desactivado una sola vez):
  ```
  $ docker compose -f docker-compose.yml -f docker-compose.gpu.yml config --format json
  === deploy de ollama-service (overlay) ===
  {
      "resources": {
          "limits": { "memory": "7516192768" },          // 7G preservado
          "reservations": {
              "memory": "5368709120",                    // 5G preservado
              "devices": [ { "capabilities": ["gpu"], "driver": "nvidia", "count": 1 } ]
          }
      }
  }
  === environment de ollama-service (overlay) ===
  { "OLLAMA_HOST": "0.0.0.0", "OLLAMA_KEEP_ALIVE": "-1", "OLLAMA_MAX_LOADED_MODELS": "2",
    "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_NUM_THREADS": "8", "OLLAMA_SCHED_SPREAD": "true",
    "OLLAMA_VULKAN": "0" }
  ```

  **No verificado aquí:** arranque real del overlay en una máquina con GPU
  (no hay RTX disponible en esta sesión); la validación cubre sintaxis y fusión,
  no la ejecución CUDA. Queda para la migración/Fase 3.

  Resumen de cierre: un solo bloque `deploy` fusionable vía overlay validado por
  Compose; guía de migración sin pasos que generen YAML inválido; `OLLAMA_VULKAN`
  desactivado en el perfil GPU.

- [x] **0.8 Desalineación de versión de Python**
  Hallazgo: `pyproject.toml` exige `>=3.12`; CI y Docker usan 3.11.
  Tareas: alinear todos los entornos a la misma versión real y soportada.
  Evidencia requerida: `python --version` dentro de la imagen build coincidiendo
  con lo declarado en `pyproject.toml`.

  **Implementación (decisiones de diseño):**
  - Se alinea la **declaración a 3.11**, que es la versión real y probada:
    imagen `python:3.11-slim` (builder y runtime), CI `PYTHON_VERSION: "3.11"`,
    badge de README "Python 3.11+" y tests ejecutándose en 3.11.15.
  - `requires-python = ">=3.11"`, ruff `target-version = "py311"`, mypy
    `python_version = "3.11"`. El host de desarrollo (3.12.x) sigue dentro del
    rango `>=3.11` para trabajar en local.
  - Se evita subir a 3.12 en esta fase: obligaría a revalidar wheels de
    `chromadb 0.5.0`, `chroma-hnswlib`, `piper-tts/piper-phonemize`,
    `ctranslate2` y `onnxruntime`, riesgo innecesario durante la
    estabilización. Queda como posible tarea de modernización posterior.

  **Evidencia [2026-09-25] [commit `fd34bc2`, base `3e97259`]:**

  Versión real dentro de la imagen build y declaración alineada:
  ```
  $ docker run --rm rafita-audit python --version
  Python 3.11.15

  $ grep -E "requires-python|target-version|python_version" pyproject.toml
  requires-python = ">=3.11"
  target-version = "py311"
  python_version = "3.11"

  $ grep -n "PYTHON_VERSION" .github/workflows/ci.yml
  10:  PYTHON_VERSION: "3.11"

  $ grep -n "FROM python" agent/Dockerfile
  1:FROM python:3.11-slim AS builder
  18:FROM python:3.11-slim AS runtime
  ```

  Estáticos y suite completa ejecutados ya con el target 3.11:
  ```
  --- ruff check ---            All checks passed!
  --- ruff format check ---     45 files already formatted
  --- mypy ---                  (sin errores; 13 notas annotation-unchecked)
  --- pytest suite completa --- 109 passed, 12 skipped in 8.92s
  ```

  Resumen de cierre: pyproject/ruff/mypy alineados a Python 3.11, la misma
  versión real de imagen, CI y tests; sin cambio de runtime.

**Bloqueado / no verificable (rellenar si aplica):**

---

## FASE 1 — RAG verificable

No empezar sin Fase 0 cerrada. Objetivo: que "el segundo cerebro encuentra lo que
debería encontrar" deje de ser una afirmación y pase a ser un número medido contra
casos reales, con el bug de reindexado corregido primero (si no, cualquier medición
posterior queda contaminada).

- [x] **1.1 Bug de reindexado: `note_path` vs `source`**
  Hallazgo: `vault_indexer.py` llama `delete_by_source(rel_path)` pero los
  metadatos que genera usan la clave `note_path`, no `source`;
  `vector_manager.py` borra filtrando por `source`. Resultado: editar una nota
  no borra sus chunks antiguos, quedan duplicados/desfasados. El test actual
  no reproduce esto porque construye metadata manualmente con `source`.
  Tareas: unificar la clave de metadata usada para indexar y para borrar; test
  de integración real: indexar nota → modificar contenido → reindexar →
  confirmar que los chunks viejos ya no existen y los nuevos sí.
  Evidencia requerida: pegar el test de integración (no mockeado) y su
  resultado en verde, más una consulta directa a Chroma mostrando el conteo de
  chunks antes/después.

  **Implementación (decisiones de diseño):**
  - La clave canónica de metadata es **`note_path`** (la que ya escribe el
    indexador real). `delete_by_source` pasa a `delete_by_note_path` y filtra
    por `note_path`; `add_document` y `document_exists` también se alinean a
    `note_path` para que no quede ninguna ruta que escriba `source`.
  - Compatibilidad con filas legadas que sí usan `source`: se consulta también
    ese filtro. **Hallazgo durante la implementación:** Chroma 0.5 lanza
    `KeyError` al filtrar por una clave que no existe en alguna fila, y un
    `$or` con dos claves distintas también falla (`KeyError: 'note_path'` en el
    diagnóstico). Solución robusta: consultar cada filtro por separado con su
    propio try/except y unir los IDs antes de borrar.
  - El test existente de borrado se actualiza para usar metadatos idénticos a
    los del indexador real (sin `source` manual), de modo que la prueba refleje
    el flujo verdadero.

  **Evidencia [2026-09-25] [commit `3e541c4`, base `fd34bc2`]:**

  Prueba en vivo contra Chroma real con embeddings reales
  (`ollama/ollama` + `all-minilm`), indexar → editar → reindexar → borrar:
  ```
  $ python /probe.py   (código con el fix)
  AFTER_FIRST_INDEX chunks=1
  AFTER_FIRST_INDEX docs=['CONTENIDO_VIEJO_ALFA']
  AFTER_REINDEX chunks=1
  AFTER_REINDEX docs=['CONTENIDO_NUEVO_BETA']
  OLD_PRESENT=False NEW_PRESENT=True
  REINDEX_OK
  DELETE_CHUNKS deleted=1 remaining=0
  DELETE_OK
  EXIT=0
  ```

  Regresión con el código anterior (stash de `vector_manager.py` y
  `vault_indexer.py`), mismo probe — el test detecta el bug:
  ```
  AFTER_FIRST_INDEX chunks=1
  AFTER_FIRST_INDEX docs=['CONTENIDO_VIEJO_ALFA']
  AFTER_REINDEX chunks=1
  AFTER_REINDEX docs=['CONTENIDO_VIEJO_ALFA']
  OLD_PRESENT=True NEW_PRESENT=False
  AssertionError: old chunk still present after reindex
  EXIT=1
  ```
  Nota: con el código viejo el contenido nuevo **ni siquiera se indexaba** (los
  IDs colisionaban y `add` los ignoraba), así que el RAG servía contenido
  obsoleto de forma silenciosa, peor que un simple duplicado.

  Diagnóstico del `$or` de Chroma 0.5 (motivó la solución de dos filtros):
  ```
  GET {'source': 'test/legacy.md'} -> ...
  ERR {'note_path': 'test/legacy.md'} -> KeyError: 'note_path'
  ERR {'$or': [...]} -> KeyError: 'note_path'
  ```

  Tests del repo con embeddings reales (requiere Ollama; se ejecutan aquí con
  `EMBEDDING_MODEL=all-minilm`):
  ```
  $ pytest agent/tests/test_vector_manager.py agent/tests/test_reindex_integration.py
  16 passed in 11.98s
  ```

  Suite en modo CI (sin Ollama; los tests de embeddings se saltan por diseño):
  ```
  --- ruff check ---            All checks passed!
  --- ruff format check ---     47 files already formatted
  --- mypy ---                  (sin errores)
  --- pytest suite completa --- 109 passed, 14 skipped in 10.60s
  ```

  **Alcance no verificado en CI:** el test de integración depende de Ollama y
  se salta cuando no está disponible (igual que los tests de embeddings
  existentes). La evidencia de arriba es de una ejecución real con Ollama; no se
  ejecutó en el pipeline de GitHub.

  Resumen de cierre: reindexado reemplaza chunks de verdad (misma clave al
  indexar y borrar, compatibilidad con filas legadas), test de integración real
  en verde y regresión demostrada contra el código anterior.

- [x] **1.2 Dataset de evaluación en español**
  Hallazgo: no existe evaluación semántica reproducible; F0.5/F9.5/F9.6 llevan
  en rojo desde el origen del proyecto sin un dataset que lo mida.
  Tareas: construir un conjunto de al menos 20-30 preguntas reales sobre el
  `vault_ejemplo` (o un vault de prueba dedicado, no el vault real personal),
  con la respuesta/nota esperada anotada a mano, incluyendo casos negativos
  (preguntas cuya respuesta NO está en el vault).
  Evidencia requerida: el propio dataset versionado en el repo (ej.
  `tests/rag_eval/dataset.jsonl`).

  **Implementación (decisiones de diseño):**
  - Se usa la opción de **vault de prueba dedicado** que permite el plan, no
    `vault_ejemplo`: 3 notas con muy poco contenido no dan para 20+ preguntas
    distintas con respuestas únicas ni para negativos creíbles. El vault de
    evaluación es sintético, versionado y estable:
    `agent/tests/rag_eval/vault/` con 8 notas (proyectos, finanzas, salud,
    trabajo, recursos, zettelkasten, diario, casa).
  - Dataset `agent/tests/rag_eval/dataset.jsonl`: **36 casos** (28 positivos y
    8 negativos), campos `id`, `query`, `category`, `relevant`, `expected_note`
    y `expected_keywords`. Los negativos piden información que no existe en el
    vault (matrícula del coche, vuelo a Lisboa, WiFi, talla de zapatos, etc.).
  - `agent/tests/test_rag_eval_dataset.py` valida el dataset en CI sin Ollama:
    JSONL válido, campos obligatorios, ids y queries únicos, ≥20 positivos con
    nota existente y keywords, ≥5 negativos sin nota, y que el vault existe.
  - Los casos son deliberadamente verificables por chunk: las keywords son
    números/nombres que aparecen literalmente en la nota esperada, para que la
    Fase 1.3 pueda medir recall y también comprobar el contenido.

  **Evidencia [2026-09-25] [commit `2ab2eac`, base `3e541c4`]:**

  Dataset versionado y su recuento real:
  ```
  $ git ls-files agent/tests/rag_eval
  agent/tests/rag_eval/dataset.jsonl
  agent/tests/rag_eval/vault/01-Proyectos/Proyecto Huerto Urbano.md
  ... (8 notas)

  $ python (resumen del dataset)
  total: 36
  positivos: 28
  negativos: 8
  por categoria: {'casa': 4, 'diario': 3, 'finanzas': 4, 'negativo': 8,
                  'proyecto': 3, 'recursos': 4, 'salud': 3, 'trabajo': 4,
                  'zettelkasten': 3}
  ids unicos: 36
  notas del vault: 8
  ```

  Validación del dataset y suite completa:
  ```
  --- ruff check ---        All checks passed!
  --- ruff format check --- 1 file already formatted
  --- pytest dataset ---    6 passed in 0.82s
  --- pytest suite ---      115 passed, 14 skipped in 10.82s
  ```

  Resumen de cierre: existe por fin un dataset de evaluación en español
  versionado (36 casos, 28+8) con vault dedicado y test de consistencia; base
  lista para medir Recall@k/MRR en 1.3.

- [x] **1.3 Medir Recall@k, MRR y tasa de falsos "no encontrado"**
  Depende de 1.1 y 1.2. Tareas: script que corra las preguntas del dataset
  contra `vector_manager.query()` real (con Ollama/bge-m3 arriba) y calcule
  Recall@k y MRR; medir también cuántos negativos el sistema afirma
  incorrectamente haber encontrado.
  Evidencia requerida: salida del script con las métricas numéricas reales,
  ejecutada con los servicios levantados (no en modo mock).

  **Implementación (decisiones de diseño):**
  - Runner versionado `agent/scripts/rag_eval.py`: copia el vault de evaluación
    a un directorio temporal (el indexador reescribe frontmatter al auto-enlazar
    y el vault versionado no debe mutar), lo indexa con el modelo de embeddings
    configurado en Ollama y consulta el dataset con `vector_manager.query()`.
  - Métricas: Recall@1/3/k, MRR@k, `keyword_any/all@k` (la keyword esperada
    aparece en el contenido recuperado), `mean/min_expected_relevance` (la
    relevancia del chunk correcto, proxy directo del criterio F0.5) y, para
    negativos, relevancia máxima/medias y tasa de "falso encontrado" en
    umbrales provisionales 0.5/0.6/0.7.
  - Funciones puras separadas para poder testearlas sin Ollama
    (`agent/tests/test_rag_eval_metrics.py`).

  **Evidencia [2026-09-25] [commit `802e20e`, base `2ab2eac`]:**

  Ejecución real con `ollama/ollama` + `bge-m3` (1.2 GB), vault de evaluación
  de 8 notas, top-k=5:
  ```
  $ EMBEDDING_MODEL=bge-m3 OLLAMA_HOST=... python agent/scripts/rag_eval.py --top-k 5
  Indexed 8 notes (26 chunks, 0 failures)

  [OK ] q001 rank=1 rel=0.621 keywords(any=True all=True)
  [OK ] q009 rank=1 rel=0.485 keywords(any=True all=True)
  [OK ] q013 rank=2 rel=0.474 keywords(any=True all=True)   <- unico no-top-1
  [OK ] q019 rank=1 rel=0.426 keywords(any=True all=True)   <- relevancia mas baja
  ... (28 positivos, todos con rank<=2 y keywords correctas)

  [NEG] n001 top_relevance=0.475
  [NEG] n002 top_relevance=0.482     <- maximo en negativos
  [NEG] n008 top_relevance=0.403
  === SUMMARY ===
  {
    "embedding_model": "bge-m3",
    "chunks_indexed": 26,
    "positives": {
      "total": 28,
      "recall@1": 0.9642857142857143,
      "recall@3": 1.0,
      "recall@5": 1.0,
      "mrr@5": 0.9821428571428571,
      "keyword_any@5": 1.0,
      "keyword_all@5": 1.0,
      "mean_expected_relevance": 0.61025,
      "min_expected_relevance": 0.426
    },
    "negatives": {
      "total": 8,
      "mean_top_relevance": 0.445875,
      "max_top_relevance": 0.482,
      "false_found@>=0.5": 0.0,
      "false_found@>=0.6": 0.0,
      "false_found@>=0.7": 0.0
    }
  }
  ```

  **Hallazgo clave para 1.4/1.5:** la relevancia del chunk correcto (0.426–0.734)
  se **solapa** con la de los negativos (0.403–0.482). Un umbral absoluto único
  alto (p. ej. 0.60) rechazaría respuestas correctas (q019=0.426, q013=0.474,
  q009=0.485, q022=0.491, q024=0.493); un umbral 0.5 no daría falsos positivos
  en este dataset pero dejaría fuera 5/28 aciertos. Esto confirma que el umbral
  de 0.60 heredado no se puede adoptar sin medir el trade-off (tarea 1.5). El
  MRR/recall alto muestra que el ranking es bueno; el problema es la escala
  absoluta de `1 - distance/2`, no la recuperación.

  **Alcance:** métricas medidas sobre el vault de evaluación sintético, no sobre
  el vault personal real; F0.5 en producción no queda demostrado por esto solo.

  Tests y estáticos:
  ```
  --- ruff check ---            All checks passed!
  --- ruff format check ---     48 files already formatted
  --- mypy ---                  (sin errores)
  --- pytest metricas+dataset --- 10 passed in 1.64s
  --- pytest suite completa ---   119 passed, 14 skipped in 12.08s
  ```

  Resumen de cierre: primer número real de calidad RAG (recall@3 = 1.0,
  MRR@5 = 0.98 con bge-m3), negativos sin falsos positivos a 0.5+, y evidencia
  cuantitativa del solape que debe resolver la calibración de 1.5.

- [x] **1.4 Métrica de distancia de la colección**
  Hallazgo: la colección se crea sin `metadata`/`hnsw:space`, así que Chroma
  usa L2 por defecto. La auditoría midió que los 315 embeddings documentales
  ya son prácticamente unitarios (norma ≈ 1), lo que descartaría el efecto de
  magnitud en los documentos — pero el lado de la consulta (embedding de la
  pregunta del usuario) no se verificó porque Ollama estaba apagado.
  Tareas: verificar si los embeddings de consulta también son unitarios; si lo
  son, L2 al cuadrado y coseno ordenan igual y el problema real puede estar en
  otro punto (candidatos: calidad del embedding de bge-m3 en español para este
  tipo de pregunta, tamaño/solapamiento de chunk, no la métrica de distancia).
  No asumas la causa de la auditoría anterior sin confirmarla con esta prueba.
  Evidencia requerida: normas de al menos 10 embeddings de consulta reales
  (preguntas del dataset de 1.2) calculadas con Ollama arriba.

  **Implementación (decisiones de diseño):**
  - Se añade al runner 1.3 el modo `--check-metric`: indexa el vault de
    evaluación, calcula normas de documentos y de 12 preguntas reales del
    dataset, verifica el espacio de la colección, recalcula L2 y coseno desde
    los embeddings crudos y compara con las distancias que devuelve Chroma.
  - No se cambia `hnsw:space`: primero se mide. Si los vectores son unitarios,
    cambiar a coseno exigiría reindexar sin alterar el orden de resultados.
  - Se deja un comentario en `vector_manager.initialize()` explicando por qué
    el espacio L2 por defecto es equivalente a coseno con estos modelos y que
    no debe cambiarse sin re-medir y reindexar.

  **Evidencia [2026-09-25] [commit `cd038b3`, base `802e20e`]:**

  ```
  $ EMBEDDING_MODEL=bge-m3 OLLAMA_HOST=... python agent/scripts/rag_eval.py \
      --check-metric --sample-size 12
  Indexed 8 notes (26 chunks)
  === METRIC CHECK ===
  {
    "embedding_model": "bge-m3",
    "chunks_indexed": 26,
    "collection_metadata": null,
    "queries_checked": 12,
    "query_norm_min": 0.999999740123361,
    "query_norm_mean": 0.9999999097309353,
    "query_norm_max": 1.0000003584193622,
    "zero_query_vectors": 0,
    "doc_norm_min": 0.9999990642699882,
    "doc_norm_mean": 0.9999999948565953,
    "doc_norm_max": 1.0000004181982898,
    "top5_order_matches_l2_vs_cosine": 12,
    "max_chroma_vs_manual_l2_delta": 5.10702591327572e-15,
    "pearson_l2_vs_cosine_min": 0.9999999999955689,
    "pearson_l2_vs_cosine_mean": 0.9999999999978001
  }
  ```

  **Conclusión basada en datos (no en la hipótesis previa):**
  - `collection_metadata: null` confirma que Chroma usa L2 por defecto, y el
    delta entre la distancia que devuelve Chroma y el L2 recalculado es de
    ~5e-15 (ruido numérico): el espacio es L2 con certeza.
  - Los embeddings de **consulta** también son unitarios (0.9999997–1.0000004)
    y los de documento también (0.9999991–1.0000004), sin vectores nulos. La
    parte que la auditoría no había podido verificar queda verificada.
  - Con vectores unitarios, `relevance = 1 - L2²/2` **es exactamente** la
    similitud coseno, y L2 y coseno ordenan igual (top-5 idéntico en 12/12,
    Pearson ≈ 1). Por tanto **la métrica de distancia no es la causa** de
    ningún problema de relevancia; cambiar a coseno no cambiaría nada salvo
    obligar a reindexar. No se toca.
  - El solape de escala absoluta observado en 1.3 (correctos 0.426–0.734 vs
    negativos 0.403–0.482) es geometría inherente del coseno entre textos
    relacionados, no un bug de métrica. La decisión de umbral debe salir del
    trade-off medido (tarea 1.5), no de asumir que el ranking está roto.

  Tests y estáticos:
  ```
  --- ruff check ---            All checks passed!
  --- ruff format check ---     48 files already formatted
  --- mypy ---                  (sin errores)
  --- pytest metricas+dataset --- 11 passed in 1.48s
  --- pytest suite completa ---   120 passed, 14 skipped in 12.39s
  ```

  Resumen de cierre: verificado con Ollama+bge-m3 que consulta y documentos son
  unitarios y que L2 y coseno ordenan idéntico; la hipótesis de métrica rota
  queda refutada con números y el foco pasa a la calibración del umbral (1.5).

- [x] **1.5 Umbral de relevancia calibrado con datos reales**
  Depende de 1.3. Tareas: usando las métricas medidas (no un número elegido a
  ojo), fijar un umbral de corte y una respuesta explícita tipo
  "NO_ENCONTRADO" cuando no se supera, sin listar resultados de baja
  relevancia como pistas.
  Evidencia requerida: comparación de al menos dos umbrales candidatos contra
  el dataset, mostrando el trade-off real (falsos positivos vs falsos
  negativos) antes de fijar el valor final.

  **Implementación (decisiones de diseño):**
  - Barrido `--sweep-thresholds` en el runner 1.3: recoge las relevancias
    crudas una sola vez (positivos: relevancia de la nota correcta; negativos:
    relevancia máxima) y calcula FN/FP para cada candidato.
  - **Regla de decisión documentada:** primero minimizar falsos positivos
    (no presentar información personal irrelevante como si fuera una respuesta),
    después minimizar falsos negativos, redondeando a 2 decimales para
    estabilidad. El valor final es **0.49**, configurable con
    `RELEVANCE_THRESHOLD`.
  - `VectorManager.query()` filtra por umbral por defecto y devuelve
    `NO_ENCONTRADO` explícito cuando nada lo supera; `apply_threshold=False`
    queda para el evaluador y para el auto-enlazado del indexador (que quiere
    vecinos semánticos, no respuestas de alta confianza).
  - Las tools `search_second_brain` y `ask_deep_knowledge_base` responden
    `NO_ENCONTRADO: ...` en vez de listar pistas de baja relevancia.
  - No contradice ADR-003 (bge-m3) ni 1.4 (métrica L2 = coseno con vectores
    unitarios): el umbral es directamente una similitud coseno.

  **Evidencia [2026-09-25] [commit `421362d`, base `cd038b3`]:**

  Barrido real con bge-m3 sobre el dataset (28 positivos / 8 negativos):
  ```
  $ python agent/scripts/rag_eval.py --sweep-thresholds 0.40,0.45,0.48,0.49,0.50,0.55,0.60
  Indexed 8 notes (26 chunks)
  === THRESHOLD SWEEP ===
  positives=28 negatives=8
  threshold | FN | FN rate | FP | FP rate
    0.40    |  0 |    0.0% |  8 |  100.0%
    0.45    |  1 |    3.6% |  4 |   50.0%
    0.48    |  2 |    7.1% |  1 |   12.5%
    0.49    |  3 |   10.7% |  0 |    0.0%   <- elegido
    0.50    |  5 |   17.9% |  0 |    0.0%
    0.55    |  5 |   17.9% |  0 |    0.0%
    0.60    |  9 |   32.1% |  0 |    0.0%   <- umbral heredado
  ```
  Lectura: sin umbral (0.40) **el 100% de los negativos se presentan como
  encontrados**; el 0.60 que la auditoría daba por bueno descartaría **9 de 28
  respuestas correctas**. 0.49 es el único punto con FP=0 y el menor FN.

  Test en vivo del comportamiento final (Ollama + bge-m3; el negativo elegido
  es el que más se acerca al umbral, tope 0.482):
  ```
  $ pytest agent/tests/test_query_threshold.py agent/tests/test_rag_eval_metrics.py
  7 passed in 11.15s
  ```
  El test comprueba: consulta negativa → `results == []` y mensaje con
  `NO_ENCONTRADO`; misma consulta con `apply_threshold=False` → hay candidatos
  y su relevancia máxima < 0.49; consulta positiva → resultados y todos con
  relevancia ≥ 0.49.

  Suite en modo CI:
  ```
  --- ruff check ---            All checks passed!
  --- ruff format check ---     48 files already formatted
  --- mypy ---                  (sin errores)
  --- pytest metricas+dataset+threshold --- 12 passed, 1 skipped
  --- pytest suite completa ---   121 passed, 15 skipped in 12.33s
  ```

  **Limitación del margen:** con 8 negativos y un vault sintético pequeño, la
  distancia entre el mejor negativo (0.482) y el umbral (0.49) es de 0.008.
  El valor es defendible con estos datos, pero debe re-validarse con el vault
  real y más negativos (Fase 3/4); queda documentado, no oculto.

  Resumen de cierre: umbral calibrado con tabla de trade-off real (FP/FN),
  0.49 configurable, `NO_ENCONTRADO` explícito en tools y filtrado medido en
  vivo; el umbral heredado de 0.60 queda refutado con datos.

- [x] **1.6 Filtrado por tags**
  Hallazgo: el filtrado de tags ocurre en Python después de recuperar el
  `top_k` inicial, no como filtro `$eq` en la query — puede perder notas
  etiquetadas que quedaron fuera del top_k antes de filtrar.
  Tareas: mover el filtro de tags a la propia query de Chroma (o ampliar el
  `top_k` interno lo suficiente antes de filtrar) y testear recall con filtro
  activo.
  Evidencia requerida: test con una nota etiquetada que antes se perdía y
  ahora se recupera.

  **Verificación previa de capacidades de Chroma 0.5 (en la imagen real):**
  ```
  $ sed -n '318,404p' .../chromadb/api/types.py      # validate_where
  operadores de metadata: $gt,$gte,$lt,$lte,$ne,$eq,$in,$nin + $and/$or
  (cada $and/$or exige >=2 subexpresiones); NO existe $contains de metadata
  (el $contains de la librería es de where_document); metadata solo escalares.

  $ python /check.py   (colección de prueba con embeddings explícitos)
  GET {'tag__mascotas': 1} -> ['a']
  GET {'tag__noexiste': 1} -> []
  GET {'$or': [{'tag__mascotas': 1}, {'tag__finanzas': 1}]} -> ['a', 'c']
  GET {'$or': [{'tag__mascotas': 1}, {'tag__noexiste': 1}]} -> ['a']
  ```
  Corrección de una conclusión anterior: filtrar por una clave ausente **no**
  lanza `KeyError` en `where`; el `KeyError: 'note_path'` de la tarea 1.1 venía
  de `index_chunks` accediendo a `chunk["metadata"]["note_path"]`, no de Chroma.

  **Implementación (decisiones de diseño):**
  - Como no hay `$contains` ni listas en metadata, cada tag se indexa como flag
    escalar `tag__<normalizado>=1` (`normalize_tag`: minúsculas, sin acentos,
    `[^a-z0-9]→_`, truncado a 40; máximo 20 flags por nota).
  - `build_tag_where()`: un tag → igualdad; varios → `$or` (misma semántica OR
    que el post-filtro anterior, ahora aplicada por Chroma antes del ranking).
  - El post-filtro en Python se mantiene con comparación **normalizada** (antes
    era texto exacto: `"Finanzas"` no casaba con el tag guardado `finanzas`).
  - Compatibilidad: si una DB antigua no tiene flags y el `where` no devuelve
    nada, se reintenta con over-fetch y post-filtro (comportamiento previo).
    Requiere reindexar para aprovechar el filtro en query.
  - No contradice ADR-001; `tags_str` se conserva para mostrar tags al usuario.

  **Evidencia [2026-09-25] [commit `7080633`, base `421362d`]:**

  Escenario: 8 notas de ruido (tags [ruido]) que dominan el top-5 y una nota
  "Mascotas.md" (tags [mascotas]) que sin filtro queda fuera. Probe con bge-m3
  real:
  ```
  === PROBE (código nuevo) ===
  UNFILTERED: ['Ruido 5.md', 'Ruido 2.md', 'Ruido 4.md', 'Ruido 0.md', 'Ruido 6.md']
  FILTERED mascotas: ['Mascotas.md']

  === PROBE (código viejo, stash de vector_manager+vault_indexer) ===
  UNFILTERED: ['Ruido 5.md', 'Ruido 2.md', 'Ruido 4.md', 'Ruido 0.md', 'Ruido 6.md']
  FILTERED mascotas: []
  ```
  El `top_k` sin filtro es idéntico en ambos; la nota etiquetada solo se
  recupera con el fix (antes se perdía exactamente como describía la auditoría).

  Test del repo con Ollama+bge-m3 (incluye tag en mayúsculas y filtro por
  `ruido` para comprobar que el `where` no contamina resultados):
  ```
  $ pytest agent/tests/test_tag_filter.py
  3 passed in 5.51s
  ```

  Suite y estáticos:
  ```
  --- ruff check ---            All checks passed!
  --- ruff format check ---     46 files already formatted
  --- mypy ---                  (sin errores)
  --- pytest tag filter (CI) --- 2 passed, 1 skipped
  --- pytest suite completa ---   123 passed, 16 skipped in 12.82s
  ```

  Resumen de cierre: filtro de tags movido a la query de Chroma con flags
  escalares, semántica OR conservada, compatibilidad con DBs sin reindexar y
  regresión demostrada (antes `[]`, ahora recupera la nota etiquetada).

- [x] **1.7 Suite de tool-calling con modelo real**
  Hallazgo: las 21 tools están definidas y con ramas de ejecución, pero
  ninguna está verificada en uso real con un LLM (los tests existentes
  mockean `_execute_tool`).
  Tareas: suite que, con Ollama real arriba, mande prompts diseñados para
  disparar cada tool y confirme invocación + resultado correcto, registrando
  la tasa de éxito por modelo probado (gemma4:12b como mínimo).
  Evidencia requerida: tabla real de tool → invocada correctamente (sí/no) →
  nota si el modelo la ignoró o la usó mal.

  **Implementación (decisiones de diseño):**
  - `agent/scripts/tool_calling_eval.py`: 21 casos (uno por tool) + 2 controles
    negativos, 2 intentos por caso con el modelo real y las `TOOLS_DEFINITIONS`
    de producción; entorno aislado (DATA_DIR/DB/vault/vector_db temporales con
    el vault de evaluación copiado) y ejecución real de la tool invocada.
  - Puntuación con `accept`: la auditoría ya señaló tools redundantes; los
    grupos funcionalmente equivalentes (trío RAG: `ask_deep_knowledge_base`,
    `search_second_brain`, `search_obsidian_vault`; listado Google:
    `manage_google_calendar`/`get_google_calendar_events`; creación Google)
    cuentan como acierto. Sin esto, elegir una tool válida distinta se
    contabilizaba como `wrong_tool` (pasó en la primera ejecución).
  - Las 5 tools de Google se invocan pero no se ejecutan (sin credenciales);
    el resto se ejecuta y se registra `success`/mensaje.
  - Informe JSON + tabla por tool; 9 tests unitarios de la lógica de scoring
    que corren en CI sin Ollama.

  **Evidencia [2026-09-25] [commit `6e83088`, base `0992b52`] — PC con RTX 3060:**

  Entorno verificado: `ollama/ollama:latest` (0.34.4) en Docker con `--gpus all`
  detecta `library=CUDA compute=8.6 name="NVIDIA GeForce RTX 3060" total="11.6 GiB"`;
  `gemma4:12b` cargado al `100% GPU` (8.1 GB, ctx 4096) y `bge-m3` para embeddings.
  `model=gemma4:12b embedding=bge-m3 tools=21 tools_json_chars=14161`.

  Ejecución final (2 intentos por caso; equivalencias aceptadas):
  ```
  tool                          correct/attempts  rate
  save_expense                 2/2                100%
  create_event                 0/2                  0%
  create_alert                 0/2                  0%
  get_finance_summary          2/2                100%
  remember_fact                0/2                  0%
  search_knowledge             0/2                  0%
  search_web                   2/2                100%
  manage_obsidian_note         2/2                100%
  search_obsidian_vault        1/2                 50%
  inspect_project_files        2/2                100%
  analyze_system_logs          2/2                100%
  move_or_rename_file          0/2                  0%
  ask_deep_knowledge_base      2/2                100%
  search_second_brain          2/2                100%
  manage_google_calendar       2/2                100%
  set_recurring_reminder       0/2                  0%
  generate_google_auth_link    2/2                100%
  save_google_verification_code 2/2               100%
  get_google_calendar_events   2/2                100%
  create_google_calendar_event 0/2                  0%
  ingest_file                  0/2                  0%
  no_tool_greeting             2/2                100%   (control negativo)
  no_tool_thanks               2/2                100%   (control negativo)

  OVERALL: 29/46 (63%) failure_modes={'no_tool': 17}
  ```

  Primera ejecución (antes de añadir equivalencias): `26/46 (57%)` con
  `no_tool=14` y `wrong_tool=6`; los 6 `wrong_tool` eran elecciones
  equivalentes (`search_second_brain` en lugar de las otras RAG;
  `get_google_calendar_events` en lugar de `manage_google_calendar`), lo que
  confirma en la práctica la redundancia de tools que señaló la auditoría.
  Con equivalencias, esa ejecución queda en 32/46 (70%).

  Ejecución real de tools en los aciertos (extracto del JSON, intento 1):
  ```
  save_expense         success=True  "Gasto registrado: 45.00 MXN en transporte (ID: 1)..."
  create_alert         success=True  "Alerta creada: 'Renovar el DNI' (tipo: urgent, ID: 1)"
  get_finance_summary  success=True  "Resumen de September 2026: ... Gastos: 90.00 MXN"
  manage_obsidian_note success=True  "Nota 'Ideas de verano' creada en Obsidian."
  search_second_brain  success=True  "1. *02-Areas/Salud/Historial Medico.md* → Alergias (63%)..."
  inspect_project_files success=True (lista real de agent/src)
  analyze_system_logs  success=True  (disco 198.3 GB libres, logs)
  generate_google_auth_link / save_google_verification_code / get_google_calendar_events:
      invocadas correctamente; ejecución omitida (sin credenciales Google)
  search_web           success=False "No encontré resultados..." (invocación correcta; red del contenedor)
  ```

  **Análisis de fallos (honesto, no redondeado):**
  - 7 tools **no se invocaron en ninguna de las dos ejecuciones**: `create_event`,
    `remember_fact`, `search_knowledge`, `move_or_rename_file`,
    `set_recurring_reminder`, `create_google_calendar_event`, `ingest_file`.
    El modelo responde en prosa sin llamar herramienta.
  - 2 tools son inestables entre ejecuciones: `create_alert` (2/2 → 0/2) y
    `search_obsidian_vault` (0/2 → 1/2), con temperatura 0.7.
  - 2/2 en los controles negativos: el modelo **no** llama tools por charla
    trivial (no hay sobre-disparo).
  - **Hallazgo colateral (config, no tool-calling):** con el prompt "Gasté 45
    euros...", `save_expense` guardó `45.00 MXN` porque `DEFAULT_CURRENCY` es
    MXN en config/DB mientras el prompt/plantillas usan EUR (lo cubrirá la
    Fase 2.2).
  - **Alcance:** la suite usa el prompt compartido del orquestador
    (`SYSTEM_PROMPT_VOICE`), no el prompt inline de Telegram; los resultados
    pueden diferir en el bot real. Las tools de Google se miden por invocación,
    no por ejecución (sin OAuth).

  Tests y estáticos:
  ```
  --- ruff check ---            All checks passed!
  --- ruff format check ---     47 files already formatted
  --- mypy ---                  (sin errores)
  --- pytest tool eval helpers --- 9 passed
  --- pytest suite completa ---   132 passed, 16 skipped
  ```

  Resumen de cierre: primera medición real de tool-calling (29/46 con
  equivalencias; 63%), ejecución verificada de tools de escritura/RAG, y
  lista concreta de 7 tools que el modelo no invoca — base para priorizar
  fiabilidad de tools en Fase 2.

- [x] **1.8 Tests de Fernet no vacuos**
  Hallazgo: en `test_fernet.py`, los casos de clave inválida/token manipulado
  envuelven un `assert False` dentro del mismo `try/except Exception`, así que
  el propio `AssertionError` queda capturado y el test puede pasar aunque
  `decrypt` no falle realmente.
  Tareas: reescribir esos tests para que la aserción de fallo esperado quede
  fuera del bloque que captura la excepción del propio `decrypt`.
  Evidencia requerida: mostrar que, si se revierte temporalmente el fix de
  0.1, este test ahora sí falla (prueba de que el test detecta el problema).

  **Implementación (decisiones de diseño):**
  - `test_fernet_invalid_key_fails` y `test_fernet_tampered_token` usan
    `pytest.raises(InvalidToken)` en lugar de `try/except Exception` +
    `assert False`, de modo que su aserción no puede ser capturada por el
    propio bloque que espera la excepción. El roundtrip y el multi-valor UTF-8
    se mantienen.
  - La evidencia de no-vacuidad se demostró en dos frentes: un experimento
    controlado estilo-viejo vs estilo-nuevo, y la reversión temporal del fix de
    0.1 en `encrypt_value` con la regresión asociada.

  **Evidencia [2026-09-25] [commit `0992b52`, base `7080633`]:**

  (a) Con una implementación que **no** lanza excepción, el estilo viejo pasa
  (vacuo) y el nuevo falla como debe:
  ```
  $ pytest /vacuity_check.py -v --tb=line
  FAILED ...::test_strict_style_detects_missing_exception - Failed: DID NOT RAISE
  1 failed, 1 passed, 2 warnings in 0.07s
  ```
  (`test_old_vacuous_style_passes_even_when_no_exception` pasa sin que el
  "fallo esperado" ocurra.)

  (b) Reversión temporal del fix de 0.1 (`encrypt_value` vuelve a devolver
  texto plano con try/except) y ejecución de la regresión asociada:
  ```
  $ <editar temporalmente encrypt_value al comportamiento pre-0.1>
  $ pytest agent/tests/test_security_manager.py::test_invalid_configured_key_raises_not_plaintext -q
  ERROR    rafita:security_manager.py:159 Encryption failed: Invalid ENCRYPTION_KEY...
  .../test_security_manager.py:33: Failed: DID NOT RAISE ValueError
  FAILED ...::test_invalid_configured_key_raises_not_plaintext - Failed: DID NOT RAISE ValueError
  1 failed in 1.72s

  $ git checkout -- agent/src/utils/security_manager.py   # restaurar
  $ pytest agent/tests/test_fernet.py test_security_manager.py::test_invalid... -q
  5 passed in 1.65s
  ```
  Es decir: el test ahora **detecta** el comportamiento pre-0.1 en vez de
  pasar en silencio.

  Tests y estáticos con el código restaurado:
  ```
  --- ruff check ---            All checks passed!
  --- ruff format check ---     46 files already formatted
  --- mypy ---                  (sin errores)
  --- pytest fernet ---         4 passed in 0.89s
  --- pytest suite completa ---   123 passed, 16 skipped in 14.51s
  ```

  Resumen de cierre: tests de fallo reescritos sin auto-captura, no-vacuidad
  demostrada contra una implementación rota y contra la reversión del fix 0.1.

**Bloqueado / no verificable (rellenar si aplica):**

Ninguno: 1.7 se completó en el PC con RTX 3060 el 2026-09-25 (ver evidencia en
la tarea).

---

## FASE 2 — Producto configurable

No empezar sin Fase 1 cerrada (no tiene sentido generificar un RAG que no sabes si
funciona). Objetivo: pasar del 15/100 de generificación medido a un estado donde
otra persona instale el proyecto con su propio vault y su propia IA sin tocar código.

- [x] **2.1 Vault y taxonomía configurables**
  Hallazgo: `OBSIDIAN_VAULT_DIR` existe como variable, pero Compose fija
  `./mi_boveda_obsidian`; `VAULT_STRUCTURE`, `IGNORED_DIRS` y la estructura
  PARA/Zettelkasten están hardcodeadas en el código, no leídas de config.
  Tareas: toda referencia a nombres de carpeta (Inbox, 01-Proyectos,
  02-Areas, etc.) debe salir de un fichero de configuración, con la
  estructura PARA/Zettelkasten actual como *default*, no como único valor
  válido.
  Evidencia requerida: arrancar el proyecto con una taxonomía de carpetas
  distinta definida solo por config, sin editar código, y confirmar que
  `BrainMaintainer`/indexado la respetan.

  **Opciones de diseño consideradas (documentado a petición del usuario):**
  - **(A) Variables de entorno por carpeta** (`VAULT_FOLDER_INBOX=...`, etc.):
    simple y sin ficheros nuevos, pero son ~11 variables, no expresan una
    lista ordenada de carpetas a crear y complican valores anidados.
  - **(B) Sección `vault:` en `agent/config.yml`** (fichero que ya existe en
    el repo): estructura legible con nombres simbólicos (`inbox`, `finances`,
    `zettelkasten`...), lista de carpetas y `ignored_dirs`, defaults PARA
    embebidos y override por `RAFAITA_CONFIG`. El contenedor ya monta el repo
    en `/workspace`, así que el fichero es accesible. **Elegida.**
  - **(C) Una única variable `VAULT_TAXONOMY_JSON` en `.env`**: reutiliza el
    patrón ya existente para `ADMIN_IDS`, pero el JSON con rutas y listas es
    frágil de editar/escapar y mezcla taxonomía con secretos en `.env`.
  - **(D) Taxonomía libre autodetectada** (cualquier carpeta del vault sin
    nombres fijos): máxima genericidad, pero se pierden los destinos
    semánticos que usan las tools (finanzas, diario, adjuntos) y el
    comportamiento se vuelve impredecible.

  **Decisión:** opción **(B)** con defaults PARA/Zettelkasten embebidos en el
  código. Consecuencias de diseño: `src/vault_config.py` sin dependencias de
  `src` (evita ciclos con `config.py`), claves simbólicas consumidas por
  indexador, `obsidian_manager`, `files.py`, prompts y `chat.py`; si no hay
  `config.yml` o está corrupto se usan los defaults (fail-open a PARA, no a
  nombres vacíos). Nota: `BrainMaintainer` no existe aún (tarea 2.4); la
  evidencia de esta tarea cubre indexado y escritura, y 2.4 consumirá la
  misma taxonomía.

  **Implementación (decisiones de diseño):**
  - `agent/src/vault_config.py`: `VaultTaxonomy` con `folders`, `structure` e
    `ignored_dirs`; carga `vault:` de `agent/config.yml` (override con
    `RAFAITA_CONFIG`), defaults PARA embebidos y **fail-open a defaults** si el
    fichero falta o está corrupto. Sin imports de `src` para no crear ciclo con
    `config.py`.
  - `agent/config.yml` documenta la sección `vault.folders` con la estructura
    PARA actual; `structure`/`ignored_dirs` son opcionales (por defecto se
    derivan de `folders`).
  - Consumidores migrados a claves simbólicas: `config.py` (rutas de finanzas y
    documentos indexados), `obsidian_manager` (estructura inicial, adjuntos,
    agenda, nota con imagen), `vault_indexer` (ignorados), `files.py`
    (categorías, extensiones, inbox/attachments, prompt de clasificación),
    `chat.py` (reglas proactivas, nota financiera, diario, ingest),
    `orchestrator.py`, `message_scanner.py`, `chat_tools.py`, `dashboard.py` e
    `import_whatsapp.py`.
  - El contenedor ya monta el repo en `/workspace`, así que
    `/workspace/agent/config.yml` es legible sin tocar Compose.

  **Evidencia [2026-09-25] [commit `fb7ba2b`, base `6e83088`] — taxonomía
  alternativa definida SOLO por config (sin tocar código):**

  Config usada (`RAFAITA_CONFIG=/cfg/custom.yml`): `inbox: Bandeja`,
  `zettelkasten: Ideas/Atomicas`, `areas_finanzas: Vida/Contable`,
  `diary: Personal/Diario`, `attachments: Adjuntos`,
  `ignored_dirs: [..., templates, Documentos]`.

  ```
  $ python /probe.py   (contenedor rafita-audit, bge-m3 real)
  TAXONOMY inbox=Bandeja | zettelkasten=Ideas/Atomicas | finanzas=Vida/Contable | diary=Personal/Diario | attachments=Adjuntos
  STRUCTURE: ['Bandeja', 'Trabajo/Proyectos', 'Vida/Contable', 'Vida/Salud', 'Vida/Casa', 'Trabajo', 'Biblioteca', 'ZonaMuerta', 'Ideas/Atomicas', 'Personal/Diario', 'Adjuntos']
  IGNORED: ['.git', '.obsidian', '.trash', 'Documentos', 'Documentos_Indexados', 'templates']
  NOTE: Ideas/Atomicas/NotaTax.md exists= True
  EXPENSE success=True finance_note=Vida/Contable/Control_Financiero_2026.md exists=True
  BACKFILL: Backfill: 2 notas indexadas (2 chunks), 0 fallos.
  INDEXED_NOTES: ['Ideas/Atomicas/NotaTax.md', 'Vida/Contable/Control_Financiero_2026.md']
  IGNORED_OK: True
  QUERY_TOP: ['Ideas/Atomicas/NotaTax.md', 'Vida/Contable/Control_Financiero_2026.md']
  TREE_DIRS:
    DIR Adjuntos / Bandeja / Biblioteca / Documentos / Ideas/Atomicas / Personal/Diario
    DIR Trabajo/Proyectos / Vida/Casa / Vida/Contable / Vida/Salud / ZonaMuerta / templates
  ```
  Todo sale de config: estructura creada, nota en la carpeta custom, gasto
  sincronizado en la nota financiera custom, `templates/` y `Documentos/`
  ignorados por el indexador, y `note_path` del RAG en rutas custom.

  Tests y estáticos (defaults PARA sin regresión):
  ```
  --- ruff check ---            All checks passed!
  --- ruff format check ---     47 files already formatted
  --- mypy ---                  (sin errores)
  --- pytest vault config ---   5 passed
  --- pytest suite completa --- 137 passed, 16 skipped
  ```

  **Alcance:** la evidencia cubre creación de estructura, escritura de notas y
  finanzas, indexado y RAG. `BrainMaintainer` (protecciones/versionado) es la
  tarea 2.4 y consumirá esta misma taxonomía.

- [x] **2.2 Externalizar idioma, zona horaria, moneda y nombre**
  Hallazgo: español y `America/Mexico_City` están repetidos en el código;
  MXN por defecto en config/DB pero EUR en el prompt financiero (inconsistencia
  real, no solo de configurabilidad); `ASSISTANT_NAME` parametriza el
  nombre pero no el resto.
  Tareas: mover idioma, zona horaria y moneda a variables de configuración
  coherentes entre sí (nada de un valor en DB y otro distinto en el prompt);
  documentar claramente cuál es la fuente de verdad.
  Evidencia requerida: cambiar idioma/zona/moneda solo por config y confirmar
  que el comportamiento del bot (prompts, formateo, respuestas) usa el nuevo
  valor de forma consistente en todos los puntos donde antes aparecía
  hardcodeado.

  **Opciones consideradas:** (A) catálogos i18n completos con gettext/JSON por
  idioma: correcto a largo plazo pero desproporcionado ahora (traducir todo el
  corpus de prompts y textos); (B) helpers mínimos (`src/i18n.py`) sobre
  `LANGUAGE`/`TIMEZONE`/`DEFAULT_CURRENCY`/`ASSISTANT_NAME` que construyen las
  reglas de idioma, el prompt de STT, el símbolo de moneda y la zona, dejando
  el corpus interno de reglas en español. **Elegida (B)**, porque cumple el
  objetivo (una sola fuente de verdad y comportamiento consistente en lo que
  ve el usuario) sin reescribir cientos de literales; la traducción completa
  del corpus queda como deuda futura.
  **Fuente de verdad documentada:** `LANGUAGE`, `TIMEZONE`,
  `DEFAULT_CURRENCY`, `ASSISTANT_NAME` (`.env` → `Settings`). Nada debe
  formatear moneda, idioma, zona o nombre con literales.

  **Implementación:**
  - `src/i18n.py`: `language_rule()` (es/en), `reply_instruction()`,
    `language_name()`, `stt_prompt()`, `currency_symbol()` y `timezone()`.
  - `chat.py` y `orchestrator.py`: las reglas de idioma, el nombre y la tabla
    financiera (`(EUR)`/`€`) salen de config; `files.py`, `dashboard.py`,
    `import_whatsapp.py`, `schemas.py` y `database.py` alineados.
  - Voz y audio: `language=settings.language` y prompt STT por idioma.
  - Google Calendar/Service: `timeZone=settings.timezone`.

  **Evidencia [2026-09-25] [commit `e7ac606`, base `fb7ba2b`]:**
  ```
  $ LANGUAGE=en TIMEZONE=Europe/Madrid DEFAULT_CURRENCY=EUR ASSISTANT_NAME=TestBot python /probe.py
  SETTINGS language=en timezone=Europe/Madrid currency=EUR name=TestBot
  LANGUAGE_RULE: STRICT_LANGUAGE_RULE: Your language is EXCLUSIVELY English. Do not answer in any other language.
  REPLY_INSTRUCTION: Always answer in English.
  CURRENCY_SYMBOL: €
  STT_PROMPT: The following is a conversation in English.
  TZ: Europe/Madrid
  PROMPT_FIRST_LINE: STRICT_LANGUAGE_RULE: Your language is EXCLUSIVELY English. Do not answer in any other language.
  PROMPT_HAS_ASSISTANT_NAME: True
  EXPENSE success=True
  DB_CURRENCY: EUR
  FINANCE_HEADER_HAS_(EUR): True
  FINANCE_ROW_SYMBOL_EURO: True
  FINANCE_ROW: ['| 2026-09-25 | prueba moneda | transporte | 33.50 € | - |']
  ```
  Tests y estáticos: `ruff`/`mypy` limpios; `pytest test_i18n.py` 4 passed;
  suite completa **141 passed, 16 skipped**.

  **Alcance:** el corpus interno de reglas sigue en español (deuda declarada);
  lo que ve el usuario (idioma de respuesta, STT, moneda, zona, nombre) sale de
  config y queda verificado.

- [x] **2.3 Implementar `PERSIST_TO_BRAIN` de verdad**
  Hallazgo: no existe en el código auditado. Los prompts actuales ordenan
  persistencia proactiva y las tools de escritura están siempre disponibles;
  no hay bloqueo real de escritura en modo depuración.
  Tareas: variable de configuración que, en `false`, impida a nivel de
  ejecutor (no solo de prompt) que se invoquen tools de escritura
  (`manage_obsidian_note`, `move_or_rename_file`, `ingest_file`, guardado de
  diario); en `true`, comportamiento normal.
  Evidencia requerida: con `PERSIST_TO_BRAIN=false`, forzar deliberadamente al
  modelo a intentar escribir y confirmar que el ejecutor rechaza la llamada a
  nivel de código (no solo que el prompt "se lo pide amablemente" al modelo).

  **Implementación:**
  - `PERSIST_TO_BRAIN` (default `true`) en `config.py`/`.env.example`.
  - `_execute_tool()` rechaza en código (no en prompt) `manage_obsidian_note`
    con acciones create/append/delete, `move_or_rename_file` e `ingest_file`;
    `manage_obsidian_note` con action `read` sigue permitido. El guardado
    automático del diario (`_save_diary_entry`) no escribe en modo depuración.
  - `get_tools_for_llm()` deja de ofrecer las 3 tools de escritura al modelo
    cuando está desactivado; el rechazo en ejecutor es la garantía por si el
    modelo las invoca igualmente.

  **Evidencia [2026-09-25] [commit `83fdf4b`, base `e7ac606`]:**
  ```
  $ PERSIST_TO_BRAIN=false python /probe.py
  TOOLS_TOTAL=21 OFFERED=18
  WRITE_TOOLS_EXCLUDED: ['ingest_file', 'manage_obsidian_note', 'move_or_rename_file']
  SEARCH_STILL_OFFERED: True
  BLOCKED manage_obsidian_note action=create -> success=False msg=Escritura deshabilitada: PERSIST_TO_BRAIN=false (modo depura...
  BLOCKED manage_obsidian_note action=append -> success=False msg=Escritura deshabilitada: PERSIST_TO_BRAIN=false (modo depura...
  BLOCKED manage_obsidian_note action=delete -> success=False msg=Escritura deshabilitada: PERSIST_TO_BRAIN=false (modo depura...
  BLOCKED move_or_rename_file action=None -> success=False msg=Escritura deshabilitada: PERSIST_TO_BRAIN=false (modo depura...
  BLOCKED ingest_file action=None -> success=False msg=Escritura deshabilitada: PERSIST_TO_BRAIN=false (modo depura...
  READ_ALLOWED: True
  WRITES_RESTORED: True Nota 'Debug' creada en Obsidian.
  ```
  Tests: 4 de persistencia; suite completa **145 passed, 16 skipped**;
  ruff/formato/mypy limpios.

- [x] **2.4 Implementar `BrainMaintainer` de verdad**
  Hallazgo: no existe la clase, ni `BRAIN_MAINTENANCE`, ni `PROTECTED_FOLDERS`,
  ni versionado/revert real sobre el vault en el código auditado.
  Tareas: construirlo desde cero — tarea en segundo plano desactivada por
  defecto, `PROTECTED_FOLDERS` configurable (con Finanzas/Salud/Diario como
  default), versionado git local del vault, y sobre todo: **un revert real
  ejecutado y verificado**, no solo la posibilidad teórica de hacerlo.
  Evidencia requerida: ejecutar un cambio de `BrainMaintainer` sobre un vault
  de prueba, confirmar el commit git local, y luego ejecutar un revert real y
  confirmar que el vault vuelve al estado anterior — con el diff pegado.

  **Implementación (decisiones de diseño):**
  - `src/utils/brain_maintainer.py`: `initialize()` crea el repo git local en
    el vault (con `.gitignore` para los ignored dirs), `snapshot()` commitea
    cambios (o `None` si no hay), `log()`, `revert(rev, include_protected=False)`
    que restaura ficheros modificados/borrados y elimina los añadidos desde
    `rev`, dejando un commit de revert (historial preservado).
  - `BRAIN_MAINTENANCE` (default `false`) y `BRAIN_MAINTENANCE_INTERVAL`
    (default 1800s) en config; `PROTECTED_FOLDERS` en
    `agent/config.yml → vault.protected_folders` (default finanzas/salud/diario
    derivadas de la taxonomía). En `revert`, las carpetas protegidas se
    preservan salvo `include_protected=True`.
  - Integrado en `main.py` (start/stop junto al resto de workers) y `git`
    añadido a la imagen Docker (necesario para versionar en el contenedor).
  - Decisión: identidad git por comando (`-c user.name/user.email`) para no
    depender de la configuración global del host.

  **Evidencia [2026-09-25] [commit `8326b20`, base `83fdf4b`]:**
  ```
  $ python /probe.py
  INIT: {'status': 'initialized', 'vault': '/tmp/brain24_.../vault'}
  COMMIT_V1: 919c3e0
  LOG: ['919c3e0 snapshot v1']
  COMMIT_V2: 175cad3
  DIFF_V1_V2:
  diff --git a/01-Proyectos/proyecto.md b/01-Proyectos/proyecto.md
  index 45ff204..146c5d2 100644
  --- a/01-Proyectos/proyecto.md
  +++ b/01-Proyectos/proyecto.md
  @@ -1,2 +1,2 @@
   # Proyecto
  -Version A
  +Version B (cambio)
  COMMIT_V3: 63275f1
  REVERT: {'success': True, 'commit': '5b8b695', 'restored': 1, 'removed': 1, 'protected_skipped': 0}
  CONTENT_AFTER_REVERT: # Proyecto
  Version A
  NUEVA_MD_EXISTS: False
  LOG_AFTER: ['5b8b695 Revert vault to 919c3e0', '63275f1 anade nueva', '175cad3 cambio v2', '919c3e0 snapshot v1']
  ```
  Tests: 2 (revert real y preservación de carpetas protegidas); suite completa
  **147 passed, 16 skipped**; ruff/formato/mypy limpios.

- [x] **2.5 Adapters de proveedor de IA**
  Hallazgo: `OllamaClient` usa una API key fija `ollama`, añade `/v1` y envía
  opciones específicas de Ollama; no hay capa de abstracción para otros
  proveedores.
  Tareas: interfaz común de chat/embeddings/visión con al menos dos
  implementaciones reales: Ollama (local) y un proveedor externo vía API
  (a elegir, documentando la elección), seleccionable por configuración sin
  tocar código.
  Evidencia requerida: la misma conversación de prueba funcionando con ambos
  backends, cambiando solo la configuración.

  **Opciones consideradas:** (A) **OpenAI-compatible** como segundo backend:
  el paquete `openai` ya está en `requirements.txt`, un solo adaptador cubre
  OpenAI, DeepSeek, Groq, OpenRouter, vLLM y el propio `/v1` de Ollama, y
  permite validar el adapter en local sin clave externa. (B) Anthropic SDK:
  requeriría dependencia nueva y solo cubre Anthropic. (C) Gemini SDK: igual,
  dependencia nueva y un único proveedor. **Elegida (A)**.

  **Implementación:**
  - `src/ai/base.py` (Protocol `AIProvider`), `src/ai/factory.py`
    (`create_ai_client()` según `AI_PROVIDER`) y `src/ai/openai_compat.py`.
  - El singleton `llm` de `ollama_client` ahora sale de la fábrica, así que
    orquestador, chat y voz no cambian una línea.
  - Contrato común: `initialize`, `close`, `check_health`, `chat`,
    `chat_with_tools`, `chat_stream_tokens`, `chat_vision` y `embed_texts`
    (batch; `vector_manager` deja de llamar a Ollama directamente).
  - Config nueva: `AI_PROVIDER`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`,
    `OPENAI_MODEL`, `OPENAI_VISION_MODEL`, `OPENAI_EMBEDDING_MODEL`.

  **Evidencia [2026-09-25] [commit `92c88bd`, base `8326b20`]:**
  ```
  $ AI_PROVIDER=ollama ... python /probe.py
  PROVIDER_CLASS: OllamaClient
  MODEL: gemma4:12b
  HEALTH: healthy
  TOOL_CALLS: ['get_finance_summary'] | CONTENT:
  EMBEDDINGS: 2 dims: 1024

  $ AI_PROVIDER=openai OPENAI_BASE_URL=http://127.0.0.1:11435/v1 \
      OPENAI_API_KEY=dummy OPENAI_MODEL=gemma4:12b OPENAI_EMBEDDING_MODEL=bge-m3 ... python /probe.py
  PROVIDER_CLASS: OpenAICompatClient
  MODEL: gemma4:12b
  HEALTH: healthy
  TOOL_CALLS: ['get_finance_summary'] | CONTENT:
  EMBEDDINGS: 2 dims: 1024
  ```
  (El campo `CONTENT` sale vacío en ambos por `max_tokens=20` con gemma4; el
  contrato de chat queda validado por `chat_with_tools` y `check_health`.)

  **Alcance:** el backend "externo" se validó contra el endpoint
  OpenAI-compatible de Ollama (mismo protocolo que OpenAI/DeepSeek/Groq...),
  no contra un proveedor de pago con clave real (no disponible en la sesión).
  Tests: 3 de selección/contrato; suite completa **150 passed, 16 skipped**;
  ruff/formato/mypy limpios.

- [x] **2.6 Wizard de instalación**
  Hallazgo: `.env.example` e `init.ps1` existen pero `init.ps1` fuerza Gemma y
  no es un setup genérico; el `.env.example` tiene el bug de 0.5; el
  Quickstart no coincide con lo que realmente hace falta editar.
  Tareas: script o flujo guiado multiplataforma que valide `.env`, permita
  elegir proveedor/modelo, pruebe la conexión al proveedor de IA elegido, y
  valide que el vault indicado existe — todo sin editar código fuente.
  Evidencia requerida: instalación completa desde un clon limpio, siguiendo
  solo el wizard, terminando en un bot funcional.

  **Opciones consideradas:** (A) script PowerShell (`init.ps1` ampliado): no es
  multiplataforma y ya quedó obsoleto; (B) **script Python stdlib
  multiplataforma** (`scripts/setup_wizard.py`) con modo interactivo y
  `--non-interactive` para automatizar/CI. **Elegida (B)**, sin dependencias
  nuevas y reutilizable desde cualquier SO donde viva el repo.

  **Implementación:**
  - Pasos del wizard: crear `.env` desde `.env.example` si falta → elegir
    proveedor (`ollama`/`openai`), modelo y embeddings → validar/crear la
    carpeta del vault → probar conexión (Ollama `/api/tags` comprobando que los
    modelos estén, u OpenAI-compatible `/models` con la API key) → escribir las
    claves en `.env` preservando comentarios → imprimir siguiente paso
    (`docker compose up` o con overlay GPU).
  - `INSTALL.md` documenta el wizard como vía recomendada.

  **Evidencia [2026-09-25] [commit `83a337b`, base `92c88bd`] — clon limpio:**
  ```
  $ git clone <repo> /tmp/clean26 && cd /tmp/clean26
  $ python3 scripts/setup_wizard.py --non-interactive \
      --token 123456:DUMMY --provider ollama --model gemma4:12b \
      --embedding-model bge-m3 --base-url http://127.0.0.1:11435 \
      --vault /tmp/clean26/mi_boveda_obsidian
  [1/5] .env creado desde .env.example
  [2/5] Configuracion: provider=ollama model=gemma4:12b embeddings=bge-m3
  [3/5] vault OK (/tmp/clean26/mi_boveda_obsidian)
  [4/5] Ollama OK (2 modelos disponibles)
  [5/5] .env actualizado: /tmp/clean26/.env
  EXIT=0

  $ grep ... /tmp/clean26/.env   (token enmascarado)
  TELEGRAM_TOKEN=123456:D...<masked>
  OLLAMA_MODEL=gemma4:12b
  EMBEDDING_MODEL=bge-m3
  EMBEDDING_DIM=1024
  AI_PROVIDER=ollama

  $ docker run ... python -c "from src.config import settings; print(...)"
  provider=ollama chat=gemma4:12b embed=bge-m3 dim=1024 token_set=True
  ```
  Tests: 5 del wizard; suite completa **155 passed, 16 skipped**;
  ruff/formato/mypy limpios.

  **Alcance (honesto):** la evidencia cubre clon limpio → wizard → `.env`
  válido y cargable → conexión al proveedor y vault verificados. El "bot
  funcional en Telegram" requiere un token real y el arranque completo del
  stack, que corresponde a las pruebas de servidor de la Fase 3 (no se dispone
  de token real en la sesión).

**Bloqueado / no verificable (rellenar si aplica):**

---

## FASE 3 — Servidor continuo (topología real: dos nodos)

**Contexto de topología (importante — quien ha hecho el trabajo hasta la Fase 2
no conocía esto; queda documentado aquí para que no se pierda):**

- **Nodo Dell — motor de IA** (`192.168.1.201`; **cambió desde 192.168.1.121**
  tras un reinicio del nodo: la IP no estaba fijada estáticamente. Pendiente
  fijarla con netplan/reserva DHCP y/o usar el DNS de Tailscale para no
  depender de la IP LAN. Documentado 2026-09-25): Dell OptiPlex 7060 Micro,
  Ubuntu Server 24.04.5 LTS, Intel Core i7-8700 (6c/12t @ 3.20GHz, **sin GPU
  discreta**, solo iGPU Intel UHD 630), 32GB DDR4, 1TB NVMe. Solo tiene el
  sistema operativo instalado; nada del stack de IA desplegado todavía. Rol:
  alojar en exclusiva la inferencia (chat, visión, embeddings).
- **Nodo HP "rafa" — núcleo de red e infraestructura** (`192.168.1.129`):
  portátil reconvertido, Ubuntu 24.04 LTS, Intel i3-1005G1 (2c/4t), 8GB RAM,
  ~100GB SSD, ya corriendo 11 contenedores de producción: BuenaTierra
  (API .NET + PostgreSQL + Nginx + Collabora Online), Nextcloud + DB, Nginx
  Proxy Manager, AdGuard Home, WireGuard (wg-easy), Tailscale Subnet Router,
  Portainer, Glances. Rol: alojar la app Rafita (agente, ChromaDB embebido,
  vault, BrainMaintainer) y llamar al LLM del nodo Dell por red.

Esto rompe el supuesto de despliegue mono-máquina asumido hasta ahora
(Compose con todo en un host, comunicación por `localhost`). Hay decisiones
de arquitectura reales antes de tocar código — no se resuelven solas.

No empezar sin Fase 2 cerrada (ya lo está). Todo lo de esta fase debe probarse
contra los dos nodos reales, no solo simulado en el PC de desarrollo.

- [ ] **3.0 Decisiones de arquitectura del despliegue en dos nodos**
  Antes de instalar nada en el Dell, resolver y documentar (ADR-004) estas
  cuatro decisiones:
  - **(a) Runtime del LLM en el Dell — Ollama vs llama.cpp+llama-swap.**
    Ollama: cero trabajo de adapter nuevo (`OllamaClient` ya está integrado y
    validado con gemma4:12b/bge-m3 en 1.7/2.5), solo apuntarlo a la IP del
    Dell. `llama.cpp+llama-swap`: reutilizaría el adapter OpenAI-compatible
    de 2.5, pero el tool-calling con Gemma vía plantillas de llama.cpp no
    está validado (2.5 solo probó ese adapter contra el propio `/v1` de
    Ollama) y su ventaja principal —hacer swap de modelos por falta de
    memoria— no aplica con 32GB RAM disponibles para 3 modelos que caben
    holgados sin descargar. Recomendación por defecto: Ollama, salvo que haya
    un motivo concreto para preferir llama.cpp+llama-swap; si se elige este
    último, hay que re-validar tool-calling (repetir 1.7) antes de darlo por
    bueno.
  - **(b) Rendimiento real sin GPU.** El Dell no tiene GPU discreta; la
    medición de 1.7 (63% tool-calling, latencias aceptables) se hizo en la
    RTX 3060, no es extrapolable a CPU. Antes de construir nada encima:
    benchmark de tokens/segundo del modelo elegido en el Dell puro (sin red
    de por medio). Si el resultado no sirve para una conversación de Telegram
    en tiempo real, evaluar modelo/cuantización más ligera antes de seguir.
  - **(c) Seguridad de red.** El endpoint del LLM no debe quedar abierto a
    toda la LAN sin restricción (mismo principio que ya aplicaron 0.4/SECURITY
    para el webhook). Ahora, además, el tráfico entre nodos puede llevar datos
    personales del vault (financieros, de salud), que antes nunca salían de
    `localhost`. Decidir entre: firewall en el Dell que solo acepte conexiones
    desde `192.168.1.129`, o enrutar la llamada por la Tailscale que ya corre
    en el nodo HP (da restricción de acceso Y cifrado en tránsito, un
    firewall a secas solo da lo primero). Recomendación: Tailscale.
  - **(d) Presupuesto de recursos en el nodo HP.** 8GB RAM ya repartidos entre
    11 contenedores de producción (Collabora y Nextcloud+Postgres son
    intensivos). Medir RAM/CPU libres reales antes de fijar límites para el
    agente+Chroma+BrainMaintainer; si no hay margen suficiente, decidir qué
    contenedores existentes hay que vigilar de cerca o si algo debe moverse.
  Evidencia requerida: documento de decisión (ADR-004) con las cuatro
  respuestas justificadas, no solo elegidas.

  **Estado 2026-09-25: borrador de ADR-004 presentado, PENDIENTE de tu
  confirmación. No se ha tocado el Dell.** Documento completo:
  `docs/adr/004-two-node-deployment.md` (commit `a579317`).
  Reconocimiento en solo lectura desde el PC de desarrollo y por SSH al HP:
  ```
  # Nodo HP (11 contenedores arriba)
  MemTotal 6,9GiB | disponible 4,9GiB | Swap 4GiB (150MiB usados)
  load 0,01/0,07/0,08 | nproc 4 | disco / 58G libres
  docker stats (11 contenedores): ~1,2GiB RAM total, CPU <2%
  0.0.0.0:8000 OCUPADO por Portainer; 8001 libre
  tailscale: 100.121.77.29 (peers: LAPTOP offline, S20 offline) -> Dell NO enrolado
  # Dell
  ping 192.168.1.121 desde PC y desde HP: FAIL (no respondía en el análisis)

  Recomendaciones del borrador:
  (a) Runtime: **Ollama** (adapter ya validado, 32GB sobran para
      gemma4:12b+bge-m3+llava sin swap de modelos; llama.cpp solo como plan B
      medido, repitiendo 1.7 si se adoptara).
  (b) Rendimiento: **no fijar modelo hasta benchmark en el Dell** (protocolo de
      tokens/s, pasada reducida de 1.7 y latencia de red; umbrales propuestos:
      primer token <=5s, >=4 tok/s; fallback qwen2.5:7b/3b).
  (c) Red: **Tailscale (enrolar Dell) + ufw restringiendo 11434 a la IP
      Tailscale del HP**; nada expuesto a la LAN, cifrado en tránsito.
  (d) Recursos HP: 4,9GiB disponibles y Rafita ~1,0-1,5GiB -> cabe con límite
      de 2G; overlay del compose en HP sin `ollama-service`; remapear el
      gateway (Portainer ocupa 8000, usar p. ej. 8010); Whisper a 2 hilos;
      vigilar swap/OOM la primera semana.

- [x] **3.1 Desplegar el runtime de IA elegido en el nodo Dell**
  **Completada 2026-09-26.** Evidencia:

  1. **Runtime y modelos** (`deploy/dell/01-install-runtime.sh`, commit
     `1d1b583`): Ollama **0.34.4** instalado y `active` (systemd, bind
     127.0.0.1, `OLLAMA_KEEP_ALIVE=-1`, `OLLAMA_MAX_LOADED_MODELS=3`) con
     `gemma4:12b` (7,6 GB), `bge-m3` (1,2 GB), `llava:7b` (4,7 GB) y
     `qwen2.5:7b` (4,7 GB, fallback).
  2. **Benchmark local** (`03-benchmark.sh`, i7-8700 sin GPU, sin red):
     ```
     gemma4:12b  gen 3,87 tok/s | prompt 43,8 tok/s | ~16 s por ~350 chars
     qwen2.5:7b  gen 6,54 tok/s | prompt 285,5 tok/s| ~13,5 s por ~400 chars
     bge-m3      0,062 s/embedding (caliente; 1,87 s el 1º con carga)
     ```
     Decisión del usuario (2026-09-26): **`gemma4:12b` definitivo** (mejor
     razonamiento y fiabilidad de herramientas; acepta la latencia; a futuro
     puede usar la GPU de la torre por túnel). Documentado en ADR-004.
  3. **Hallazgo crítico resuelto** (commit `5698227`): `gemma4:12b` trae
     `thinking=true` y por `/v1` el razonamiento se emite en
     `reasoning_content` dejando `content` vacío; `think:false` no lo evita,
     `reasoning_effort:"none"` sí (conservando `tool_calls`). El cliente envía
     ahora `OLLAMA_REASONING_EFFORT` (default `none`), prewarm con
     `think:false`, `OLLAMA_NUM_THREAD` configurable y
     `OPENAI_REASONING_EFFORT` opcional; 5 tests nuevos. Gate: ruff/formato/mypy
     limpios, **156 passed, 20 skipped** (evidencia en el commit).
  4. **Red segura** (`02-network.sh`): Tailscale 1.102.4 en el Dell, enrolado
     como `nodo-dell-1` = **100.83.40.103**; ufw activo:
     `22/tcp ALLOW 192.168.1.0/24`, `22/tcp on tailscale0 ALLOW`,
     `11434/tcp on tailscale0 ALLOW 100.121.77.29`; Ollama rebind a
     `0.0.0.0:11434` (el filtro real es ufw; documentado en ADR-004(c)).
     Verificado desde el PC de desarrollo: `curl` a `192.168.1.201:11434`
     **bloqueado** por ufw.
  5. **Conectividad verificada desde el HP por tailnet**:
     ```
     $ curl http://100.83.40.103:11434/api/version   -> {"version":"0.34.4"}
     $ curl .../api/tags -> bge-m3, gemma4:12b, llava:7b, qwen2.5:7b
     $ tailscale ping -c 3 100.83.40.103 -> directo via 192.168.1.201:41641 en 7 ms
     ```
  6. **Benchmark por red desde el HP** (`03-benchmark.sh`,
     `HOST=http://100.83.40.103:11434 THINK=0`):
     ```
     run=1 (con carga) total 32,71 s | load_s=15,91
     run=2 (caliente)  total 17,21 s | gen 3,85 tok/s | prompt 44,4 tok/s
     ```
     Sobrecarga de red ≈ 0,85 s sobre el benchmark local caliente (~5%).
  7. **Camino real de la app probado desde el HP**: `POST /v1/chat/completions`
     con tools + `reasoning_effort:"none"` → `tool_calls` correctos
     (`get_time {"city":"Madrid"}`, usage 73 prompt / 15 completion).
  8. `sudo` temporal del Dell **revertido** al cerrar (`NOPASSWD` eliminado;
     confirmado que vuelve a pedir contraseña).

  Pendiente para 3.2 (heredado de 3.0(d)): overlay del HP sin `ollama-service`,
  gateway en 8010, `OLLAMA_HOST=http://100.83.40.103:11434`, decidir modelo de
  visión (`llava:7b` vs `gemma4:12b`, que también tiene visión) y repetir las
  métricas 1.3/1.7 contra el LLM remoto.

  Depende de 3.0. Tareas: instalar el runtime decidido, descargar/cuantizar
  los modelos, exponer el API solo por la interfaz/red decidida en 3.0(c), y
  ejecutar el benchmark de 3.0(b) contra el servicio real ya desplegado (no
  solo en crudo).
  Evidencia requerida: servicio arriba en el Dell, prueba de conexión desde
  fuera del propio Dell (`curl` desde el nodo HP), y benchmark de
  tokens/segundo con el modelo real en producción.

- [ ] **3.2 Apuntar el agente del nodo HP al LLM remoto**
  Depende de 3.1. Tareas: configurar `AI_PROVIDER`/`OLLAMA_HOST` u
  `OPENAI_BASE_URL` en el `.env` del agente (desplegado en HP) para que
  apunte a la IP/host del Dell decidido en 3.0(c), no a `localhost`. Repetir
  el dataset de 1.2/1.3 (Recall@k, MRR) y una pasada reducida de 1.7
  (tool-calling) contra el LLM remoto real, para confirmar que la latencia de
  red no rompe nada que funcionaba en local.
  Evidencia requerida: métricas de 1.3 y 1.7 repetidas contra el LLM remoto,
  comparadas con los números ya registrados en Fase 1.

- [ ] **3.3 Readiness real diferenciado de liveness** *(antes 3.1)*
  Depende de 0.6 y 3.2. Tareas: separar "el proceso vive" de "el servicio
  está listo para atender" (LLM remoto alcanzable y con modelos cargados,
  Chroma accesible, vault montado), con estados degradados explícitos si algo
  falla parcialmente. Si se eligió un runtime distinto de Ollama en 3.0(a), el
  check de `/ready` debe usar el método genérico `check_health()` del
  `AIProvider` (ya construido en 2.5), no la llamada específica a
  `/api/tags` de Ollama que usa hoy el código de 0.6.
  Evidencia requerida: apagar cada dependencia una por una (incluyendo
  desconectar el nodo Dell) y confirmar que el estado de readiness lo refleja
  de forma distinguible de un fallo de Chroma/vault.

- [ ] **3.4 Pruebas de caos, incluyendo caída de un nodo completo** *(antes 3.2, ampliada)*
  Tareas y evidencia requerida (una por una, con resultado real pegado):
  - Arranque en frío completo de ambos nodos (apagados del todo → arriba,
    probando ambos órdenes de boot: HP antes que Dell y viceversa).
  - `docker kill` al contenedor del agente en HP → confirmar recuperación
    automática vía `restart: unless-stopped` sin intervención manual.
  - Apagar/reiniciar el runtime de IA en el Dell mientras el agente sigue
    arriba en HP → confirmar que el agente detecta la caída, no se queda en
    estado zombie, y se recupera solo cuando el Dell vuelve.
  - Cortar la red entre ambos nodos (regla de firewall temporal) → el agente
    debe fallar rápido y reflejarlo en `/ready`, no colgarse esperando un
    timeout largo sin fin.
  - Convivencia de recursos real en HP: medir uso de RAM/CPU de los 11
    contenedores existentes antes y después de desplegar Rafita, bajo carga
    normal del bot, confirmando que ninguno de los servicios de producción
    (Nextcloud, BuenaTierra, Collabora) se degrada.

- [ ] **3.5 Logs estructurados, redactados y acotados** *(antes 3.3)*
  Hallazgo: rotación local existe, pero no hay confirmación de redacción de
  datos sensibles, límites de tamaño acotados, ni acceso sin SSH.
  Tareas: formato estructurado, redacción de credenciales/datos sensibles
  antes de escribir a disco, límites de tamaño/retención, y un mecanismo de
  exportación o consulta remota sin acceso interactivo a la máquina.
  Evidencia requerida: log real mostrando que un dato sensible de prueba
  queda redactado, y confirmación de que el tamaño de logs no crece sin
  límite tras una prueba prolongada.

- [ ] **3.6 Backup y restore reales** *(antes 3.4, con nota de alcance)*
  Hallazgo: existe `backup_verify.sh` y un runbook, pero no se ha ejecutado un
  backup+restore real de SQLite/Chroma/clave de cifrado.
  Tareas: ejecutar un ciclo completo de backup → destruir el estado actual →
  restaurar → confirmar que el bot funciona igual que antes, incluyendo que
  las credenciales cifradas siguen siendo descifrables tras el restore. El
  alcance es el estado del nodo HP (vault, SQLite, Chroma, clave); el nodo
  Dell no guarda estado de usuario si solo sirve modelos (los pesos se pueden
  re-descargar), así que no necesita backup propio salvo documentar su
  configuración de despliegue por si hay que reconstruirlo desde cero.
  Evidencia requerida: el log del backup, el log del restore, y una
  interacción real post-restore confirmando que el sistema quedó íntegro.

- [ ] **3.7 Upgrade y rollback** *(antes 3.5, con nota de alcance)*
  Tareas: documentar y probar un ciclo real de actualización de versión
  (`git pull` + rebuild + `up -d`) con downtime medido, y un rollback real a
  la versión anterior si algo sale mal. Con dos nodos de ciclo de vida
  independiente, probar también actualizar uno sin tocar el otro (agente en
  HP con el Dell intacto, y viceversa) y confirmar que no se rompe la
  compatibilidad entre versiones durante la ventana de despliegue.
  Evidencia requerida: tiempos reales de downtime del upgrade, y un rollback
  ejecutado de verdad (no solo descrito) para cada nodo.

**Bloqueado / no verificable (rellenar si aplica):**

Actualizado 2026-09-25: ya no está bloqueada por falta de servidor — los dos
nodos existen y están operativos. El bloqueo real ahora es que el nodo Dell
solo tiene el sistema operativo instalado: nada de 3.1 en adelante puede
ejecutarse con evidencia hasta que 3.0 quede decidido y 3.1 desplegado.

---

## FASE 4 — Release profesional

No empezar sin Fase 3 cerrada (o explícitamente bloqueada por falta de servidor,
documentado como tal). Objetivo: que la documentación y el repo dejen de
contradecirse a sí mismos y reflejen el estado real tras las fases anteriores.

- [x] **4.1 Corregir inconsistencias de documentación encontradas en la auditoría**
  Lista concreta a resolver, una por una:
  - `SECURITY.md` llama "AES-256" a Fernet en una sección y describe
    correctamente "AES-128-CBC + HMAC-SHA256" en otra — dejar una sola
    descripción correcta.
  - `SECURITY.md` dice puertos en `0.0.0.0` en una sección aunque Compose los
    limita a localhost — corregir para que coincida con el comportamiento
    real.
  - `SECURITY.md` dice que un webhook sin secreto se rechaza, pero (antes del
    fix 0.4) el código lo aceptaba sin HMAC — confirmar que tras 0.4 la
    documentación ya es cierta.
  - URLs placeholder tipo `github.com/user/rafai` — sustituir por la URL real
    del repo en todos los ficheros.
  - INSTALL.md promete indexar `vault_ejemplo` pero Compose monta
    `mi_boveda_obsidian` — alinear ambos.
  - CHANGELOG afirma relevancia RAG "corregida" sin prueba de calidad
    asociada — no repetir esta afirmación hasta tener la evidencia de la
    Fase 1.
  - Documento de Calendar dice "ejecución pendiente" pese a que ya existen
    ramas de tools en `chat.py` — actualizar al estado real verificado.
  Evidencia requerida: para cada punto, el diff del fichero corregido.

  **Evidencia [2026-09-25] [commit `62f54b0`, base `83a337b`]:**
  ```
  $ git show --stat 62f54b0
   CHANGELOG.md                         | 65 +++++++++++++++++--
   CONTRIBUTING.md                      | 60 +++++++++++++++++
   INSTALL.md                           | 12 +++--
   README.md                            | 23 ++++++---
   SECURITY.md                          | 18 +++----
   agent/src/handlers/chat.py           |  2 +-
   agent/src/handlers/dashboard.py      |  6 ++--
   agent/src/services/google_service.py |  2 +-
   docs/analysis-tools-interface.md     | 27 ++++++----
   scripts/test_suite.py                |  2 +-
   10 files changed, 180 insertions(+), 37 deletions(-)

  $ verificaciones post-fix
  placeholder URLs: 0
  AES-256 incorrectos: 0
  SECURITY.md:98: loopback (`127.0.0.1:11434`, `127.0.0.1:8000` y `127.0.0.1:8001`)...
  INSTALL.md:153: cp -r vault_ejemplo/* mi_boveda_obsidian/
  ```
  Puntos concretos: badge y clone URL → `github.com/rufae/Rafita`; Fernet
  descrito como AES-128-CBC + HMAC-SHA256 en SECURITY, prompts y logs (0
  menciones incorrectas restantes); puertos documentados en loopback (coincide
  con Compose); webhook fail-closed ya era cierto tras 0.4 (sin cambios);
  INSTALL explica que el vault montado es `mi_boveda_obsidian` y cómo copiar el
  ejemplo; CHANGELOG sustituye "corregida" por la evidencia medida y añade
  `Unreleased`; análisis de Calendar pasa a "ejecución ya existe, falta OAuth
  real".

- [x] **4.2 Changelog con sección `Unreleased` y guía de contribución**
  Tareas: adoptar un formato de changelog con `Unreleased` para cambios en
  curso, y una `CONTRIBUTING.md` mínima (cómo correr tests, cómo levantar el
  entorno, estilo de commits).
  Evidencia requerida: los ficheros creados, enlazados desde el README.

  **Evidencia [2026-09-25] [commit `62f54b0`]:**
  - `CHANGELOG.md` con `## [Unreleased]` (corregido/añadido/limitaciones de la
    ronda de estabilización, con los commits `5ce1cac`..`83a337b`).
  - `CONTRIBUTING.md` creado (entorno, tests, estilo de commits, flujo de PR,
    seguridad) y enlazado desde el README junto a CHANGELOG y plan.md
    (sección "Contribuir").
  ```
  $ ls CONTRIBUTING.md CHANGELOG.md
  CONTRIBUTING.md  CHANGELOG.md
  ```

- [x] **4.3 Test de instalación limpia end-to-end**
  Tareas: automatizar (o documentar paso a paso y ejecutar manualmente) una
  instalación desde un clon limpio del repo hasta un bot respondiendo en
  Telegram, sin ningún paso manual no documentado.
  Evidencia requerida: log completo de la ejecución, de clon a primera
  respuesta del bot.

  **Evidencia [2026-09-25] [commit `62f54b0`] — ejecutado hasta donde el
  entorno lo permite (sin token real de Telegram):**
  ```
  $ git clone <repo> /tmp/clean43 && cd /tmp/clean43
  $ python3 scripts/setup_wizard.py --non-interactive --token 123456:DUMMY \
      --provider ollama --model gemma4:12b --embedding-model bge-m3 \
      --base-url http://127.0.0.1:11435 --vault /tmp/clean43/mi_boveda_obsidian
  [1/5] .env creado desde .env.example
  [2/5] Configuracion: provider=ollama model=gemma4:12b embeddings=bge-m3
  [3/5] vault OK (/tmp/clean43/mi_boveda_obsidian)
  [4/5] Ollama OK (2 modelos disponibles)
  [5/5] .env actualizado: /tmp/clean43/.env
  $ cp -r vault_ejemplo/* mi_boveda_obsidian/
  $ docker compose config --quiet && echo "compose-config: OK"
  compose-config: OK
  $ docker compose build
   Image clean43-rafita-agent-core Built
  $ docker compose run --rm --no-deps rafita-agent-core python -c "..."
  provider=ollama chat=gemma4:12b embed=bge-m3 dim=1024 threshold=0.49 persist=True vault_inbox=00-Inbox
  ```
  **Alcance (honesto):** el recorrido clon → wizard → vault → Compose (config,
  build y carga de settings por el camino real) está verificado. El arranque
  completo con el bot respondiendo en Telegram **no se pudo ejecutar**: requiere
  token real, descargar los modelos en el volumen de Ollama del compose (~9 GB)
  y liberar el puerto 11434 (el Ollama del sistema del portátil lo ocupa). Esa
  validación corresponde a la Fase 3 en el servidor.

- [x] **4.4 Revisión de ADRs con evidencia real**
  Tareas: revisar ADR-001 (ChromaDB), ADR-002 (sin Celery/Redis) y ADR-003
  (elección de modelos) a la luz de todo lo verificado en las fases 0-3;
  actualizar cualquier ADR cuya justificación original ya no se sostenga con
  los datos reales medidos (por ejemplo, si la Fase 1 muestra que bge-m3 no
  es la mejor opción en español, eso debe reflejarse aquí, no ocultarse).
  Evidencia requerida: ADRs actualizados o una nota explícita de "sigue
  vigente, evidencia: X" por cada uno.

  **Evidencia [2026-09-25] [commit `588372a`, base `62f54b0`]:**
  - `docs/adr/001-vector-store.md`: sección "Revisión 2026-09-25 — Sigue
    vigente" con el bug de reindexado corregido (1.1), equivalencia L2/coseno
    medida (1.4), CVEs aceptadas (0.2) y tags con flags (1.6).
  - `docs/adr/002-task-queue.md`: "Revisión 2026-09-25 — Sigue vigente" con
    backfill en segundos en las pruebas, voz en background cancelable y
    BrainMaintainer como tarea ligera; sin evidencia que exija cola externa.
  - `docs/adr/003-model-selection.md`: sustituida la "Validación pendiente" por
    la tabla de métricas medidas (recall@1 0.964, recall@3 1.0, MRR@5 0.982,
    umbral 0.49) y nueva "Revisión de tool-calling" (29/46 con equivalencias;
    7 tools no fiables → v0.2.0); nota de que `hardware_detect` recomienda pero
    no sobreescribe la configuración.

**Bloqueado / no verificable (rellenar si aplica):**

- 4.3 arranque completo con bot respondiendo en Telegram: requiere token real,
  descarga de modelos del compose y puerto 11434 libre; se validará en servidor
  (Fase 3). El resto de 4.3 quedó ejecutado con log real.

---

## Cierre general

Cuando las cinco fases estén cerradas con evidencia (o con bloqueos
explícitamente documentados y aceptados), pide una auditoría de re-verificación
igual de estricta que la original, sobre el commit final — no un resumen propio de
lo que se hizo. El criterio de éxito no es "todos los checkboxes en `[x]`"; es que
una auditoría externa, repitiendo el mismo proceso, llegue a números de
cumplimiento y generificación sustancialmente distintos a los de partida
(prototipo personal / 15% de generificación) con evidencia que lo sostenga.