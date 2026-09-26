# Seguridad y Privacidad — Rafita AVP

Rafita AVP es 100% local. No hay nube, no hay telemetría, no hay servidores externos.
Cada instalación es de un único usuario con sus propios datos. Este documento describe
el modelo de amenaza, las protecciones implementadas y las configuraciones recomendadas.

---

## Modelo de amenaza

### Qué protegemos

| Activo | Formato | Riesgo principal |
|---|---|---|
| Vault de Obsidian (`mi_boveda_obsidian/`) | Markdown plano en disco | Acceso no autorizado al contenido del segundo cerebro (DNI, historial médico, finanzas, notas personales) |
| Base de datos SQLite (`data/db/rafita.db`) | SQLite con historial de chat + credenciales | Exposición de conversaciones y claves cifradas |
| Credenciales (API keys, contraseñas) | Fernet (AES-128-CBC + HMAC-SHA256) en SQLite | Robo de claves si se rompe el cifrado |
| Vector DB (`data/vector_db/`) | ChromaDB con embeddings | Reconstrucción parcial del contenido del vault vía embeddings |
| `.env` | Texto plano con tokens y configuración | Exposición del token de Telegram y claves de API |

### Qué NO protegemos (fuera del alcance)

- **Ataques en caliente**: si un atacante tiene acceso al sistema mientras Rafita está corriendo (con los volúmenes montados y las claves en memoria), puede leer los datos. Esto aplica a cualquier sistema de cifrado en reposo.
- **Malware en el host**: si el sistema operativo está comprometido, el atacante puede leer cualquier archivo que el usuario pueda leer.
- **Ingeniería social / phishing**: Rafita no protege contra ataques que engañen al usuario para revelar información.
- **Análisis de tráfico de red**: aunque Rafita solo expone puertos localmente, un atacante con acceso a la red local podría interceptar tráfico si los puertos se exponen por error (ver sección de red más abajo).

---

## Cifrado en reposo

### Capa 1: Cifrado de disco (recomendado para todas las instalaciones)

El vault de Obsidian contiene notas en texto plano con datos personales sensibles.
La protección más efectiva y transparente es el **cifrado de disco completo** a nivel
de sistema operativo:

| Sistema | Herramienta | Comando/Configuración |
|---|---|---|
| **Windows** | BitLocker | `Manage BitLocker` → Encrypt drive |
| **Linux** | LUKS (dm-crypt) | `cryptsetup luksFormat /dev/sdX` |
| **macOS** | FileVault | System Preferences → Security → FileVault |
| **Servidor/NAS** | LUKS o VeraCrypt | Montar volumen cifrado para `/data/` |

**Por qué cifrado de disco y no por archivo:**
- Transparente: Rafita no necesita saber que el disco está cifrado. El SO maneja la clave al iniciar sesión.
- Cubre TODO: vault, SQLite, ChromaDB, logs, `.env` — sin excepciones.
- Sin cambios de código: no hay que modificar Rafita para añadir cifrado.
- Probado en batalla: BitLocker/LUKS/FileVault tienen décadas de auditorías de seguridad.

**Limitación**: no protege contra un atacante con acceso al sistema en caliente (sesión abierta).

### Capa 2: Cifrado de credenciales (implementado en Rafita)

Las API keys, contraseñas y tokens guardados con `/guardar_clave` se cifran con
**Fernet (AES-128-CBC + HMAC-SHA256)** antes de almacenarse en SQLite.

- Clave maestra: generada automáticamente en el primer arranque, almacenada en `ENCRYPTION_KEY` dentro de `.env`.
- Cada credencial se cifra individualmente con la misma clave maestra.
- Los valores NUNCA se muestran completos en las respuestas del bot (solo enmascarados).
- Si un atacante obtiene el archivo `.db` pero no el `.env`, no puede descifrar las credenciales.

**Procedimiento de rotación de clave Fernet**

Si necesitas rotar la clave de cifrado (por compromiso sospechado o mantenimiento periódico):

1. **Haz backup** de `data/db/rafita.db` y `.env`.
2. **Detén Rafita**: `docker compose down`
3. **Genera una nueva clave**:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
4. **Actualiza `.env`**: reemplaza `ENCRYPTION_KEY` con la nueva clave.
5. **Inicia Rafita**: `docker compose up -d`
6. **Re-encripta credenciales existentes** (script de migración):
   ```bash
   docker exec rafita-agent-core python /workspace/scripts/rekey_credentials.py --old-key <OLD_KEY> --new-key <NEW_KEY>
   ```
   > ⚠️ Este script no existe actualmente. Debe implementarse antes de la primera rotación real.
   > Mientras tanto, la rotación implica perder las credenciales antiguas (no podrán descifrarse con la nueva clave).
   > Las credenciales se pueden volver a guardar manualmente con `/guardar_clave`.
