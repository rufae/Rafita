# CHANGELOG — Web de Rafita (SPA + página de llamada)

Auditoría "nivel profesional" con loop plan → implementar → verificar →
documentar. Cada punto lleva su evidencia real (comandos, salidas, capturas).

---

## FASE 0 — Seguridad y riesgos reales (2026-09-29)

### 0.1 XSS real en `addTranscript()` y `log()` — CORREGIDO
- **Hallazgo**: `call_rafita.html` insertaba `msg.text`/`msg` via
  `innerHTML` (líneas 256-266 originales). Cualquier HTML en la transcripción
  STT o en la respuesta del LLM (p. ej. prompt injection leída en voz alta)
  se ejecutaba en el navegador del usuario.
- **Cambio**: ambos usan ahora `createElement` + `textContent` /
  `createTextNode` (nada de `innerHTML` con datos externos):
  `web/call_rafita.html` (`log()`, `addTranscript()`).
- **Evidencia (Playwright + Chromium, payload
  `<img src=x onerror="window.__pwned=1">`)**:
  - ANTES: `window.__pwned = 1` y `window.__pwned_log = 1` → **XSS ejecutado**
    en las dos funciones; el HTML resultante era
    `<img src="x" onerror="window.__pwned=1">` (captura `/tmp/antes_llamada.png`).
  - DESPUÉS: `window.__pwned = undefined` y el transcript muestra el texto
    literal `'Rafita: <img src=x onerror="window.__pwned=1">'`
    (captura `/tmp/despues_final.png`).

### 0.2 Token de sesión viajaba en query string — CORREGIDO
- **Hallazgo**: `withToken()` añadía `?token=...` a cada fetch y a la URL del
  WebSocket (visible en historial, logs de proxy y Referer).
- **Cambio** (`web/call_rafita.html`, `web/app/app.js`,
  `agent/src/voice_stream/server.py`):
  - `withToken` eliminado; los fetch mandan `X-Call-Token` (el backend ya lo
    aceptaba); el WS se abre sin token y autentica con el **primer mensaje**
    `{"type":"auth","token":"..."}` (el navegador no puede poner cabeceras en
    WebSocket). El token llega a la página por **postMessage** desde el iframe
    de la SPA o por el fragmento `#token=` (no viaja al servidor); la URL queda
    limpia (`history.replaceState`).
  - Servidor: token explícito inválido → rechazo inmediato sin revelar si la
    sesión existe; sin token → `auth_required` → valida el primer mensaje →
    `ready`. La query `?token=` sigue aceptada por compatibilidad.
- **Evidencia (red real, Playwright + E2E WS)**:
  - ANTES: `withToken('/call/start')` → `/call/start?token=TESTE123` (token en
    URL).
  - DESPUÉS: `typeof withToken === 'undefined'`; `authHeaders()` →
    `{'X-Call-Token': '<token>'}`; petición real capturada:
    `{'url': 'http://.../call/start', 'x-call-token': '<token>'}` (URL sin
    token); URL del WS sin `token=`; URL del navegador limpia tras el
    fragmento.
  - E2E WS contra el servidor desplegado (4/4):
    (a) sin token + auth correcta → `auth_required` → `ready`;
    (b) sin token + mensaje basura → `auth_required` → `error`;
    (c) `?token=malo` → `error (invalid call token)` inmediato;
    (d) `?token=<bueno>` → `ready` (compatibilidad).

### 0.3 Sin `frame-ancestors` en la página de llamada — CORREGIDO
- **Hallazgo**: `call_rafita.html` se servía sin cabeceras; cualquier sitio
  podía iframearla (página con micro y token).
- **Cambio** (`agent/src/voice_stream/server.py`,
  `_call_page_security_headers()`): `Content-Security-Policy:
  frame-ancestors 'self' <WEB_ALLOWED_ORIGINS>`, `X-Content-Type-Options:
  nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy:
  camera=(), geolocation=(), payment=()`.
