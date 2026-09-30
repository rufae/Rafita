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

---

## ARREGLOS DE USO REAL: HTTPS de Tailscale, errores amigables y móvil (2026-09-29)

### Contexto (lo que encontró el usuario)
- `https://nodochicohp.taildafbf0.ts.net/` (Tailscale Serve) apuntaba a la
  **voz** (`127.0.0.1:8001`), no a la SPA → abrir esa URL daba la página de
  llamada sin token y un **401 crudo**.
- `rafita.home` es HTTP y el micrófono exige contexto seguro → no funcionaba.
- En móvil (390 px) **la barra de pestañas quedaba aplastada a 26 px** y las
  pestañas se salían de pantalla (x=427/504/622): "no se ven las opciones".

### Recomendación de entrada (documentada)
Usar el **HTTPS real de Tailscale** como entrada canónica (certificado válido,
sin avisos, funciona desde cualquier dispositivo del tailnet):
```bash
sudo tailscale serve --bg --https=443  http://127.0.0.1:8010   # SPA
sudo tailscale serve --bg --https=8443 http://127.0.0.1:8001   # voz
```
`rafita.home` queda como comodidad de LAN (HTTP, sin micrófono); no se le pone
certificado propio porque sería autofirmado (avisos) y el ts.net ya da HTTPS
válido. Alternativa: NPM con 301 de `rafita.home` al ts.net (opcional).

### Preparado en código para ese HTTPS
- La SPA **elige sola el origen de voz**: HTTPS → mismo host `:8443`;
  HTTP en LAN → `:8001`; `config.js` puede forzarlo (en el HP queda vacío).
- `WEB_ALLOWED_ORIGINS` del HP incluye los orígenes ts.net: la CSP de la SPA
  los permite en `frame-src` y la página de llamada en `frame-ancestors`
  (verificado en cabeceras reales).

### Errores amigables (nunca un código en crudo)
- **Llamada sin token** → "Abre esta página desde la pestaña Llamar de Rafita
  para autorizar el micrófono." (antes: 401 crudo). Además 401/403/404/5xx y
  fallos de red tienen mensajes propios (`mensajeHttp()`).
- **SPA, sesión caducada** (401) → vuelve al login con "Tu sesión ha caducado.
  Vuelve a entrar." (antes: logout silencioso).
- **SPA, red caída** → "No se pudo conectar con el servidor. Comprueba tu
  conexión."; 5xx → "El servidor no pudo completar la petición…".
- Verificado con Playwright: los tres mensajes, literales.

### Móvil (bug real corregido)
- **Causa**: `.tabs` tenía `flex: 1` (basis 0) y con `flex-wrap` no saltaba de
  línea; a 390 px se comprimía a 26 px y las pestañas quedaban fuera.
- **Arreglo**: en móvil `.tabs { flex: 0 0 100%; min-width: 0 }`, hueco de
  pestañas a 12 px (las 4 caben a 360 px), toolbars en columna a ancho
  completo, objetivos táctiles más grandes y `overflow-wrap` en burbujas.
- **Dos violaciones axe nuevas detectadas y corregidas**: la hora dentro del
  bloque del usuario (contraste) → color mezclado terracota/tinta; el chat
  scrolleable → `tabindex="0"` + `aria-label`.
- **Verificación**: a 390 y 360 px las 4 pestañas dentro del viewport, sin
  scroll horizontal, **axe 0** en login/chat/baúl y E2E real **9/9**.
- Capturas actualizadas en `docs/web-screenshots/` (móvil 390 y 360).

---

## LOGIN CON GOOGLE: diagnóstico y UX (2026-09-29)

### Por qué no funcionaba
`GOOGLE_WEB_CLIENT_ID` y `GOOGLE_WEB_CLIENT_SECRET` están **vacíos** en el
`.env` del HP: no hay credenciales OAuth de la web. Además, al pulsar el botón
la SPA caía al endpoint de redirect y mostraba **JSON crudo** (503).

### Cambios
- **`GET /api/auth/google/status`** (nuevo): la SPA sabe si Google está
  configurado antes de ofrecer el botón.
- **`/auth/google/start`** sin configurar → `307 /app/#google-error=no_config`
  (antes: JSON 503). **Callback** con error/estado caducado/fallo de Google →
  `307 /app/#google-error=denied|state|google` (antes: JSON 400/401).
