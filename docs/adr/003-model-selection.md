# ADR-003: Modelos de IA — gemma4 vs qwen2.5, bge-m3 vs nomic-embed-text

**Estado**: Aceptada  
**Fecha**: 2026-08-10 (retrospectiva)  
**Decisión**: gemma4:12b (chat, con GPU), bge-m3 (embeddings, universal)  

---

## Contexto

Rafita AVP necesita dos tipos de modelos:

1. **Chat/razonamiento**: genera respuestas, invoca tools, sigue reglas (PROACTIVE_BRAIN_RULE, etc.)
2. **Embeddings**: convierte texto a vectores para búsqueda semántica (RAG)

El hardware varía entre instalaciones (CPU-only 16GB → GPU 12GB VRAM). Los modelos deben
elegirse por calidad en español y capacidad de tool use.

## Criterios de evaluación

| Criterio | Peso | Descripción |
|---|---|---|
| Calidad en español | Alto | Comprensión y generación en español nativo |
| Tool use / seguimiento de instrucciones | Alto | Capacidad de invocar funciones correctamente |
| Huella de recursos | Alto | RAM/VRAM necesaria para el modelo |
| Velocidad de inferencia | Medio | Tokens por segundo en hardware típico |

## Chat: gemma4:12b vs qwen2.5:7b

### Evidencia empírica

El 10 de agosto de 2026, el usuario envió una transcripción de audio de 14.161 caracteres
con datos personales (nombre, DNI, salario, coche, historial médico). El modelo entonces en
uso (`qwen2.5:7b`) dio una respuesta genérica y **NO guardó los datos en el segundo cerebro**
a pesar de tener la PROACTIVE_BRAIN_RULE en el prompt. Los datos tuvieron que extraerse
manualmente.

Este es el fallo que motiva el cambio. La PROACTIVE_BRAIN_RULE requiere que el modelo:
1. Detecte datos personales en la conversación
2. Decida si son relevantes para persistir
3. Invoque la tool correcta (`manage_obsidian_note` o `ingest_file`)
4. Confirme al usuario

`qwen2.5:7b` falló en el paso 2 (no decidió persistir). `gemma4:12b` tiene mejor rendimiento
documentado en tool use (benchmark BFCL v3).

### Comparativa

| | qwen2.5:7b | gemma4:12b |
|---|---|---|
| Tamaño | 4.7GB (Q4_K_M) | 7.6GB (Q4_K_M) |
| Español | Bueno | Excelente (entrenado con corpus multilingüe) |
| Tool use | Aceptable (falló PROACTIVE_BRAIN_RULE) | Superior (Google, benchmarks BFCL) |
| VRAM necesaria | ~5GB | ~8GB |
| CPU viable | Sí (lento pero funciona) | No (<12GB RAM host → OOM) |
| Velocidad (GPU) | ~40 tok/s | ~25 tok/s |
| Velocidad (CPU) | ~3 tok/s | N/A (no cabe) |

### Decisión

**gemma4:12b como modelo principal cuando hay GPU ≥10GB VRAM o RAM ≥24GB**.
**qwen2.5:7b como fallback para CPU-only o GPU <6GB VRAM**.

El sistema de detección de hardware (`hardware_detect.py`) selecciona automáticamente.

La validación real (>60% relevancia con bge-m3) está pendiente de realizar en el PC con
RTX 3060 (12GB VRAM). En el portátil actual (CPU, 16GB RAM) se usa qwen2.5:7b.

## Embeddings: bge-m3 vs nomic-embed-text

### Evidencia empírica

Pruebas realizadas el 11 de agosto de 2026:

| Query | nomic-embed-text relevancia | bge-m3 relevancia |
|---|---|---|
| "gasto en gasolina" | ~25% (resultado incorrecto) | 67.8% (chunk correcto) |
| "salud" | ~30% | ~55% |
| "DNI" | ~20% | ~45% |
| "Audi" | ~35% | ~52% |

nomic-embed-text fue entrenado principalmente en inglés. Su rendimiento en español es
notablemente inferior (20-40% relevancia en queries semánticas). bge-m3 es un modelo
multilingüe de BAAI entrenado con MCL (Multi-Contrastive Learning) que cubre 100+ idiomas.

### Comparativa

| | nomic-embed-text | bge-m3 |
|---|---|---|
| Tamaño | 274MB | 1.2GB |
| Dimensión | 768 | 1024 |
| Idiomas | Principalmente inglés | 100+ (multilingüe) |
| Relevancia español | 20-45% | 50-80% |
| RAM necesaria | ~500MB | ~1.5GB |
| Velocidad (CPU) | ~1s/chunk | ~15s/chunk |
| Velocidad (GPU) | Instantáneo | Instantáneo |
| Benchmarks (MTEB) | 62.3 (inglés) | 67.4 (multilingüe) |

### Decisión

**bge-m3 como modelo de embeddings por defecto**. La diferencia de relevancia en español
(50-80% vs 20-45%) justifica el mayor tamaño (1.2GB vs 274MB). En hardware modesto (CPU, <16GB RAM),
se puede degradar a nomic-embed-text como fallback, con la expectativa documentada de
menor calidad en búsquedas en español.

El cambio de dimensionalidad (768 → 1024) requiere borrar la colección ChromaDB y reindexar.
Esto lo maneja automáticamente el backfill al detectar `total_chunks == 0`.

## Visión: llava:7b

Se mantiene sin cambios. Es el modelo de visión por defecto. Alternativa `moondream` para
hardware muy limitado (CPU-low profile).

---

## Validación pendiente

**F0.5 — Relevancia RAG >60% en español con bge-m3**: Este criterio NO se ha validado en
producción porque el hardware actual de desarrollo (portátil CPU-only, 16GB RAM) no puede
ejecutar bge-m3 + gemma4:12b simultáneamente. La validación queda pendiente de realizar en
el PC con RTX 3060 (12GB VRAM). La métrica de referencia (67.8% en test controlado con bge-m3)
es prometedora pero no conclusiva. Ver `cambios.md` y README para seguimiento.

*ADR aceptada. Los perfiles de hardware documentados en `hardware_detect.py` implementan
esta decisión automáticamente.*
