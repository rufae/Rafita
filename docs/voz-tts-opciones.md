# Opciones de voz TTS open source (investigación, 2026-09)

> **Implementado (2026-09-28)**: backend **Kokoro** con `kokoro-onnx` (int8,
> ~142 MB) y backend **Voicebox** por REST, con cadena de respaldo
> `kokoro → piper → espeak`. Selector por caso de uso:
> `TTS_ENGINE` (notas de voz/briefing), `TTS_ENGINE_CHAT` (respuestas de voz
> del chat) y `TTS_ENGINE_CALL` (llamada en vivo). Voces en español:
> `ef_dora`, `em_alex`, `em_santa`.
>
> **Rendimiento medido (importante)**: en el HP (Intel i3-1005G1, 2 núcleos)
> Kokoro int8 da **RTF ~2,5** (2,5 s de cálculo por segundo de audio): una
> nota de 30 s tarda ~75 s. En una CPU moderna (2 hilos) baja a RTF ~1,4.
> Por eso la llamada y el chat usan Piper por defecto; Kokoro queda para
> audio asíncrono (briefing, notas). Alternativas para acelerar: servir
> Kokoro en otra máquina (Dell/torre con GPU) por HTTP — el backend
> `voicebox` ya soporta un servicio remoto — o usar Voicebox con GPU.

Estado actual: Piper (`es_ES-davefx-medium`) con limpieza de texto para voz
(sin Markdown, emojis, unidades expandidas). Es rápido y gratis, pero suena
sintético. Aquí están las alternativas evaluadas para una voz menos robótica,
todas **locales y gratis**.

## Resumen de candidatos

| Motor | Licencia | Voces / idiomas | ¿Clona voz? | Hardware | Latencia | Veredicto |
|---|---|---|---|---|---|---|
| **Piper** (actual) | MIT | Muchas por idioma (es: davefx, sharvard, claude-high…) | No | CPU (incluso Raspberry Pi) | ~40 ms primera frase | Rápido pero robótico |
| **Kokoro-82M** | Apache 2.0 | 54 voces fijas, 8 idiomas (**es: ef_dora, em_alex, em_santa**) | No | **CPU** (más rápido que tiempo real); GPU ~1-2 GB | ~90 ms | **Mejor calidad/esfuerzo** |
| **Chatterbox** (Resemble AI) | MIT | Multilingüe (incluye español), emoción | **Sí** (3 s de muestra) | GPU (~4-6 GB) | Media | Clonación con licencia libre |
| **XTTS v2** (Coqui) | CPML (**no comercial**) | 17 idiomas, cross-lingual | **Sí** (6 s de muestra) | GPU (~4-6 GB); en CPU inviable | ~600 ms | Solo uso personal/investigación |
| **Voicebox** (app, jamiepine) | MIT | Envuelve 7 motores: Qwen3-TTS, Chatterbox, Kokoro, LuxTTS, TADA… | **Sí** | GPU recomendada (Docker/Linux desde fuente) | Variable | Interesante como “estudio” con API REST |
| **Bark** (Suno) | MIT | Multilingüe | No | GPU ~5 GB | ~3× tiempo real | Expresivo pero lento |
| **Meta Voicebox** | — | — | — | — | — | **Descartado: Meta no liberó código ni pesos** |

## Análisis

### Kokoro-82M — la opción recomendada (fase 1)
- 82M parámetros (~350 MB), salida 24 kHz, Apache 2.0 (uso comercial libre).
- 3 voces en español: `ef_dora` (mujer), `em_alex` y `em_santa` (hombres),
  `lang_code='e'`.
- Corre en CPU más rápido que tiempo real → encaja en el Dell/HP sin GPU y en
  el flujo de llamada (fragmentos cortos).
- Integración sencilla: `pip install kokoro soundfile` + `espeak-ng`; se añade
  un backend `kokoro` a `tts_manager` seleccionable con `TTS_ENGINE`.
- **Cuidado**: su G2P español no expande números ni abreviaturas → hay que
  convertir cifras a palabras (p. ej. `num2words`, que además ya encaja con la
  normalización de unidades existente) y trocear en frases (límite ~510
  tokens, ya lo hacemos en el modo llamada).

### Voicebox — la opción “estudio” (fase 2)
- App de escritorio local-first (MIT) con **API REST** por motor; clona voz
  desde ~3 s y trae Qwen3-TTS (instrucciones de estilo: “habla despacio”),
  Chatterbox Multilingual, Kokoro, etc.
- Linux: build desde fuente o Docker (`docker compose up`); prefiere GPU.
- Encaje: desplegarlo en la torre (GPU) y que Rafita use su endpoint REST para
  notas de voz y llamada; se podría **clonar la propia voz del usuario**.
- Coste: medio (contenedor + descarga de modelos); mantener Piper/Kokoro como
  respaldo si el servicio no está.

### Chatterbox — clonación con licencia libre (fase 2 alternativa)
- MIT, ~0.5B, clonación + emoción, multilingüe. Necesita GPU. Si se quiere
  clonar sin la restricción no comercial de XTTS, es la vía.

### XTTS v2 — solo si es uso personal
- La mejor clonación zero-shot, pero **licencia no comercial** y requiere GPU.
  Descartado para un producto; válido para experimentos privados.

### Descartado
- **Meta Voicebox**: Meta anunció explícitamente que no libera modelo ni
  código. Los “voicebox” de GitHub son proyectos distintos (la app MIT de
  arriba).

## Plan de implementación propuesto
1. **Fase 1 (barata)**: backend Kokoro en `tts_manager` + `TTS_ENGINE=piper|kokoro`
   + `TTS_VOICE` por motor + expansión de números a palabras. Probar en llamada
   y notas de voz y comparar con Piper.
2. **Fase 2 (calidad/clonación)**: desplegar Voicebox en la torre (GPU) o
   Chatterbox, exponer su REST y añadir backend `http` genérico al TTS
   (endpoint configurable `TTS_HTTP_URL`), con fallback automático a Kokoro/Piper.
3. Mantener la limpieza de texto (`sanitize_for_tts`) como capa común: es
   independiente del motor y ya elimina Markdown, emojis y repeticiones.

## Referencias
- Kokoro-82M: https://huggingface.co/hexgrad/Kokoro-82M (voces: `VOICES.md`)
- Voicebox: https://github.com/jamiepine/voicebox y https://voicebox.sh
- Chatterbox: https://github.com/resemble-ai/chatterbox
- Coqui XTTS v2 (fork mantenido): https://github.com/idiap/coqui-ai-TTS
- Piper: https://github.com/OHF-Voice/piper1-gpl
