# deploy/hp — nodo de aplicación (HP nodochicohp, usuario server, 192.168.1.129)

Aquí vive la app Rafita (agente, ChromaDB embebido, vault, BrainMaintainer).
El LLM **no** se ejecuta en este nodo: se llama al nodo Dell por la tailnet.

## Pendiente (tarea 3.2)

- `docker-compose.hp.yml`: overlay del compose **sin** `ollama-service` y sin
  el `depends_on` asociado, con `OLLAMA_HOST=http://<dell-tailscale>:11434`.
- Publicar el gateway en otro puerto (Portainer ya ocupa `0.0.0.0:8000`; usar
  p. ej. `8010`). El 8001 de voz está libre.
- Límites de recursos del agente: 2G de límite / 512M de reserva (medido en
  3.0(d): ~4,9 GiB disponibles en el HP).
- Reducir Whisper a 2 hilos (`voice_stream`) por los 2c/4t del i3.

## Contexto medido (2026-09-25)

- 11 contenedores de producción consumen ~1,2 GiB de RAM en total.
- Quedan ~4,9 GiB disponibles de 6,9 GiB y 58 GB de disco libre.
