# Migración a Otro Equipo

Este documento explica cómo mover una instancia existente de Rafita AVP a un equipo diferente.

**Importante**: Esto es para **migración** (mover tu instancia existente), no para **instalación nueva**. Si quieres instalar Rafita desde cero en un equipo nuevo, consulta [INSTALL.md](INSTALL.md).

---

## Instalación Nueva vs Migración

| Aspecto | Instalación Nueva | Migración |
|---------|-------------------|-----------|
| **Credenciales** | Generadas automáticamente en primer arranque | Copiadas del equipo origen |
| **Vault de Obsidian** | Vault de ejemplo (`vault_ejemplo/`) | Tu vault real con tus notas |
| **Bot de Telegram** | Nuevo bot creado con @BotFather | Mismo bot, mismo token |
| **Base de datos** | Vacía, se crea desde cero | Copiada del equipo origen |
| **Vector DB** | Se genera con backfill inicial | **NO copiar** - regenerar en destino |
| **Clave Fernet** | Generada automáticamente | Copiada del `.env` origen |

---

## Qué NO Copiar

### ❌ `data/vector_db/`

**Nunca copies `data/vector_db/` entre máquinas.**

**Razón**: Los embeddings están optimizados para el hardware específico (CPU vs GPU, modelo de embeddings). Copiar la vector DB puede causar:
- Incompatibilidad de dimensionalidad (768d vs 1024d)
- Rendimiento degradado
- Errores en búsquedas RAG

**Solución**: La vector DB se regenera automáticamente con un backfill limpio en el hardware de destino. Esto tarda 15-60 minutos dependiendo del tamaño del vault y hardware.

### ❌ Archivos temporales

No copies:
- `data/logs/*.log` (logs antiguos)
- `data/excels/*.xlsx` (exportaciones antiguas)
- `data/exports/*` (exportaciones antiguas)

Estos se regeneran automáticamente si son necesarios.

---

## Qué SÍ Copiar

### ✅ Archivos Esenciales

```
# Copia estos archivos/directorios del equipo origen al destino:

.env                           # Token de Telegram, clave Fernet, configuración
mi_boveda_obsidian/            # Tu vault real de Obsidian con todas tus notas
data/db/rafita.db              # Base de datos SQLite (historial, finanzas, credenciales cifradas)
data/db/rafita.db-shm          # SQLite shared memory (si existe)
data/db/rafita.db-wal          # SQLite write-ahead log (si existe)
```

### ✅ Opcional

```
# Copia solo si quieres mantener el historial:
data/logs/                     # Logs históricos (opcional)

# Copia solo si tienes exportaciones importantes:
data/excels/                   # Exportaciones de Excel (opcional)
data/exports/                  # Otras exportaciones (opcional)
```

---

## Advertencias Críticas

### ⚠️ No Ejecutar Dos Instancias Simultáneas

**Nunca ejecutes Rafita con el mismo bot token en dos máquinas al mismo tiempo.**

**Consecuencias**:
- El bot de Telegram recibirá mensajes duplicados
- El vault de Obsidian puede divergir sin sincronización
- La base de datos puede corromperse
- Las credenciales cifradas pueden desincronizarse

**Solución**: Antes de iniciar Rafita en el equipo destino, asegúrate de que está detenido en el equipo origen:
```bash
# En el equipo origen:
docker compose down
```

### ⚠️ Copia Local, Nunca por Cloud

**Nunca copies los archivos esenciales a través de servicios cloud** (Dropbox, Google Drive, etc.).

**Razón**: Los archivos contienen:
- Token de Telegram (acceso completo a tu bot)
- Clave Fernet (acceso a todas tus credenciales cifradas)
- Tu vault completo con datos personales sensibles

**Solución**: Copia los archivos directamente entre máquinas usando:
- USB drive cifrado
- SCP/SFTP sobre SSH
- Red local (SMB/NFS)
- Tailscale (si las máquinas están en redes diferentes)

