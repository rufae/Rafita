# Web de Rafita

La web son **dos superficies** que funcionan como un producto coherente:

| Superficie | Ficheros | Se sirve desde |
|---|---|---|
| **SPA** (chat, Baúl, Reuniones, Llamada embebida) | `web/app/` | el gateway (`/app`), puerto 8000/8010 |
| **Página de llamada** (voz en tiempo real) | `web/call_rafita.html` | el servidor de voz (`/`), puerto 8001 |

## ¿Por qué dos orígenes? (decisión)

La página de llamada vive en su propio origen **a propósito**:
- necesita **micrófono** y un **WebSocket** propios (su CSP y su token son
  independientes: `VOICE_CALL_TOKEN` + `frame-ancestors` restringido a la SPA);
- se embebe por iframe en la vista Llamada y también se usa directamente;
- el servidor de voz puede reiniciarse sin tocar la SPA y viceversa.
Lo comparten: `web/app/design-tokens.css` (sistema de diseño único, el servidor de
 voz publica `/design-tokens.css`) y la paleta/marca.

## `call_rafita.html` es un monolito (decisión)

HTML + CSS + JS inline en un **único fichero**. Se mantiene así a propósito:
una sola petición sirve toda la página de llamada, no requiere build ni rutas
adicionales y el despliegue es `scp`/rsync de un archivo. No es un accidente
histórico. Si creciera mucho, el primer paso sería extraer `design-tokens.css`
(ya externo) seguido de un `call.js` servido por el mismo servidor de voz.

## Configuración por instalación

`web/app/config.js` (se sirve con `Cache-Control: no-store`, editable en
caliente):

```js
window.RAFITA_CONFIG = {
  // URL pública del servidor de llamadas para el iframe (vacío = mismo host,
  // puerto 8001). Ej. detrás de NPM: "https://voz.midominio.com"
  callOrigin: "",
};
```

Otras variables relevantes viven en `.env` (ver `.env.example`):
`WEB_ALLOWED_ORIGINS` (orígenes confiables: SPA + llamada, para CSP y
Permissions-Policy), `WEB_AUTH_SECRET`, `WEB_ADMIN_EMAIL`/`WEB_ADMIN_PASSWORD`,
`VOICE_CALL_TOKEN`, `TTS_*`.

## Build (producción)

`scripts/build_web.py` (Python: `rjsmin` + `csscompressor`; sin Node):
minifica y añade hash de contenido (`app-<hash8>.js`…) → `web/app-dist/`.
El gateway sirve `web/app-dist/` si existe (producción) y si no `web/app/`
(desarrollo). En el despliegue real, tras copiar `app-dist/`, **copia también
tu `config.js`**: `cp web/app/config.js web/app-dist/config.js`.

## Formato y lint

`biome.json` (Biome 2.5): `biome check --write web/` para formatear y
`biome ci web/` como comprobación (también en CI, job `web`). Cubre
`web/**/*.js|css` + manifest; el HTML no está cubierto por Biome (su JS
inline se prueba con tests).

## Tests

- **Unitarios de funciones puras del JS**: `agent/tests/test_web_js_functions.py`
  (formatDuration, escapeHtml, debounce, mejorMimeGrabacion, rmsFromAnalyser,
  authHeaders/getWsBase, fetchConTimeout) — Playwright, sin backend.
- **E2E mínimo**: `agent/tests/test_web_e2e.py` (login → chat → respuesta;
  crear/abrir/borrar nota del Baúl). Requiere entorno:
  `RAFITA_WEB_URL`, `RAFITA_WEB_EMAIL`, `RAFITA_WEB_PASSWORD` (sin ellas se
  salta, como en CI).

## PWA

`web/app/manifest.webmanifest` (id, iconos, screenshots, categorías) +
`sw.js` (shell cacheado, API siempre a la red). Requiere **HTTPS** (o
localhost) para instalarse y para el micrófono de la llamada.
`call_rafita.html` queda **fuera** de la experiencia instalable a propósito
(otro origen, fuera del `scope`; se embebe en la vista Llamada).
