# Gastos automáticos desde el móvil (Tasker → Banco)

Registra cada pago con tarjeta directamente en Rafita a partir de la
notificación de la app de tu banco, sin teclear nada.

```
Notificación del banco (Android)
        │  Tasker (listener de notificaciones)
        ▼
POST http://100.121.77.29:8010/automation/banco-gasto   (header X-Banco-Token)
        │
        ▼
Rafita Gateway (HP) ──► dedupe 48 h, descarta domiciliados
        ├─► BD finance_records
        ├─► Obsidian Control_Financiero_2026
        └─► Aviso en Telegram con el ID ("borra el gasto 42")
```

## 1. En el HP (una vez)

Edita `~/proyectos/rafita/.env` (sin sudo, es tu usuario):

```bash
# token fijo para Tasker: genera uno con
#   openssl rand -hex 16
BANCO_WEBHOOK_TOKEN=<tu-token>
```

> El gateway **ya está expuesto en `:8010`** en el HP (overlay
> `deploy/hp/docker-compose.hp.yml`): no hay que abrir puertos ni tocar
> `GATEWAY_*`; sin token configurado el endpoint devuelve 503 y no registra
> nada.

Reinicia (en el HP hay que pasar el overlay; el CI/CD lo hace en cada push):

```bash
cd ~/proyectos/rafita && docker compose -f docker-compose.yml \
  -f deploy/hp/docker-compose.hp.yml up -d rafita-agent-core
```

## 2. En el móvil

1. **Tailscale** (Play Store, gratis): entra con la misma cuenta que el HP.
   Comprueba `http://100.121.77.29:8010/ready` desde el navegador.
2. **Tasker** (Play Store, pago único / prueba) o **MacroDroid** (gratis).

## 3. Perfil y tarea (Tasker)

1. **Ajustes Android → Apps → Acceso a notificaciones** → activar Tasker.
2. Perfil nuevo → **Evento → Aplicación → Notificación** → *Application*:
   elige tu app del banco (Tasker rellena el paquete solo).
3. Tarea → **Acción → Red → HTTP Request**:
   - Method `POST`, URL `http://100.121.77.29:8010/automation/banco-gasto`
   - Headers:
     ```
     Content-Type: application/json
     X-Banco-Token: <tu-token>
     ```
   - Request Body (raw):
     ```json
     {"text": "%NTITLE — %NTEXT"}
     ```
     `%NTITLE`/`%NTEXT` es el título y cuerpo de la notificación; no hace
     falta que pongas el importe, Rafita lo extrae del texto
     (`45,20 €`, `1.250,00 €`, `12.99 EUR`...).

Con MacroDroid: Trigger *Notificación de app* → Action *HTTP Request*
(POST, mismo URL/headers/body).

### ¿Tengo que tener la VPN siempre activa? No

- **Leer** la notificación es local: Tasker la capta con la VPN apagada,
  sin datos y en avión.
- **Enviar** sí necesita red (tailscale). Si falla, en vez de perder el
  pago se encola en el móvil y se reenvía solo cuando vuelva la VPN.

Dos perfiles en Tasker:

**Perfil 1 — evento de notificación** (el de arriba) con esta tarea:

1. **Action → Code → JavaScript**: guarda el cuerpo ya escrito
   `let t = {text: global('NTITLE') + ' — ' + global('NTEXT')};
   setGlobal('BANCO_BODY', JSON.stringify(t));`
2. **HTTP Request** (igual que arriba) con Request Body `%BANCO_BODY`.
3. **If** `%http_code` *doesn't match* `2..` →
   **Action → File → Write File** → Path `Tasker/banco_cola.txt`,
   **Append ON**, Text `%BANCO_BODY` (así cada línea ya es JSON válido).

**Perfil 2 — Evento → Time → Every 10 min** (o *State → Net → Connected*),
tarea "Reintentar":

1. **File → Read File** → `Tasker/banco_cola.txt`,
   *One Line Per Element* ON → queda en `%file()`.
2. **For Each** `%file()`, variable `%linea`:
   **HTTP Request** con el mismo URL/headers y Request Body `%linea`;
   Variable Set `%falla` a `1` si `%http_code` *doesn't match* `2..`.
3. Al salir del bucle: si `%falla` no está definido → **File → Clear File**
   (`Tasker/banco_cola.txt`) — todo llegó, se vacía. Si algo falló, el
   fichero queda como estaba y se reintenta en la próxima pasada.

Resultado: la VPN puede estar apagada días; al activarla (o con ella
siempre activa), la cola sale sola en ≤10 min. Si prefieres prescindir de
tailscale **en casa**, basta con usar la IP local del HP: el gateway ya
escucha en todos los interfaces (`0.0.0.0:8010`), así que
`http://192.168.1.x:8010/...` funciona sin VPN en la misma WiFi; fuera de
la WiFi sigue funcionando por la cola.

## 4. Probar

Desde cualquier equipo en tailscale:

```bash
curl -sS http://100.121.77.29:8010/automation/banco-gasto \
  -H 'Content-Type: application/json' \
  -H 'X-Banco-Token: <tu-token>' \
  -d '{"text": "Google Pay ha sido 4,50 € en Metro"}'
```

Esperas `{"success": true, ...}` y un aviso en Telegram con `ID`. Luego
dinle «borra el gasto N» si era una prueba.

## Qué hace y qué no el backend

| Caso | Resultado |
|---|---|
| Pago normal (`45,20 € en Mercadona`) | Se registra + aviso con ID |
| Domiciliación, cuota, recibo, SEPA | Se ignora (no avisa, no registra) |
| Mismo pago notificado 2 veces (reintentos) | Dedupe 48 h por hash importe+concepto+fecha |
| Importe imposible de leer | Responde `success: false`, nada se guarda |

- Los domiciliados no se registran a propósito: solo pagos con
  tarjeta/wallet (gimnasio, seguros... ya salen en `/finanzas` si algún día
  los apuntas a mano).
- La fecha de la notificación no viaja: si necesitas la hora exacta del
  pago, añade `"fecha": "2026-10-08 12:34:56"` al JSON (Tasker: `%TIMES`).
- Sin HTTPS: el trato va cifrado por WireGuard (tailscale) de punta a punta.
  Si algún día lo expones a internet, ponlo detrás de un proxy con TLS.
