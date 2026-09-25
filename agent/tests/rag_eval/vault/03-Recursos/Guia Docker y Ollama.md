---
id: eval-docker
title: Guia Docker y Ollama
type: recurso
tags: [docker, ollama, guia]
status: abierto
created: 2026-03-10
updated: 2026-03-10
related: []
---

## Instalar Ollama

Descargar Ollama desde ollama.com y ejecutar `ollama pull qwen2.5:7b` para descargar el modelo base.

## Modelos recomendados

- qwen2.5:7b para equipos sin GPU (CPU).
- gemma4:12b para equipos con GPU de al menos 12 GB de VRAM.

## Solucion de problemas

Si el contenedor no arranca, revisar `docker compose logs`. El puerto por defecto de Ollama es 11434.
