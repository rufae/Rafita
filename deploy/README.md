# deploy/ — Despliegue en dos nodos (Fase 3)

Topología real (ver `docs/adr/004-two-node-deployment.md`):

```
[ HP "rafa" 192.168.1.129 ]                    [ Dell "nodo-dell-1" 192.168.1.201 ]
  agente Rafita + ChromaDB                       Ollama (chat, embeddings, visión)
  vault + BrainMaintainer                        sin GPU discreta (i7-8700, 32GB)
  Tailscale 100.121.77.29  ────── WireGuard ────  Tailscale (a enrolar)
```

El LLM vive **solo** en el Dell. La app vive **solo** en el HP y llama al LLM
por la tailnet. El nodo HP no arranca `ollama-service` (ver tarea 3.2).

## Estructura

```
deploy/
├── README.md                 # este documento
├── dell/                     # nodo de IA (nodo-dell-1)
│   ├── 01-install-runtime.sh # Ollama + systemd + modelos (idempotente)
│   ├── 02-network.sh         # Tailscale + ufw + bind final
│   └── 03-benchmark.sh       # tokens/s y latencia contra la API real
└── hp/                       # nodo de aplicación (rafa)
    ├── docker-compose.hp.yml # tarea 3.2 (sin ollama-service, puerto remapeado)
    └── README.md             # notas de despliegue del HP
```

## Orden de despliegue

1. **Dell** — `01-install-runtime.sh` (con sudo), benchmark local (`03`).
2. **Dell** — `02-network.sh` con `HP_TS_IP=100.121.77.29`: enrola Tailscale,
   activa `ufw` y deja Ollama accesible solo por la tailnet desde el HP.
3. **HP** — desplegar la app con `docker-compose.hp.yml` y `OLLAMA_HOST` al
   Dell (tarea 3.2), repetir métricas de 1.3/1.7 contra el LLM remoto.

## Uso remoto de los scripts (sin copiar ficheros)

```bash
# desde el PC de desarrollo / HP
ssh server@192.168.1.201 'sudo bash -s' < deploy/dell/01-install-runtime.sh
ssh server@192.168.1.201 'sudo HP_TS_IP=100.121.77.29 bash -s' < deploy/dell/02-network.sh
ssh server@192.168.1.201 'bash -s' < deploy/dell/03-benchmark.sh
```

## Convenciones

- Scripts `NN-nombre.sh` ejecutados en orden; idempotentes (se pueden repetir).
- Variables por entorno con defaults seguros: `MODELS`, `OLLAMA_BIND`,
  `HP_TS_IP`, `MODEL`, `HOST`, `PROMPT`.
- Ningún puerto se expone a la LAN: Ollama escucha detrás de `ufw`, que solo
  permite 11434 desde la IP Tailscale del HP.
