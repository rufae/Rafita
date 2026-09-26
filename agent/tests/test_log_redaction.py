"""Log redaction, structured format and rotation tests (task 3.5)."""

import json
import logging

from src.config import settings
from src.logger import (
    JsonFormatter,
    RedactingFilter,
    redact_text,
    setup_logging,
    tail_logs,
)

TELEGRAM_TOKEN = "123456789:AAFakeTokenForTests_0123456789abcd"
OPENAI_KEY = "sk-abcdefghijklmnopqrstuvwxyz012345"


def test_redacts_telegram_bot_token():
    result = redact_text("token del bot: %s" % TELEGRAM_TOKEN)
    assert TELEGRAM_TOKEN not in result
    assert "***" in result


def test_redacts_openai_style_key():
    result = redact_text("usando %s para el proveedor" % OPENAI_KEY)
    assert OPENAI_KEY not in result


def test_redacts_key_value_secrets():
    text = "TELEGRAM_TOKEN=abc123 password: hunter2 api_key=xyz ENCRYPTION_KEY=Zm9vYmFy"
    result = redact_text(text)
    assert "abc123" not in result
    assert "hunter2" not in result
    assert "xyz" not in result
    assert "Zm9vYmFy" not in result
    assert result.count("***") >= 4


def test_redacts_bearer_header():
    result = redact_text("Authorization: Bearer abcdef1234567890xyz")
    assert "abcdef1234567890xyz" not in result


def test_redaction_is_idempotent():
    once = redact_text("password=secret")
    assert redact_text(once) == once


def test_redacting_filter_masks_log_output():
    import io

    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(message)s"))
    test_logger = logging.getLogger("rafita.test_redaction")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.DEBUG)
    test_logger.propagate = False
    try:
        test_logger.info("guardando token %s", TELEGRAM_TOKEN)
        output = buffer.getvalue()
        assert TELEGRAM_TOKEN not in output
        assert "***" in output
    finally:
        test_logger.removeHandler(handler)
        handler.close()


def test_json_formatter_is_structured():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="rafita",
        level=logging.INFO,
        pathname=__file__,
        lineno=7,
        msg="hola %s",
        args=("mundo",),
        exc_info=None,
    )
    payload = json.loads(formatter.format(record))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "rafita"
    assert payload["msg"] == "hola mundo"
    assert payload["line"] == 7
    assert "ts" in payload


def test_setup_logging_rotation_is_bounded(tmp_path):
    root = logging.getLogger()
    try:
        setup_logging(log_dir=tmp_path, log_format="json", max_bytes=512, backup_count=2)
        test_logger = logging.getLogger("rafita.rotation")
        for i in range(200):
            test_logger.info("linea larga de prueba numero %d relleno relleno relleno" % i)
        files = sorted(p.name for p in tmp_path.glob("rafita.log*"))
        assert "rafita.log" in files
        assert any(name.endswith(".1") for name in files), files
        total = sum(p.stat().st_size for p in tmp_path.glob("rafita.log*"))
        assert total < 512 * 3 + 4096, total
    finally:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            handler.close()
        setup_logging()


def test_tail_logs_redacts_on_read(tmp_path, monkeypatch):
    log_file = tmp_path / "rafita.log"
    log_file.write_text(
        "linea normal\nTELEGRAM_TOKEN=%s\n" % TELEGRAM_TOKEN,
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "log_dir", str(tmp_path))
    lines = tail_logs(10)
    assert len(lines) == 2
    assert TELEGRAM_TOKEN not in "\n".join(lines)
    assert "***" in lines[-1]


def test_tail_logs_rejects_unknown_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "log_dir", str(tmp_path))
    (tmp_path / "secret.txt").write_text("no debe leerse", encoding="utf-8")
    assert tail_logs(5, filename="../secret.txt") == []
