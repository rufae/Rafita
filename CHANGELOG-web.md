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

---

## FASE 3 — Coherencia visual y de producto (2026-09-29)

### 3.3 Primero: referencias y mockup (antes de tocar el CSS en bloque)
- **`docs/diseno-web.md`**: brief con referencias (Linear: tipografía compacta
  y jerarquía; Raycast: glass sutil + degradados de marca; Things/Notion:
  estados vacíos con CTA y skeletons) y decisiones (paleta slate+cian→índigo,
  escala tipográfica 12/14/16/20/28, espaciado 4-48, radios 8/12/20/píldora,
  sombras 3 niveles + glow, movimiento `--ease` 120-200 ms).
- **`web/mockup/diseno.html`** + captura `/tmp/f3_mockup.png`: estado objetivo
  (barra con marca, pestañas con indicador, skeleton, vacío con CTA, burbujas,
  modal). Con este mockup aprobado se aplicó a la app real.

### 3.1 Sistema de diseño unificado — IMPLEMENTADO
- **Hallazgo**: dos paletas distintas redefinidas de cero
  (`--bg/--panel/--accent` en la SPA; `--bg-1/--glass/--user-1/…` en la
  llamada), sin fuente común.
- **Cambio**: **`web/app/tokens.css` es la fuente única** (color semántico,
  marca, tipografía, espaciado, radios, sombras, movimiento, ambos temas). La
  SPA lo sirve en `/app/tokens.css` y lo consume (`tokens.css` antes de
  `styles.css`, tokens duplicados eliminados); la página de llamada lo consume
  desde su origen (`GET /tokens.css` nuevo en el servidor de voz) con alias de
  sus variables decorativas (`--err: var(--danger)`, orbes sobre `--brand-500/
  --indigo-500/--violet-500`). `call_rafita.html` sigue siendo fichero aparte.
- **Evidencia**: `tokens.css cargados: True` en la página de llamada (medido
  con `getComputedStyle`); misma paleta semántica en ambas superficies.

### 3.2 Tema de la página de llamada — DECISIÓN EXPLÍCITA + IMPLEMENTADA
- **Decisión**: **ambas superficies respetan `prefers-color-scheme`** (antes la
  llamada era oscuro fijo por omisión). Motivo: coherencia de producto;
  el degradado, el cristal y los orbes se adaptan al tema claro.
- **Cambio**: bloque `@media (prefers-color-scheme: light)` en la página de
  llamada (fondo claro, cristal blanco, `--ok/--warn` del tema, transcripción
  con azul/índigo oscuros para el contraste) — documentado en
  `docs/diseno-web.md`.
- **Evidencia**: contraste y axe en **ambos temas** de la llamada:
  min 5.71:1 (oscuro) / 6.33:1 (claro), 0 violaciones axe. Capturas
  `/tmp/f3_despues_llamada_dark.png` y `_light.png`.

### 3.3b Aplicación del diseño (micro-interacciones con propósito)
- Tipografía con `font-feature-settings: "tnum"`; degradado de marca en
  botones primarios e indicador de pestañas; `focus-visible` con `--glow`;
  hover con elevación y `translateX` en listas; entrada animada de burbujas y
  modal; transiciones con `--ease` (120-200 ms).
- **Evidencia visual**: capturas antes (`/tmp/f3_antes_*.png`) y después
  (`/tmp/f3_despues_chat_dark.png`, `_light.png`, `f3_mockup.png`).
  **Sin regresión de accesibilidad**: axe 0 en chat/baúl/reuniones/llamada
  tras los cambios (Fase 2 mantenida).

### 3.4 Estados vacíos y de carga — IMPLEMENTADO
- **Cambio** (`web/app/app.js`, `styles.css`): `skeletonHTML()` con shimmer
  en las tres listas (chat, baúl, reuniones) y estados vacíos con
  **icono + texto + CTA real** («Nueva nota» → editor; «Grabar una reunión» →
  grabación; «Limpiar búsqueda» → resetea filtros).
- **Evidencia (Playwright)**: `skeleton visibles durante la carga: 4` (captura
  `/tmp/f3_despues_skeleton.png`); estado vacío de reuniones con CTA:
  `True | 🎙️ / Aún no hay reuniones / Graba tu primera reunión…`
  (`/tmp/f3_despues_vacio_reuniones.png`); el CTA «Limpiar búsqueda» vacía el
  filtro (`True`).

