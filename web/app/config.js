/* Configuracion de la web por instalacion (editable sin tocar el codigo).
   callOrigin: URL publica del servidor de voz (puerto 8001 por defecto).
   Ejemplos:
     "" (vacio)                      -> mismo host, puerto 8001
     "https://voz.midominio.local"   -> proxy NPM con WebSocket hacia 8001
*/
window.RAFITA_CONFIG = { callOrigin: "" };
