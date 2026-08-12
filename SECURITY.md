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
| Credenciales (API keys, contraseñas) | AES-256 Fernet dentro de SQLite | Robo de claves si se rompe el cifrado |
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
**AES-256 (Fernet)** antes de almacenarse en SQLite.

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

### Puertos expuestos por defecto

| Puerto | Servicio | Expuesto a | Propósito |
|---|---|---|---|
| 11434 | Ollama API | `localhost` (Docker network interna) | Inferencia de modelos |
| 8000 | FastAPI Gateway | `localhost` | API interna del asistente |
| 8001 | Voice Stream WS | `localhost` | Streaming de voz por WebSocket |

**Verificación**: en `docker-compose.yml`, los puertos 8000 y 8001 se mapean como
`0.0.0.0:8000-8001->8000-8001/tcp`. Esto significa que son accesibles desde
**cualquier interfaz de red** del host, no solo localhost.

**RECOMENDACIÓN**: para entornos de producción o servidores, restringir a localhost:
```yaml
ports:
  - "127.0.0.1:8000:8000"
  - "127.0.0.1:8001:8001"
```

Si necesitas acceso remoto (homelab), usa **Tailscale** o un túnel SSH en lugar de
exponer los puertos directamente a internet.

---

## Seguridad de la aplicación

### Cifrado de credenciales (Fernet)
- Algoritmo: AES-128-CBC con HMAC-SHA256 (via Fernet)
- Clave: 256 bits (32 bytes), generada con `Fernet.generate_key()`
- Almacenamiento: `ENCRYPTION_KEY` en `.env` (raíz del proyecto)
- Verificación en caliente: `/clave <servicio>` devuelve valor enmascarado

### Webhook server
- `webhook_server.py` implementa verificación HMAC de firma para webhooks entrantes.
- Sin `WEBHOOK_SECRET` configurado, los webhooks se rechazan.
- (Ver F2.5 para auditoría completa de path traversal en este módulo.)

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