7. **Verifica**: `/claves` en Telegram debería mostrar las credenciales (enmascaradas).
8. **Destruye el backup antiguo** de forma segura (shred en Linux, cipher /w en Windows).

---

## Seguridad de red

### Topología desplegada (dos nodos, actualizado 2026-09-26)

| Componente | Nodo | Expuesto a | Control |
|---|---|---|---|
| Ollama API (11434) | Dell | `0.0.0.0:11434` | ufw + Tailscale (**ver nota**) |
| Gateway (8010) y voz (8001) | HP | `127.0.0.1` | solo local |
| Tailscale (WireGuard) | ambos | tailnet del propietario | cifrado en tránsito |

**Nota importante (hallazgo de la prueba de caos 3.4):** en el Dell, la cadena
`ts-input` de Tailscale se evalúa **antes** que las reglas de ufw y acepta todo
el tráfico de `tailscale0`. La regla de ufw "11434 solo desde la IP del HP"
queda por tanto **sombreada** (comprobado: borrarla no corta el acceso). En la
práctica, el motor de IA es accesible desde **cualquier dispositivo
autenticado en el tailnet** (incluido un móvil fuera de casa), no solo desde
el HP. Es una **decisión aceptada explícitamente por el propietario** por
utilidad; para restringirlo hay procedimiento documentado en `plan.md`
(3.4.1): ACL de Tailscale o regla iptables insertada antes de `ts-input`.

**La red local sigue protegida**: el `deny` por defecto de ufw bloquea 11434
desde la LAN (verificado con un `curl` desde el PC de desarrollo).

**Nodo GPU opcional (torre, 2026-09-26)**: el contenedor `rafita-ollama-gpu`
publica el puerto **11435 solo en la IP Tailscale** de la torre
(`100.97.252.19`), por lo que la LAN no puede alcanzarlo; cualquier
dispositivo del tailnet sí (mismo modelo de acceso aceptado que el Dell).
La torre conserva además su propio Ollama nativo (v0.21.2) escuchando en
`0.0.0.0:11434` para otros usos: **revisar/limitar ese servicio si la torre
deja de ser un equipo de confianza en la LAN**.

**Cifrado en tránsito**: todo el tráfico entre nodos viaja por WireGuard
(Tailscale); no hay HTTP en claro entre máquinas.

**Backups**: repositorio restic **cifrado** en un USB (vfat); la contraseña
vive solo en el HP (root-only) y en el gestor del propietario. Extracción
segura con `usb-eject`.

**Timeouts del cliente**: una caída de red silenciosa está acotada por
`OLLAMA_REQUEST_TIMEOUT` (600 s por defecto); una conexión rechazada falla en
segundos; los streams tienen un techo de 120 s entre fragmentos. La sonda
`/ready` falla en ≤10 s.

**Si se despliega en otro entorno**: mantén el prefijo `127.0.0.1:` en los
puertos del agente y usa **Tailscale** o túnel SSH para el acceso remoto, nunca
exposición directa a internet.

---

## Seguridad de la aplicación

### Cifrado de credenciales (Fernet)
- Algoritmo: AES-128-CBC con HMAC-SHA256 (via Fernet)
- Clave: 256 bits (32 bytes), generada con `Fernet.generate_key()`
- Almacenamiento: `ENCRYPTION_KEY` en `.env` (raíz del proyecto)
- Verificación en caliente: `/clave <servicio>` devuelve valor enmascarado

### Webhook server
- `webhook_server.py` implementa verificación HMAC (SHA-256) de firma para webhooks entrantes.
- El secreto es **único por instancia**: si `WEBHOOK_SECRET` está vacío, se genera
  uno aleatorio en el primer arranque y se persiste en `.env` (nunca un valor
  por defecto compartido en el repositorio).
- **Fail-closed**: si no hay secreto configurado, los endpoints de webhook
  responden `503` ("Webhook secret not configured") en vez de aceptar la
  petición; una firma ausente o inválida responde `401`.
- Los endpoints protegidos son `POST /webhook/{source}`, `POST|DELETE /connector/{name}`,
  `POST /gmail/check` y `POST /homeassistant/{entity_id}`.
- Las operaciones del vault pasan por confinamiento de rutas
  (`utils/path_safety.resolve_within`, tarea 0.3).

### Pipeline de ingesta de archivos
- (Ver F2.5 para auditoría de path traversal / injection.)

### Privacidad en llamadas de voz (call_rafita.html)