- **SPA**: clic en Google sin configurar → mensaje claro y se queda en la SPA
  (sin navegar); si el flujo de dispositivo no está disponible, usa el redirect
  (sus errores vuelven como fragmento y se explican); mensajes para
  `no_config/denied/state/google` y limpieza del fragmento.
- **Página de llamada** abierta sin token: además del aviso, enlace
  «Ir a Rafita» cuando se sirve por HTTPS.
- `deploy/hp/tailscale-serve.sh`: un solo `sudo bash …` para exponer SPA (443)
  y voz (8443) por HTTPS con certificado válido de Tailscale (documentado en
  `docs/web.md`).

### Evidencia
- HP: `status` → `{"configured":false,"login_ready":true}`;
  `start` → `307 location: /app/#google-error=no_config`;
  `callback?error=access_denied` → `307 … google-error=denied`.
- SPA (Playwright): clic en Google → mensaje amigable, **sin navegación a
  JSON**; `#google-error=denied` → «Has cancelado el acceso con Google…» y
  fragmento limpiado.
- Test nuevo `test_google_status_y_errores_vuelven_a_la_spa`; **1.325 tests**.

### Para activarlo (usuario)
1. Ejecutar una vez: `sudo bash ~/proyectos/rafita/deploy/hp/tailscale-serve.sh`
   (deja la SPA en `https://nodochicohp.taildafbf0.ts.net/` y la voz en `:8443`).
2. Google Cloud Console → Credenciales → crear cliente **Web application** con
   *Authorized redirect URI*:
   `https://nodochicohp.taildafbf0.ts.net/api/auth/google/callback`.
3. Pegar en `.env`: `GOOGLE_WEB_CLIENT_ID`, `GOOGLE_WEB_CLIENT_SECRET` y
   `GOOGLE_WEB_REDIRECT_URI=https://nodochicohp.taildafbf0.ts.net/api/auth/google/callback`
   y `docker compose … up -d rafita-agent-core`.
   (Alternativa sin dominio: cliente *TVs and Limited Input devices* y flujo de
   dispositivo con código — ya implementado.)

---

## GOOGLE ACTIVADO + CALIDAD DE VOZ/STT + CONTEXTO DE LLAMADA (2026-09-29)

### Google: credenciales pasadas al HP y flujo listo
- Credenciales del `.env` local pasadas al HP
  (`GOOGLE_WEB_CLIENT_ID/SECRET/REDIRECT_URI`, callback en el ts.net).
- Verificado en el HP: `/api/auth/google/status` → `configured: true`;
  `/auth/google/start` → redirige a `accounts.google.com` con el client_id
  real. (El JSON descargado no hace falta: son los mismos valores.)
- Recordatorio: el *Authorized redirect URI* en Google Cloud debe ser
  `https://nodochicohp.taildafbf0.ts.net/api/auth/google/callback` (ya está en
  el `.env`).

### STT (transcripciones): de "lamentables" a precisas
- **Modelo**: `WHISPER_MODEL=small` en el HP (antes `tiny`→`base`).
- **Voz en llamada**: `beam_size=5`, `speech_pad_ms=300`, fallback de
  temperatura `[0.0, 0.2, 0.4]` (más robusto en audios cortos) e
  `initial_prompt` en español (ya existía).
- **Notas de voz de Telegram**: `beam_size=5`, `initial_prompt`,
  `condition_on_previous_text=False`.
- **Reuniones**: `initial_prompt` + `condition_on_previous_text=False`.
- **Evidencia (piper → STT en el HP)**:
  - «Hola Rafita, quiero que me digas qué tiempo hace mañana en Sevilla.» →
    **exacto**.
  - «Ponme un recordatorio para mañana a las nueve de la mañana.» → **exacto**.
  - «Hola, ¿qué tal estás?» → «Hola, ¿qué tal le estás?» (mínimo).
  - «Busca el correo de Soraya…» → «…de sol allá…» (nombre propio sintético).
  - **Limitación honesta**: una palabra aislada de ~0,4 s («Hola» solo) es
    inestable en Whisper-small (alucina «Bueno/Por favor» según la pasada);
    con frases normales (≥1 s) es fiable. Documentado.

### Llamada: contexto limpio y respuestas con sentido
- **Causa del sinsentido** («…el correo de Soraya…» al decir «Hola»): el
  historial de voz (chat_id 0) se acumulaba **entre llamadas** y contaminaba.
  **Arreglo**: al iniciar cada llamada se limpia el historial de voz
  (`delete_chat_history(0)`), así cada llamada empieza limpia.
