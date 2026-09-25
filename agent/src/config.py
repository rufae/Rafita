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
    llm_temperature: float = Field(0.7, alias="LLM_TEMPERATURE", ge=0.0, le=2.0)
    llm_max_tokens: int = Field(4096, alias="LLM_MAX_TOKENS", ge=128, le=16384)

    data_dir: str = Field("/data", alias="DATA_DIR")
    db_path: str = Field("/data/db/rafita.db", alias="DB_PATH")
    excel_dir: str = Field("/data/excels", alias="EXCEL_DIR")
    export_dir: str = Field("/data/exports", alias="EXPORT_DIR")
    log_dir: str = Field("/data/logs", alias="LOG_DIR")
    obsidian_vault_dir: str = Field("/data/obsidian_vault", alias="OBSIDIAN_VAULT_DIR")

    language: str = Field("es", alias="LANGUAGE")
    timezone: str = Field("America/Mexico_City", alias="TIMEZONE")
    max_history_per_chat: int = Field(50, alias="MAX_HISTORY_PER_CHAT", ge=1, le=500)
    cleanup_interval: int = Field(3600, alias="CLEANUP_INTERVAL", ge=60)
    chat_inactivity_timeout: int = Field(86400, alias="CHAT_INACTIVITY_TIMEOUT", ge=3600)

    default_currency: str = Field("MXN", alias="DEFAULT_CURRENCY")

    whisper_model: str = Field("tiny", alias="WHISPER_MODEL")
    proactive_check_time: str = Field("09:00", alias="PROACTIVE_CHECK_TIME")
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


settings = Settings()  # type: ignore[call-arg]
