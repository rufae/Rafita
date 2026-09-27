# WhatsApp en Rafita: guía de implementación futura

Estado (2026-09-27): **no implementado**. Decisión del propietario: dejarlo
documentado para hacerlo más adelante. Todo lo de abajo es gratuito.

## Opciones (ordenadas por utilidad)

### A. Evolution API autoalojada (recomendada si se quiere completo)
- **Qué da**: enviar y **recibir** mensajes de WhatsApp con tu número personal,
  desde cualquier contacto (Rafita como tus manos).
- **Coste**: 0 €. Software libre (Baileys/WhatsApp Web). Requiere un contenedor
  siempre encendido: el HP ya tiene Docker (12 contenedores) y sobra CPU.
- **Riesgo**: no es API oficial → posible **bloqueo del número**. Mitigación:
  usar un número secundario (prepago) para el bot.
- **Esbozo de integración**:
  1. Servicio en `docker-compose.yml`:
     `evoapicloud/evolution-api:latest`, volumen `evolution_data`,
     `AUTHENTICATION_API_KEY=${EVOLUTION_API_KEY}` (en `.env`, nunca en git),
     puerto interno 8080 (sin exponer a la LAN).
  2. Emparejar una vez: abrir el manager, escanear el QR desde el WhatsApp del
     número elegido.
  3. Webhook de Evolution → `POST /webhook/whatsapp` del gateway (misma firma
     HMAC que el resto). En el handler: `orchestrator.generate_response()` y
     responder con `POST {EVOLUTION_API_URL}/message/sendText/{instance}`.
  4. Herramienta nueva `send_whatsapp(to, text)` en `chat_tools.py` +
     despacho en `chat.py` (mismo patrón que `send_gmail`), resolviendo el
     nombre del contacto si no es un número.
  5. Tests: webhook firmado (recibir), envío con httpx mockeado; batería con
     frases tipo «manda un whatsapp a mamá».
  6. Rollback: parar el contenedor y quitar la herramienta; no afecta al resto.

### B. Meta WhatsApp Cloud API (oficial)
- **Qué da**: API oficial de Meta; responde dentro de la ventana de 24 h.
- **Coste**: acceso gratis; desde 01/10/2026 **1.000 mensajes de servicio
  gratis al mes** por número (los mensajes iniciados por ti fuera de la
  ventana son plantillas y se pagan; marketing siempre se paga).
- **Límites**: requiere cuenta Meta Business, número dedicado y plantillas
  aprobadas para iniciar conversación.
- **Cuándo elegirla**: si quieres estabilidad oficial y te basta con
  responder a quien te escriba.

### C. CallMeBot (mínimo esfuerzo)
- **Qué da**: enviar mensajes **solo a tu propio número** por HTTP GET.
- **Coste**: 0 € (uso personal, opt-in enviando un mensaje al bot).
- **Cuándo**: solo para avisos/notificaciones a tu WhatsApp. No sirve para
  hablar con contactos.

## Qué NO es gratis
- Twilio, 360dialog y demás BSP: cobran por mensaje o suscripción.
- «Ilimitado gratis»: no existe en ninguna vía (las no oficiales arriesgan
  bloqueo; las oficiales cobran fuera de las ventanas gratuitas).

## Legalidad y ToS (verificado 2026-09-27)
- **Evolution API (Baileys)**: usa el protocolo de WhatsApp Web, **no la API
  oficial**; Meta puede bloquear el número por uso no autorizado. Es la vía
  gratuita más completa, pero asume ese riesgo (mitigación: número secundario).
- **Cloud API**: es la vía oficial; cumplir sus políticas es obligatorio.
- **Transparencia de IA (Ley UE de IA, art. 50)**: desde el **02/08/2026** los
  sistemas de IA que conversan con personas (chatbots incluidos) deben
  identificarse como IA al inicio de la interacción. Si Rafita responde por
  WhatsApp, el primer mensaje debe decirlo. Sanciones de hasta 15 M € o 3 %
  de la facturación (art. 99).
- **RGPD**: los mensajes y contactos son datos personales; guardar solo lo
  necesario, con finalidad clara y borrado a petición.

## Referencias
- Evolution API: <https://github.com/evolution-foundation/evolution-api>
- Precios WhatsApp: <https://developers.facebook.com/docs/whatsapp/pricing>
- CallMeBot: <https://www.callmebot.com/>
