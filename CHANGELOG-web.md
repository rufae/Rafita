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

---

## FASE 1 — Robustez y manejo de errores (2026-09-29)

### 1.1 Reconexión del WebSocket con backoff — IMPLEMENTADO
- **Hallazgo**: si `ws.onclose` saltaba sin colgar, la app pasaba a "Llamada
  finalizada" sin reintentar.
- **Cambio** (`web/call_rafita.html`): estado nuevo `RECONNECTING`
  (`.status.reconnecting` con punto intermitente), `programarReconexion()`
  con backoff 1s/2s/4s (3 intentos, máximo) y distinción clara entre
  "reconectando" y "llamada terminada" (solo el colgado explícito termina;
  `intentionalHangup` + limpieza del timer).
- **Evidencia (Playwright, servidor de voz local, llamada real con micro
  falso)**: cierre inesperado del WS a mitad de llamada →
  `"Conexión perdida. Reconectando (intento 1/3)..."` → a los ~1s
  `"Reconectado. Te escucho..."` con `isCallActive === true` (la llamada sigue
  viva). Nota: `set_offline` de Chromium no corta los `ws://` ya abiertos, así
  que el corte se fuerza desde la página (mismo camino `onclose` que un corte
  de red real).

### 1.2 ScriptProcessorNode → AudioWorkletNode — IMPLEMENTADO
- **Hallazgo**: `createScriptProcessor` está deprecado, corre en el hilo
  principal y requería el parche `micGain.gain.value = 0`.
- **Cambio** (`web/call_rafita.html`): `AudioWorkletNode` cargado desde un
  Blob (la página sigue siendo un único fichero), processor `pcm-capture` que
  envía PCM Int16 por `port.postMessage`; salida en silencio para mantener el
  nodo vivo. `ScriptProcessorNode` queda **solo como fallback** si el navegador
  no soporta AudioWorklet (mensaje en el log que distingue qué modo se usa).
- **Evidencia (Playwright)**: log real de la llamada:
  `Captura de audio lista (AudioWorklet, 16000 Hz)` (antes:
  `Captura de audio lista (16000 Hz)` con ScriptProcessor).
- **Nota de proceso**: el primer reemplazo falló en silencio (oldString mal
  escrito) y dejó `startAudioCapture().catch()` sobre `undefined`, colgando la
  UI en "Conectando..."; lo delató la evidencia (log con formato viejo +
  estado sin actualizar) y se corrigió en el mismo loop.

### 1.3 fetch sin timeout → AbortController — IMPLEMENTADO
- **Hallazgo**: una petición colgada dejaba la UI muerta indefinidamente.
- **Cambio**: `fetchConTimeout()` (10s en general, 30s para subir audio)
  implementado en **ambas superficies** (`web/app/app.js` y
  `web/call_rafita.html`); error claro: *"La petición tardó demasiado (10s).
  Comprueba la conexión."*
- **Evidencia (Playwright, respuesta retrasada 12s)**:
  - SPA (búsqueda del Baúl): `Error: La petición tardó demasiado (10s)…`
  - Llamada (`/call/start`): `Error al iniciar: La petición tardó demasiado (10s)…`

### 1.4 Baúl: aviso de cambios sin guardar — IMPLEMENTADO
- **Hallazgo**: cambiar de nota con la anterior editada perdía el contenido.
- **Cambio** (`web/app/app.js`): flag `noteDirty` + modal de confirmación
  ("¿Descartarlos?" / "Seguir editando") al cambiar de nota, crear nueva o
  salir de la página (`beforeunload`); se limpia al guardar/borrar.
- **Evidencia (Playwright)**: editada la nota A y clic en la B → modal
  *"La nota actual tiene cambios sin guardar…"*; al cancelar el contenido
  sigue intacto (`editado sin guardar` presente); al descartar abre la otra
  nota.

### 1.5 MediaRecorder: mimeType soportado — IMPLEMENTADO
- **Hallazgo**: se asumía `audio/webm` (falla en Safari, que prefiere mp4).
- **Cambio** (`web/app/app.js`): `mejorMimeGrabacion()` prueba
  `audio/webm;codecs=opus` → `audio/webm` → `audio/mp4` → `audio/ogg` con
  `MediaRecorder.isTypeSupported()`; si ninguno sirve, mensaje claro (sin
  grabación silenciosa); el nombre del fichero se adapta (`.webm`/`.mp4`).
- **Evidencia (Playwright)**: `mimeType elegido: audio/webm;codecs=opus`;
  forzando `mejorMimeGrabacion = () => null` → mensaje
  *"Tu navegador no permite grabar audio (falta soporte de webm/mp4)."*

### 1.6 alert()/confirm() nativos → modal propio — IMPLEMENTADO
- **Hallazgo**: los diálogos nativos rompían el diseño (borrar nota, errores).
- **Cambio** (`web/app/index.html`, `styles.css`, `app.js`): `mostrarModal()`
  (título, mensaje, confirmar/cancelar) con `role="dialog"` y `aria-modal`,
  `mostrarAviso()` para avisos; usado en borrar nota, cambios sin guardar y
  errores de llamada.
- **Evidencia (Playwright)**: `1.6 dialogos nativos disparados: 0` (no se
  lanzó ningún `window.confirm/alert`), modal visible con el texto correcto y
  funcionales Cancelar/Aceptar.

### Gate de la Fase 1
`ruff`/`mypy` limpios; **1.312 tests** en verde; desplegado en el HP
(`mostrarModal`, `WORKLET_CODE`, `programarReconexion` verificados servidos).