- **Evidencia**: llamada 1 («…llamar a Soraya mañana») → respuesta correcta;
  llamada 2 («Hola») → «De acuerdo, ¿en qué puedo ayudarte hoy?» **sin
  mencionar a Soraya**.
- **Fillers**: el texto ya no dice «estoy buscando» (engañoso cuando solo
  tarda el LLM); piscina neutra («Dame un momento, por favor…», «Un
  segundo…»…) y umbral 2,2 s.
- **Herramientas en voz**: lo dictado por llamada (tareas/eventos/gastos/CRM)
  se guarda con el **chat del administrador** (`tool_chat_id`), no con
  `chat_id 0` invisible. Con Google conectado, las tareas van a Google Tasks
  (verificado en la prueba E2E).
- **Guardia de honestidad ampliada**: «He **anotado**…» no se detectaba
  (el modelo afirmó haber anotado una tarea sin llamar a la herramienta);
  añadidas variantes («he anotado», «he tomado nota», «tomé nota»…) + test de
  regresión.
- **Latencia**: el LLM está en la **torre GPU** (verificado `backend=gpu`);
  una llamada simple tardó ~4 s total.

### Gate
`ruff`/`mypy`/`biome` limpios; **1.326 tests** (regresión «He anotado»);
desplegado y verificado en el HP.

---

## REUNIONES: EDITAR Y BORRAR + RAG (2026-09-29)

### ¿Están en el Baúl y las puede usar Rafita? SÍ
- Cada reunión guarda su **acta en `Reuniones/`** de la bóveda (resumen,
  puntos clave, decisiones, tareas y transcripción) y aparece en la vista
  Baúl. El **VaultIndexer la indexa automáticamente** (evidencia en logs:
  `Reuniones/prueba-editar.md -> 5 chunks`).
- **Recuperación verificada (RAG)**: consulta a la base vectorial con
  «punto X importante reunion de prueba» → devuelve los fragmentos del acta
  (`Reunion de prueba sobre el punto X.`, `- punto X importante`). Es decir,
  Rafita puede citarla en chat/llamada.

### Editar — IMPLEMENTADO
- **API**: `PATCH /api/meetings/{id}` con `{title?, transcript?}`.
- **Servicio**: actualiza la BD y el acta del Baúl de forma **quirúrgica**
  (título del frontmatter + sección de transcripción, tolerando cabecera con
  o sin tilde) **conservando** resumen, puntos clave, decisiones y tareas;
  el watcher la reindexa.
- **UI**: el título es editable y la transcripción también; botones
  «Guardar»/«Borrar» que aparecen al seleccionar una reunión. El resumen
  (generado por IA) se sigue editando desde el Baúl.
- **Evidencia**: PATCH → título/transcripción actualizados en BD y en el
  acta, secciones conservadas; UI: «Cambios guardados ✓».

### Borrar — IMPLEMENTADO
- **API**: `DELETE /api/meetings/{id}?borrar_nota=true` (por defecto borra
  también el acta).
- **Servicio**: elimina audio (`.webm`/`.wav`), acta del Baúl (con
  `resolve_within`) y la fila de la BD.
- **UI**: botón «Borrar» con modal propio («Se borrará la reunión, su audio
  y su acta del Baúl»).
- **Evidencia**: UI → modal → aceptar → la reunión desaparece de la lista y
  el fichero del acta ya no existe en el HP.

### Tests y gate
Nuevos: `test_editar_reunion_actualiza_nota_y_conserva_secciones`,
`test_borrar_reunion_borra_su_acta`, `test_meetings_editar_y_borrar`
(API). **1.329 tests**, ruff/mypy/biome limpios, desplegado y verificado en
el HP.

---

## CHAT PROFESIONAL + COHERENCIA LLAMADA + BATERÍA DE HERRAMIENTAS (2026-09-30)

### Chat: burbujas, agrupación y composición — IMPLEMENTADO
- Burbuja real para Rafita (fondo `--surface-2`, borde, radios asimétricos),
  avatar por grupo (`reagruparBurbujas()`: los mensajes consecutivos del mismo
  emisor van en `.continuacion` sin repetir avatar/nombre), columna de lectura
  de 820 px centrada, ritmo vertical por emisor/turno, indicador de escritura
  con 3 puntos animados en burbuja, compositor sticky y **render de tablas
  Markdown** seguro (`renderizarMarkdown`: escape + tablas + negritas + código).
