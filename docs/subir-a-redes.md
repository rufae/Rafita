# Subida a redes (YouTube Shorts + Instagram Reels)

Arquitectura: **subida binaria en local → aprobación en Telegram vía n8n →
publicación**. Sin servicios de terceros (descartado Upload-Post).

```
hacer_videos.py ──► sidecar .json (copy) ──► subir_a_redes.py (timer 10 min)
                                                  │
                     YouTube: resumable, queda PRIVADO ◄─┤
                     Instagram: contenedor Reels + binario ◄─┤
                                                  ▼
                                   POST /webhook/mpt-video (n8n, HP)
                                                  ▼
                              Telegram: vídeo + ✅ Publicar / ❌ Descartar
                                       (espera máx. 23 h)
                                                  ▼
                          ✅ → YouTube videos.update → público
                              Instagram media_publish → Reel
                              mensaje con enlaces
                          ❌ → mensaje "nada publicado"
```

## Por qué 23 h y no 48 h

Los contenedores de Instagram caducan a las 24 h (`status_code: EXPIRED`).
La espera de aprobación se puso en 23 h para que el `media_publish` siempre
caiga dentro de la ventana. YouTube no tiene prisa (el vídeo está subido en
privado).

## Ficheros (proyecto MoneyPrinterTurbo)

| Fichero | Rol |
|---|---|
| `copy_redes.py` | genera/copía copy (título YT, descripción, caption IG, hashtags) |
| `hacer_videos.py` | produce el mp4 y escribe el sidecar `.json` |
| `subir_a_redes.py` | sube a YT (privado) + contenedor IG, luego envía a n8n |
| `pedir_token_yt.py` | pide el `yt_refresh_token` a Google (una vez) |
| `n8n/mpt-subir-a-redes.json` | workflow importado en n8n (`MPTsubiraredes0001`) |
| `storage/subidas.json` | estado por vídeo (`pendiente/parcial/enviado/error` + ids) |
| `~/.config/systemd/user/mpt-subir.timer` | cada 10 min, `--enviar` |

## Credenciales

- **`config.toml` `[subidas]`** (local, no va a git): `yt_client_id`,
  `yt_client_secret`, `yt_refresh_token`, `ig_access_token`, `ig_user_id`.
  Vacías = el envío a n8n se hace igual pero con ids vacíos (el workflow
  responde "aviso parcial" y no publica nada).
- **`.env` del HP (n8n)**: `WEBHOOK_URL` (base para los botones),
  `YT_CLIENT_ID/SECRET/REFRESH_TOKEN` (publicar), `IG_ACCESS_TOKEN`,
  `IG_USER_ID` (publicar). Sincronizo yo desde `config.toml`.

## Workflow n8n (16 nodos)

`Webhook → Clave (X-MPT-Secret) → Aprobacion (sendVideo + botones) →
Espera 23 h → Aprueba?` — la rama ❌/caducada acaba en `Descartado`; la ✅
comprueba `Subida lista?` (yt_id), refresca el token de Google,
`YouTube publico` → `IG listo?` → `Instagram publica` (onError) →
`IG ok? → Enlace IG → Hecho`, con salidas de aviso (`Aviso parcial`,
`Aviso IG`) si falta algo. El suffix de los botones es
`/webhook-waiting/<exec>/aprobacion?signature=…&accion=si|no`.

## Operación

```bash
uv run python subir_a_redes.py --check   # estado + ids yt✓/ig✓
uv run python subir_a_redes.py --enviar  # (re)intenta los pendientes
uv run python subir_a_redes.py --reset   # borra el estado
```

Reanudación: si YouTube sube bien y Instagram falla, queda `error` con
`yt_id` guardado; el siguiente turno sólo reintenta IG. Tras aprobar en
Telegram, el estado pasa a `enviado` y no se reenvía.

## Límites y trampas

- YouTube: bucket propio de 100 subidas/día (1 unidad por vídeo); el
  cambio a público son 50 unidades por `videos.update`.
- Instagram: 50 posts/24 h y 400 contenedores/24 h; app con **Facebook
  Login for Business** (para la subida resumable); Reel vertical ≤3 min.
- El binario NO sobrevive entre nodos HTTP de n8n: la subida binaria
  ocurre en local, antes del webhook.
- `WEBHOOK_URL` debe ser una URL que Telegram acepte (no `localhost`):
  los botones apuntan al n8n de la LAN/Tailscale.
