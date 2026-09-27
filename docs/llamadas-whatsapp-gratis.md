# Llamadas y WhatsApp gratis: viabilidad (2026-09-27)

Resumen para decidir sin gastar nada. Fuentes consultadas en la investigación:
documentación de precios de Meta (WhatsApp Business Platform), CallMeBot,
Evolution API (GitHub), IPComms/FlyNumber (SIP) y foros self-hosted.

## 1. WhatsApp — SÍ hay vías gratuitas

| Opción | Coste real | Qué permite | Riesgos/límites |
|---|---|---|---|
| **Evolution API** (autoalojada) | **0 €** (software libre) | Enviar y **recibir** mensajes con tu WhatsApp personal (cualquier contacto) | No es API oficial (Baileys/WhatsApp Web): **riesgo de bloqueo** del número; necesita un contenedor siempre encendido (el HP puede) |
| **Meta WhatsApp Cloud API** (oficial) | 0 € hasta el límite | Responder dentro de la ventana de 24 h (mensajes de servicio); desde 01/10/2026 hay **1.000 mensajes de servicio gratis al mes** por número | Requiere cuenta Meta Business y número dedicado; **tú no puedes iniciar** conversación fuera de la ventana (plantillas de marketing siempre se pagan) |
| **CallMeBot** | 0 € (uso personal) | Enviar mensajes **solo a tu propio número** (notificaciones) | Unidireccional, pensado para scripts/IoT |

Recomendación: si quieres que Rafita **escriba y reciba WhatsApp de tus contactos**
gratis, la única vía completa es **Evolution API** (asumiendo el riesgo de
bloqueo, mitigable con un número secundario). Si solo quieres notificaciones a
tu WhatsApp, CallMeBot es inmediato. La Cloud API oficial es la opción estable
si aceptas sus límites.

## 2. Llamadas — NO existe nada 100 % gratis para contestar en vivo

- La parte software es gratis: **Asterisk/FreeSWITCH** (centralita SIP libre) +
  el endpoint `/call` de Rafita (ya implementado, ver abajo).
- La parte de **número de teléfono** no es gratis (verificado 2026-09-27):
  - SIP trunk gratis (IPComms) pero el **DID cuesta ~1,50 $/mes** + 0,009 $/min.
  - **Pruebas gratuitas temporales en España**: Flash Telecom (alta gratis
    hasta fin de mes, incluye número y entrantes) y SIP Trunk Hub (demo 15
    días, solo empresas). No son permanentes.
  - DIDs gratuitos (Google Voice, TextNow) **no soportan SIP** (solo su app;
    Google Voice además es solo EE. UU.). Los números 900 de Zadarma los paga
    quien llama, pero el titular paga ~0,0x €/min de entrada.
  - DID español permanente: ~5-6 €/mes (Voicetophone, SIP Trunk Hub) o
    ~1,50 $/mes en EE. UU. (IPComms).
  - Apps Android de "contestador con IA": Android moderno no deja a una app
    hablar dentro de la llamada sin root → inviable.
- Alternativa 100 % gratis: **el llamante deja un WhatsApp** al bot (Evolution)
  o un audio de Telegram, y Rafita resume igual.

Conclusión honesta: el **contestador con IA funcionando de verdad** cuesta
~1,50-6 €/mes (número) y el resto es gratis; gratis-gratis no existe hoy.

## 2.1 Legalidad (verificado 2026-09-27)

- **Grabación en España**: grabar tu propia conversación es lícito (STC
  114/1984; consentimiento de una parte a efectos penales). Ahora bien, si se
  **trata y almacena** sistemáticamente (transcripción, resumen, historial),
  aplica el RGPD/LOPDGDD: base jurídica, información al interlocutor,
  minimización y plazos de conservación (guías AEPD 2026).
- **Transparencia de IA (Ley UE de IA, art. 50)**: desde el **02/08/2026**, un
  asistente de voz que atiende una llamada **debe identificarse como IA al
  inicio** de la interacción (declaración hablada). Ya está aplicado en el
  prompt del contestador (`webhook_server._call_system_prompt`).
- **Práctica recomendada**: aviso al inicio («le atiende un asistente de IA;
  la llamada se transcribe para dejarle el recado»), retención corta del
  resumen y borrado a petición.

## 3. Contestador implementado (listo para conectar)

`POST /call` en el gateway (misma firma HMAC que el resto de webhooks):

```json
{"call_id": "abc", "caller": "+34600...", "text": "Hola, soy Ana", "end": false}
```

- Responde con `{"reply": "...", "end": false}` → el proveedor lo convierte a voz.
- Con `"end": true` genera un **resumen** (quién llama, motivo, urgencia,
  contacto, acción sugerida) y lo manda al **Telegram del propietario**.
- Sesiones con caducidad (1 h) y límite de turnos; fail-closed si no hay firma.
- Cualquier centralita que pueda hacer STT/TTS y llamar a este endpoint vale
  (script de Asterisk ARI, Twilio, FreeSWITCH, VAPI...).
- Tras el aviso, el propietario puede responder en Telegram "agenda una
  reunión con Ana el jueves" y el bot la crea en Google Calendar.

## 4. Decisión (2026-09-27)

1. WhatsApp: **aplazado**; guía lista en `docs/whatsapp-upgrade.md` para
   implementarlo cuando se decida (recomendado: Evolution API autoalojada).
2. Llamadas: **seguir investigando** alternativas gratuitas; el endpoint
   `/call` queda implementado y probado, listo para conectar cuando exista una
   vía sin coste (o si se acepta el DID de ~1,50-6 €/mes).

## 5. Fuentes (consultadas 2026-09-27)

- Precios WhatsApp: <https://developers.facebook.com/docs/whatsapp/pricing>
- Evolution API: <https://github.com/evolution-foundation/evolution-api>
- CallMeBot: <https://www.callmebot.com/>
- SIP gratis/barato: IPComms, Flash Telecom (prueba gratuita), SIP Trunk Hub
  (demo 15 días), Zadarma (900), Voicetophone (~6 $/mes)
- Grabación en España: STC 114/1984; RGPD art. 6/13; LOPDGDD; guías AEPD 2026
- Ley UE de IA art. 50: guías de la Comisión (20/07/2026); obligaciones de
  transparencia aplicables desde el 02/08/2026