- Evidencia (Playwright, ambos temas y 390 px): agrupación correcta, tabla con
  1 tabla/2 th, 3 puntos, columna 820 px, axe 0, sin overflow, horas AA
  (4,68–11,22). Capturas: `chat-antes-*.png` / `chat-despues-*.png`.
- Fixes encontrados al verificar: `--panel-2` inexistente (la burbuja quedaba
  transparente) → `--surface-2`; hora del bot/usuario a `--ink-soft`/`--ink`
  (2,93/1,52 → 4,68/5,62 y 10,78/11,22); data-URIs SVG con espacios que el
  minificador rompía (avatar invisible) → codificados `%20` (verificado en el
  CSS minificado: `svg%20xmlns`).

### Timeout del chat — CORREGIDO
- `api()` tenía 10 s fijos (Fase 1): las respuestas RAG tardaban 10,5 s y el
  cliente abortaba dejando burbujas cruzadas. Ahora `api(path, opts, ms)` y el
  chat usa 180 s. (Regresión detectada por la batería.)

### Llamada: coherencia visual
- Componente `.notice` (info/warn/error/ok con icono) para la zona de avisos;
  checkboxes VAD/eco → interruptores `.switch`; botones unificados `.ctl`
  (Iniciar/Parar/Mantener + «Ir a Rafita» ghost). Presencias verificadas:
  misma técnica en ambas (fondo plano + morph), solo cambia el color. axe 0
  en ambos temas. Capturas `llamada-antes-*.png` / `llamada-despues-*.png`.

### Batería de herramientas (chat web real, 24 casos)
- VERIFICADAS con efecto real: Google Calendar crear/listar/borrar (evento
  creado, listado y eliminado — comprobado por API), n8n `trigger_n8n`
  (ejecución 97 en el flujo «Rafita · 7 Automatizacion · Ejecutable desde
  chat/voz»), Gmail búsqueda (encuentra el correo de prueba), Google Drive
  listar, nota en el Baúl, `save_expense` y `manage_google_tasks` en contexto
  fresco (comprobado en BD), `get_finance_summary` (BD: 3 transacciones).
- HALLAZGOS (reportados, no arreglados en esta tanda):
  1. Tool-calling **no determinista** de qwen2.5:7b: el mismo mensaje funciona
     o no según la ejecución (p. ej. «Registra un gasto de 5 €» falló y
     «7 €» funcionó seguido; con 106 mensajes de historial empeora). La
     guardia de honestidad evita datos falsos (respuesta honesta) pero la
     acción se pierde.
  2. Guardia de honestidad (`orchestrator.py:33-40`): **falsos positivos**
     (`encontr[eé]`, `resultados?`, `busc\w+`, `aquí tienes`, `te muestro`,
     `un momento` matan respuestas honestas «no encontré nada») y **falsos
     negativos** (`he eliminado`, `he borrado`, «ya he programado el envío»,
     «he marcan») → llegó a afirmar tareas borradas/completadas y correos
     enviados sin tool call.
  3. `create_event` (chat.py:950-958): espera `event_datetime` «YYYY-MM-DD
     HH:MM» sin parseo relativo; el modelo le pasa `when` (nombre de otras
     tools) y el error dice «No se proporcionó una fecha válida» (engañoso).
  4. ~~`manage_google_tasks` gestiona la BD local aunque Google esté
     conectado~~ **CORREGIDO EL DIAGNÓSTICO (2026-09-30)**: la tool SÍ usa
     Google Tasks cuando está conectado; la tarea local fue un artefacto de
     mi script de prueba (proceso sin `google_services.initialize()`).
     Verificado en la app real: crear/listar/completar/borrar van a Google.
  5. No existe tool para **listar alertas** (`create_alert` solo crea):
     «¿qué alertas tengo?» acabó creando una alerta duplicada y respondiendo
     con eventos de Calendar.
  6. No existe tool meteorológica en el chat (AEMET solo en el briefing);
     responde con honestidad o busca en web.
  7. `get_finance_summary` devuelve **texto plano**, no tabla Markdown (el
     chat sí renderiza tablas: verificado con DOM real).
  8. Chroma: warnings «Add of existing embedding ID» re-indexando
     `Calendario Semanal.md` + errores de telemetría posthog (ruido).

---

