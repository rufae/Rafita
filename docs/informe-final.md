# Informe final — Rafita AVP

**Fecha:** 2026-09-26
**Alcance:** estabilización y profesionalización del asistente personal Rafita AVP
tras la auditoría técnica externa del 2026-09-24 (tag `v0.1.0`).
**Estado:** **completado — 35/35 tareas del plan con evidencia verificable.**
**Documento técnico de detalle:** `plan.md` (fuente de verdad, con comandos y
salidas reales por tarea). Este informe es el resumen para revisión no técnica.

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

## 4. Resultados por fase

| Fase | Contenido | Estado |
|---|---|---|
| 0 | Desbloqueo y seguridad crítica (cifrado, path traversal, webhook, readiness) | Completada |
| 1 | RAG verificable y tool-calling medido (dataset propio, métricas) | Completada |
| 2 | Producto configurable (taxonomía, idioma, proveedores IA, wizard) | Completada |
| 3 | Servidor continuo: despliegue 2 nodos, caos, logs, backup, upgrade | Completada |
| 4 | Documentación, changelog, ADRs, instalación limpia | Completada |

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

## 6. Seguridad y privacidad

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

## 7. Operación

- **Backup diario 03:30** al USB (solo si está conectado) con **aviso por
  Telegram** de éxito o fallo; extracción segura del USB con un comando.
- **Snapshot diario de configuración** del nodo de IA.
- **Runbook** con procedimientos: caídas, restauración desde backup,
  actualización/rollback y checklist de salud.
- **Changelog** y documentación de instalación para terceros.

## 8. Riesgos y recomendaciones (priorizadas)

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

## 9. Cómo verificar (anexo)

- Evidencia completa por tarea: `plan.md`.
- Decisiones de arquitectura: `docs/adr/001..004`.
- Operación y recuperación: `docs/runbook.md`.
- Cambios por versión: `CHANGELOG.md`.
- Estado del servicio en vivo: `GET /ready` y `GET /health` (incluye versión).
- Historial de cambios de código: repositorio Git (`rufae/Rafita`).

---

**Conclusión:** el proyecto está en un estado apto para uso continuado con
operación y recuperación documentadas, métricas verificables y riesgos
conocidos acotados. No hay tareas del plan pendientes; las recomendaciones de
la sección 8 son mejoras de mantenimiento, no bloqueantes.
