# Informe final — Rafita AVP

**Fecha:** 2026-09-26
**Alcance:** estabilización y profesionalización del asistente personal Rafita AVP
tras la auditoría técnica externa del 2026-09-24 (tag `v0.1.0`).
**Estado:** **completado — 35/35 tareas del plan con evidencia verificable.**
**Documento técnico de detalle:** `plan.md` (fuente de verdad, con comandos y
salidas reales por tarea). Este informe incluye el resumen ejecutivo, el
trabajo detallado por fase y el registro completo de decisiones, dudas y
respuestas. **No contiene credenciales ni secretos.**

---

## 1. Resumen ejecutivo

Rafita AVP pasó de prototipo personal (índice de "generificación" 15/100, sin
despliegue continuo) a un **servicio en producción doméstica** en dos nodos:

- **Motor de IA** en un servidor Dell (sin GPU) con `gemma4:12b`, `bge-m3` y
  visión, accesible solo por red privada cifrada (Tailscale).
- **Aplicación** (bot de Telegram, base vectorial, bóveda de notas) en un
  segundo servidor HP que llama al motor por esa red privada.
- **Backup diario automático** de todos los servicios al USB, con restauración
  probada, y **procedimiento de actualización/rollback** medido.
- **Seguridad**: cifrado de credenciales, cierres fail-closed, redacción de
  secretos en logs y revisión de dependencias.

La afirmación clave: **todo lo reportado como hecho tiene evidencia ejecutada**
(comando + salida real + fecha + commit) en `plan.md`.

## 2. Arquitectura desplegada

```
[ HP "nodochicohp" ]                            [ Dell "nodo-dell-1" ]
  Bot de Telegram (Rafita)                        Ollama 0.34.4
  Base vectorial (ChromaDB)                       gemma4:12b  (chat + visión)
  Bóveda de notas (Obsidian)                      bge-m3      (embeddings)
  Backups diarios → USB                           qwen2.5:7b  (respaldo)
  Tailscale 100.121.77.29  ── WireGuard ──►       Tailscale 100.83.40.103
       (conexión directa, ~7 ms; sin exponer nada a la red local)
```

- El Dell **solo ejecuta el modelo**; todos los datos de usuario están en el HP.
- Los datos viajan **cifrados** entre nodos (WireGuard) y no se expone ningún
  puerto a la red local (verificado: acceso bloqueado desde la LAN).
- El sistema **se recupera solo**: si el Dell se apaga, el agente sigue vivo y
  reporta "no listo"; al volver el Dell, se recupera sin intervención
  (probado con apagado físico de ambos nodos en los dos órdenes de arranque).

## 3. Metodología

Auditoría externa → plan por fases con una regla innegociable: **ningún ítem se
marca hecho sin evidencia real pegada**. Cinco fases (0 a 4) con 35 tareas:
seguridad, calidad RAG, proveedores de IA, despliegue en servidor continuo,
pruebas de caos, backups, observabilidad y documentación.

## 4. Resultados por fase (resumen)

| Fase | Contenido | Tareas | Estado |
|---|---|---|---|
| 0 | Desbloqueo y seguridad crítica (cifrado, path traversal, webhook, readiness) | 0.1–0.8 | Completada |
| 1 | RAG verificable y tool-calling medido (dataset propio, métricas) | 1.1–1.8 | Completada |
| 2 | Producto configurable (taxonomía, idioma, proveedores IA, wizard) | 2.1–2.6 | Completada |
| 3 | Servidor continuo: despliegue 2 nodos, caos, logs, backup, upgrade | 3.0–3.7 | Completada |
| 4 | Documentación, changelog, ADRs, instalación limpia | 4.1–4.4 | Completada |

## 5. Métricas clave (medidas, no estimadas)

| Área | Resultado |
|---|---|
| **Calidad RAG** (36 casos, español) | recall@3 = **1.0**, MRR@5 = **0.982**, 0 falsos positivos (umbral 0.49). Repetido en el despliegue real con resultado idéntico. |
| **Fiabilidad de herramientas** (21 tools, 2 intentos) | **46/46 (100%)** en el despliegue real. La medición previa de 29/46 era un defecto del modo "razonamiento" del modelo, corregido. |
| **Latencia del modelo** (CPU, sin GPU) | 3,87 tok/s; respuesta típica ~16 s. La red entre nodos añade solo ~0,85 s. |
| **Recursos del HP** | Agente: **160 MB / 2 GB**; los 11 servicios existentes no se degradan (carga 0,08–0,18 bajo backup completo). |
| **Detección de fallos** | Dell caído: `/ready` en **1,2 s**; corte de red: 10 s (tope de diseño). Recuperación automática en todos los casos. |
| **Backup** | 2,18 GB por snapshot, cifrado, verificado (`restic check` sin errores); retención 7 diarios/4 semanales/6 mensuales. |
| **Restauración** | Probada sin destruir producción: base de datos íntegra, índice vectorial correcto, credenciales descifrables y agente arrancando "listo" con los datos restaurados. Postgres de BuenaTierra y Nextcloud restaurados y contados. |
| **Actualización / rollback** | Downtime real medido: **4,7 s** (actualizar) y **5,1 s** (volver atrás), con rollback ejecutado de verdad. |
| **Calidad de código** | 186 tests automáticos en verde, linter y tipado sin errores. |