---

## Proceso de Migración Paso a Paso

### Paso 1: Preparar Equipo Origen

```bash
# 1. Detener Rafita en el equipo origen
docker compose down

# 2. Verificar que no hay procesos activos
docker ps | grep rafita
# Debe mostrar nada

# 3. Crear backup completo (opcional pero recomendado)
tar -czf rafita-backup-$(date +%Y%m%d).tar.gz \
  .env \
  mi_boveda_obsidian/ \
  data/db/

# 4. Verificar integridad del backup
tar -tzf rafita-backup-*.tar.gz | head -20
```

### Paso 2: Copiar Archivos al Equipo Destino

```bash
# Opción A: USB drive
# Copia .env, mi_boveda_obsidian/, data/db/ a un USB
# Conecta el USB al equipo destino y copia los archivos

# Opción B: SCP sobre SSH
scp -r .env mi_boveda_obsidian/ data/db/ usuario@equipo-destino:~/RafAI/

# Opción C: Red local (SMB/NFS)
# Monta el share del equipo destino y copia los archivos
```

**Verificación**: En el equipo destino, verifica que los archivos existen:
```bash
ls -lh .env mi_boveda_obsidian/ data/db/rafita.db
```

### Paso 3: Instalar Rafita en Equipo Destino

```bash
# 1. Clonar el repositorio (si no existe)
git clone https://github.com/rufae/Rafita.git
cd Rafita

# 2. Copiar archivos del backup (si no lo hiciste en Paso 2)
# cp /path/to/backup/.env .
# cp -r /path/to/backup/mi_boveda_obsidian .
# cp -r /path/to/backup/data/db data/

# 3. NO copiar data/vector_db/ (se regenerará automáticamente)
rm -rf data/vector_db/

# 4. Verificar permisos
chmod 600 .env

# 5. Iniciar Rafita
docker compose up -d
```

### Paso 4: Verificar Migración

```bash
# 1. Verificar que los contenedores están corriendo
docker compose ps
# Debe mostrar: ollama-service (healthy), rafita-agent-core (healthy)

# 2. Verificar logs del backfill
docker compose logs -f rafita-agent-core | grep -i "backfill"
# Debe mostrar: "Backfill: X notas indexadas (Y chunks), 0 fallos"

# 3. Verificar que el bot responde en Telegram
# Envía un mensaje de prueba al bot: "Hola, ¿qué sabes de mí?"

# 4. Verificar que el RAG funciona
# Pregunta algo específico de tu vault: "¿Qué sabes de mi coche?"
# Debe responder con información de tus notas

# 5. Verificar que las credenciales funcionan
# En Telegram: /claves
# Debe mostrar tus credenciales guardadas (enmascaradas)
```

### Paso 5: Limpieza (Opcional)

```bash
# Eliminar archivos temporales del backup
rm -f rafita-backup-*.tar.gz

# Eliminar logs antiguos (si copiaste data/logs/)
find data/logs/ -name "*.log" -mtime +30 -delete
```

---

## Verificación Post-Migración

### Checklist

- [ ] Contenedores corriendo y healthy (`docker compose ps`)
- [ ] Backfill completado sin errores (ver logs)
- [ ] Bot responde en Telegram
- [ ] RAG funciona (pregunta sobre tu vault)
- [ ] Credenciales accesibles (`/claves` en Telegram)
- [ ] Finanzas intactas (`/finanzas` en Telegram)
- [ ] Historial de chat preservado

### Solución de Problemas

**Problema**: El bot no responde en Telegram
```bash
# Verificar que el token es correcto
grep TELEGRAM_TOKEN .env

# Verificar logs
docker compose logs rafita-agent-core | grep -i "telegram"
```

**Problema**: El RAG no encuentra información
```bash
# Verificar que el backfill se completó
docker compose logs rafita-agent-core | grep -i "backfill"

# Si el backfill falló, forzar reindexado
docker compose exec rafita-agent-core python -c "
import asyncio
from src.utils.vault_indexer import VaultIndexer
async def reindex():
    indexer = VaultIndexer()
    result = await indexer.index_all()
    print(result)
asyncio.run(reindex())
"
```

