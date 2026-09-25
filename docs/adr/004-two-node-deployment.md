# ADR-004: Despliegue en dos nodos (LLM en Dell, app Rafita en HP)

**Estado**: Propuesta — **pendiente de confirmación del usuario** antes de tocar
el nodo Dell.
**Fecha**: 2026-09-25
**Contexto de plan**: tarea 3.0 de `plan.md` (Fase 3).

---

## Contexto

Hasta la Fase 2 el despliegue se asumió mono-máquina (`localhost`). La
topología real pasa a ser:

- **Dell OptiPlex 7060 Micro** (`192.168.1.201`; originalmente `.121`, cambió tras un reinicio — IP no estática): i7-8700 (6c/12t), 32 GB RAM,
  1 TB NVMe, **sin GPU discreta** (iGPU Intel UHD 630). Solo tiene el SO
  (Ubuntu Server 24.04.5). Rol: inferencia (chat, visión, embeddings).
- **HP "rafa"** (`192.168.1.129`): i3-1005G1 (2c/4t), 8 GB RAM (~6.9 GiB
  visibles), ~100 GB SSD. Ya corre 11 contenedores de producción. Rol: agente
  Rafita, ChromaDB embebido, vault, BrainMaintainer; llama al LLM del Dell por
  red.

## Evidencia recogida (solo lectura, sin tocar el Dell)

Fecha 2026-09-25, desde el PC de desarrollo (192.168.1.206) y por SSH al HP
(`nodochicohp`):

```
# Nodo HP
$ free -h
               total   usado    libre  compartido  búf/caché  disponible
Mem:           6,9Gi   2,1Gi    620Mi       273Mi      4,8Gi        4,9Gi
Swap:          4,0Gi   150Mi    3,9Gi
MemTotal: 7284996 kB   MemAvailable: 5113776 kB   SwapFree: 4040668 kB
$ uptime
 16:49:46 up 6 days, 22:03, 4 users, load average: 0,01, 0,07, 0,08
$ nproc
4
$ df -h /
/dev/mapper/ubuntu--vg-ubuntu--lv   98G   35G   58G  38% /
$ docker stats --no-stream (11 contenedores en marcha)
 suma aproximada de RAM de los 11 contenedores: ~1,2 GiB
 (los mayores: nextcloud_app 194,6MiB, collabora 193,2MiB, adguardhome 181,6MiB,
  buenatierra_api 159,9MiB, nextcloud_db 26,8MiB; CPU total < 2%)
$ ss -tln | grep -v 127.0.0.1
 0.0.0.0:8000 LISTEN   <- Portainer (0.0.0.0:8000->8000/tcp)
 0.0.0.0:8001 NO aparece (libre)
 80/443/81/9443/51821/5434/61208/22 en uso
$ tailscale status
100.121.77.29 nodochicohp ... (peers: LAPTOP-6GIU8MDH offline, S20 android offline)
# El Dell NO está enrolado en la tailnet todavía.

# Alcance del Dell en el momento del análisis
$ ping 192.168.1.121   (desde PC y desde HP)
DELL_LAN_FAIL  -> el Dell no respondía a ICMP en el momento del análisis
```

---

## (a) Runtime del LLM en el Dell — **Ollama** (recomendado)

**Decisión propuesta:** Ollama, y `AI_PROVIDER=ollama` en el agente.

**Por qué (no es solo inercia):**
1. **Cero trabajo de adapter**: `OllamaClient` ya está integrado y validado con
   `gemma4:12b`/`bge-m3` en 1.7 (tool-calling) y 2.5 (contrato de proveedor).
   Pasar a `llama.cpp+llama-swap` obligaría a re-validar tool-calling con las
   plantillas de Gemma (2.5 solo probó el adapter OpenAI-compatible contra el
   `/v1` de Ollama, no contra llama.cpp).
2. **La ventaja principal de llama-swap no aplica aquí**: existe para hacer
   swap de modelos cuando la RAM/VRAM no permite tenerlos cargados a la vez.
   El Dell tiene **32 GB** y los tres modelos suman ~13,5 GB
   (`gemma4:12b` 7,6 GB + `bge-m3` 1,2 GB + `llava:7b` ~4,7 GB según tamaños ya
   descargados en el PC), así que caben holgados sin descargar.
3. Ollama envuelve llama.cpp y expone `/api/embed` (que ya usa el proyecto) y
   `/v1` compatible OpenAI; además simplifica el pin de versión, el
   `keep_alive` y la gestión de modelos.

**Salvedad honesta:** si el benchmark de (b) mostrara que Ollama no exprime el
CPU (p. ej. mala selección de hilos o falta de flags AVX2), la alternativa a
evaluar sería `llama.cpp+llama-swap` (o llama.cpp servido con `--threads`), pero
**solo como plan B medido**, no como elección por defecto. En ese caso hay que
repetir la suite 1.7 antes de aceptarlo.

**Consecuencias:** fijar versión de Ollama en el Dell (reproducibilidad),
`OLLAMA_KEEP_ALIVE=-1` para los tres modelos, `OLLAMA_MAX_LOADED_MODELS=3`.

## (b) Rendimiento sin GPU — **no fijar modelo hasta medir**

**Decisión propuesta:** el modelo definitivo se elige con el benchmark de 3.1;
`gemma4:12b` es el candidato por calidad de tool-calling, **no un hecho
asumido** (1.7 se midió con RTX 3060 y no es extrapolable a CPU).

**Protocolo de benchmark (se ejecutará en 3.1, tras el OK):**
1. `ollama run <modelo> --verbose` o API `/api/generate` leyendo
   `eval_count`/`eval_duration`: tokens/s de generación y de prompt-eval, por
   modelo (`gemma4:12b q4`, `qwen2.5:7b q4`, `qwen2.5:3b q4`).