---

## 6. Trabajo detallado por fase

### Fase 0 — Desbloqueo y seguridad crítica

| Tarea | Hallazgo | Trabajo realizado y resultado |
|---|---|---|
| 0.1 | Cifrado de credenciales podía degradarse a texto plano | Cifrado **fail-closed** y persistente: la clave se guarda en el `.env`, sin fallback ni clave efímera. Test dedicado. |
| 0.2 | 63 vulnerabilidades iniciales en dependencias; el escaneo no bloqueaba el CI | Actualizadas `pypdf`, `fastapi`, `starlette`; `pip-audit` pasa a **gate bloqueante** con 3 excepciones documentadas (solo ChromaDB, ver §8). |
| 0.3 | Posible *path traversal* en la bóveda | Confinamiento con resolución real de ancestro (symlinks y prefijos hermanos incluidos) + tests. |
| 0.4 | Webhook sin secreto único / aceptaba peticiones inválidas | Secreto único por instancia y **fail-closed**: 503 sin secreto, 401 con firma inválida. |
| 0.5 | `ADMIN_IDS` del ejemplo no funcionaba tal cual | Acepta CSV, JSON, id único o vacío, sin edición extra. |
| 0.6 | `/ready` no comprobaba dependencias reales | `/ready` verifica IA, base vectorial y Telegram; `/health` queda como *liveness*. |
| 0.7 | Overlay GPU con YAML inválido (dos bloques `deploy`) | Overlay corregido y documentado en `docs/MIGRATION.md`. |
| 0.8 | Runtime Python ambiguo | Fijado **Python 3.11** en imagen y documentación. |

### Fase 1 — RAG verificable y calidad medible

| Tarea | Trabajo realizado y resultado |
|---|---|
| 1.1 | Reindexado corregido: la clave de borrado coincide con la de indexado (`note_path`), con compatibilidad para filas antiguas. |
| 1.2 | **Dataset de evaluación propio**: 36 casos en español (28 positivos, 8 negativos) sobre una bóveda de 8 notas / 26 fragmentos. |
| 1.3 | **Métricas reales**: recall@3 = 1.0, MRR@5 = 0.982, con salida numérica completa pegada en `plan.md`. |
| 1.4 | Verificado que la métrica de distancia usada equivale a similitud coseno con estos embeddings (vectores unitarios). |
| 1.5 | **Umbral calibrado a 0.49**: 0 falsos positivos y 3/28 falsos negativos; el umbral anterior (0.60) habría descartado 9/28 aciertos. |
| 1.6 | Filtrado por etiquetas en la consulta vectorial (flags escalares). |
| 1.7 | **Primera medición de tool-calling** con modelo real: 29/46 en GPU. En la Fase 3 se descubrió que los fallos eran del modo "razonamiento" del modelo; corregido: **46/46**. |
| 1.8 | Tests de cifrado reescritos para que no puedan pasar "en falso". |

### Fase 2 — Producto configurable

| Tarea | Trabajo realizado y resultado |
|---|---|
| 2.1 | Taxonomía de la bóveda configurable (rutas simbólicas, no cadenas fijas). |
| 2.2 | Idioma, zona horaria, moneda y nombre del asistente configurables (i18n). |
| 2.3 | `PERSIST_TO_BRAIN` bloquea escrituras en modo depuración. |
| 2.4 | **BrainMaintainer** (nuevo): versionado del vault con snapshots y revert real, carpetas protegidas configurables, desactivado por defecto. |
| 2.5 | **Adapters de proveedor de IA**: Ollama local o cualquier endpoint compatible con OpenAI, seleccionable por configuración; misma interfaz para el resto del sistema. |
| 2.6 | **Wizard de instalación** multiplataforma y guía de contribución. |

### Fase 3 — Servidor continuo (dos nodos)

