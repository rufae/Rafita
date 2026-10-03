# Secretos file-based (Docker secrets / sops)

Desde la mejora 7, cualquier secreto de la instalación se puede servir desde
un fichero en lugar de dejarlo en el `.env`. Es la forma recomendada con
Docker secrets (`/run/secrets/<nombre>`) o con un gestor como sops/age.

## Uso

Define la variable `<SECRETO>_FILE` con la ruta del fichero que contiene el
valor. Si la variable `<SECRETO>` ya tiene valor (entorno o `.env`), esa
tiene prioridad y el fichero se ignora.

```bash
# Ejemplo: el token de Telegram vive en un secreto de Docker
TELEGRAM_TOKEN_FILE=/run/secrets/telegram_token
```

Secretos soportados: `TELEGRAM_TOKEN`, `WEBHOOK_SECRET`, `WEB_AUTH_SECRET`,
`VOICE_CALL_TOKEN`, `WHISPER_REMOTE_TOKEN`, `WEB_ADMIN_PASSWORD`,
`GOOGLE_WEB_CLIENT_SECRET`, `AEMET_API_KEY`, `ENCRYPTION_KEY`,
`OPENAI_API_KEY`, `PASSWORD_APPLICATION` y `APPLICATION_PASSWORD`.

## Docker Compose

```yaml
services:
  rafita-agent-core:
    environment:
      TELEGRAM_TOKEN_FILE: /run/secrets/telegram_token
    secrets:
      - telegram_token

secrets:
  telegram_token:
    file: ./secrets/telegram_token.txt
```

## Comportamiento

- **Prioridad**: variable de entorno > `.env` > fichero secreto.
- **Fail-closed**: si `<SECRETO>_FILE` apunta a un fichero ilegible, la
  aplicación no arranca (mejor no arrancar que quedarse sin secreto).
- El valor del fichero se recorta (espacios y saltos de línea finales).
- Los secretos **nunca** se versionan en git; `secrets/` debe estar fuera del
  repositorio (o en `.git/info/exclude`).

## Rotación

1. Cambia el contenido del fichero secreto (o del `.env`).
2. Reinicia el contenedor: `docker compose ... restart rafita-agent-core`.
3. Verifica `/health` y `/ready` en el gateway.