---

## FASE 2 — Accesibilidad real (2026-09-29)

Herramienta: **axe-core 4.10.3** (DevTools) en Chromium real + cálculo de
contraste WCAG desde tokens (`getComputedStyle`). Puntuación antes/después:

| Superficie/vista | axe ANTES | axe DESPUÉS |
|---|---|---|
| login | 2 (`landmark-one-main`, `region`×7) | **0** |
| chat | 2 (`page-has-heading-one`, `region`×3) | **0** |
| baúl | 4 (`color-contrast` **serious**, `scrollable-region-focusable` **serious**, `page-has-heading-one`, `region`×3) | **0** |
| reuniones | 3 (`color-contrast` **serious**×2, `page-has-heading-one`, `region`×3) | **0** |
| llamada (vista) | 3 (`color-contrast` **serious**×2, `page-has-heading-one`, `region`×3) | **0** |
| página de llamada | 0 | **0** |
| **Total** | **14 violaciones (6 serious)** | **0** |

### 2.1 Pestañas con semántica WAI-ARIA completa — IMPLEMENTADO
- **Hallazgo**: `role="tab"` suelto, sin `aria-selected`, `aria-controls`,
  `tabindex` gestionado ni navegación por flechas.
- **Cambio** (`web/app/index.html`, `app.js`): `tablist` con `aria-label`,
  pestañas con `id`/`aria-controls`/`aria-selected`/roving `tabindex`
  (activa 0, resto -1), paneles `role="tabpanel"` +
  `aria-labelledby` + `tabindex="0"`, y teclado completo (←/→/Home/End)
  en el patrón WAI-ARIA tabs.
- **Evidencia (Playwright, solo teclado)**: `ArrowRight` → `tab-vault`
  (`aria-selected=true`, vista `view-vault` visible); `End` → `tab-call`;
  `Home` → `tab-chat` con `tabIndex 0` (activa) / `-1` (inactiva).

### 2.2 Estado de la llamada sin `aria-live` — IMPLEMENTADO
- **Cambio** (`web/call_rafita.html`): `#status` con `role="status"`,
  `aria-live="polite"` y `aria-atomic="true"` (el lector de pantalla anuncia
  "Escuchando…", "Pensando…", "Hablando…", "Reconectando…").
- **Evidencia**: `aria-live en #status: polite | role: status`.

### 2.3 Botones con emoji: `aria-label` y `aria-pressed` — IMPLEMENTADO
- **Cambio** (`web/call_rafita.html`): `aria-label` en los tres botones
  ("Iniciar llamada"/"Colgar llamada" dinámico, "Parar de hablar",
  "Mantener pulsado para hablar") y `aria-pressed` en el push-to-talk
  (se actualiza en `pttDown`/`pttUp`, no solo la clase CSS).
- **Evidencia (llamada activa, micro falso)**: `PTT presionado: true` →
  `PTT soltado: false`; labels: `callBtn=Iniciar llamada, stopBtn=Parar de
  hablar, pttBtn=Mantener pulsado para hablar`.

### 2.4 Contraste WCAG AA medido y corregido — IMPLEMENTADO
- **Hallazgos (axe, tema claro)**: `#note-delete` **3.76:1** (min 4.5),
  `#meet-tab`/`p` **4.34:1**, `#meet-summary` **3.86:1**; y en tokens oscuros
  `--danger` 3.71:1 sobre `--panel` + `.log` de la página de llamada 3.2:1.
- **Cambio** (`web/app/styles.css`, `web/call_rafita.html`): `--muted` claro
  `#64748b` → `#4e5a72`; `--danger` por tema (`#f87171` oscuro / `#b91c1c`
  claro); `.log` `#5b6b8c` → `#7b8bb0`.
- **Evidencia (cálculo de tokens, ambos temas)** — todo ≥ 4.5:1:
  `--muted` sobre `--bg`/`--panel`/`--panel-2`: 4.89-6.96:1 (oscuro) y
  5.62-6.93:1 (claro); `--danger`: 5.29-6.47:1; `--text`: 14.48-16.30:1.
  Página de llamada: `.log` 5.6:1 (aprox. sobre el degradado; axe no puede
  evaluar degradados).

### 2.5 Formularios sin `<label>` asociado — IMPLEMENTADO
- **Cambio** (`web/app/index.html`, `styles.css`): `<label class="sr-only">`
  asociado por `for` en búsqueda del Baúl, carpeta, ruta de nota, título de
  reunión y chat (la página de llamada ya lo tenía desde la Fase 0).
- **Evidencia**: `2.5 inputs sin label asociado: []`.

### Bonus (hallazgos de axe, corregidos en el mismo loop)
- `page-has-heading-one` → `<h1 class="sr-only">Rafita</h1>` en la app.
- `landmark-one-main`/`region` → `#login` pasa a `<main>`, `<header>` con
  `role="banner"` (está dentro de un `<section>` y perdía su rol) y el modal
  se movió dentro de `<main>`.
- `heading-order` (h3 sin h2) → `#meet-detail-title` y `#modal-titulo` a `h2`.
- `scrollable-region-focusable` → listas de notas/reuniones con `tabindex="0"`
  (scroll con teclado).

### Gate de la Fase 2
`ruff`/`mypy` limpios; **1.312 tests** en verde; desplegado en el HP y
verificado con axe-core contra el servidor real.