| Tarea | Trabajo realizado y resultado |
|---|---|
| 3.0 | **ADR-004** con las cuatro decisiones de arquitectura (runtime, rendimiento, red, recursos), confirmadas por el propietario y ejecutadas. |
| 3.1 | **Motor desplegado**: Ollama 0.34.4 con `gemma4:12b`, `bge-m3`, `llava:7b`, `qwen2.5:7b`. Benchmark real: 3,87 tok/s (chat), 0,062 s/embedding. **Hallazgo crítico**: el modelo "razonaba" por defecto y devolvía respuestas vacías; se desactivó por configuración (y eso arregló el tool-calling). Red privada Tailscale + firewall; acceso LAN bloqueado; conexión directa a 7 ms. |
| 3.2 | **Aplicación desplegada en el HP** apuntando al Dell: sin motor local, puerto 8010, Whisper a 2 hilos. `/ready` en verde a 21 ms; consumo 160 MB. RAG repetido con resultado idéntico; tool-calling **46/46**; visión validada con imagen real. |
| 3.3 | **Readiness real**: distingue "listo" / "degradado" / "no listo" (IA alcanzable y modelo cargado, base vectorial, bóveda, Telegram) y falla rápido (<10 s) si un nodo cae. Cinco escenarios de fallo verificados. |
| 3.4 | **Pruebas de caos**: agente reiniciado solo tras un fallo real; Dell reiniciado con el agente vivo (detectado en 1,2 s); corte de red simulado (10 s); **arranque en frío de ambos nodos en los dos órdenes** (aquí se encontró y corrigió un fallo: el agente se bloqueaba si arrancaba con el Dell apagado); convivencia de recursos con los 11 servicios existentes sin degradarlos. |
| 3.5 | **Logs estructurados y seguros**: formato JSON opcional, **redacción de secretos antes de escribir a disco** (verificado con datos de prueba), límites de tamaño en fichero y en Docker, y comando remoto `/logs` (solo administradores) para consultar sin SSH. |
| 3.6 | **Backup y restore reales** (ampliado por el propietario a *todos* los servicios): restic cifrado al USB, diario 03:30 solo si el USB está conectado, retención 7/4/6, snapshot diario de configuración del Dell y **aviso por Telegram**. Restauración verificada sin destruir producción (detalle en §7). Incluyó el **renombrado seguro del usuario del HP** a `server` (mismo UID/GID, sin pérdida de datos). |
| 3.7 | **Actualización y rollback**: versión verificable (0.2.0 en imagen y `/health`), downtime medido (**4,7 s** actualizar, **5,1 s** volver atrás), rollback ejecutado de verdad y prueba de nodos independientes (Dell actualizado con el agente en marcha). |

### Fase 4 — Documentación y entrega

| Tarea | Trabajo realizado y resultado |
|---|---|
| 4.1 | Corregidas las inconsistencias de documentación detectadas en la auditoría (URLs, puertos, estado real de funcionalidades). |
| 4.2 | `docs/CHANGELOG.md` con sección *Unreleased* y guía de contribución. |
| 4.3 | **Instalación limpia de extremo a extremo** documentada y probada; la parte de "bot respondiendo en Telegram" se validó en la Fase 3 con el despliegue real. |
| 4.4 | ADRs 001–003 revisadas con evidencia real y ADR-004 añadida. |

---

## 7. Backup, restauración y operación (detalle)

- **Qué se respalda**: Rafita (base de datos, índice vectorial, bóveda, clave de
  cifrado), BuenaTierra, Nextcloud, Nginx Proxy Manager, AdGuard, WireGuard,
  Portainer y la configuración del sistema; más un snapshot diario de la
  configuración del nodo de IA.
- **Cómo**: base de datos con volcados consistentes en caliente; ficheros con
  modo mantenimiento de Nextcloud y una parada de ~5 s de Portainer (aprobado);
  todo empaquetado con **restic cifrado e incremental**.
- **Dónde**: USB "RAFAEL" (117 GB) con estructura
  `Servidor/server-nodochicohp/` (repositorio y logs) y `Servidor/server-dell/`.
  El contenido previo del USB se preservó intacto.
- **Cuándo**: diario a las 03:30, **solo si el USB está presente**; si no,
  se omite y se registra. Aviso por Telegram de éxito o fallo.
- **Verificación real de restauración** (sin destruir producción): base de datos
  íntegra (`integrity_check` = ok), índice vectorial con sus registros, clave de
  cifrado idéntica y funcional (descifrado correcto), agente arrancando en
  estado "listo" con los datos restaurados, y bases PostgreSQL de BuenaTierra y
  Nextcloud restauradas en un contenedor temporal (36 y 204 tablas,
  respectivamente, con datos).