El modo de voz usa síntesis TTS (Piper) para leer las respuestas en voz alta
por los altavoces. Esto introduce un riesgo de privacidad distinto al del
chat por Telegram:

- **Las respuestas se pronuncian en voz alta** en el entorno físico donde esté
  el dispositivo. Cualquier persona presente puede escucharlas.
- **El RAG semántico puede recuperar información sin que lo anticipes**:
  una pregunta aparentemente inocente puede hacer que `search_second_brain`
  encuentre una nota con datos de salud, finanzas o información personal, y
  Rafita la leerá en voz alta antes de que puedas reaccionar. A diferencia
  de leer en pantalla (donde ves el texto y decides si seguir leyendo), aquí
  no hay vista previa del resultado.
- **Recomendación**: no uses el modo de voz en espacios públicos o compartidos
  si tu vault contiene información sensible (salud, finanzas, datos personales).
  Usa el chat de Telegram para consultas que puedan devolver datos confidenciales.
- **Futuro (v0.2.0)**: se planea añadir filtrado por tags sensibles
  (`salud`, `finanzas`) en el pipeline de voz para excluir automáticamente
  esos resultados de las respuestas por audio, manteniéndolos accesibles solo
  por texto.

### Dependencias con avisos aceptados (ChromaDB)

**Fecha de revisión: 2026-09-26** (re-verificado tras el aviso de Dependabot:
1 crítica + 2 altas). Estado: sin parche disponible upstream; `1.5.9` (última
versión publicada) sigue dentro del rango afectado (`last_affected: 1.5.9`).

`chromadb` 0.4.17–1.5.9 (1.5.9 es la última versión publicada) tiene tres
avisos abiertos:

| Aviso | CVE | Descripción | Superficie afectada |
|---|---|---|---|
| PYSEC-2026-3813 | CVE-2026-45830 | Lectura/escritura/borrado en colecciones de otros tenants | Servidor HTTP con autenticación |
| PYSEC-2026-3814 | CVE-2026-45833 | Inyección de código vía `trust_remote_code=True` al registrar un modelo remoto en `/api/v2/...` | Servidor HTTP con permiso UPDATE_COLLECTION |
| PYSEC-2026-3815 | CVE-2026-45831 | `SimpleRBACAuthorizationProvider` no valida tenant/database/collection | Servidor HTTP con RBAC |

**Por qué se aceptan:** los tres requieren el **servidor HTTP de Chroma**
(`chroma run` / client/server), autenticación y multi-tenant; Rafita usa
`chromadb.PersistentClient` **embebido**, mono-usuario, sin API HTTP, sin
autenticación, sin tenants y sin modelos remotos (`trust_remote_code` no se
usa). Los embeddings los genera Ollama y se pasan como vectores.

**Mitigaciones vigentes:**
- Nunca levantar Chroma en modo servidor ni exponer un puerto de Chroma.
- No configurar funciones de embedding remotas con `trust_remote_code`.
- El CI ignora exactamente esos tres IDs (`--ignore-vuln` en el job Security);
  cualquier aviso nuevo rompe el build.
- **Invariante verificada por test**: `agent/tests/test_chromadb_embedded_only.py`
  falla si el código introduce `HttpClient`/`AsyncHttpClient`/`chromadb.Client(`
  o `trust_remote_code`. Así la mitigación no depende solo de esta prosa.
- Dependabot seguirá mostrando las 3 alertas mientras la dependencia fijada
  esté en rango afectado; el tratamiento correcto es marcarlas en GitHub como
  "not affected" (el código vulnerable no se usa), no subir de versión: no hay
  versión sin los avisos. Al publicarse una versión corregida, actualizar
  `agent/requirements.txt` y quitar los ignores.

### Verificación de cifrado Fernet
- Test dedicado: `agent/tests/test_fernet.py` (4 tests: roundtrip, clave inválida,
  multi-valor con UTF-8, token manipulado). Se ejecuta en CI para verificar
  que las actualizaciones de `cryptography` no rompan el cifrado.

---

## Checklist de seguridad para nuevas instalaciones

- [ ] Cifrado de disco activado (BitLocker/LUKS/FileVault)
- [ ] `.env` con permisos restrictivos (`chmod 600 .env` en Linux)
- [ ] Puertos 8000/8001 limitados a `127.0.0.1` si no se usa Tailscale
- [ ] `ENCRYPTION_KEY` generada automáticamente (no reutilizar entre instalaciones)
- [ ] Backup de `data/` y `.env` en ubicación segura (también cifrada)
- [ ] Tailscale configurado si se accede desde fuera de la red local
- [ ] El vault de ejemplo (`vault_ejemplo/`) no contiene datos reales
