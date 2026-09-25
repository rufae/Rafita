---
id: eval-chunking
title: Chunking semantico para RAG
type: nota-atomica
tags: [rag, embeddings]
status: abierto
created: 2026-05-02
updated: 2026-05-02
related: []
---

## Idea principal

Dividir las notas por encabezados preserva el contexto de cada sección. El tamaño recomendado es de 500 tokens con un solape de 50 tokens entre fragmentos.

## Por que importa

Los fragmentos demasiado largos diluyen la relevancia de la búsqueda; los fragmentos demasiado cortos pierden contexto.

## Herramientas

El modelo de embeddings bge-m3 es multilingüe y funciona bien en español.