## ARREGLOS DE LA BATERÍA DE HERRAMIENTAS (2026-09-30)

Petición del usuario: los datos de prueba seguían en Google Calendar/Tasks y
había que arreglar los hallazgos anteriores "al 100% real". Hecho:

### Limpieza de datos visibles
- **Google Calendar**: eliminado el evento "Prueba bateria" (30/09 10:00) que
  las baterías dejaron atrás (CAL-3 había alucinado el borrado). Verificado
  por API: solo quedan eventos reales.
- **Google Tasks**: eliminadas "Revisar los documentos de la carpeta docs/…"
  y "Test Audit Google Services" (de auditorías previas). Lista vacía.
- Además se limpiaron los datos creados durante esta sesión (eventos/tareas/
  alertas/gastos de prueba y la nota `Control_Financiero_2026.md`).

### Guardia de honestidad — corregida (falsos positivos y negativos)
- `orchestrator.py` `_ACTION_CLAIM_RE`: cubre ahora `he eliminado/borrado/
  marcado/programado/movido/actualizado` (y variantes con raíz, p. ej. "he
  marcan") y el sustantivo "búsqueda"; `_NEGACION_RE` evita falsos positivos
  ("no he encontrado", "sin resultados", "no puedo buscar"). Tests:
  `test_hallucination_risk_cubre_verbos_que_faltaban` y
  `..._no_confunde_negaciones_ni_negativas`.
- **Guardia de tool fallida** (`generate_response`): si TODAS las tools
  devolvieron `success=False` y el texto afirma éxito, se responde con el
  error real ("No he podido completar la acción: …").

### Reintentos de tool-calling (qwen2.5:7b no determinista)
- Umbrales semánticos calibrados con mensajes reales (bge-m3): reintento
  suave ≥0.45; forzado ≥0.50; con confianza ≥0.60 se ofrece **una sola**
  tool, si no el **top-3**.
- **Selector por texto**: cuando el ranking no es fiable, el modelo elige la
  herramienta viendo el catálogo con descripciones (sin embeddings).
- El reintento forzado usa **contexto limpio** (system + petición + aviso):
  el historial sesgaba al modelo a responder sin herramientas.
- **Recuperación**: si todas las tools fallan, se pregunta por la correcta
  viendo el catálogo COMPLETO y se fuerza una vez; si acierta, se usan esos
  resultados.
- **Composición vacía**: reintento sin herramientas (evita "No pude generar
  una respuesta").
- `ACTION_RULE` añadida al prompt: si una herramienta puede hacerlo, llámala
  en el mismo turno.

### Herramientas nuevas y corregidas
- `create_event`: acepta `when`/`start_datetime`/`datetime_str` (el modelo
  pasaba `when` y fallaba con "fecha no válida") y parsea fechas relativas
  con `parse_relative_datetime` (+ fallback ISO). Tests nuevos.
- `manage_google_tasks`: complete/delete por `task_title`/`title` (además de
  `task_id`) — el modelo no suele tener el ID.
- `get_alerts` (nueva): lista alertas pendientes en tabla Markdown.
- `get_weather` (nueva): AEMET si hay clave (ciudad conocida) u open-meteo
  (geocoding + forecast), caché 15 min; nunca inventa.
- `get_finance_summary`: devuelve tabla Markdown (2 tablas en el DOM real:
  resumen + gastos por categoría).
- `manage_google_calendar`/`create_google_calendar_event`/`create_event`/
  `manage_google_tasks`: descripciones afinadas (borrar/eliminar, cita vs
  tarea) calibradas con el selector real.
- `google_services.list_events`: `timeMin=now` por defecto (sin él devolvía
  eventos de 2001).
- Chroma: `upsert` en `index_chunks` (adiós "Add of existing embedding ID")
  y logger de posthog silenciado (adiós errores de telemetría).

### Evidencia (batería final, chat web real)
- Calendario: crear (relativo) → listar → **borrar** ✓ (verificado por API).
- Tareas: crear → listar → borrar por título ✓ (verificado en Google Tasks).
- Alertas: `get_alerts` lista ✓. Tiempo: AEMET/open-meteo responde ✓.
- Finanzas: tabla renderizada en el DOM ✓. Contactos: teléfono ✓.
- Gmail: encuentra correos reales ✓. Negativas: "no encontré" honesto sin
  alucinar ✓. Small talk sin herramientas ✓.
- Logs: se ven los reintentos suaves/forzados y la recuperación actuando.

### Limitación honesta que queda
qwen2.5:7b sigue teniendo varianza de redacción (a veces convierte la tabla
de finanzas en prosa, o dice "no encontré" aunque la tool devolvió datos, o
elige "evento" para un recado ambiguo). Las **herramientas** ejecutan y se
verifican al 100% (API/BD); lo que varía es cómo lo cuenta el modelo. El
selector + recuperación reducen el problema, pero no lo eliminan con un 7B.

---

## VOZ Y MODELO: gemma4:12b + STT large-v3 en GPU + VAD anti-ruido (2026-09-30)

Petición: todas las herramientas al 100%, gemma4:12b en la GPU de la torre
con fallback al Dell, llamada coherente, y transcripciones limpias (una
reunión real se transcribió como basura). Análisis y arreglos:

### Diagnóstico (¿era el modelo?)
- **Transcripción**: NO era solo el modelo. La reunión usaba Whisper `small`
  en CPU (HP: 6 GB, 4 núcleos) con `beam_size=1`, sin filtro de ruido y sin
  diarización de confianza. El audio real de prueba (`229bf9f1….wav`) con
  `small+beam1` daba basura ("¿Tú estás a la mierda, asistente?"); con
  **large-v3 + VAD en la GPU** da `"¡Gracias!"` (nsp=0.07) y descarta el
  ruido. Además el HP no puede con modelos grandes.
- **Tool-calling**: comparativa real con el catálogo completo:
  gemma4:12b 10/16 y qwen2.5:7b 10/16 en primera pasada (fallos similares en
  alertas/tiempo/finanzas), pero gemma es **más coherente al redactar** y
  similar en latencia en GPU (1,7-10 s vs 0,6-8,6 s). En CPU (Dell) gemma
  tarda 80-140 s/turno: aceptable como fallback de chat, no para llamada.

### Modelo: gemma4:12b con GPU de la torre y fallback al Dell
- `OLLAMA_MODEL=gemma4:12b` en el HP. `_pick_backend` ya prefería la torre
  (`OLLAMA_GPU_HOST`) y cae al Dell si la torre está apagada (mismo modelo en
  CPU, más lento). Verificado en logs: `model=gemma4:12b backend=gpu`.
- Batería real por el chat web con gemma: tarea crear/borrar, evento
  crear/borrar, tiempo, alertas, finanzas, contacto y negativas honestas ✓.

### STT: servicio large-v3 en la GPU de la torre + fallback local
- Nuevo `deploy/tower/whisper_service.{py,sh}` + unidad systemd de usuario:
  faster-whisper **large-v3** en la RTX 3060, endpoint `/transcribe` con
  filtros (nsp/logprob) y `/health`. Inferencia ~0,7 s para 7,5 s de audio.
- Nuevo `agent/src/services/stt_service.py`: STT unificado (remoto primero,
  local si no responde) con filtros anti-ruido y lista negra de
  alucinaciones ("suscríbete", "subtítulos realizados…").
- Lo usan: llamada (`voice_stream`), reuniones (`meeting_service`, ahora
  beam 5 + prompt) y voz de Telegram (`handlers/audio`).
- **E2E real**: conversación sintética de 2 voces (Piper + Kokoro) con ruido
  de fondo al 3% → transcrita en 4 s, texto casi exacto y **diarización
  correcta** (Hablante 1 / Hablante 2). Antes: basura.
- El HP mantiene `WHISPER_MODEL=small` como fallback (si la torre está off).

### Llamada: VAD adaptativo y anti-ruido
- `_vad_adaptativo`: umbral = percentil 20 del RMS de los últimos 3 s × 3,
  con suelo 200 y **tope 1000** (sin tope, un monólogo continuo convertía la
  voz en su propio umbral y recortaba el inicio).
- **Barge-in solo con voz sostenida** (~160 ms); un pico de ruido ya no corta
  a Rafita. Fin de turno con ~320 ms de silencio real; blips <120 ms se
  descartan; turno máximo 30 s.
- Los segmentos sin voz (nsp alto) se descartan en el STT: el ruido no genera
  respuesta.
- **E2E real contra el servidor de llamada**: pregunta hablada → transcripción
  exacta (`"¿Qué tiempo hace en Sevilla?"`), ruido de fondo durante la
  respuesta → **no corta**, voz sostenida → **corta (barge-in)** ✓.
- Tests nuevos: VAD (2), stt_service (6), meeting async, y los de la
  llamada adaptados al VAD por milisegundos. **1.357 tests**.

---

## FALLOS REPORTADOS POR EL USUARIO (2026-09-30, 2ª tanda)

Cinco fallos reales encontrados usando a Rafita; todos corregidos y probados:

### 1. No marcaba tareas como realizadas — CORREGIDO
- **Evidencia**: logs 17:41-17:42: `complete` con `task_title='Marcala como
  realizada'` (la instrucción) y con `task_id='Programa Buena Tierra'` (el
  título en el campo del ID) → Google API falla.
- **Arreglo**: `manage_google_tasks` acepta el título también en `task_id`
  (un ID real no tiene espacios), limpia palabras de instrucción
  (`_limpiar_titulo_tarea`), reintenta por título si el ID falla y, si no
  encuentra, devuelve la lista de pendientes para que el modelo pregunte.
  Además tolera las variantes de gemma (`complete_task`, `task_name`,
  `action` vacía → alias).
- **Verificado**: "Apunta que tengo que comprar pilas" → "Marca como
  realizada la tarea comprar pilas" → Google Tasks responde
  `status: completed` (comprobado por API).

### 2. No entendía el símbolo @ en correos dictados — CORREGIDO
- **Evidencia**: `send_gmail to:'anabel.84.amg.gmail.com'` (sin @) y
  `to:'arroba gmail.com'`.
- **Arreglo**: `normalize_dictated_email` (arroba→@, punto→., guion bajo→_)
  aplicado al STT (remoto y local), al ejecutor de `send_gmail` y a
  `send_email`; regla EMAIL_RULE en el prompt; ejemplo de dictado en el
  `initial_prompt` de Whisper.

### 3. No encontraba el contacto "Ana" con correo — CORREGIDO
- **Evidencia**: la búsqueda funcionaba pero la Ana con correo quedaba en
  segunda posición y el relleno de la frase ("busca un contacto con el nombre
  Ana que tiene puesto su correo") rompía la consulta.
- **Arreglo**: `_limpiar_consulta_contacto` (quita "busca/contacto/con el
  nombre/que tiene correo..."), relevancia que prioriza contactos **con
  email** y mensaje con nombre+correo+teléfono.
- **Verificado**: "Busca un contacto con el nombre Ana que tiene puesto su
  correo" → "El que tiene correo es: Ana!!❤️: anabel.84.amg@gmail.com".

### 4. Fillers ("estoy en ello") hasta en un "hola" — CORREGIDO
- **Arreglo**: retardo 2,2 → **5,5 s** (configurable `VOICE_FILLER_DELAY_S`)
  y los saludos cortos ("hola", "gracias", "¿qué tal?") ya no los reciben.
- **Tests**: `_es_saludo_corto` y filler que no habla para saludos.

### 5. "Hola, soy Rafita..." tras preguntar por Drive — CORREGIDO
- **Evidencia**: logs 17:51:08: tras `list_google_drive` el modelo compuso un
  saludo genérico. No hubo reset (el historial seguía con 56 mensajes).
- **Arreglo**: historial 6 → **12 mensajes** (latencia medida: 1,11 vs 1,12 s
  con gemma4:12b en GPU, inapreciable) + guardia post-herramientas
  determinista: si la respuesta es un saludo genérico, una negación falsa
  ("no tengo acceso", "no se han recibido resultados"...) o una plantilla
  inventada ("[Nombre de la carpeta 1]"), se reintenta y, si insiste, se
  responde con el mensaje real de la herramienta. Además, si la tool devolvió
  una lista y la respuesta no menciona **ningún** elemento, se usa la lista
  real (`_tool_lista_ignorada`). Igual en el streaming de voz (primera frase
  validada antes de emitirla).
- **Verificado**: "¿Qué carpetas y archivos tengo en mi Drive?" → responde
  la lista real de Drive (antes: saludo/negación/plantilla).

### Tolerancia extra a gemma4:12b (visto en esta tanda)
- Alias de acciones en tareas y calendario (`complete_task`, `add`,
  `create_event`, `update`...) y de argumentos (`task_name`).
- La recuperación puede reintentar la MISMA herramienta cuando el fallo fue
  de argumentos (antes se saltaba por estar ya probada).
- Composición vacía + todas las tools fallidas → se responde el error real.
- **1.381 tests**, ruff/mypy/biome limpios, desplegado y verificado en el HP.
