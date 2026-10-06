/**
 * Carga de `POST /api/chat` — gateway Rafita (FastAPI, puerto 8000).
 *
 * Ruta verificada en `agent/src/utils/web_api.py` (router con prefix "/api"
 * + `@router.post("/chat")`): el cuerpo es {"message": "..."} y la respuesta
 * 200 trae {"reply": "...", "chat_id": 900000000+}. La autenticación es un
 * JWT Bearer que se obtiene en `setup()` con `POST /api/auth/login`
 * {"email", "password"} (o se pasa ya creado con RAFITA_TOKEN).
 *
 * Escenario: rampa 1 → 5 → 10 VUs con meseta y bajada (~4 min en total).
 *
 * Umbral p95: 8000 ms por defecto. Un turno completo pasa por el orquestador
 * (prompt de sistema + RAG + posible bucle de herramientas) contra un modelo
 * local de 12B, y a 10 VUs las peticiones se encolan en la GPU: 2500 ms solo
 * es alcanzable con respuestas cortas y sin herramientas. Para exigir ese
 * SLA estricto (o relajarlo en un soak largo) se cambia por variable:
 *   -e P95_MS=2500   -e P95_MS=15000
 *
 * Ejecución (ver deploy/k6/README.md):
 *   k6 run -e BASE_URL=http://localhost:8000 \
 *       -e RAFITA_EMAIL=yo@ejemplo.com -e RAFITA_PASSWORD=**** \
 *       deploy/k6/chat.js
 */

import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const P95_MS = Number(__ENV.P95_MS || 8000);
const P50_MS = Number(__ENV.P50_MS || 4000);
const TIMEOUT = __ENV.K6_TIMEOUT || '120s';

// Mensajes representativos: saludo corto, pregunta de conocimiento, tarea con
// herramienta probable y petición de resumen (el mismo tráfico que la SPA).
const MENSAJES = [
  'Hola, ¿qué tienes pendiente para hoy?',
  '¿Cuántos días tiene febrero de 2028?',
  'Apúntame un recordatorio para beber agua en 30 minutos.',
  'Resume en tres frases lo que hemos hablado esta semana.',
];

export const options = {
  setupTimeout: '60s',
  scenarios: {
    carga_chat: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: '30s', target: 1 }, // calentamiento (modelo ya cargado)
        { duration: '60s', target: 5 }, // rampa hasta 5 VUs
        { duration: '90s', target: 10 }, // rampa hasta 10 VUs
        { duration: '45s', target: 10 }, // meseta a máxima carga
        { duration: '30s', target: 0 }, // bajada ordenada
      ],
      gracefulRampDown: '30s',
      tags: { escenario: 'carga_chat' },
    },
  },
  thresholds: {
    'http_req_duration{endpoint:chat}': [`p(95)<${P95_MS}`, `p(50)<${P50_MS}`],
    'http_req_failed{endpoint:chat}': ['rate<0.01'],
    checks: ['rate>0.99'],
  },
};

/** Obtiene el token Bearer una sola vez antes de las VUs. */
export function setup() {
  if (__ENV.RAFITA_TOKEN) {
    return { token: __ENV.RAFITA_TOKEN };
  }
  const email = __ENV.RAFITA_EMAIL;
  const password = __ENV.RAFITA_PASSWORD;
  if (!email || !password) {
    throw new Error(
      'Faltan credenciales: pasa RAFITA_TOKEN o RAFITA_EMAIL + RAFITA_PASSWORD (ver deploy/k6/README.md)'
    );
  }
  const res = http.post(
    `${BASE_URL}/api/auth/login`,
    JSON.stringify({ email, password }),
    {
      headers: { 'Content-Type': 'application/json' },
      tags: { endpoint: 'login' },
      timeout: '30s',
    }
  );
  if (res.status !== 200) {
    throw new Error(
      `Login falló (HTTP ${res.status}): ¿existe el usuario y está WEB_AUTH_SECRET configurado? ` +
        String(res.body || '').slice(0, 200)
    );
  }
  const token = res.json('token');
  if (!token) {
    throw new Error('El login no devolvió token');
  }
  return { token: token };
}

export default function (datos) {
  const mensaje = MENSAJES[(__VU + __ITER) % MENSAJES.length];
  const res = http.post(
    `${BASE_URL}/api/chat`,
    JSON.stringify({ message: mensaje }),
    {
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${datos.token}`,
      },
      tags: { endpoint: 'chat' },
      timeout: TIMEOUT,
    }
  );

  // Solo se interpreta el cuerpo en el 200: los errores vienen en JSON con
  // otra forma ({detail} o {success, error}) y romperían res.json('reply').
  let reply = '';
  let chatId;
  if (res.status === 200) {
    try {
      reply = res.json('reply') || '';
      chatId = res.json('chat_id');
    } catch (e) {
      reply = '';
    }
  }
  check(res, {
    'HTTP 200 en /api/chat': (r) => r.status === 200,
    'reply no vacío': () => typeof reply === 'string' && reply.length > 0,
    'chat_id presente': () => chatId !== undefined && chatId !== null,
  });

  // Pausa entre iteraciones: sin ella el bucle dispara el LLM sin tasa.
  sleep(1);
}
