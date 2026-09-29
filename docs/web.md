# Web de Rafita (SPA: chat, baúl y llamada)

Aplicación web propia servida por el gateway, con las mismas capacidades que
Telegram (comparten el **Agent Core**). Telegram nunca se desactiva.

## Vistas

| Vista | Qué hace |
|---|---|
| **Chat** | Conversación con Rafita (mismas herramientas: correo, tareas, CRM, bóveda…). Historial por usuario. |
| **Baúl** | CRUD de la bóveda de Obsidian: buscar (nombre/contenido), filtrar por carpeta, leer, crear/editar y borrar notas `.md`. |
| **Reuniones** | Grabar (micro o pestaña con MediaRecorder), transcribir con Whisper local, separar hablantes, resumir y guardar el acta en el Baúl. |
| **Llamada** | Sesión de voz en tiempo real (reutiliza la página y el WebSocket de la Fase de voz). |

## Reuniones (tipo NotebookLM)

1. Pulsa **Grabar micro** o **Grabar pestaña** (captura el audio de una pestaña
   o pantalla compartida; ideal para videollamadas). **Parar** sube el audio.
2. El servidor procesa en segundo plano: convierte a WAV mono 16 kHz
   (ffmpeg), transcribe por segmentos (faster-whisper local), **estima los
   interlocutores** por tono fundamental (heurística ligera: separa voces
   claramente distintas; no sustituye a una diarización profesional) y genera
   con el LLM local un **resumen ejecutivo, puntos clave, decisiones y
   tareas/compromisos**.
3. El acta queda como nota en `Reuniones/` de la bóveda (con la transcripción
   completa `[mm:ss] Hablante N: …`) y aparece en la vista **Baúl**.
4. La vista muestra el progreso y el resultado; puedes borrar la reunión y su
   audio desde la propia interfaz.

Endpoints: `POST /api/meetings` (multipart `file` + `title`),
`GET /api/meetings`, `GET /api/meetings/{id}`, `DELETE /api/meetings/{id}`.
El audio se guarda en `data/meetings/` (máx. 200 MB).

La app es **PWA instalable**: `manifest.webmanifest` + service worker (funciona
como app en móvil/escritorio). Al ser estática y sin dependencias, es
empaquetable con Capacitor/Tauri apuntando a la URL o a los ficheros.

## Puertos y acceso

| Servicio | Contenedor | Host HP (referencia) |
|---|---|---|
| Gateway + SPA + API | 8000 | `127.0.0.1:8010` |
| Servidor de voz (llamada) | 8001 | `127.0.0.1:8001` |

Los puertos están publicados **solo en localhost**; el acceso desde el
navegador se hace por un proxy (Nginx Proxy Manager en el HP). Ejemplo:

1. NPM → Proxy Host `rafita.home` → `http://127.0.0.1:8010` (la SPA y la API).
2. NPM → Proxy Host `voz.rafita.home` → `http://127.0.0.1:8001` con
   **Websockets Support** activado (la llamada).
3. En `web/app/config.js` pon el origen de voz para que la vista Llamada lo use:
   `window.RAFITA_CONFIG = { callOrigin: "https://voz.rafita.home" };`
   (vacío = mismo host, puerto 8001).

> **Importante si el proxy está en Docker**: publicar los puertos en
> `127.0.0.1` no sirve al proxy (no puede alcanzar la IP LAN del host). O se
> publican en la LAN (`"8010:8000"` / `"8001:8001"`) o se conecta el
> contenedor del proxy a la red `rafita-network` y se usa
> `http://rafita-agent-core:8000` como upstream. Síntoma típico: NPM marca el
> host «Online» pero responde **502**.
>
> **PWA**: el service worker y la instalación requieren **contexto seguro**
> (HTTPS). Con `HTTP Only` en NPM la web funciona, pero no se podrá instalar
> como app hasta habilitar un certificado (self-signed o dominio real).

## Usuarios y autenticación

- **Email + contraseña** (scrypt + JWT firmado con `WEB_AUTH_SECRET`, que se
  autogenera al primer arranque).
- **Admin inicial**: define `WEB_ADMIN_EMAIL` y `WEB_ADMIN_PASSWORD` en `.env`;
  se crea solo si no hay ningún usuario web.
- Crear o cambiar usuarios en cualquier momento:
  ```bash
  docker exec rafita-agent-core python /workspace/scripts/set_web_password.py \
      email@ejemplo.com 'clave-nueva' [--admin]
  ```
- Registro abierto opcional con `WEB_ALLOW_REGISTRATION=true`.
- **Sign in with Google (OAuth 2.0, opcional)**: define `GOOGLE_WEB_CLIENT_ID`,
  `GOOGLE_WEB_CLIENT_SECRET` y `GOOGLE_WEB_REDIRECT_URI`
  (`https://TU-DOMINIO/api/auth/google/callback`) y añade esa URL como
  *Authorized redirect URI* en Google Cloud Console. Nota: Google exige un
  **dominio real**; los dominios internos (`*.home` de la LAN) no son válidos
  como redirect URI, así que este botón necesita un dominio público
  (por ejemplo vía túnel). El resto de la app no lo necesita.
  El login por Google crea el usuario automáticamente (el primero es admin).

## API principal

| Endpoint | Descripción |
|---|---|
| `POST /api/auth/login` · `POST /api/auth/register` · `GET /api/auth/me` | Auth |
| `GET /api/auth/google/start` · `GET /api/auth/google/callback` | Sign in with Google |
| `POST /api/chat` · `GET /api/chat/history` | Chat (chat_id propio por usuario web) |
| `GET /api/vault/notes` · `GET/POST/DELETE /api/vault/note` | Baúl (confinado a la bóveda) |
| `GET /api/call/token` | Token para la vista Llamada |
| `GET /api/auth/users` | Lista de usuarios (solo admin) |

## Seguridad

- Contraseñas con scrypt y sal por usuario; sesiones JWT HS256 con caducidad
  (7 días) firmadas con `WEB_AUTH_SECRET` (fail-closed si falta).
- La API exige `Authorization: Bearer <token>`; el Baúl valida rutas con
  `resolve_within` (nada de `..`, simlinks fuera o rutas ocultas).
- Los webhooks de n8n siguen con HMAC y **Telegram sigue funcionando igual**.
