# ADR-002: Cola de trabajos para tareas pesadas

**Estado**: Aceptada  
**Fecha**: 2026-08-11  
**Decisión**: No añadir cola de trabajos externa. Usar asyncio + backfill síncrono en startup.  

---

## Contexto

Rafita AVP ejecuta tareas potencialmente pesadas:
- **Transcripción de audio** (faster-whisper): 5-30s por audio en CPU
- **Visión/OCR** (llava:7b): 30-120s por imagen en CPU
- **Reindexado del vault** (backfill): ~1h para 300+ chunks con bge-m3 en CPU
- **Catch-up de mensajes** (message_scanner): <10s por lote de 30 mensajes

Actualmente estas tareas se ejecutan inline (bloquean la respuesta de Telegram). El usuario
espera mientras se procesan. La pregunta es si necesitamos una cola de trabajos con persistencia.

## Criterios de evaluación

| Criterio | Peso | Descripción |
|---|---|---|
| Complejidad operativa | Alto | ¿Añade un servicio nuevo que cada instalador debe gestionar? |
| Persistencia | Alto | ¿Sobrevive a reinicios del contenedor? |
| Adecuación a single-instance | Alto | ¿Está diseñado para un solo worker o para flotas? |
| Integración con asyncio | Medio | ¿Funciona nativamente con el stack async de Rafita? |
| Curva de aprendizaje | Medio | ¿Qué tiene que entender un colaborador nuevo? |

## Opciones evaluadas

### Sin cola externa (asyncio + procesamiento inline)

- **Funcionamiento**: Las tareas se ejecutan en el event loop de asyncio. Telegram usa `asyncio.wait_for` con timeout. Si la tarea tarda más de 300s, se devuelve mensaje de timeout al usuario.
- **Ventaja**: 0 servicios extra, 0 dependencias nuevas, 0 configuración.
- **Desventaja**: Si el contenedor muere durante el backfill, se pierde el progreso. El backfill es un proceso batch que ocurre una vez (primer arranque).
- **Persistencia**: Ninguna. Si el proceso muere, la tarea se pierde.

### huey

- **Instalación**: `pip install huey`. Backend: SQLite (ya tenemos), Redis, o en memoria.
- **Huella**: 0 servicios extra si se usa SQLite. ~5MB RAM para el worker.
- **Persistencia**: Con SQLite, las tareas sobreviven a reinicios. El worker las retoma al arrancar.
- **Curva**: API simple: `@huey.task()` decorator. Documentación clara.
- **Adecuación**: Diseñado para single-instance. Sin dependencias de red.
- **Por qué se rechaza**: El worker es un proceso/hilo separado, no async nativo — requiere wrapper para integrarse con el event loop de asyncio. La API de tareas periódicas es limitada comparada con arq. SQLite como backend de cola funciona pero no es su caso de uso diseñado. Se reconsideraría si surge la necesidad de procesar archivos en background mientras se chatea, por ser la opción con 0 servicios extra.

### Redis + RQ / arq

- **Instalación**: Requiere contenedor Redis (`redis:alpine`). + `pip install rq` o `arq`.
- **Huella**: Redis: ~5MB RAM idle, ~30MB con datos. Contenedor ~30MB.
- **Persistencia**: Redis persiste a disco (RDB/AOF). Sobrevive a reinicios.
- **Curva**: RQ es síncrono (necesita wrapper async). arq es async nativo.
- **Adecuación**: Redis es un servicio extra. Bien para multi-worker, overkill para single-instance.
- **Por qué se rechaza**: Añade un servicio (+1 contenedor Redis) que cada instalador debe gestionar (backups de Redis, configuración de persistencia RDB/AOF). La ganancia (persistencia + reintentos + monitorización) no justifica la complejidad operativa añadida para un solo usuario. arq es la mejor opción técnica dentro de esta familia, pero sigue requiriendo Redis.

### Celery

- **Instalación**: `pip install celery`. Requiere broker (Redis/RabbitMQ) + result backend.
- **Huella**: El worker de Celery consume ~50MB RAM. Broker aparte.
- **Persistencia**: Depende del broker.
- **Curva**: API compleja. Configuración extensa. Diseñado para flotas de workers distribuidos.
- **Por qué se rechaza**: Celery está diseñado para sistemas con decenas de workers en múltiples máquinas (broker + result backend + monitoring). Rafita es single-instance, single-user. Usar Celery aquí es como usar un camión para ir al supermercado. Añade 1-2 servicios extra y ~100MB de RAM que compiten con los modelos de Ollama.

## Comparativa cuantitativa

| | asyncio (actual) | huey + SQLite | Redis + arq | Celery |
|---|---|---|---|---|
| Servicios extra | 0 | 0 | +1 (Redis) | +1-2 (broker + backend) |
| RAM extra | 0MB | ~5MB | ~35MB | ~100MB |
| Persistencia | ❌ | ✅ SQLite | ✅ Redis RDB | ✅ broker |
| Async nativo | ✅ | ❌ (sync) | ✅ | ❌ (sync tasks) |
| Reintentos | Manual | ✅ nativo | ✅ nativo | ✅ nativo |
| Monitorización | Logs | ❌ | ✅ Redis CLI | ✅ Flower |
| Complejidad | 0 | Baja | Media | Alta |

## Decisión

**No añadir cola de trabajos externa. Mantener asyncio + procesamiento inline.**

Razones:

1. **Single-instance, single-user**: Rafita procesa una tarea a la vez. Una cola de trabajos no añade
   paralelismo real — el cuello de botella es la CPU/GPU, no la orquestación.

2. **El backfill es batch, no continuo**: La tarea más pesada (backfill de embeddings) ocurre UNA vez
   en el primer arranque. No es un trabajo recurrente que necesite cola. Si falla, el usuario
   reinicia el contenedor y vuelve a intentar.

3. **Telegram ya proporciona feedback**: Con `asyncio.wait_for` y timeouts visibles ("cargando modelo..."),
   el usuario sabe que la tarea está en progreso. Una cola desacoplaría el feedback del progreso.

4. **Cero servicios extra**: Añadir Redis SQLite-configurado como broker añade un servicio que cada
   instalador debe entender y mantener. Esto contradice el objetivo de instalación simple.

5. **huey podría reconsiderarse si surge necesidad**: Si en el futuro el usuario pide procesar
   múltiples archivos en background mientras chatea, huey+SQLite es la opción correcta (0 servicios
   extra, persistencia, simplicidad). Pero hoy no hay evidencia de esa necesidad.

## Consecuencias

- Las tareas de transcripción/visión/reindexado seguirán siendo bloqueantes para la respuesta de Telegram.
- El timeout de chat (300s) cubre la mayoría de casos. Para backfill, el healthcheck tiene 7200s.
- Si se añade procesamiento de múltiples archivos en background, se adoptará **huey + SQLite**.
- El mensaje "cargando modelo" da feedback inmediato mientras la tarea se ejecuta.

---

*ADR revisable si el usuario pide explícitamente procesamiento asíncrono de archivos o si surgen
tareas recurrentes programadas que necesiten persistencia.*