- **Evidencia** (`curl -s -D - http://100.121.77.29:8001/`):
  - ANTES: las 3 cabeceras `(AUSENTE)`.
  - DESPUÉS: `content-security-policy: frame-ancestors 'self' http://rafita.home
    http://voz.rafita.home http://localhost:8001` + nosniff + no-referrer +
    permissions-policy. Test unitario `test_call_page_security_headers`.

### 0.4 Controles de depuración expuestos — CORREGIDO
- **Hallazgo**: input `serverUrl` (servidor arbitrario) y botón "Probar TTS"
  visibles para cualquier usuario.
- **Cambio** (`web/call_rafita.html`): envueltos en `#debugControls` oculto por
  defecto; solo se muestran con `?debug=1` (nunca por defecto).
- **Evidencia (Playwright)**:
  - ANTES: `serverUrl visible: True` sin flag.
  - DESPUÉS: `serverUrl oculto por defecto: True`; con `?debug=1`:
    `visible: True`.
  - (Nota: el primer intento dejó el bloque dentro de `animationLoop` por un
    reemplazo ambiguo; detectado por doble `requestAnimationFrame` y corregido.)

### 0.5 Cabeceras en la SPA principal — CORREGIDO
- **Hallazgo**: `/app` se servía sin CSP, `nosniff` ni `Referrer-Policy`; el
  token vive en `localStorage` (XSS lo leería), por lo que la CSP es la
  mitigación principal.
- **Cambio** (`agent/src/utils/webhook_server.py`, middleware): CSP
  (`default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'
  data:; connect-src 'self'; frame-src <origenes confiables>; worker-src
  'self'; base-uri 'self'; form-action 'self'; object-src 'none';
  frame-ancestors 'self'`) + `X-Content-Type-Options: nosniff` +
  `Referrer-Policy: no-referrer` en todas las respuestas del gateway.
  `WEB_ALLOWED_ORIGINS` (coma-separada) es la lista de orígenes confiables de
  la instalación (SPA + llamada + localhost).
- **Evidencia** (`curl -s -D - http://100.121.77.29:8010/app/`):
  - ANTES: las 4 cabeceras `(AUSENTE)`.
  - DESPUÉS: CSP completa, `nosniff`, `no-referrer`, `Permissions-Policy`.
  - Tests `test_spa_security_headers` (incluye que `/api` no lleva CSP de SPA).

### 0.6 Permissions-Policy para el iframe de llamada — CORREGIDO + HALLAZGO
- **Hallazgo mayor**: los navegadores **solo dan acceso al micro en contexto
  seguro (HTTPS o localhost)**. Con la web servida en `http://rafita.home` /
  `http://voz.rafita.home`, `navigator.mediaDevices` es `undefined` y el micro
  está bloqueado por completo (el error viejo era "Permiso de micrófono
  denegado", confuso).
- **Cambio**:
  - SPA: `Permissions-Policy: microphone=(self <origenes confiables>)` para
    delegar el micro al iframe cross-origin (además del `allow="microphone"`
    ya presente).
  - Página de llamada: si no hay contexto seguro, mensaje claro
    *"El micrófono requiere HTTPS: accede por https:// (o desde localhost) o
    añade un certificado en el proxy"* (`startCall()`).
- **Evidencia (Playwright, iframe real sobre localhost = contexto seguro)**:
  - Delegación correcta (`allow="microphone"` + Permissions-Policy del padre):
    `window.__mic = 'ok'`.
  - Sin delegación (padre sin `allow` y política `microphone=(self)`):
    `window.__mic = 'denegado: NotAllowedError'` (el bloqueo por política
    solicitado).
  - Sobre `http://<ip-de-lan>` (sin HTTPS): `navigator.mediaDevices` =
    `false` y el usuario ve el mensaje de HTTPS. Esto exige **activar SSL en
    NPM** (self-signed o dominio real) para usar la llamada desde otros
    dispositivos; documentado en `docs/web.md`.
  - Tests unitarios de cabeceras (`test_call_page_security_headers`,
    `test_spa_security_headers`).

### Gate de la Fase 0
`ruff` + `mypy` limpios; **1312 tests en verde** (2 nuevos de cabeceras);
desplegado en el HP y verificado contra el servidor real.