**Problema**: Las credenciales no se pueden descifrar
```bash
# Verificar que la clave Fernet es la misma
grep ENCRYPTION_KEY .env

# Si la clave es diferente, las credenciales no se pueden recuperar
# Necesitas volver a guardarlas con /guardar_clave
```

---

## Migración de CPU a GPU

Si migras de un equipo sin GPU a uno con GPU (ej: RTX 3060):

### Cambios Automáticos

La detección automática de hardware (Fase 4) seleccionará el perfil óptimo:
- **CPU-only**: `qwen2.5:7b` + `bge-m3` (768d)
- **GPU ≥10GB VRAM**: `gemma4:12b` + `bge-m3` (1024d)

### Habilitar Soporte GPU en Docker

**Importante**: Para que la detección de hardware funcione en el equipo destino con GPU NVIDIA, necesitas habilitar el soporte GPU en Docker.

#### Paso 1: Instalar NVIDIA Container Toolkit

En el equipo destino (PC con RTX 3060):

```bash
# Ubuntu/Debian
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

#### Paso 2: Habilitar GPU en docker-compose.yml

Edita `docker-compose.yml` y descomenta las líneas de GPU:

```yaml
services:
  ollama-service:
    # ... otras configuraciones ...
    
    # Descomentar estas líneas para GPU NVIDIA:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
```

#### Paso 3: Verificar Detección GPU

Después de iniciar Rafita, verifica en los logs que se detectó la GPU:

```bash
docker compose logs rafita-agent-core | grep -i "hardware detected"
```

Deberías ver algo como:
```
Hardware detected: GPU=NVIDIA GeForce RTX 3060 (12.0GB VRAM), RAM=32.0GB, CPU=8 cores
Recommended profile: gpu-high (chat=gemma4:12b, embed=bge-m3/1024d, vision=llava:7b)
```

Si ves `gpu-high` con `gemma4:12b`, la detección funcionó correctamente.

### Cambios Manuales (Opcional)

Si quieres forzar un perfil específico:

```bash
# Editar .env
nano .env

# Cambiar modelo de chat
OLLAMA_MODEL=gemma4:12b  # Para GPU
OLLAMA_MODEL=qwen2.5:7b  # Para CPU

# Cambiar modelo de embeddings
EMBEDDING_MODEL=bge-m3        # Para GPU (1024d, mejor calidad)
EMBEDDING_MODEL=nomic-embed-text  # Para CPU (768d, más rápido)
EMBEDDING_DIM=1024            # Para bge-m3
EMBEDDING_DIM=768             # Para nomic-embed-text
```

### Regenerar Vector DB

Si cambias el modelo de embeddings, **debes regenerar la vector DB**:

```bash
# Detener Rafita
docker compose down

# Eliminar vector DB
rm -rf data/vector_db/

# Iniciar Rafita (el backfill se ejecutará automáticamente)
docker compose up -d

# Monitorear el backfill
docker compose logs -f rafita-agent-core | grep -i "backfill"
```

---

## Resumen

| Paso | Acción | Tiempo Estimado |
|------|--------|-----------------|
| 1 | Preparar equipo origen | 5 min |
| 2 | Copiar archivos | 10-30 min (depende del vault) |
| 3 | Instalar Rafita en destino | 10 min |
| 4 | Backfill automático | 15-60 min (depende del vault y hardware) |
| 5 | Verificación | 10 min |
| **Total** | | **50-120 min** |

---

## Soporte

Si encuentras problemas durante la migración:
1. Revisa los logs: `docker compose logs rafita-agent-core`
2. Consulta el [runbook de incidentes](docs/runbook.md)
3. Abre un issue en [GitHub](https://github.com/rufae/Rafita/issues)
