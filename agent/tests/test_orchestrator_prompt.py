"""Prompt del sistema con contexto dinámico (auditoría Google 2026-09-26)."""

from datetime import datetime

import src.core.orchestrator as orch
from src.config import settings
from src.core.orchestrator import build_system_prompt


def test_prompt_includes_current_date(monkeypatch):
    monkeypatch.setattr(settings, "timezone", "Europe/Madrid")
    prompt = build_system_prompt()
    assert "CONTEXTO_ACTUAL" in prompt
    assert datetime.now().strftime("%Y") in prompt
    assert "Nunca inventes fechas" in prompt


def test_prompt_reports_google_connected(monkeypatch):
    class _Ready:
        is_ready = True
        calendar_id = "yo@gmail.com"

    monkeypatch.setattr(orch, "google_services", _Ready())
    prompt = build_system_prompt()
    assert "conectado (calendario: yo@gmail.com)" in prompt
    assert "NO ofrezcas enlaces de autorización" in prompt


def test_prompt_reports_google_missing(monkeypatch):
    class _Missing:
        is_ready = False
        calendar_id = "primary"

    monkeypatch.setattr(orch, "google_services", _Missing())
    prompt = build_system_prompt()
    assert "no configurado" in prompt
