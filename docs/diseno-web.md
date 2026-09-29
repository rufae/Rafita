# Diseño web de Rafita — identidad y sistema

> Rediseño visual completo (2026-09-29). Sustituye por completo la identidad
> anterior (navy + cian, glass, orbes radiales).

## Dirección

**Editorial + artesanal + tecnológica + minimalista.** Rafita es un asistente
privado que vive en el servidor del usuario: la interfaz se trata como un
**cuaderno de campo** — papel cálido, tinta, terracota — no como un panel de
servidor ni un dashboard SaaS.

Referencias conceptuales (sin copiar): el detalle y la jerarquía de Linear, la
personalidad de las interfaces editoriales modernas, la claridad de Obsidian.

## Sistema de diseño (fuente única)

`web/app/design-tokens.css` define **todo** el sistema y lo consumen la SPA y
la página de llamada (esta última lo carga desde su propio origen:
`GET /design-tokens.css` del servidor de voz):

- **Color** (claro): papel marfil `--paper #f5efe3`, superficie `--surface`,
  beige `--surface-2/3`, tinta carbón `--ink #2a251e`, tinta suave/fina,
  **terracota** `--accent #a94f2b`, salvia `--secondary #566a48`, petróleo
  `--info` (solo informativo), y estados `--ok/--warn/--danger` maduros.
- **Color** (oscuro): carbón cálido `--paper #191611` con superficies
  diferenciadas, texto marfil, acento terracota claro — **no es una inversión**.
- **Tipografía**: dos familias. Identidad en **serif editorial**
  (`--font-display`: Iowan/Palatino/Georgia) para wordmark, títulos, mensajes
  de Rafita y el editor del Baúl (lectura larga); **sans humanista**
  (`--font-ui`: Avenir/Segoe) para la interfaz; `--font-mono` para datos.
  Escala: display/h1/h2/h3/body/sm/label/caption con interlineados y
  `letter-spacing` propios.
- **Espaciado** escala 4 (`--sp-1…--sp-20`), **radios** 3/5/8/12/píldora,
  **bordes** `--line/--line-strong`, **sombras** muy suaves (`--shadow-1/2/lift`),
  **z-index** (`--z-sticky/overlay/modal/toast`), **movimiento**
  (`--t-instant/fast/base/slow/breath` + `--ease-out/inout/spring`),
  **breakpoints** de referencia (640/900/1200).
- `prefers-reduced-motion` pone todas las duraciones a 0 y desactiva
  animaciones; `prefers-color-scheme` elige el tema completo.

## Composición (no solo color)

- **Sin tarjetas flotantes ni cristal**: superficies planas, **líneas finas**
  como separadores, sombras casi imperceptibles y espacio negativo.
- **Masthead** con wordmark serif + marca (glyph) y pestañas como
  **navegación subrayada** (el activo lleva un filete terracota), no píldoras.
- **Login asimétrico**: columna editorial (marca, tesis del producto, líneas
  de cuaderno como motivo gráfico) + columna de acceso; en móvil se apila.
- **Chat**: Rafita = fila editorial con **marca propia** (glyph terracota),
  nombre en serif y hora; el usuario = bloque derecho con tinte terracota
  suave; indicador de escritura con tres puntos orgánicos. Nada de
  "burbuja azul / burbuja gris".
- **Baúl** como herramienta editorial: lista con títulos en serif y metadatos
  en versalitas, editor con **cuerpo en serif** para lectura larga y ruta en
  monoespaciada.
- **Botones**: un sistema (`primary/secondary/tertiary/ghost/danger/icon`,
  tamaño `sm`) con estados normal/hover/active/focus/disabled/loading. El
  hover cambia color/borde (no `translateY`).
- **Iconografía**: sprite SVG propio (trazo 1.5, mismo lenguaje) para
  navegación, acciones y estados; el glyph de marca (`i-spark`) es el logo,
  el avatar de Rafita y el favicon. Wordmark, marca, avatar y favicon tienen
  tratamientos diferenciados.
- **Grano de papel** sutil (SVG feTurbulence al 3,5%) como textura de fondo.

## Llamada: dos presencias conectadas

Los orbes radiales desaparecieron. La escena son **dos presencias orgánicas
unidas por un hilo**:

- Cada presencia es una forma que **respira** (morfología de radios animada),
  con un **anillo de tinta discontinuo** girando lentamente y un halo que
  pulsa solo cuando está activa.
- Estados con identidad propia: **idle** (quietud), **listening** (la
  presencia del usuario crece con el nivel real del micro y el hilo fluye
  hacia Rafita en salvia), **thinking** (partículas mínimas orbitando dentro
  de la presencia de Rafita y el hilo en terracota), **speaking** (la
  presencia de Rafita se deforma con el nivel real de su voz y el hilo fluye
  rápido), **reconnecting** (hilo atenuado y lento).
- El nivel de audio sigue viniendo del `--level` real (analizadores);
  no hay "audio visualizer": son presencias, no barras.

## Verificación

- **axe-core**: 0 violaciones en login/chat/baúl/reuniones/llamada, en claro
  y oscuro, y en la página de llamada.
- **Contraste** (medido desde tokens): tinta 13-14:1, tinta suave 5,2-6,7:1,
  captions 5,0-4,9:1, acento 4,8-6,2:1, texto sobre acento 5,1-5,9:1,
  peligro 6,0-6,7:1 — todo ≥ 4,5:1.
- **Sin scroll horizontal** en 390 px (login, chat, baúl, llamada).
- **E2E real**: login → chat con respuesta del cerebro → crear/abrir/borrar
  nota del Baúl → 9/9 tests.

Capturas reales de todas las vistas en `docs/web-screenshots/`.
