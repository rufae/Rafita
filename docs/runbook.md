# Runbook de incidentes — Rafita AVP

Guía rápida para diagnosticar y resolver problemas comunes en una instancia
de Rafita AVP. Asume Docker Compose en Linux/macOS/Windows.

---

## 1. El contenedor no arranca (crash loop)

### Síntomas
- `docker compose ps` muestra `rafita-agent-core` en estado `restarting` o `unhealthy`.
- `docker compose logs rafita-agent-core` muestra errores repetidos.

### Diagnóstico

```bash
# Ver logs de los últimos 5 intentos
docker compose logs rafita-agent-core --tail 100

# Ver estado exacto
docker compose ps
```

### Causas comunes y soluciones

| Error en logs | Causa | Solución |
|---|---|---|
| `ModuleNotFoundError: No module named 'watchdog'` | Imagen no actualizada | `docker compose build rafita-agent-core --no-cache` |
| `Model x is not available` | Modelo no descargado en Ollama | `docker exec ollama-service ollama pull <modelo>` |
| `sqlite3.OperationalError: database is locked` | Dos procesos accediendo a la BD | Reiniciar: `docker compose restart` |
| `MemoryError` o OOM kill | RAM insuficiente para el modelo | Usar modelo más pequeño. Editar `.env`: `OLLAMA_MODEL=qwen2.5:3b` |
| `Connection refused` a ollama:11434 | ollama-service no está healthy | Esperar a que `docker compose ps` muestre ollama-service `healthy` |

### Si nada funciona

```bash
# Reset completo
docker compose down -v
docker compose build --no-cache
docker compose up -d
```

---

## 2. El backfill falla o no completa

### Síntomas
- `/cerebro` muestra `0 chunks from 0 documents`.
- Los logs muestran `Backfill: X notas indexadas (Y chunks), Z fallos` con Z > 0.
- El contenedor se queda en `health: starting` más de 10 minutos.

### Diagnóstico

```bash
# Ver logs específicos del backfill
docker compose logs rafita-agent-core | grep -i "backfill\|Ollama embedding failed\|FAILED\|chunks"

# Verificar estado de la BD vectorial
docker exec rafita-agent-core python -c "
import asyncio
async def t():
    from src.utils.vector_manager import vector_db
    await vector_db.initialize()
    s = await vector_db.get_stats()
    print(f'Chunks: {s[\"total_chunks\"]}, Docs: {s[\"total_documents\"]}')
    await vector_db.close()
asyncio.run(t())
"
```

### Causas y soluciones

| Causa | Solución |
|---|---|
| Embedding model timeout (CPU lenta) | Aumentar timeout en `OllamaEmbeddingFunction`: timeout=600 → 1200 |
| Embedding model no descargado | `docker exec ollama-service ollama pull bge-m3` |
| RAM insuficiente (modelo embedding + chat juntos) | En CPU-only, usar `nomic-embed-text`. Editar `.env`: `EMBEDDING_MODEL=nomic-embed-text`, `EMBEDDING_DIM=768` |
| Archivo corrupto en el vault | Mover el archivo problemático fuera del vault, reiniciar |

### Reindexado manual

```bash
# Borrar BD vectorial y forzar backfill
rm -rf data/vector_db/*
docker compose restart rafita-agent-core
```

---

## 3. El bot no responde en Telegram

### Diagnóstico

```bash
# Ver si el bot está polling
docker compose logs rafita-agent-core | grep -i "polling\|Telegram\|bot"

# Ver si hay mensajes entrantes
docker compose logs rafita-agent-core | grep "TELEMETRY A"
```

### Causas comunes

| Causa | Solución |
|---|---|
| Token de Telegram inválido | Verificar `TELEGRAM_TOKEN` en `.env` |
| Bot bloqueado por el usuario | Enviar `/start` al bot en Telegram |
| Rate limiting de Telegram | Esperar 1-2 minutos, los mensajes se acumulan |
| Timeout de respuesta (CPU lenta) | El bot responde "cargando modelo...". Esperar hasta 5 min en CPU. |
| Error de conexión a Telegram API | Verificar conectividad: `docker exec rafita-agent-core curl -s https://api.telegram.org` |

---

## 4. Rotación de credenciales (Fernet)

### Cuándo rotar
- Sospecha de compromiso del archivo `.env`.
- Migración de la instancia a otra máquina.
- Mantenimiento periódico (cada 6-12 meses).

### Procedimiento