### 3.5 manifest.webmanifest — REVISADO Y COMPLETADO
- **Cambio**: `id: "/app/"`, `categories: [productivity, utilities,
  business]`, `screenshots` reales generadas de la SPA
  (`icons/shot-desktop.png` 1280x720 wide + `icons/shot-mobile.png` 540x720
  narrow) y **decisión documentada**: `call_rafita.html` queda **fuera** de la
  experiencia instalable a propósito (otro origen, fuera del `scope`; la vista
  Llamada lo embebe) — comentado en `sw.js` y `docs/diseno-web.md`. `tokens.css`
  añadido al `SHELL` del service worker (offline).
- **Evidencia**: `manifest: id=/app/ categories=[...] screenshots=2
  (accesibles: True) display=standalone`.

### Gate de la Fase 3
`ruff`/`mypy` limpios; **1.312 tests** en verde; desplegado en el HP con
`tokens.css` servido por ambos orígenes; axe sin regresión (0/0/0/0) y
contraste ≥5.71:1 en ambos temas.

---

## FASE 4 — Rendimiento y calidad técnica (2026-09-29)

### 4.4 Auditoría Lighthouse completa (ANTES → DESPUÉS)
Herramienta: **Lighthouse 13.5.0** (Node 20 + chrome-headless-shell real).

| Superficie | Rendimiento | Accesibilidad | Buenas prácticas | SEO |
|---|---|---|---|---|
| SPA | 100 → **100** | 100 → **100** | 78 → **78** | 100 → **100** |
| Página de llamada | 100 → **100** | 100 → **100** | 78 → **78** | 90 → **100** |

- **SEO de la llamada 90→100**: añadido `<meta name="description">`.
- **Buenas prácticas 78**: el único fallo restante es `is-on-https` +
  `redirects-http` (despliegue en HTTP; la habilitación de SSL en NPM lo
  lleva a 100 — decisión de infraestructura documentada en `docs/web.md`).

### 4.1 Audio de la llamada: PCM vs Opus — EVALUADO + DECISIÓN DOCUMENTADA
- **Evaluación**: PCM Int16 16 kHz mono = ~32 KB/s (1,9 MB/min) frente a
  Opus 24 kbps ≈3 KB/s (10× menos). Opus exige `MediaRecorder`/WebCodecs
  (+100-300 ms por trozo y CPU en el hilo principal) y **decodificar en el
  servidor** (ffmpeg por trozo = la latencia que se eliminó a propósito en
  2026-09-27, o un decodificador streaming).
- **Decisión**: se **mantiene PCM por latencia** (turnos de 4-11 s; 0,26
  Mbit/s no es el cuello de botella en LAN/Tailnet). Documentado en
  `docs/web.md` con el camino futuro (`CALL_AUDIO_CODEC=opus`). No es omisión:
  el trade-off está cuantificado.

### 4.2 Build mínimo: minificación + cache-busting — IMPLEMENTADO (decisión documentada)
- **Cambio**: `scripts/build_web.py` (Python puro: `rjsmin` + `csscompressor`,
  **sin Node/Vite** — decisión documentada: para 3 ficheros no compensa).
  Genera `web/app-dist/` con `app-<hash8>.js`/`styles-<hash8>.css`/
  `tokens-<hash8>.css` (caché `immutable` un año), `index.html`/`sw.js`
  reescritos (SHELL hasheado, `CACHE=rafita-shell-<hash>`) y `config.js` sin
  hashear con `Cache-Control: no-store` (editable por instalación). El gateway
  sirve `app-dist/` (producción) o `app/` (desarrollo).
- **Evidencia (tamaños reales)**: `app.js 22236→17319 (78%)`,
  `styles.css 8680→6984 (80%)`, `tokens.css 2025→1156 (57%)`. Cabeceras
  verificadas: `public, max-age=31536000, immutable` en el JS hasheado y
  `no-store` en `config.js`. Test automatizado (`test_build_web_minifica_y_hashea`).
- **Proceso**: el primer deploy dejó `app-dist/config.js` fuera (mi
  `--exclude config.js` de rsync lo excluía también del build) → el navegador
  recibía JSON en un `<script>` y Lighthouse lo penalizó (74). Detectado por
  Lighthouse/console; corregido inyectando el `config.js` real de la
  instalación en `app-dist/` (build en el HP con la fuente local) y con la
  caché `no-store` correspondiente.