2. Repetir una **pasada reducida de la suite 1.7** contra el LLM en el Dell
   (mismos prompts/equivalencias) para medir tool-calling real en CPU.
3. Medir latencia de red HP→Dell añadida (p50/p95) con el endpoint real.
4. Embeddings: tiempo medio por chunk de `bge-m3` en el Dell (afecta al
   backfill del vault en 3.2, que se hace cruzando la red).

**Umbrales de aceptación propuestos (revisables por el usuario):**
- primer token ≤ 5 s y generación ≥ 4 tok/s para el chat;
- suite de tool-calling sin caída grave frente al baseline de 1.7 (29/46);
- si `gemma4:12b` no los cumple: pasar a `qwen2.5:7b` (esperado más rápido en
  CPU) y, si tampoco, `qwen2.5:3b`, repitiendo 1.7 y documentando la pérdida
  de calidad. No se construye 3.2 encima sin este número.

**Riesgo declarado:** 12B en un i7-8700 sin AVX-512 puede quedar en ~3–6 tok/s;
respuestas cortas de Telegram podrían tardar 20–40 s. Si el usuario acepta esa
latencia como “no tiempo real”, se documenta; si no, se degrada de modelo.

## (c) Seguridad de red — **Tailscale (WireGuard) + firewall de respaldo**

**Decisión propuesta:** enrolar el Dell en la misma tailnet que el HP
(instalación en 3.1), y:
- Ollama en el Dell escuchando **solo** en su IP Tailscale
  (`OLLAMA_HOST=100.x.y.z:11434`) además de loopback;
- `ufw` en el Dell aceptando 11434 **solo** desde la IP Tailscale del HP
  (defensa en profundidad; la ACL de Tailscale como control principal);
- nada de exponer 11434 a `0.0.0.0`/LAN.

**Por qué Tailscale y no solo un firewall:** el tráfico ahora puede llevar
fragmentos del vault (finanzas, salud) dentro de prompts y respuestas. Un
firewall restringe origen pero **no cifra**; Tailscale da restricción por
identidad y **cifrado en tránsito** sin abrir puertos a la LAN. El HP ya corre
Tailscale (100.121.77.29) y es subnet router; el Dell aún **no está enrolado**
(verificado en `tailscale status`), por lo que este paso es parte de 3.1.

**Alternativa rechazada por ahora:** firewall LAN-only con HTTP en claro;
suficiente contra exposición accidental, insuficiente para datos personales en
tránsito.

## (d) Presupuesto de recursos en el HP — **cabe con margen, con ajustes**

**Medición real (11 contenedores arriba):** 6,9 GiB totales, **~4,9 GiB
disponibles**, swap 4 GiB apenas usado (150 MiB), load < 0,1, disco 58 GB
libres.

**Estimación de Rafita en HP (sin inferencia local):** agente Python + Chroma
embebido + vault + BrainMaintainer ≈ **1,0–1,5 GiB** en régimen normal; Whisper
`base` int8 añade ~150–300 MB solo mientras hay voz; los embeddings y el LLM
van al Dell. Total esperado ≤ 2 GiB, dejando ≥ 2,5 GiB de margen para picos de
Collabora/Nextcloud.

**Decisiones propuestas:**
1. Mantener el límite de 2 G del contenedor del agente (reserva 512 M) y
   vigilar swap/OOM la primera semana; no subir de 2 G salvo evidencia.
2. **El compose del HP no debe arrancar `ollama-service`**: la app debe usar
   `OLLAMA_HOST=<dell>`, y el `depends_on: ollama-service` actual no aplica.
   Se necesita un overlay `docker-compose.hp.yml` (o perfil) sin ese servicio
   (trabajo de 3.2).
3. **Conflicto de puerto detectado:** Portainer ya ocupa `0.0.0.0:8000`; el
   gateway de Rafita deberá publicarse en otro puerto (p. ej. `8010`) en el HP.
   8001 (voz) está libre.
4. Reducir los hilos de Whisper de 4 a 2 en el HP (`voice_stream`), porque el
   i3 solo tiene 2c/4t y la transcripción no debe degradar Nextcloud/Collabora.
5. Mantener el swap actual como red de seguridad (no añadir zram por ahora);
   si el swap empieza a moverse con regularidad, revisar antes de ampliar RAM.

---

## Consecuencias y trabajo derivado (para 3.1/3.2)

- `.env` del HP: `AI_PROVIDER=ollama`, `OLLAMA_HOST=http://100.<dell>:11434`,
  `OLLAMA_MODEL` según benchmark, `EMBEDDING_MODEL` según benchmark.
- 3.3 (readiness) debe usar `AIProvider.check_health()` (2.5) en lugar del
  `/api/tags` específico de Ollama si en el futuro cambia el runtime.
- Pin de versión de Ollama y de los modelos en el Dell; documentar
  configuración para reconstruirlo (3.6).
- Los pesos viven en el NVMe del Dell; no requieren backup de datos de usuario.

## Elementos abiertos / riesgos

- El Dell **no respondía a ping** en el análisis (¿apagado? ¿sin red?), y no
  está en Tailscale: 3.1 empieza por confirmar encendido/red y enrolarlo.
- `ufw` no pudo consultarse por SSH sin sudo; confirmar estado real en 3.1.
- La latencia CPU del modelo grande es el riesgo principal; mitigación medida
  en (b), no asumida.
- Portainer en 8000 obliga a remapear el gateway en HP.

**Estado:** propuesta presentada. No se instala ni configura nada en el Dell
hasta confirmación explícita del usuario.
