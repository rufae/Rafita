# ADR-001: Almacén vectorial — ChromaDB vs Qdrant vs pgvector

**Estado**: Aceptada  
**Fecha**: 2026-08-11  
**Decisión**: Mantener ChromaDB  

---

## Contexto

Rafita AVP necesita un almacén vectorial para búsqueda semántica (RAG) sobre el segundo cerebro.
Actualmente usa **ChromaDB 0.5.0** embebido, sin servicio externo. Con ~314 chunks (18 documentos
en el vault actual) y crecimiento esperado a ~5000 chunks máximo para un usuario individual.

El stack actual es Docker Compose con 2 contenedores (ollama-service + rafita-agent-core).
Añadir un servicio nuevo añade complejidad operativa que cada instalador debe gestionar.

## Criterios de evaluación

| Criterio | Peso | Descripción |
|---|---|---|
| Facilidad de instalación | Alto | ¿Qué tiene que instalar/configurar un usuario nuevo? |
| Huella de recursos | Alto | RAM/CPU/disco en hardware modesto (portátil 16GB RAM, sin GPU) |
| Backup/restore | Medio | ¿Cómo se hace backup y restore? ¿Es atómico? |
| Concurrencia | Bajo | ¿Soporta lecturas concurrentes? (Rafita es single-user) |
| Madurez | Medio | Estabilidad, comunidad, frecuencia de releases |
| Rendimiento | Bajo | Latencia de búsqueda para ~5000 chunks |

## Opciones evaluadas

### ChromaDB (actual)

- **Instalación**: `pip install chromadb`. 0 configuración. Arranca con el proceso Python.
- **Huella**: Embebido en el mismo proceso. Sin consumo extra de RAM (los embeddings los genera Ollama aparte). ~10MB de overhead en disco para el índice HNSW.
- **Backup**: Copiar la carpeta `data/vector_db/`. No es atómico pero es trivial. Restore: copiar la carpeta de vuelta.
- **Concurrencia**: Cliente único (PersistentClient). Single-writer por diseño.
- **Madurez**: 15K+ GitHub stars, releases mensuales. Comunidad activa. La versión 0.5.0 tiene algunos bugs (posthog telemetry roto) pero funciona.
- **Rendimiento**: <50ms para queries HNSW con 314 chunks. Escala linealmente.
- **Puntos débiles**: Sin backup atómico. La colección se corrompe si el proceso muere durante escritura (poco probable en single-user). No hay autenticación ni red (no necesaria en localhost).

### Qdrant

- **Instalación**: Requiere un contenedor Docker aparte (`qdrant/qdrant`). Puerto 6333 expuesto. Nuevo servicio en docker-compose.yml.
- **Huella**: ~50MB RAM en idle, ~200MB con índice cargado. Contenedor de ~80MB.
- **Backup**: API nativa de snapshots (`POST /collections/{name}/snapshots`). Atómico, versionado.
- **Concurrencia**: Multi-cliente, multi-colección. gRPC + REST.
- **Madurez**: 22K+ GitHub stars. Usado en producción por empresas. Releases semanales.
- **Rendimiento**: <10ms para colecciones pequeñas. Más rápido que ChromaDB para >10K vectores.
- **Por qué se rechaza**: Servicio externo (+1 contenedor, +1 proceso que puede fallar). Para 314-5000 chunks, su ventaja de rendimiento (<10ms vs <50ms) es imperceptible para un humano. El backup por snapshots es mejor, pero a esta escala copiar una carpeta es suficiente. Contradice el objetivo de instalación simple. Se reconsideraría si el proyecto escala a multi-usuario con >100K vectores.

### pgvector (PostgreSQL)

- **Instalación**: Requiere PostgreSQL 12+ con extensión pgvector. Contenedor `pgvector/pgvector:pg16`. Nuevo servicio en docker-compose.yml.
- **Huella**: PostgreSQL base ~50MB RAM idle. Con pgvector y datos ~100MB. Contenedor ~200MB.
- **Backup**: `pg_dump` nativo. Atómico, probado en batalla durante décadas. Point-in-time recovery.
- **Concurrencia**: ACID completo. Multi-cliente nativo. Transacciones.
- **Madurez**: PostgreSQL: 35+ años. pgvector: 13K+ GitHub stars, releases mensuales.
- **Rendimiento**: Índice IVFFlat/HNSW. <20ms para colecciones pequeñas. Excelente para >100K vectores.
- **Por qué se rechaza**: La opción más pesada (~200MB RAM, requiere init de extensión). Su ventaja real (ACID, transacciones, PITR) no se necesita para un índice single-user con 5000 chunks. Es la herramienta correcta para sistemas multi-usuario con requisitos de integridad transaccional — Rafita no es ese sistema hoy. Se reconsideraría si MisterAI se convierte en consumidor multi-tenant.

## Comparativa cuantitativa

| | ChromaDB | Qdrant | pgvector |
|---|---|---|---|
| Servicios extra | 0 | +1 (qdrant) | +1 (postgres) |
| RAM idle | ~0MB (embebido) | ~50MB | ~100MB |
| RAM con datos | ~10MB | ~200MB | ~200MB |
| Tamaño contenedor | N/A (pip) | ~80MB | ~200MB |
| Backup | Copia de carpeta | API snapshots | pg_dump |
| Complejidad instalación | 0 pasos | +1 servicio | +1 servicio + extensión |
| Tiempo hasta funcionar | Inmediato | ~30s pull + start | ~60s pull + init + extensión |
| Justificado para <5000 chunks | ✅ Sí | ❌ No | ❌ No |

## Decisión

**Mantener ChromaDB** como almacén vectorial por las siguientes razones:

1. **Cero servicios extra**: Rafita AVP se instala con `docker compose up -d`. Añadir Qdrant o PostgreSQL
   requiere que cada usuario instale, configure y mantenga un servicio adicional. Esto contradice
   el principio de "cualquiera pueda instalar en su propio ordenador".

2. **Huella mínima**: En un portátil con 16GB RAM, cada MB cuenta. ChromaDB es embebido (0 RAM extra).
   Qdrant o pgvector consumirían 100-200MB adicionales que compiten con los modelos de Ollama.

3. **Single-user por diseño**: Rafita es monousuario. La concurrencia multi-cliente de Qdrant/pgvector
   no aporta valor. ChromaDB con PersistentClient cubre el caso de uso perfectamente.

4. **Backup suficiente**: Para ~5000 chunks, copiar una carpeta es trivial y rápido. No se necesita
   atomicidad de snapshots a esta escala.

5. **Facilidad de desarrollo**: Sin servicio externo, los tests son autónomos (no necesitan Docker).
   El ciclo de desarrollo es más rápido.

## Consecuencias

- El backup del segundo cerebro requiere copiar `data/vector_db/` junto con `data/db/rafita.db`.
- Si en el futuro Rafita escala a multi-usuario (MisterAI como consumidor), se reevaluará pgvector.
- El bug de posthog telemetry (chromadb 0.5.0) no es bloqueante y se actualizará cuando chromadb lo arregle.

---

*ADR revisable si los requisitos cambian (multi-usuario, >100K chunks, necesidad de replicación).*
