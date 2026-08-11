# Instalación de Rafita AVP

Guía completa para instalar tu propio Asistente Virtual Privado desde cero.

---

## Requisitos de hardware

### Perfiles recomendados

| Perfil | RAM mínima | GPU | Modelos | Experiencia |
|---|---|---|---|---|
| **Básico** | 8 GB | No | qwen2.5:3b + nomic-embed-text | Respuesta lenta (~30s), funcionalidad completa |
| **Recomendado** | 16 GB | No | qwen2.5:7b + bge-m3 | Respuesta aceptable (~60s primer mensaje), buena calidad RAG |
| **Óptimo** | 16 GB + GPU | ≥6 GB VRAM | qwen2.5:7b + bge-m3 con GPU | Respuesta rápida (~5s), excelente calidad RAG |
| **Máximo** | 16 GB + GPU | ≥12 GB VRAM | gemma4:12b + bge-m3 | Mejor tool use y español, respuesta rápida |

Rafita detecta tu hardware automáticamente y selecciona el perfil adecuado.
Puedes sobreescribirlo editando `.env`.

### Espacio en disco

- Código: ~5 MB
- Modelos Ollama: 2-12 GB (según perfil)
- Datos (BD + vector DB): ~50-200 MB

---

## 1. Requisitos previos

### Docker y Docker Compose

- **Windows**: [Docker Desktop](https://docs.docker.com/desktop/install/windows-install/)
- **macOS**: [Docker Desktop](https://docs.docker.com/desktop/install/mac-install/)
- **Linux**: Docker Engine + Docker Compose plugin
  ```bash
  curl -fsSL https://get.docker.com | sh
  sudo apt install docker-compose-plugin  # o docker-compose-v2
  ```

Verifica:
```bash
docker --version
docker compose version
```

### Bot de Telegram

1. Abre Telegram y habla con [@BotFather](https://t.me/BotFather)
2. Envía `/newbot` y sigue las instrucciones
3. Guarda el **token** que te da (ej: `123456:ABCdef...`)

### Git (opcional, para clonar)

Si no tienes git, descarga el ZIP del repositorio.

---

## 2. Instalación

```bash
# Clonar el repositorio
git clone https://github.com/user/rafai.git
cd rafai

# Crear archivo de configuración
cp .env.example .env
```

### Configurar `.env`

Edita `.env` con tu editor de texto. **Mínimo imprescindible**:

```env
TELEGRAM_TOKEN=123456:ABCdef...   # El token de @BotFather
```

Opcional pero recomendado:

```env
ASSISTANT_NAME=Rafita             # El nombre que quieras darle
ADMIN_IDS=[123456789]             # Tu ID de Telegram (opcional)
```

El sistema de detección de hardware configurará automáticamente los modelos.
Si quieres forzar modelos específicos:

```env
OLLAMA_MODEL=qwen2.5:7b           # Modelo de chat
EMBEDDING_MODEL=bge-m3            # Modelo de embeddings
EMBEDDING_DIM=1024                # Dimensión del embedding
```

---

## 3. Primer arranque

```bash
# Construir e iniciar
docker compose up -d

# Ver logs (Ctrl+C para salir)
docker compose logs -f rafita-agent-core
```

El primer arranque tarda más porque:

1. **Descarga modelos de Ollama** (se hace automáticamente): gemma4/qwen + bge-m3 + llava
2. **Pre-carga los modelos en RAM**: 1-3 minutos en GPU, 3-5 minutos en CPU
3. **Indexa el vault de ejemplo** (backfill): ~30 segundos
4. **Arranca el bot de Telegram**: conecta y empieza a recibir mensajes

Cuando veas `Startup complete. All services running.` en los logs, tu Rafita está vivo.

### Verificar que funciona

```bash
# Estado de los contenedores
docker compose ps
# Ambos deben mostrar "healthy"

# Probar el bot
# Abre Telegram, busca tu bot y escribe /start
```

---

## 4. Configurar tu vault de Obsidian

Por defecto Rafita usa `vault_ejemplo/` con notas de demostración.
Para usar tu propio vault:

1. Copia tu vault de Obsidian a la carpeta `mi_boveda_obsidian/` del proyecto
   (o crea un enlace simbólico)
2. El vault debe tener estructura PARA+Zettelkasten:
   ```
   mi_boveda_obsidian/
   ├── 00-Inbox/
   ├── 01-Proyectos/
   ├── 02-Areas/
   ├── 03-Recursos/
   ├── 04-Archivo/
   ├── 05-Zettelkasten/
   └── 06-Diario/
   ```
3. Reinicia:
   ```bash
   docker compose restart
   ```
4. El backfill indexará tu vault automáticamente (primer arranque con vault nuevo)

---

## 5. Personalización

### Nombre del asistente

En `.env`:
```env
ASSISTANT_NAME=TuNombre
```

### Modelos de IA

Ver `OLLAMA_MODEL`, `EMBEDDING_MODEL` y `OLLAMA_VISION_MODEL` en `.env`.

Para descargar modelos adicionales:
```bash
docker exec ollama-service ollama pull nombre-modelo
```

### Acceso remoto (homelab)

Los puertos están restringidos a `127.0.0.1` por seguridad.
Para acceder desde fuera de tu red local, configura **Tailscale**:

1. Instala Tailscale en tu máquina y en tu dispositivo móvil
2. Conéctate a tu red Tailscale
3. Usa la IP de Tailscale para acceder a los puertos

No expongas los puertos directamente a internet.

---

## 6. Mantenimiento

### Actualizar Rafita

```bash
git pull
docker compose build --no-cache rafita-agent-core
docker compose up -d
```

### Backup

```bash
# Backup automático (diario recomendado)
docker exec rafita-agent-core bash /workspace/scripts/backup_verify.sh

# Backup manual
tar -czf rafita_backup_$(date +%Y%m%d).tar.gz data/db/ data/vector_db/ .env
```

### Restaurar backup

```bash
docker compose down
tar -xzf rafita_backup_YYYYMMDD.tar.gz
docker compose up -d
```

### Logs

```bash
# Logs en tiempo real
docker compose logs -f rafita-agent-core

# Logs de las últimas 100 líneas
docker compose logs --tail 100 rafita-agent-core

# Buscar errores
docker compose logs rafita-agent-core | grep -i error
```

---

## 7. Solución de problemas

Ver [runbook.md](docs/runbook.md) para una guía completa de incidentes.

### Problemas comunes

| Síntoma | Solución |
|---|---|
| "No module named 'watchdog'" | `docker compose build rafita-agent-core --no-cache` |
| El bot no responde | Verifica `TELEGRAM_TOKEN` en `.env` y reinicia |
| Respuesta muy lenta | Primer mensaje tras arranque tarda más. Siguientes son más rápidos. En CPU puede tardar 1-3 min |
| Error de memoria | Usa un modelo más pequeño: `OLLAMA_MODEL=qwen2.5:3b` |
| Backfill no completa | Usa `EMBEDDING_MODEL=nomic-embed-text` y `EMBEDDING_DIM=768` en hardware limitado |
| ChromaDB corrupta | Borra `data/vector_db/` y reinicia para reindexar |

---

## Desinstalación

```bash
docker compose down -v    # Elimina contenedores y volúmenes
cd .. && rm -rf rafai     # Elimina el proyecto
```

Los modelos de Ollama se guardan en `ollama/models/`. Para eliminarlos también:
```bash
rm -rf ollama/models/
```
