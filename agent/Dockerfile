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


FROM python:3.11-slim AS runtime

LABEL maintainer="RafAI-Rafita"
LABEL description="Rafita Agent Core - Telegram AI Assistant"
LABEL org.opencontainers.image.source="https://github.com/user/rafai"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/home/rafita/.local/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libsndfile1 espeak-ng sqlite3 \
    && rm -rf /var/lib/apt/lists/* \
    && adduser --disabled-password --gecos "" rafita \
    && mkdir -p /app/src /data/db /data/excels /data/logs /data/exports \
    && chown -R rafita:rafita /app /data

COPY --from=builder /root/.local /home/rafita/.local

WORKDIR /app
COPY src/ ./src/

USER rafita

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=300s \
    CMD python -c "import httpx; r=httpx.get('http://localhost:8000/health', timeout=5); exit(0) if r.status_code==200 else exit(1)" || exit 1

CMD ["python", "-m", "src.main"]
