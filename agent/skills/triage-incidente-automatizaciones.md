---
description: Que revisar cuando una automatizacion diaria no llega (briefing, radar, correo).
---

# Triage: automatizacion que no llega

Si el usuario dice que no ha recibido el briefing, el radar o similar:

1. Pregunta/comprueba la fecha (nota esperada: `Briefings/Briefing YYYY-MM-DD.md`
   o `Radar/Radar IA YYYY-MM-DD.md` en la boveda).
2. El heartbeat diario ya deberia haber avisado y regenerado; si no, lanzalo
   a mano con `run_automation` (webhook `manual-briefing` o `manual-radar-ia`).
3. Si la regeneracion falla: revisa `get_automation_runs` y `analyze_system_logs`
   (errorWorkflow de n8n enmascara fallos: mira si el nodo salio vacio).
4. Causas tipicas: agente caido (ECONNREFUSED), timeout de HTTP en n8n
   (configurado en 600s desde 2026-10-07) o Ollama degradado (mensaje honesto
   del circuit breaker).
5. Reporta al usuario que paso y que se hizo, sin maquillar el fallo.
