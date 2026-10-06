# Wake word («manos libres») en la torre

Daemon para hablar con Rafita sin tocar nada: el micro de la **torre** escucha
localmente la palabra de activación, graba tu frase y la manda a la llamada de
voz de Rafita (mismo protocolo que la web: `voice_stream` en el HP). La
respuesta se reproduce por los altavoces.

**Privacidad**: el audio no sale del equipo hasta que dices la palabra de
activación; la detección corre en local (openwakeword, ONNX).

## 1. Requisitos

- Torre con micro y altavoces (o headset).
- El agente Rafita desplegado en el HP (puerto de voz 8001 accesible).
- Python 3.11+ en la torre.

## 2. Instalación (en la torre)

```bash
pip install numpy sounddevice openwakeword websockets
```

Comprueba los índices de audio:

```bash
python -c "import sounddevice as sd; print(sd.query_devices())"
```

## 3. Token de llamada

En la web de Rafita (sesión iniciada) se genera con `GET /call/token`; para
obtenerlo por CLI puedes abrir la web y usar la consola del navegador, o bien
generarlo desde el HP:

```bash
# en el HP (ejemplo; el token vive en la BD de la web)
curl -s http://127.0.0.1:8000/api/health >/dev/null  # web arriba
```

El token lo devuelve la web al pulsar «Llamada» (`/call/token`); cópialo.

## 4. Arranque

```bash
RAFITA_BASE=http://<ip-del-hp>:8001 \
CALL_TOKEN=<token> \
WAKEWORD=hey_jarvis \
python agent/scripts/wake_word.py
```

Variables:

| Variable | Por defecto | Descripción |
|---|---|---|
| `WAKEWORD` | `hey_jarvis` | Palabra de activación (`hey_jarvis` o `alexa`) |
| `WAKE_THRESHOLD` | `0.5` | Sube/baja la sensibilidad |
| `INPUT_DEVICE` / `OUTPUT_DEVICE` | automático | Índices de `sounddevice` |
| `TALK_TIMEOUT_S` | `20` | Duración máxima de tu frase |

Uso: di **«Hey Jarvis»** (o la que configures), espera el pitido de
activación del log y habla; pausa ~1 s para cerrar el turno.

## 5. Arranque con el sistema (systemd --user)

`~/.config/systemd/user/rafita-wake.service`:

```ini
[Unit]
Description=Rafita wake word
After=network-online.target

[Service]
Environment=RAFITA_BASE=http://<ip-del-hp>:8001
Environment=CALL_TOKEN=<token>
ExecStart=/usr/bin/python3 %h/proyectos/rafita/agent/scripts/wake_word.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now rafita-wake.service
loginctl enable-linger $USER   # que corra sin sesión abierta
```

## 6. Problemas frecuentes

- **No escucha nada**: `python -c "import sounddevice as sd; print(sd.query_devices())"`
  y fija `INPUT_DEVICE`. En PipeWire/PulseAudio revisa que el micro no esté
  en silencio (`pavucontrol`).
- **Se activa solo**: sube `WAKE_THRESHOLD` (0.6-0.7).
- **No oigo respuesta**: fija `OUTPUT_DEVICE`; la respuesta llega como WAV.
- **Timeout al hablar**: el turno es PTT; cierra con ~1 s de silencio o baja
  `SILENCE_END_S` en el script.