- **Extracción segura del USB** con un comando (`usb-eject`); procedimiento de
  recuperación completo en `docs/runbook.md` (sección 5).

## 8. Seguridad y privacidad

- **Credenciales cifradas** (Fernet) con clave persistente y sin fallback a
  texto plano; rotación documentada.
- **Cierres fail-closed**: sin secreto configurado, los endpoints rechazan en
  lugar de aceptar.
- **Path traversal** de la bóveda corregido y cubierto por tests.
- **Logs**: secretos redactados antes de escribir a disco (verificado con
  datos de prueba), rotación acotada y consulta remota autenticada.
- **Red**: Tailscale + firewall; nada expuesto a la LAN. Hallazgo documentado:
  la regla de firewall para la red privada queda cubierta por la propia
  Tailscale, de modo que **cualquier dispositivo de la red privada del usuario
  puede usar la IA** (decisión consciente del propietario, que lo considera
  útil; restringible con una política de Tailscale si se quisiera).
- **Dependencias**: 3 avisos de la base vectorial sin corrección upstream;
  afectan solo a un modo servidor que **no se usa** (el proyecto usa modo
  embebido), con un test que impide introducir ese modo por accidente.
  Pendiente: marcarlos como "no afectados" en GitHub.

## 9. Riesgos y recomendaciones (priorizadas)

1. **Contraseña de la base de datos de Nextcloud en texto plano** en su
   fichero de configuración: rotarla y moverla a un fichero de entorno
   protegido (tarea corta, recomendada).
2. **Marcar en GitHub los 3 avisos de dependencias como "no afectados"**
   (documentado el motivo y con test de invariante).
3. **Validar el RAG con la bóveda personal real** (la medición actual usa una
   bóveda sintética de 36 casos).
4. **Vigilar memoria/swap del HP** durante la primera semana en producción
   (equipo de 4 hilos compartido con 11 servicios).
5. **Política de Tailscale** si en el futuro se quiere restringir la IA a un
   solo dispositivo (hoy accesible desde toda la red privada, por decisión
   del propietario).
6. **Antes de actualizar Ollama**: snapshot de configuración (se instaló por
   script, sin vuelta atrás automática de versión).

---

## 10. Registro completo de decisiones, dudas y respuestas

Registro de todo lo consultado durante el proyecto y la respuesta o decisión
adoptada (el propietario puede confirmar cada punto):

| # | Duda / opción planteada | Decisión / respuesta |
|---|---|---|
| 1 | **Modelo de IA**: `gemma4:12b` (más lento en CPU) vs `qwen2.5:7b` (1,7× más rápido) | **`gemma4:12b` definitivo**: más inteligente y fiable con herramientas; se acepta la mayor latencia; posibilidad futura de usar una GPU por túnel sin reconfigurar. `qwen2.5:7b` queda instalado como respaldo. |
| 2 | El modelo devolvía respuestas vacías por su modo "razonamiento" | Se **desactiva el razonamiento por configuración** (con esto el tool-calling pasó de 29/46 a 46/46). |
| 3 | **Configuración del HP**: copiar el entorno del PC o crear uno nuevo | **Copiar** (mismo bot y mismas llaves); ajustado el modelo y la dirección del motor. |
| 4 | `ADMIN_IDS` con valores de ejemplo | Se indicó cómo obtener el ID real (@userinfobot); el propietario lo añadió y se copió al HP. |
| 5 | Aparecían **dos carpetas** del proyecto en el HP | Se identificó la correcta (minúscula, con el servicio en marcha); la copia vieja se eliminó con autorización. |
| 6 | **Modelo de visión**: `llava:7b` vs `gemma4:12b` | **`gemma4:12b`** (mismo modelo para chat y visión); validado con imagen real; se optimizó para no recargar el modelo en cada imagen. `llava` queda como respaldo. |
| 7 | Hilos de transcripción de voz en el HP | **2 hilos** (equipo pequeño compartido con 11 servicios), configurable. |
| 8 | **Avisos de dependencias de GitHub** (1 crítica, 2 altas) | Revisados: son de la base vectorial, **sin corrección upstream y no aplican** al modo usado; se añadió un test que impide el modo vulnerable. Pendiente marcarlos como "no afectados" en GitHub. |
| 9 | El envío a GitHub pedía credenciales interactivas | Se registró la **clave SSH** del PC y se cambió el repositorio a SSH; ya no pide credenciales. |
| 10 | **Puerto de la aplicación** en el HP (el 8000 estaba ocupado) | Gateway en **8010**; el motor se referencia por la **IP de la red privada** (la IP local puede cambiar). |
| 11 | **Direcciones IP fijas** de ambos nodos | **Reserva DHCP en el router** (la opción sin conflictos): Dell `192.168.1.121`, HP `192.168.1.129`. La dirección estable entre nodos es la de Tailscale. |
| 12 | **Renombrar el usuario del HP** `rafa` → `server` para unificar | **Sí**, ejecutado de forma segura (mismo UID/GID, datos intactos, servicios recreados y verificados). |
| 13 | **Alcance del backup**: ¿solo Rafita o todo el homelab? | **Todo el homelab** (BuenaTierra, Nextcloud, NPM, AdGuard, WireGuard, Portainer, Rafita y configuración). |
| 14 | **Destino y formato del backup** | **USB "RAFAEL"** conectado al HP, con estructura `Servidor/<nodo>/`; **restic cifrado**; el contenido previo del USB no se toca. |
| 15 | **Frecuencia y condición** | **Diario a las 03:30 solo si el USB está conectado**; retención 7 diarios/4 semanales/6 mensuales. |
| 16 | Paradas mínimas durante el backup | Aprobado: **Portainer ~5 s** y **Nextcloud en modo mantenimiento** durante la copia. |
| 17 | **Verificación del restore**: destructiva o no | **No destructiva** (restaurar a un directorio temporal y validar); producción nunca se destruye. |
| 18 | **Avisos por Telegram** del resultado del backup | Sí; implementado y probado (dos mensajes de prueba recibidos). |
| 19 | **Acceso a la IA desde otros dispositivos** (móvil, etc.) | El propietario confirmó que **puede usarse desde cualquier dispositivo de su red privada**; restringirlo queda como opción futura. |
| 20 | Cómo se entrega el resultado para evaluación | Este informe con el trabajo detallado + registro de decisiones; `plan.md` como anexo técnico con la evidencia cruda. |

