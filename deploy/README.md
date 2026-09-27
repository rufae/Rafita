# deploy/ — Despliegue en dos nodos (Fase 3)

Topología real (ver `docs/adr/004-two-node-deployment.md`):

```
[ nodo-hp (server) <IP_LAN_HP> ]                 [ nodo-dell <IP_LAN_DELL> ]
  agente Rafita + ChromaDB                       Ollama 0.34.4 (chat, embeddings,
  vault + BrainMaintainer                        visión; i7-8700, 32GB, sin GPU)
  Tailscale <TS_HP>   ────── WireGuard ────      Tailscale <TS_DELL>
                                                 (directo, ~7 ms; ufw filtra
                                                  11434 solo desde el HP)
```

Modelos desplegados en el Dell (decisiones 3.1/3.2, 2026-09-26): `gemma4:12b`
(chat **y visión**, con `reasoning_effort=none` para desactivar su thinking por
defecto), `bge-m3` (embeddings); `qwen2.5:7b` y `llava:7b` quedan como
fallback (chat y visión respectivamente).

El LLM vive **solo** en el Dell. La app vive **solo** en el HP y llama al LLM
por la tailnet. El nodo HP no arranca `ollama-service` (ver tarea 3.2).

La dirección estable entre nodos es la **IP Tailscale del Dell** (la IP LAN es
una reserva DHCP para gestión/SSH desde la LAN; antes rotó por DHCP y no debe
usarse en configuraciones).

## Estructura

```
deploy/
├── README.md                 # este documento
├── dell/                     # nodo de IA (nodo-dell)
│   ├── 01-install-runtime.sh # Ollama + systemd + modelos (idempotente)
│   ├── 02-network.sh         # Tailscale + ufw + bind final
│   └── 03-benchmark.sh       # tokens/s y latencia contra la API real
└── hp/                       # nodo de aplicación (nodo-hp)
    ├── README.md             # notas de despliegue del HP
    ├── docker-compose.hp.yml # tarea 3.2 (sin ollama-service, puerto remapeado)
    └── ready_probe.py        # sonda de readiness para evidencias (3.3/3.4)
```

## Orden de despliegue

1. **Dell** — `01-install-runtime.sh` (con sudo), benchmark local (`03`).
2. **Dell** — `02-network.sh` con `HP_TS_IP=<TS_HP>`: enrola Tailscale,
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
ssh server@<IP_DELL> 'sudo bash -s' < deploy/dell/01-install-runtime.sh
ssh server@<IP_DELL> 'sudo HP_TS_IP=<TS_HP> bash -s' < deploy/dell/02-network.sh
ssh server@<IP_DELL> 'bash -s' < deploy/dell/03-benchmark.sh
```

## Convenciones

- Scripts `NN-nombre.sh` ejecutados en orden; idempotentes (se pueden repetir).
- Variables por entorno con defaults seguros: `MODELS`, `OLLAMA_BIND`,
  `HP_TS_IP`, `MODEL`, `HOST`, `PROMPT`.
- Ningún puerto se expone a la LAN: Ollama escucha detrás de `ufw`, que solo
  permite 11434 desde la IP Tailscale del HP.
