---
description: Como capturar contenido en el segundo cerebro con frontmatter y tags validos.
---

# Captura al segundo cerebro

Cuando el usuario pida guardar algo en la boveda o en el segundo cerebro:

1. Elige carpeta con `get_taxonomy()` (no metas todo en la raiz).
2. Frontmatter minimo: `type`, `tags`, `status`, `created`, `updated`, `related: []`.
3. Titulo descriptivo con fecha si es un log (ej: "Captura 2026-10-07 1830").
4. Cuerpo con encabezados (`## Resumen`, `## Contenido`) para que el chunker
   por headings indexe bien en Chroma.
5. Confirma al usuario la nota creada con su nombre exacto.

Si el contenido viene de un PDF/DOCX/TXT/CSV, no lo dupliques: deja que el
intake automatico (`intake_document`) cree la nota companera y se enriquezca
solo con tags, resumen y entidades.
