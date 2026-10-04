import json
import os
import re
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from src.vault_config import get_taxonomy

ENV_FILE_PATH = Path(os.environ.get("ENV_FILE", "/workspace/.env"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    telegram_token: str = Field(..., alias="TELEGRAM_TOKEN")
    admin_ids: Annotated[list[int], NoDecode] = Field(default_factory=list, alias="ADMIN_IDS")
    assistant_name: str = Field("Rafita", alias="ASSISTANT_NAME")

    ollama_host: str = Field("http://ollama:11434", alias="OLLAMA_HOST")
    ollama_model: str = Field("qwen2.5:7b", alias="OLLAMA_MODEL")
    ollama_vision_model: str = Field("gemma4:12b", alias="OLLAMA_VISION_MODEL")
    ollama_reasoning_effort: str = Field("none", alias="OLLAMA_REASONING_EFFORT")
    ollama_num_thread: int = Field(0, alias="OLLAMA_NUM_THREAD", ge=0)
    ollama_request_timeout: int = Field(600, alias="OLLAMA_REQUEST_TIMEOUT", ge=30)
    ollama_gpu_host: str = Field("", alias="OLLAMA_GPU_HOST")
    ollama_gpu_probe_interval: int = Field(60, alias="OLLAMA_GPU_PROBE_INTERVAL", ge=10)
    llm_temperature: float = Field(0.7, alias="LLM_TEMPERATURE", ge=0.0, le=2.0)
    llm_max_tokens: int = Field(4096, alias="LLM_MAX_TOKENS", ge=128, le=16384)

    data_dir: str = Field("/data", alias="DATA_DIR")
    db_path: str = Field("/data/db/rafita.db", alias="DB_PATH")
    excel_dir: str = Field("/data/excels", alias="EXCEL_DIR")
    export_dir: str = Field("/data/exports", alias="EXPORT_DIR")
    log_dir: str = Field("/data/logs", alias="LOG_DIR")
    obsidian_vault_dir: str = Field("/data/obsidian_vault", alias="OBSIDIAN_VAULT_DIR")

    log_format: str = Field("text", alias="LOG_FORMAT")

    @field_validator("google_auth_mode", mode="before")
    @classmethod
    def normalize_google_auth_mode(cls, v: Any) -> str:
        value = "" if v is None else str(v).strip().lower()
        return value if value in ("auto", "service_account", "oauth") else "auto"

    @field_validator("log_format", mode="before")
    @classmethod
    def normalize_log_format(cls, v: Any) -> str:
        value = "" if v is None else str(v).strip().lower()
        return value if value in ("text", "json") else "text"

    language: str = Field("es", alias="LANGUAGE")
    timezone: str = Field("America/Mexico_City", alias="TIMEZONE")
    max_history_per_chat: int = Field(50, alias="MAX_HISTORY_PER_CHAT", ge=1, le=500)
    cleanup_interval: int = Field(3600, alias="CLEANUP_INTERVAL", ge=60)
    chat_inactivity_timeout: int = Field(86400, alias="CHAT_INACTIVITY_TIMEOUT", ge=3600)

    default_currency: str = Field("MXN", alias="DEFAULT_CURRENCY")

    # keep_alive de Ollama por backend (2026-10-01): en la torre GPU el modelo
    # se fija (-1) para respuestas rapidas; en el Dell (CPU 24/7) se deja
    # expirar (5m) para no retener RAM (el Dell llego al 90% por modelos
    # inactivos fijados con keep_alive=-1).
    ollama_keep_alive_gpu: str = Field("-1", alias="OLLAMA_KEEP_ALIVE_GPU")
    ollama_keep_alive_cpu: str = Field("5m", alias="OLLAMA_KEEP_ALIVE_CPU")

    whisper_model: str = Field("tiny", alias="WHISPER_MODEL")
    whisper_cpu_threads: int = Field(4, alias="WHISPER_CPU_THREADS", ge=1, le=32)
    # STT remoto (2026-09-30): servicio faster-whisper large-v3 en la GPU de
    # la torre (deploy/tower/whisper_service.sh). El HP no puede con modelos
    # grandes (6 GB, sin GPU) y `small` en CPU alucina con ruido. Si esta
    # vacio o no responde, se usa el Whisper local como fallback.
    whisper_remote_url: str = Field("", alias="WHISPER_REMOTE_URL")
    whisper_remote_token: str = Field("", alias="WHISPER_REMOTE_TOKEN")
    whisper_remote_timeout: float = Field(600.0, alias="WHISPER_REMOTE_TIMEOUT", ge=5.0, le=3600.0)
    # Voz de Piper (calidad media suena mucho mas humana que x_low).
    # Formato: es_ES-davefx-medium, es_ES-sharvard-medium, es_ES-carlfm-x_low...
    tts_voice: str = Field("es_ES-davefx-medium", alias="TTS_VOICE")
    tts_speed: float = Field(0.0, alias="TTS_SPEED")
    # Motor TTS (2026-09-28): piper | kokoro | voicebox.
    # Kokoro (mas natural, mas lento en CPU) para notas/briefing; la llamada
    # usa TTS_ENGINE_CALL (por defecto piper) para no perder fluidez.
    tts_engine: str = Field("piper", alias="TTS_ENGINE")
    tts_engine_call: str = Field("piper", alias="TTS_ENGINE_CALL")
    tts_engine_chat: str = Field("piper", alias="TTS_ENGINE_CHAT")
    kokoro_threads: int = Field(0, alias="KOKORO_THREADS", ge=0, le=32)
    # Voicebox (opcional): REST local del estudio de voz (github.com/jamiepine/voicebox).
    voicebox_url: str = Field("", alias="VOICEBOX_URL")
    voicebox_profile_id: str = Field("", alias="VOICEBOX_PROFILE_ID")
    voicebox_language: str = Field("es", alias="VOICEBOX_LANGUAGE")
    proactive_check_time: str = Field("09:00", alias="PROACTIVE_CHECK_TIME")

    # Voz (2026-09-28): token de la pagina de llamadas y STT especulativo.
    voice_call_token: str = Field("", alias="VOICE_CALL_TOKEN")
    voice_speculative_stt: bool = Field(True, alias="VOICE_SPECULATIVE_STT")
    # Frase de espera en llamada solo si la respuesta tarda mas de esto
    # (2026-09-30: con 2,2 s salia hasta en un "hola"; el usuario pidio 5-6 s).
    voice_filler_delay_s: float = Field(5.5, alias="VOICE_FILLER_DELAY_S", ge=1.0, le=60.0)

    # Seguridad web (2026-09-28): origenes CORS permitidos (separados por
    # coma). Vacio = solo mismo origen/localhost.
    web_allowed_origins: str = Field("", alias="WEB_ALLOWED_ORIGINS")

    # Web SPA (Fase 3, 2026-09-29): auth por email/clave + Sign in with Google.
    web_auth_secret: str = Field("", alias="WEB_AUTH_SECRET")
    web_admin_email: str = Field("", alias="WEB_ADMIN_EMAIL")
    web_admin_password: str = Field("", alias="WEB_ADMIN_PASSWORD")
    web_allow_registration: bool = Field(False, alias="WEB_ALLOW_REGISTRATION")
    google_web_client_id: str = Field("", alias="GOOGLE_WEB_CLIENT_ID")
    google_web_client_secret: str = Field("", alias="GOOGLE_WEB_CLIENT_SECRET")
    google_web_redirect_uri: str = Field("", alias="GOOGLE_WEB_REDIRECT_URI")
    # Web push para la PWA (mejora 6, 2026-10-04): fichero PEM con la clave
    # privada VAPID (generar con scripts/generate_vapid_keys.py) y el "sub"
    # de las claims (mailto de contacto). Sin clave -> push desactivado.
    vapid_key_file: str = Field("/data/vapid_private.pem", alias="VAPID_KEY_FILE")
    vapid_subject: str = Field("mailto:admin@example.com", alias="VAPID_SUBJECT")

    # Briefing matutino y recordatorios proactivos (mejoras 2 y 6, 2026-09-28).
    briefing_enabled: bool = Field(True, alias="BRIEFING_ENABLED")
    briefing_time: str = Field("08:00", alias="BRIEFING_TIME")
    briefing_lat: float = Field(0.0, alias="BRIEFING_LAT")
    briefing_lon: float = Field(0.0, alias="BRIEFING_LON")
    # Tiempo con AEMET (Espana, gratis): clave y municipio (codigo INE).
    aemet_api_key: str = Field("", alias="AEMET_API_KEY")
    briefing_municipio: str = Field("", alias="BRIEFING_MUNICIPIO")
    # Zona meteoalerta para avisos CAP (61 = Andalucia; 62 = Aragon...; ver
    # anexo 2 del Plan Meteoalerta de AEMET).
    aemet_area: str = Field("61", alias="AEMET_AREA")

    # n8n (mejora 1): mapa JSON {"nombre": "https://n8n.../webhook/xxx"}.
    n8n_webhooks: str = Field("", alias="N8N_WEBHOOKS")
    # Base para los webhooks manuales del catalogo (tarea 13, orquestador).
    n8n_base_url: str = Field("http://n8n:5678", alias="N8N_BASE_URL")

    # Radar de IA (punto 4): feeds RSS separados por coma (vacio = lista por
    # defecto del codigo).
    radar_feeds: str = Field("", alias="RADAR_FEEDS")

    backup_retention_days: int = Field(30, alias="BACKUP_RETENTION_DAYS")
    embedding_model: str = Field("nomic-embed-text", alias="EMBEDDING_MODEL")
    embedding_dim: int = Field(768, alias="EMBEDDING_DIM")
    obsidian_vault_name: str = Field("mi_boveda_obsidian", alias="OBSIDIAN_VAULT_NAME")
    vector_db_dir: str = Field("/data/vector_db", alias="VECTOR_DB_DIR")
    chunk_size: int = Field(512, alias="CHUNK_SIZE")
    chunk_overlap: int = Field(64, alias="CHUNK_OVERLAP")
    indexer_interval: int = Field(3600, alias="INDEXER_INTERVAL")
    encryption_key: str = Field("", alias="ENCRYPTION_KEY")
    webhook_secret: str = Field("", alias="WEBHOOK_SECRET")
    relevance_threshold: float = Field(0.49, alias="RELEVANCE_THRESHOLD", ge=0.0, le=1.0)
    persist_to_brain: bool = Field(True, alias="PERSIST_TO_BRAIN")
    brain_maintenance: bool = Field(False, alias="BRAIN_MAINTENANCE")
    brain_maintenance_interval: int = Field(1800, alias="BRAIN_MAINTENANCE_INTERVAL", ge=60)

    infra_check_minutes: int = Field(60, alias="INFRA_CHECK_MINUTES", ge=5)
    infra_alert_cooldown_hours: float = Field(6.0, alias="INFRA_ALERT_COOLDOWN_HOURS", gt=0)
    cert_check_dir: str = Field("/data/certs", alias="CERT_CHECK_DIR")
    connectivity_urls: str = Field(
        "https://api.telegram.org,https://github.com", alias="CONNECTIVITY_URLS"
    )

    ai_provider: str = Field("ollama", alias="AI_PROVIDER")
    openai_api_key: str = Field("", alias="OPENAI_API_KEY")
    openai_base_url: str = Field("https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openai_model: str = Field("gpt-4o-mini", alias="OPENAI_MODEL")
    openai_vision_model: str = Field("", alias="OPENAI_VISION_MODEL")
    openai_embedding_model: str = Field("", alias="OPENAI_EMBEDDING_MODEL")
    openai_reasoning_effort: str = Field("", alias="OPENAI_REASONING_EFFORT")
    google_calendar_id: str = Field("primary", alias="GOOGLE_CALENDAR_ID")
    google_drive_folder_id: str = Field("", alias="GOOGLE_DRIVE_FOLDER_ID")
    google_auth_mode: str = Field("auto", alias="GOOGLE_AUTH_MODE")

    @field_validator("ollama_reasoning_effort", "openai_reasoning_effort", mode="before")
    @classmethod
    def normalize_reasoning_effort(cls, v: Any) -> str:
        """Empty means "do not send the parameter at all"."""
        return "" if v is None else str(v).strip().lower()

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, v: Any) -> list[int]:
        """Accept CSV ("1,2"), JSON ("[1,2]"), single id, or an empty value.

        `NoDecode` stops pydantic-settings from json.loads()-ing the raw value
        before this validator runs, so the CSV documented in `.env.example`
        works as-is.
        """
        if v is None:
            return []
        if isinstance(v, int):
            return [v]
        if isinstance(v, (list, tuple, set)):
            return [int(x) for x in v]
        if not isinstance(v, str):
            return []
        raw = v.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = []
            return [int(x) for x in parsed if str(x).strip().lstrip("-").isdigit()]
        result = []
        for part in re.split(r"[,;\s]+", raw):
            part = part.strip()
            if not part:
                continue
            try:
                result.append(int(part))
            except ValueError:
                continue
        return result

    @field_validator(
        "data_dir", "db_path", "excel_dir", "export_dir", "log_dir", "vector_db_dir", mode="before"
    )
    @classmethod
    def validate_paths(cls, v: str) -> str:
        return v.strip().rstrip("/\\")

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def db_path_obj(self) -> Path:
        return Path(self.db_path)

    @property
    def excel_path(self) -> Path:
        return Path(self.excel_dir)

    @property
    def export_path(self) -> Path:
        return Path(self.export_dir)

    @property
    def log_path(self) -> Path:
        return Path(self.log_dir)

    @property
    def obsidian_vault_path(self) -> Path:
        return Path(self.obsidian_vault_dir)

    @property
    def obsidian_finanzas_path(self) -> Path:
        return self.obsidian_vault_path / get_taxonomy().path("areas_finanzas")

    @property
    def vector_db_path(self) -> Path:
        return Path(self.vector_db_dir)

    @property
    def indexed_docs_path(self) -> Path:
        return self.obsidian_vault_path / get_taxonomy().path("indexed_docs")

    @property
    def encryption_key_bytes(self) -> bytes:
        if not self.encryption_key:
            return b""
        import base64

        return base64.urlsafe_b64decode(self.encryption_key)


# Secretos file-based (mejora 7): si se define `<VAR>_FILE`, el valor se lee
# del fichero indicado (estilo Docker secrets, p. ej. /run/secrets/telegram_token)
# siempre que la variable no venga ya dada por el entorno o por el .env.
_SECRET_ENVS = (
    "TELEGRAM_TOKEN",
    "WEBHOOK_SECRET",
    "WEB_AUTH_SECRET",
    "VOICE_CALL_TOKEN",
    "WHISPER_REMOTE_TOKEN",
    "WEB_ADMIN_PASSWORD",
    "GOOGLE_WEB_CLIENT_SECRET",
    "AEMET_API_KEY",
    "ENCRYPTION_KEY",
    "OPENAI_API_KEY",
    "PASSWORD_APPLICATION",
    "APPLICATION_PASSWORD",
)


def _value_in_env_file(name: str) -> bool:
    try:
        for line in ENV_FILE_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                return True
    except Exception:
        pass
    return False


def _apply_secret_files(entorno: dict[str, str] | None = None) -> None:
    """Rellena variables de secreto desde `<VAR>_FILE` si no tienen valor.

    Prioridad: variable de entorno > .env > fichero secreto. Un fichero ilevible
    es un error duro (fail-closed): mejor no arrancar que quedarse sin secreto.
    """
    target = os.environ if entorno is None else entorno
    for name in _SECRET_ENVS:
        if target.get(name) or _value_in_env_file(name):
            continue
        path = target.get("%s_FILE" % name)
        if not path:
            continue
        try:
            valor = Path(path).read_text(encoding="utf-8").strip()
        except OSError as e:
            raise RuntimeError("No se pudo leer el secreto %s_FILE (%s): %s" % (name, path, e))
        if valor:
            target[name] = valor


_apply_secret_files()

settings = Settings()  # type: ignore[call-arg]
