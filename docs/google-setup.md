# Guía de configuración de Google (cuenta de servicio)

**Fecha:** 2026-09-26 · Proyecto GCP: `rafita-500317` (id `552699919273`)
**Cuenta de servicio:** `rafita@rafita-500317.iam.gserviceaccount.com`

La integración usa una **cuenta de servicio**: solo accede a lo que se le
comparte explícitamente. No necesita tu contraseña ni acceso al resto de tu
cuenta.

## 1. APIs que deben estar habilitadas

| API | Estado | Enlace para habilitar |
|---|---|---|
| Calendar API | ✅ habilitada | — |
| Drive API | ✅ habilitada | — |
| **Sheets API** | ❌ falta | https://console.cloud.google.com/apis/library/sheets.googleapis.com?project=552699919273 |
| **Docs API** | ❌ falta | https://console.cloud.google.com/apis/library/docs.googleapis.com?project=552699919273 |
| Tasks API | opcional (OAuth) | https://console.cloud.google.com/apis/library/tasks.googleapis.com?project=552699919273 |
| Gmail API | opcional (OAuth) | https://console.cloud.google.com/apis/library/gmail.googleapis.com?project=552699919273 |

Tras habilitar Sheets y Docs, vuelve a ejecutar el test E2E (sección 4) para
confirmar.

## 2. Compartir recursos con la cuenta de servicio

| Recurso | Cómo | Estado |
|---|---|---|
| **Calendario** | Google Calendar → tu calendario → «Compartir con determinadas personas» → `rafita@rafita-500317.iam.gserviceaccount.com` con «Hacer cambios en los eventos» | ✅ hecho |
| **Drive** | Compartir la unidad, carpeta o ficheros concretos con el mismo email | ✅ hecho |
| **Carpeta destino (opcional)** | Si quieres que los documentos/hojas que cree Rafita aparezcan en TU Drive: crea una carpeta, compártela con el email y define `GOOGLE_DRIVE_FOLDER_ID` en el `.env`. Si no, se crean en el Drive de la cuenta de servicio (el test E2E los borra). | — |

## 3. Variables de entorno (`.env` del HP)

```bash
GOOGLE_CALENDAR_ID=tu-correo@gmail.com   # opcional: se fija con /calendario
GOOGLE_DRIVE_FOLDER_ID=                  # opcional: carpeta destino en tu Drive
GOOGLE_APPLICATION_CREDENTIALS=          # opcional: ruta alternativa al JSON
TIMEZONE=Europe/Madrid                   # ya configurado
```

El calendario se puede fijar desde el propio bot (recomendado, no hace falta
tocar el `.env`): comando **`/calendario tu-correo@gmail.com`** (solo
administradores). Se guarda en la base de datos local y se valida el acceso.

## 4. Test E2E

```bash
# En el HP, sin tocar la base de datos de producción:
cd ~/proyectos/rafita
docker run --rm --network host \
  -e TELEGRAM_TOKEN=dummy -e PYTHONPATH=/app \
  -e DATA_DIR=/tmp/e2e/data -e DB_PATH=/tmp/e2e/data/db/rafita.db \
  -e VECTOR_DB_DIR=/tmp/e2e/data/vector_db -e OBSIDIAN_VAULT_DIR=/tmp/e2e/vault \
  -e LOG_DIR=/tmp/e2e/logs -e GOOGLE_CALENDAR_ID=tu-correo@gmail.com \
  -v "$PWD/agent/src:/app/src" -v "$PWD/credentials:/workspace/credentials:ro" \
  -v "$PWD:/workspace" -w /app rafita-rafita-agent-core \
  sh -c 'mkdir -p /tmp/e2e/data/db /tmp/e2e/vault /tmp/e2e/logs && \
         python -u /workspace/agent/scripts/test_google_services.py'
```

Prueba Calendar (crear/listar/borrar), Drive (listar), Sheets (crear/
escribir/leer/borrar), Docs (crear/escribir/leer/borrar) y, si hay OAuth,
Tasks y Gmail. Crea y borra sus propios recursos de prueba.

## 5. Tasks y Gmail

No funcionan con cuenta de servicio: **Google Tasks no permite compartir
listas** y una cuenta de servicio **no tiene buzón de Gmail**. Para esos dos
servicios hace falta la vía OAuth (crear un cliente «Aplicación de
escritorio» en el proyecto, subir su JSON y completar `/setup_google`).
