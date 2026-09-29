# Diseño web de Rafita — brief y decisiones (Fase 3, 2026-09-29)

## Referencias (nivel de producto, sin copiar interfaces)

| Referencia | Qué se toma prestado |
|---|---|
| **Linear** | Tipografía compacta y consistente, listas con jerarquía clara, acentos vivos sobre base oscura, estados vacíos con intención. |
| **Raycast** | Cristal sutil (`glass`) sobre gradientes calmados, degradados de marca en elementos de identidad, densidad alta sin ruido. |
| **Things / Notion** | Estados vacíos con icono/ilustración + llamada a la acción; skeletons discretos en cargas; micro-animaciones de entrada (no decorativas). |

## Identidad «Rafita»

Asistente personal: cálido en el trato, técnico por dentro. La identidad visual
ya existente (base azul pizarra + acento cian, orbes de la llamada) se refina,
no se reinventa.

- **Paleta**: base `slate` (azul pizarra) + acento **cian→índigo** (degradado de
  marca), verde para acción/voz, coral suave para errores. Semántica en
  tokens: `--bg/--panel/--panel-2/--text/--muted/--accent/--accent-2/
  --ok/--warn/--danger`.
- **Tipografía**: pila del sistema (sin fuentes externas: la app debe funcionar
  offline). Escala 12/14/16/20/28 (razón ≈1.25), pesos 400/600/700,
  `font-feature-settings: "tnum"` en cifras, `letter-spacing` negativo en
  títulos.
- **Espaciado**: escala 4/8/12/16/24/32/48 (`--sp-1…--sp-12`).
- **Radios**: 8/12/20/píldora. **Sombras**: 3 niveles con alfa consistente
  (`--shadow-sm/md/lg`) + `--glow` para foco.
- **Movimiento**: `--ease: cubic-bezier(.2,.7,.3,1)`, 120-200 ms. Solo
  micro-interacciones con propósito: elevación en hover, indicador de pestaña,
  entrada de burbujas/modal, shimmer del skeleton.

## Decisiones explícitas

1. **Tema (3.2)**: **ambas superficies respetan `prefers-color-scheme`**. La
   página de llamada era oscuro fijo por omisión; ahora consume los tokens
   compartidos (oscuro por defecto y claro en su `@media`). Decisión: coherencia
   de producto por encima del «look cinematográfico» fijo; el degradado y los
   orbes se adaptan al tema.
2. **Tokens únicos (3.1)**: `web/app/tokens.css` es la **fuente única**
   (color, tipografía, espaciado, radios, sombras, movimiento). La SPA lo
   sirve en `/app/tokens.css`; la página de llamada lo consume desde su propio
   origen (el servidor de voz publica `/tokens.css`). `call_rafita.html`
   sigue siendo un fichero aparte, pero ya no duplica la paleta.
3. **call_rafita.html fuera de la PWA instalable (3.5)**: vive en otro origen
   (`voz.rafita.home`), fuera del `scope` del manifest; la vista Llamada de la
   SPA lo embebe. Se documenta como decisión (no es un accidente).
4. **Estados vacíos/carga (3.4)**: skeletons con shimmer en las tres listas y
   estados vacíos con icono SVG + CTA real (no solo texto gris).

## Mockup

`web/mockup/diseno.html` (fichero de diseño, no se sirve en producción) con el
estado propuesto: barra con marca, pestañas con indicador, lista con skeleton,
estado vacío con CTA, burbujas de chat, botones y tarjeta modal. Se genera su
captura como referencia antes de aplicar los cambios a la app real.
