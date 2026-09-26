import json
import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.config import settings

_TEXT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Task 3.5: secrets must never reach disk in clear text.
_SECRET_PATTERNS = (
    # Telegram bot tokens: 123456789:AA...
    (re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"), "***"),
    # OpenAI-style keys
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "***"),
    # Authorization: Bearer <token>
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}=*"), "Bearer ***"),
    # key=value / key: value for sensitive names (also prefixed, e.g.
    # TELEGRAM_TOKEN=..., OPENAI_API_KEY=...)
    (
        re.compile(
            r"(?i)([A-Za-z0-9_]*(?:token|secret|password|passwd|api[_-]?key"
            r"|authorization|encryption[_-]?key))(\s*[:=]\s*)([^\s,;\"']+)"
        ),
        r"\1\2***",
    ),
)


def redact_text(text: str) -> str:
    """Mask credentials/tokens in a string (idempotent)."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Applied to every handler so secrets never reach disk or stdout.

    Only string arguments are rewritten: numbers/objects keep their type so
    `%d`/`%f` style formatting keeps working.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: redact_text(v) if isinstance(v, str) else v for k, v in record.args.items()
                }
            else:
                record.args = tuple(
                    redact_text(a) if isinstance(a, str) else a for a in record.args
                )
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line (structured logs, task 3.5)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "func": record.funcName,
            "line": record.lineno,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _build_formatter(log_format: str) -> logging.Formatter:
    if (log_format or "text").strip().lower() == "json":
        return JsonFormatter()
    return logging.Formatter(_TEXT_FORMAT, _DATE_FORMAT)


def setup_logging(
    log_dir: Path | None = None,
    log_format: str | None = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    error_max_bytes: int = 5 * 1024 * 1024,
    error_backup_count: int = 3,
) -> logging.Logger:
    if log_dir is None:
        log_dir = settings.log_path
    if log_format is None:
        log_format = settings.log_format

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError):
        pass

    formatter = _build_formatter(log_format)
    redactor = RedactingFilter()

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(redactor)
    root_logger.addHandler(console_handler)

    try:
        file_handler = RotatingFileHandler(
            filename=str(log_dir / "rafita.log"),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redactor)
        root_logger.addHandler(file_handler)
    except (PermissionError, OSError):
        pass

    try:
        error_handler = RotatingFileHandler(
            filename=str(log_dir / "error.log"),
            maxBytes=error_max_bytes,
            backupCount=error_backup_count,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        error_handler.addFilter(redactor)
        root_logger.addHandler(error_handler)
    except (PermissionError, OSError):
        pass

    httpx_logger = logging.getLogger("httpx")
    httpx_logger.setLevel(logging.WARNING)

    httpcore_logger = logging.getLogger("httpcore")
    httpcore_logger.setLevel(logging.WARNING)

    telegram_logger = logging.getLogger("telegram")
    telegram_logger.setLevel(logging.INFO)

    return logging.getLogger("rafita")


def tail_logs(lines: int = 50, filename: str = "rafita.log") -> list[str]:
    """Last `lines` lines of a log file, redacted (remote query, task 3.5).

    Only the whitelisted log files can be read; content is redacted again on
    read (defense in depth for lines written before the filter existed).
    """
    if filename not in ("rafita.log", "error.log"):
        filename = "rafita.log"
    path = settings.log_path / filename
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            content = fh.readlines()
    except OSError:
        return []
    return [redact_text(line.rstrip("\n")) for line in content[-max(1, lines) :]]


logger = setup_logging()
