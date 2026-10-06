# k6 — pruebas de carga del gateway Rafita

`chat.js` ejerce `POST /api/chat` (ruta real: router `APIRouter(prefix="/api")`
en `agent/src/utils/web_api.py` + `@router.post("/chat")`) con una rampa de
1 → 5 → 10 VUs y meseta, ~4 minutos en total.

- **Auth**: JWT Bearer. Se obtiene en `setup()` con
  `POST /api/auth/login` (`{"email", "password"}`); requiere que exista el
  usuario y que `WEB_AUTH_SECRET` esté definido (el registro está
  deshabilitado por defecto: `WEB_ALLOW_REGISTRATION=false`).
- **Umbrales**: `p95 < 8000 ms` y `p50 < 4000 ms` sobre
  `http_req_duration{endpoint:chat}`, `<1 %` de peticiones fallidas y `>99 %`
  de checks en verde. El p95 de 8000 ms contempla el pipeline completo
  (RAG + herramientas + cola de GPU con un modelo local de 12B); para exigir
  el SLA estricto de 2500 ms usa `-e P95_MS=2500`.

## Instalación

```bash
# Debian/Ubuntu (repositorio oficial)
sudo gpg -k
sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg \
  --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D68
echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" | \
  sudo tee /etc/apt/sources.list.d/k6.list
sudo apt-get update && sudo apt-get install -y k6

# macOS
brew install k6

# Sin instalar nada (Docker)
docker pull grafana/k6
```

## Ejecución

```bash
# Con usuario/contraseña (lo habitual)
k6 run -e BASE_URL=http://localhost:8000 \
  -e RAFITA_EMAIL=yo@ejemplo.com -e RAFITA_PASSWORD=**** \
  deploy/k6/chat.js

# Con un token ya emitido (dura 7 días; el de la SPA sirve)
k6 run -e BASE_URL=http://localhost:8000 -e RAFITA_TOKEN=eyJ... deploy/k6/chat.js

# SLA estricto o relajado
k6 run -e P95_MS=2500  ... deploy/k6/chat.js   # objetivo duro
k6 run -e P95_MS=15000 ... deploy/k6/chat.js   # soak sin colgar la GPU

# Docker (monta el repo)
docker run --rm -v "$PWD:/workspace" -w /workspace \
  -e BASE_URL=http://host.docker.internal:8000 \
  -e RAFITA_EMAIL=... -e RAFITA_PASSWORD=... grafana/k6 run deploy/k6/chat.js
```

## Variables

| Variable            | Por defecto             | Descripción                                  |
| ------------------- | ----------------------- | -------------------------------------------- |
| `BASE_URL`          | `http://localhost:8000` | URL base del gateway (puerto 8000 en `main`)  |
| `RAFITA_EMAIL`      | —                       | Email del usuario web para el login           |
| `RAFITA_PASSWORD`   | —                       | Contraseña del usuario web                    |
| `RAFITA_TOKEN`      | —                       | Token Bearer ya emitido (evita el login)      |
| `P95_MS`            | `8000`                  | Umbral p95 de `http_req_duration{endpoint:chat}` |
| `P50_MS`            | `4000`                  | Umbral p50 del mismo                          |
| `K6_TIMEOUT`        | `120s`                  | Timeout HTTP por petición (el LLM es lento)   |

## Observar la carga mientras corre

```bash
# Exposition Prometheus (ver A del informe: gauge p50/p95 por herramienta)
curl -s http://localhost:8000/metrics.prom | grep -E 'rafita_(tool|llm|ai)_'

# El JSON de siempre, sin cambios de formato
curl -s http://localhost:8000/metrics | python3 -m json.tool | head -40
```