1. **Backup previo**:
   ```bash
   docker exec rafita-agent-core bash /workspace/scripts/backup_verify.sh
   cp .env .env.backup.$(date +%Y%m%d)
   ```

2. **Detener Rafita**:
   ```bash
   docker compose down
   ```

3. **Generar nueva clave**:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

4. **Actualizar `.env`**: reemplazar `ENCRYPTION_KEY` con la nueva clave.

5. **Iniciar Rafita**:
   ```bash
   docker compose up -d
   ```

6. **Re-guardar credenciales**: las credenciales antiguas NO se pueden descifrar con la nueva clave.
   Vuelve a guardarlas manualmente con `/guardar_clave <servicio> <valor>` en Telegram.

7. **Verificar**: `/claves` en Telegram debe mostrar las credenciales re-guardadas (enmascaradas).

8. **Destruir backup antiguo**:
   ```bash
   # En Linux/macOS
   shred -u .env.backup.*
   # En Windows
   cipher /w:C:\Users\...\RafAI
   ```

---

## 5. Restauración desde backup

### 5.1 Backup diario del homelab (USB RAFAEL, restic)

El backup diario cubre TODOS los servicios del HP (tarea 3.6): Rafita,
BuenaTierra, Nextcloud, NPM, AdGuard, WireGuard, Portainer y configuración.
Repositorio: `/mnt/rafael/Servidor/server-nodochicohp/restic`.

```bash
# Montar el USB si hace falta
sudo systemctl start mnt-rafael.mount

# Listar snapshots (contrasena: /root/.restic-password o tu gestor)
sudo bash -c 'export RESTIC_REPOSITORY=/mnt/rafael/Servidor/server-nodochicohp/restic \
  RESTIC_PASSWORD_FILE=/root/.restic-password; restic snapshots'

# Verificación NO destructiva: restaurar a un directorio temporal
sudo restic restore latest --target /var/lib/rafita-backup/verify
sudo restic restore latest --target /var/lib/rafita-backup/verify \
  --include /var/lib/rafita-backup/stage      # dumps SQL y SQLite del stage

# Restaurar solo un servicio, p. ej. Rafita
sudo restic restore latest --target /tmp/restore --include /home/server/proyectos/rafita
```

Postgres (BuenaTierra y Nextcloud): los dumps `-Fc` están en el stage del
snapshot; se restauran en un contenedor temporal o en el de producción:

```bash
docker run -d --rm --name restore-pg -e POSTGRES_HOST_AUTH_METHOD=trust postgres:15.4-alpine
docker cp .../buenatierra.dump restore-pg:/tmp/
docker exec restore-pg createdb -U postgres buenatierra
docker exec restore-pg pg_restore -U postgres -d buenatierra --no-owner /tmp/buenatierra.dump
# Nextcloud: el dump referencia el rol oc_admin; crearlo o usar --no-owner
```

Verificación de Rafita restaurado (evidencia de 3.6): `PRAGMA integrity_check`
= ok, `chroma.sqlite3` con sus embeddings, `.env` con la misma `ENCRYPTION_KEY`
(roundtrip Fernet correcto) y `ready_probe.py` con `DATA_DIR`/`VECTOR_DB_DIR`/
`OBSIDIAN_VAULT_DIR` apuntando al restore → `HTTP 200 ready`.

### 5.2 Backup local de Rafita (rápido, sin USB)

```bash
# 1. Detener Rafita
docker compose down

# 2. Restaurar SQLite
tar -xzf data/backups/rafita_backup_YYYYMMDD_HHMMSS.tar.gz -C /tmp/restore/
cp /tmp/restore/rafita.db data/db/rafita.db

# 3. Restaurar Vector DB
rm -rf data/vector_db/*
cp -r /tmp/restore/vector_db/* data/vector_db/

# 4. Limpiar y arrancar
rm -rf /tmp/restore/
docker compose up -d

# 5. Verificar
docker compose logs rafita-agent-core | grep "Vector DB ready"
```

---

## 6. Checklist de salud periódica

Ejecutar semanalmente o tras cambios:

```bash
# Estado de contenedores
docker compose ps

# Logs recientes sin errores
docker compose logs rafita-agent-core --since 1h | grep -i "error\|fail\|exception" || echo "Sin errores"

# Tamaño de la BD
ls -lh data/db/rafita.db

# Chunks en vector DB (ver sección 2)
# Backup reciente
ls -lt data/backups/ | head -5

# Endpoint de salud
curl -s http://localhost:8000/health | python -m json.tool
```
