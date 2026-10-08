FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ build-essential libffi-dev libssl-dev curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --user -r requirements.txt

# Kokoro (voz mas natural, ONNX+CPU): se instala sin dependencias porque
# kokoro-onnx declara numpy>=2 y el proyecto usa numpy 1.26 (verificado que
# funciona); onnxruntime ya viene con piper-tts.
RUN pip install --no-cache-dir --user --no-deps kokoro-onnx==0.6.1 && \
    pip install --no-cache-dir --user phonemizer==3.4.0 espeakng-loader==0.2.4

# Obscura (fase 3, 2026-10-07): motor headless en Rust para leer paginas con
# JS (solo lectura). Version fija + sha256 por arquitectura (cadena de
# suministro). En arquitecturas sin binario oficial se omite la instalacion
# y la tool cae al modo estatico httpx (honesto, no humo).
ARG OBSCURA_VERSION=0.2.4
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) oc_arch="x86_64"; \
             oc_sha="757e7b597ba5cdd53af9fc701e0a9b80f0a8d788f545d4f82ec9ad32db77f000" ;; \
      arm64) oc_arch="aarch64"; \
             oc_sha="d9ba3719c2442a227567d7664b5505b38ab68236f3f5388884eee2e27f55db28" ;; \
      *) echo "obscura: sin binario oficial para $arch; modo estatico"; \
         mkdir -p /opt/obscura; exit 0 ;; \
    esac; \
    curl -fsSL --retry 3 -o /tmp/obscura.tar.gz \
      "https://github.com/h4ckf0r0day/obscura/releases/download/v${OBSCURA_VERSION}/obscura-${oc_arch}-linux.tar.gz"; \
    echo "${oc_sha}  /tmp/obscura.tar.gz" | sha256sum -c -; \
    mkdir -p /opt/obscura; \
    tar -xzf /tmp/obscura.tar.gz -C /opt/obscura obscura obscura-worker; \
    chmod 755 /opt/obscura/obscura /opt/obscura/obscura-worker; \
    /opt/obscura/obscura --version; \
    rm /tmp/obscura.tar.gz


FROM python:3.11-slim AS runtime

LABEL maintainer="RafAI-Rafita"
LABEL rafita.version="0.2.0"
LABEL description="Rafita Agent Core - Telegram AI Assistant"
LABEL org.opencontainers.image.source="https://github.com/user/rafai"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/home/rafita/.local/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libsndfile1 espeak-ng sqlite3 git openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && adduser --disabled-password --gecos "" rafita \
    && mkdir -p /app/src /data/db /data/excels /data/logs /data/exports \
    && chown -R rafita:rafita /app /data

COPY --from=builder /root/.local /home/rafita/.local
COPY --from=builder /opt/obscura/ /usr/local/bin/

WORKDIR /app
COPY src/ ./src/

USER rafita

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=300s \
    CMD python -c "import httpx; r=httpx.get('http://localhost:8000/ready', timeout=5); exit(0) if r.status_code==200 else exit(1)" || exit 1

CMD ["python", "-m", "src.main"]