### 4.3 Paginación real (notas y chat) — IMPLEMENTADO
- **Cambio**: `/api/vault/notes` con `offset`/`limit` y `total` real; DB
  `get_chat_history(limit, offset)`; cliente con botón **«Cargar más (N
  restantes)»** en el Baúl (páginas de 50, append) y **«Cargar mensajes
  anteriores»** en el chat (offset, insertados arriba en orden cronológico).
- **Evidencia (Playwright, 52 notas de prueba)**: primera página `51` filas
  (50 notas + botón `Cargar más (2 restantes)`) → al pulsar `52` (crece: True).
  Tests del endpoint: páginas sin solaparse, `total` constante, offsets
  correctos. Limpieza posterior verificada.
- **Proceso**: `debounce` reenviaba el evento como parámetro `acumular`
  (truthy) y desactivaba la paginación; lo delató el test real y se fijó con
  `acumular === true` (test futuro blindado).

### Gate de la Fase 4
`ruff`/`mypy` limpios; **1.316 tests** (3 nuevos: build, paginación de notas,
offset de chat); Lighthouse sin regresiones y con el único limitador restante
(HTTPS) documentado.

---

## FASE 5 — Calidad de código y mantenibilidad (2026-09-29)

### 5.1 Linter/formateador para JS/CSS — IMPLEMENTADO
- **Cambio**: **Biome 2.5.14** (`biome.json` en la raíz) para `web/**/*.js|css`
  + manifest; `biome check --write web/` formatea (una vez aplicado sobre
  todo el código) y `biome ci web/` queda como comprobación. **Nuevo job
  `web` en `.github/workflows/ci.yml`**. Regla `complexity/noImportantStyles`
  desactivada a propósito (`.hidden` necesita `!important`); el HTML no lo
  cubre Biome (documentado; su JS inline se prueba con tests).
- **Evidencia**: `biome check web/` → `Checked 6 files. No fixes applied.`
  (0 errores, 0 warnings; antes: 21 errores + 14 warnings, todo corregido —
  formato, `useTemplate`, `useLiteralKeys`, y 5 callbacks de `forEach` que
  devolvían valor: `lint/suspicious/useIterableCallbackReturn`).
- **Verificación tras formatear** (el formateo no debe romper nada): smoke
  real con Playwright → `errores de consola: ninguno`, `axe: 0 violaciones`,
  `estado vacío con CTA: True`, build regenerado y servido.

### 5.2 Tests automatizados del JS — IMPLEMENTADOS
- **`agent/tests/test_web_js_functions.py`** (unitarios de funciones puras,
  en navegador real, sin backend): `formatDuration` (0/90/3600 s),
  `escapeHtml` (no deja `<img` vivo), `debounce` (3 llamadas → 1),
  `mejorMimeGrabacion`, `rmsFromAnalyser(null)`, `authHeaders()` sin/con
  token, `getWsBase()` sin `token=` en la URL, `fetchConTimeout` (aborta y
  dice «tardó demasiado»).
- **`agent/tests/test_web_e2e.py`** (E2E mínimo real): login → enviar mensaje
  de chat → **respuesta del cerebro real** → crear/abrir/guardar/borrar una
  nota del Baúl con el modal propio; exige `RAFITA_WEB_URL/EMAIL/PASSWORD` y
  se salta sin ellas (comportamiento CI).
- **Evidencia**: sin entorno (como CI): `8 passed, 1 skipped` (el E2E);
  **contra el HP real: `9 passed`** (incluido el E2E completo, sin errores de
  página).

### 5.3 `call_rafita.html` monolito — DECISIÓN: se MANTIENE a propósito
- **Decisión**: HTML+CSS+JS inline en un solo fichero **a propósito**: una sola
  petición, sin build ni rutas extra, despliegue trivial en un origen
  independiente (CSP/token/micro propios), y lo compartido ya vive fuera
  (`tokens.css` servido por el servidor de voz). Documentado en
  `web/README.md`; si crece, el primer paso sería extraer `call.js` en el
  mismo servidor de voz.

### 5.4 `web/README.md` — IMPLEMENTADO
- Cómo se sirven las dos superficies y **por qué dos orígenes** (micrófono,
  WebSocket, CSP/token independientes, reinicios aislados), qué espera
  `config.js`, el build (`scripts/build_web.py` + copia de `config.js`),
  formato/lint (Biome), tests (unitarios + E2E con env) y el estado PWA.

