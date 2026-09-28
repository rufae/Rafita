"""Cobertura complementaria de src/logger.py: formatos, permisos y tail_logs."""

import json
import logging

import src.logger as logger_mod
from src.config import settings
from src.logger import (
    JsonFormatter,
    RedactingFilter,
    _build_formatter,
    redact_text,
    setup_logging,
    tail_logs,
)


def test_json_formatter_includes_exception_traceback():
    formatter = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        exc_info = sys.exc_info()
    record = logging.LogRecord(
        name="t",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="fallo",
        args=(),
        exc_info=exc_info,
    )
    payload = json.loads(formatter.format(record))
    assert payload["msg"] == "fallo"
    assert payload["level"] == "ERROR"
    assert "ValueError: boom" in payload["exc"]


def test_build_formatter_json_and_text():
    assert _build_formatter("json").__class__.__name__ == "JsonFormatter"
    assert _build_formatter("text").__class__.__name__ == "Formatter"
    assert _build_formatter(None).__class__.__name__ == "Formatter"
    assert _build_formatter("  JSON  ").__class__.__name__ == "JsonFormatter"


def test_redacting_filter_rewrites_dict_args():
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="clave %s",
        args={"token": "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "n": 5},
        exc_info=None,
    )
    filt = RedactingFilter()
    assert filt.filter(record) is True
    assert record.args["token"] == "***"
    assert record.args["n"] == 5


def test_redacting_filter_keeps_non_string_args():
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="total %d",
        args=(42,),
        exc_info=None,
    )
    RedactingFilter().filter(record)
    assert record.args == (42,)


def test_setup_logging_with_json_format_and_resilient_to_permission_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "log_dir", str(tmp_path))

    def boom_mkdir(self, *args, **kwargs):
        raise PermissionError("sin permisos")

    monkeypatch.setattr(type(tmp_path), "mkdir", boom_mkdir)

    def boom_handler(*args, **kwargs):
        raise PermissionError("sin permisos")

    monkeypatch.setattr(logger_mod, "RotatingFileHandler", boom_handler)
    result = setup_logging(log_dir=tmp_path, log_format="json")
    assert result.name == "rafita"
    root = logging.getLogger()
    assert root.handlers, "el handler de consola debe instalarse siempre"


def test_setup_logging_creates_nested_log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "log_dir", str(tmp_path))
    result = setup_logging(log_dir=tmp_path / "sub" / "dir", log_format="text")
    assert result.name == "rafita"
    assert (tmp_path / "sub" / "dir" / "rafita.log").exists()


def test_tail_logs_clamps_empty_window(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "log_dir", str(tmp_path))
    log_file = tmp_path / "rafita.log"
    log_file.write_text("uno\ndos\ntres\n", encoding="utf-8")
    assert tail_logs(lines=0) == ["tres"]
    assert tail_logs(lines=-3) == ["tres"]


def test_tail_logs_unreadable_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "log_dir", str(tmp_path))
    log_file = tmp_path / "rafita.log"
    log_file.write_text("contenido", encoding="utf-8")

    def boom_open(self, *args, **kwargs):
        raise OSError("disco ilegible")

    monkeypatch.setattr(type(log_file), "open", boom_open)
    assert tail_logs(lines=5) == []


def test_tail_logs_reads_error_log_and_redacts(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "log_dir", str(tmp_path))
    (tmp_path / "error.log").write_text("fallo con api_key=secreto12345\n", encoding="utf-8")
    lines = tail_logs(lines=5, filename="error.log")
    assert lines == ["fallo con api_key=***"]
    assert redact_text("api_key=secreto12345") == "api_key=***"
