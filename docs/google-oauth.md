# Guía OAuth: Drive completo, Gmail, Tasks, People y Fitness

**Para qué:** la cuenta de servicio actual sirve para Calendar y para **leer**
Drive, pero no puede crear archivos (sin cuota), ni acceder a Tasks, Gmail,
Contactos o Fitness. Con **OAuth** el bot actúa como tu propio usuario y todo
eso funciona.

## 1. Habilitar las APIs en tu proyecto (`000000000000`)

| API | Enlace |
|---|---|
| Calendar (ya) | https://console.cloud.google.com/apis/library/calendar-json.googleapis.com?project=000000000000 |
| Drive (ya) | https://console.cloud.google.com/apis/library/drive.googleapis.com?project=000000000000 |
| Sheets (ya) | https://console.cloud.google.com/apis/library/sheets.googleapis.com?project=000000000000 |
| Docs (ya) | https://console.cloud.google.com/apis/library/docs.googleapis.com?project=000000000000 |
| **Tasks** | https://console.cloud.google.com/apis/library/tasks.googleapis.com?project=000000000000 |
| **Gmail** | https://console.cloud.google.com/apis/library/gmail.googleapis.com?project=000000000000 |
| **People** | https://console.cloud.google.com/apis/library/people.googleapis.com?project=000000000000 |
| **Fitness** | https://console.cloud.google.com/apis/library/fitness.googleapis.com?project=000000000000 |

## 2. Crear el cliente OAuth

1. Ve a **Credenciales**: https://console.cloud.google.com/apis/credentials?project=000000000000
2. **Crear credenciales → ID de cliente de OAuth**.
3. Tipo de aplicación: **Aplicación de escritorio**.
4. Nombre: `Rafita Desktop`.
5. **Crear** y **descargar el JSON** (`client_secret_....json`).
6. Renómbralo a **`credentials.json`**.
7. Envíalo por Telegram al bot como archivo (o cópialo a
   `~/proyectos/rafita/credentials/` con permisos 600).

> Importante: si en la pantalla de consentimiento la app está en modo
> «Prueba», añade tu cuenta como **usuario de prueba** (Usuarios de prueba →
> Añadir usuarios) o la autorización fallará con `access_denied`.

## 3. Autorizar

1. En Telegram: **`/setup_google`**.
2. Abre el enlace que te da, elige tu cuenta y autoriza todos los permisos.
3. El navegador terminará en una página que **no carga**
   (`http://localhost:8080/?code=...`). Eso es normal.
4. Copia el valor de **`code=`** de la barra de direcciones y envíalo:
   **`/setup_google <codigo>`**.
5. El token queda **cifrado (Fernet)** en la base de datos; no hay que
   repetir esto salvo revocación.

## 4. Verificar

```bash
cd ~/proyectos/rafita
docker run --rm --network host \
  -e TELEGRAM_TOKEN=dummy -e PYTHONPATH=/app \
  -e DATA_DIR=/tmp/e2e/data -e DB_PATH=/tmp/e2e/data/db/rafita.db \
  -e VECTOR_DB_DIR=/tmp/e2e/data/vector_db -e OBSIDIAN_VAULT_DIR=/tmp/e2e/vault \
  -e LOG_DIR=/tmp/e2e/logs \
  -v "$PWD/agent/src:/app/src" -v "$PWD/credentials:/workspace/credentials:ro" \
  -v "$PWD:/workspace" -w /app rafita-rafita-agent-core \
  sh -c 'mkdir -p /tmp/e2e/data/db /tmp/e2e/vault /tmp/e2e/logs && \
         python -u /workspace/agent/scripts/test_google_services.py'
```

Con OAuth el script prueba además **Tasks** (crear/listar/borrar), **Gmail**
(perfil) y **People/Fitness**. Si todo sale `[OK]`, listo.

## 5. Permisos que se solicitan (mínimo necesario)

- Calendar (completo), Drive (completo), Sheets, Docs.
- Gmail **solo lectura**, Contactos **solo lectura**, Fitness (actividad).

Para revocar el acceso: https://myaccount.google.com/permissions

## Estado (27/09/2026)

- OAuth **completado** con todos los permisos.
- Verificado con el E2E: Calendar, Drive completo, Sheets, Docs, Tasks,
  Gmail (lectura) y Fitness → **OK**.
- **Pendiente**: habilitar la **People API** (Contactos) con este enlace:
  https://console.cloud.google.com/apis/library/people.googleapis.com?project=000000000000
  Después, el E2E probará también los contactos.