### Gate de la Fase 5
`ruff`/`mypy` limpios; **1.324 tests** (8 unitarios JS + 1 E2E, que se salta sin entorno);
`biome ci web/` limpio y añadido a CI; smoke post-formateo sin regresiones.

---

## REDISEÑO VISUAL COMPLETO (2026-09-29)

Encargo: identidad editorial/artesanal propia; prohibido navy+cian, glass,
tarjetas rgba, glow, degradados radiales genéricos, estética SaaS/hacker,
hover con `translateY(-1px)`. Solo presentación: **endpoints, API, auth,
almacenamiento, navegación, WebSocket, audio, vault y lógica intactos**
(confirmado con el E2E real: 9/9).

### Sistema de diseño
- **`web/app/design-tokens.css`** sustituye a `tokens.css`: fuente única
  (color, tipografía, espaciado, radios, bordes, sombras, z-index, duraciones,
  curvas, breakpoints). La SPA y la llamada lo consumen; la llamada desde su
  origen (`GET /design-tokens.css`, ruta y tests renombrados).
- Paleta de **papel cálido + tinta + terracota** (claro) y **carbón cálido**
  (oscuro, no inversión); salvia y petróleo como secundarios; estados maduros.
  Tipografía: **serif editorial** (identidad: wordmark, títulos, chat de
  Rafita, editor del Baúl) + **sans humanista** (interfaz) — fuera `system-ui`
  como identidad.

### Composición
- Fuera tarjetas flotantes y cristal: superficies planas, **líneas finas**,
  sombras casi imperceptibles, espacio negativo y **grano de papel** sutil.
- Masthead con wordmark serif + marca propia; pestañas como **navegación
  subrayada** (filete terracota en la activa).
- **Login asimétrico** en dos columnas editoriales (marca/tesis + acceso),
  apilado en móvil.
- **Chat** editorial: Rafita con marca glyph, nombre en serif y hora; usuario
  en bloque terracota suave; indicador de escritura de tres puntos.
- **Baúl** como editor real: títulos serif, metadatos en versalitas, cuerpo
  del editor en serif para lectura larga.
- **Botones**: sistema completo (primary/secondary/tertiary/ghost/danger/icon
  + sm) con estados (hover/active/focus/disabled/loading); hover por
  color/borde, sin `translateY`.
- **Iconografía**: sprite SVG propio (trazo 1.5) — logo, avatar de Rafita,
  favicon (SVG nuevo) y wordmark diferenciados; iconos PNG regenerados en la
  nueva paleta.
- **Llamada**: los orbes radiales desaparecen → **dos presencias orgánicas
  conectadas por un hilo**; morfología animada, anillo de tinta, halos por
  estado, partículas mínimas en "pensando", hilo que fluye según
  listening/thinking/speaking/reconnecting; el `--level` real sigue animando
  las presencias. `prefers-reduced-motion` respetado en ambas superficies.

### Verificación (ronda real de revisión y corrección)
- **axe-core**: **0 violaciones** en login/chat/baúl/reuniones/llamada en
  claro y oscuro y en la página de llamada.
- **Contraste medido desde tokens**: todo ≥ 4,5:1 (tinta 13,3-14,2; tinta
  suave 5,2-6,7; acento 4,8-6,2; texto sobre acento 5,1-5,9; peligro 6,0-6,7).
  **Problema encontrado y corregido en la revisión**: `--ink-faint` daba
  3,23/3,91 (captions) → oscurecido a `#6f6656` (claro) y `#8d8471` (oscuro)
  y recapturado. axe no lo detectaba porque el grano de papel le impide
  resolver el fondo → por eso el medidor de tokens.
- **Sin scroll horizontal** a 390 px (login/chat/baúl/llamada).
- **Revisión de composición**: serif aplicada al wordmark, login a dos
  columnas, filete de pestaña activa, sans humanista en el cuerpo.
- **Lógica intacta**: E2E real (login → chat con respuesta del cerebro →
  crear/abrir/borrar nota) **9/9**; `biome check web/` limpio; **1.324 tests**.
- **Capturas reales** (19) en `docs/web-screenshots/`: login/chat/baúl/
  reuniones/llamada en claro y oscuro, móvil, y los cuatro estados de la
  llamada (idle/listening/thinking/speaking) + idle en ambos temas. Las
  capturas del manifest (`icons/shot-*.png`) se regeneraron con el rediseño.
  Nota: la captura de "speaking" se obtuvo disparando el **handler real de la
  UI** (el turno completo contra el LLM local excedía la ventana de captura).
