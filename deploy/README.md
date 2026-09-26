# deploy/ — Despliegue en dos nodos (Fase 3)

Topología real (ver `docs/adr/004-two-node-deployment.md`):

```
[ HP "rafa" 192.168.1.129 ]                    [ Dell "nodo-dell-1" 192.168.1.121 ]
  agente Rafita + ChromaDB                       Ollama 0.34.4 (chat, embeddings,
  vault + BrainMaintainer                        visión; i7-8700, 32GB, sin GPU)
  Tailscale 100.121.77.29  ────── WireGuard ────  Tailscale 100.83.40.103
                                                 (directo, ~7 ms; ufw filtra
                                                  11434 solo desde el HP)
```

Modelos desplegados en el Dell (decisiones 3.1/3.2, 2026-09-26): `gemma4:12b`
(chat **y visión**, con `reasoning_effort=none` para desactivar su thinking por
defecto), `bge-m3` (embeddings); `qwen2.5:7b` y `llava:7b` quedan como
fallback (chat y visión respectivamente).

El LLM vive **solo** en el Dell. La app vive **solo** en el HP y llama al LLM
por la tailnet. El nodo HP no arranca `ollama-service` (ver tarea 3.2).

La dirección estable entre nodos es la **IP Tailscale `100.83.40.103`** (la IP
LAN `.121` es una reserva DHCP para gestión/SSH desde la LAN; antes rotó por
DHCP y no debe usarse en configuraciones).

## Estructura

```
deploy/
├── README.md                 # este documento
├── dell/                     # nodo de IA (nodo-dell-1)
│   ├── 01-install-runtime.sh # Ollama + systemd + modelos (idempotente)
│   ├── 02-network.sh         # Tailscale + ufw + bind final
│   └── 03-benchmark.sh       # tokens/s y latencia contra la API real
└── hp/                       # nodo de aplicación (rafa)
    ├── README.md             # notas de despliegue del HP
    └── docker-compose.hp.yml # tarea 3.2 (sin ollama-service, puerto remapeado)
```

## Orden de despliegue

1. **Dell** — `01-install-runtime.sh` (con sudo), benchmark local (`03`).
2. **Dell** — `02-network.sh` con `HP_TS_IP=100.121.77.29`: enrola Tailscale,
   activa `ufw` y deja Ollama accesible solo por la tailnet desde el HP.
3. **HP** — desplegar la app con el overlay (sin Ollama local, puerto 8010) y
   `OLLAMA_HOST` al Dell (tarea 3.2); repetir métricas de 1.3/1.7 contra el
   LLM remoto:

   ```bash
   # en el HP, desde la raíz del repo
   docker compose -f docker-compose.yml -f deploy/hp/docker-compose.hp.yml up -d --build
   curl -s http://127.0.0.1:8010/ready   # readiness con el LLM remoto
   ```

## Uso remoto de los scripts (sin copiar ficheros)

```bash
# desde el PC de desarrollo / HP
ssh server@192.168.1.121 'sudo bash -s' < deploy/dell/01-install-runtime.sh
ssh server@192.168.1.121 'sudo HP_TS_IP=100.121.77.29 bash -s' < deploy/dell/02-network.sh
ssh server@192.168.1.121 'bash -s' < deploy/dell/03-benchmark.sh
```

## Convenciones

- Scripts `NN-nombre.sh` ejecutados en orden; idempotentes (se pueden repetir).
- Variables por entorno con defaults seguros: `MODELS`, `OLLAMA_BIND`,
  `HP_TS_IP`, `MODEL`, `HOST`, `PROMPT`.
- Ningún puerto se expone a la LAN: Ollama escucha detrás de `ufw`, que solo
  permite 11434 desde la IP Tailscale del HP.