---

## 11. Anexo A — Inventario de datos y servicios

| Servicio | Dónde vive | Qué se respalda |
|---|---|---|
| Rafita (bot, base vectorial, bóveda) | HP (`/home/server/proyectos/rafita`) | Base SQLite, índice vectorial, bóveda, clave de cifrado, código y configuración |
| BuenaTierra | HP (`/home/server/proyectos/BuenaTierra` + volúmenes) | Volcado PostgreSQL + ficheros y volúmenes |
| Nextcloud | HP (`/opt/homelab/nextcloud`) | Ficheros + volcado PostgreSQL |
| Nginx Proxy Manager | HP (`/home/server/npm`) | Base SQLite + certificados |
| AdGuard Home | HP (`/data/compose/4`) | Configuración y datos |
| WireGuard | HP (`/home/server/wireguard`) | Configuración de túneles |
| Portainer | HP (volumen Docker) | Definiciones de stacks |
| Motor de IA (Ollama) | Dell | Solo configuración (los modelos se re-descargan) |

## 12. Anexo B — Glosario para revisión no técnica

- **RAG**: técnica que permite al asistente responder citando tus propias notas
  (recupera fragmentos relevantes y los usa como contexto).
- **recall@3 / MRR**: métricas de calidad de esa recuperación (si el fragmento
  correcto aparece entre los 3 primeros; y en qué posición media).
- **Tool-calling**: capacidad del modelo de ejecutar acciones (registrar un
  gasto, crear un evento…) en lugar de solo conversar.
- **Embeddings**: representación numérica del texto para poder buscar por
  significado.
- **Readiness / liveness**: "listo para atender" frente a "el proceso está
  vivo"; el sistema distingue ambos y avisa de estados degradados.
- **Downtime**: tiempo en que el servicio no está disponible durante una
  actualización.
- **restic / snapshot**: herramienta de copias cifradas e incrementales; cada
  copia es un "snapshot" recuperable.
- **Tailscale / WireGuard**: red privada cifrada entre dispositivos, sin abrir
  puertos a la red local.

## 13. Cómo verificar (anexo)

- Evidencia completa por tarea: `plan.md`.
- Decisiones de arquitectura: `docs/adr/001..004`.
- Operación y recuperación: `docs/runbook.md`.
- Cambios por versión: `docs/CHANGELOG.md`.
- Estado del servicio en vivo: `GET /ready` y `GET /health` (incluye versión).
- Historial de cambios de código: repositorio Git (`rufae/Rafita`).

---

**Conclusión:** el proyecto está en un estado apto para uso continuado con
operación y recuperación documentadas, métricas verificables y riesgos
conocidos acotados. No hay tareas del plan pendientes; las recomendaciones de
la sección 9 son mejoras de mantenimiento, no bloqueantes.
