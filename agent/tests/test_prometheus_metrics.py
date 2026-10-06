"""Tests de la vista Prometheus (Fase 2): render_prometheus y GET /metrics.prom.

`GET /metrics` (JSON) debe seguir intacto: aqui se verifica la retrocompat.
Todo local: el health check de IA se mockea, sin red ni Ollama.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.utils import telemetry, webhook_server

# ---------- telemetry.render_prometheus ----------


def _snapshot_completo() -> dict[str, Any]:
    return {
        "counters": {
            "tool_calls": 7,
            "tool_calls.search_web": 3,
            "llm_requests": 2,
        },
        "gauges": {
            "infra_checks_ok": 1.0,
            "nombre con signos!": 2.0,
        },
        "histograms": {
            "tool_latency_ms.create_event": {
                "count": 4,
                "p50": 10.0,
                "p95": 40.5,
                "p99": 99.0,
                "avg": 20.0,
                "min": 5.0,
                "max": 99.0,
            },
            "basura_no_dict": "no-soy-un-dict",
        },
    }


def test_render_contadores_con_label_tool():
    texto = telemetry.render_prometheus(_snapshot_completo())
    assert "# TYPE rafita_tool_calls counter" in texto
    assert "rafita_tool_calls 7" in texto
    assert 'rafita_tool_calls{tool="search_web"} 3' in texto
    assert "rafita_llm_requests 2" in texto
    # El nombre invalido se sanea (prefijo + [a-zA-Z0-9_:]).
    assert "rafita_nombre_con_signos_ 2" in texto


def test_render_histograma_como_quantiles_sin_buckets():
    texto = telemetry.render_prometheus(_snapshot_completo())
    assert "# TYPE rafita_tool_latency_ms gauge" in texto
    assert 'rafita_tool_latency_ms{quantile="p50",tool="create_event"} 10' in texto
    assert 'rafita_tool_latency_ms{quantile="p95",tool="create_event"} 40.5' in texto
    assert 'rafita_tool_latency_ms{quantile="p99",tool="create_event"} 99' in texto
    # _sum = avg * count (80), _count = muestras; son contadores aparte.
    assert 'rafita_tool_latency_ms_sum{tool="create_event"} 80' in texto
    assert 'rafita_tool_latency_ms_count{tool="create_event"} 4' in texto
    assert "# TYPE rafita_tool_latency_ms_sum counter" in texto
    # stats que no son dict se ignoran sin romper.
    assert "basura" not in texto.split("\n")[0]


def test_render_vacio_y_valores_especiales():
    assert telemetry.render_prometheus({}) == ""
    texto = telemetry.render_prometheus(
        {
            "counters": {"a": 1.0},
            "gauges": {"nan_g": math.nan, "inf_g": math.inf, "ninf_g": -math.inf},
        }
    )
    assert "rafita_a 1" in texto
    assert "rafita_nan_g NaN" in texto
    assert "rafita_inf_g +Inf" in texto
    assert "rafita_ninf_g -Inf" in texto


def test_render_histograma_sin_muestras_no_emite_cuantiles():
    texto = telemetry.render_prometheus(
        {"histograms": {"tool_latency_ms.x": {"count": 0, "p50": None, "avg": 0.0}}}
    )
    assert 'quantile="' not in texto
    assert 'rafita_tool_latency_ms_count{tool="x"} 0' in texto


def test_prom_block_escalado_y_vacio():
    bloque = telemetry.prom_block(
        "metrica rara",
        "gauge",
        "ayuda con\nsalto y \\ barra",
        [({"k": 'va"l\nor'}, 1.5), ({}, 2.0)],
    )
    assert "# HELP rafita_metrica_rara ayuda con\\nsalto y \\\\ barra" in bloque
    assert 'rafita_metrica_rara{k="va\\"l\\nor"} 1.5' in bloque
    assert "rafita_metrica_rara 2" in bloque
    assert telemetry.prom_block("x", "gauge", "y", []) == ""


# ---------- GET /metrics.prom (endpoin) ----------


@pytest.fixture
def cliente(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def check_ia_ok() -> dict[str, Any]:
        return {
            "status": "ok",
            "provider": "ollama",
            "model": "gemma4:12b",
            "model_available": True,
            "latency_ms": 12.5,
        }

    monkeypatch.setattr(webhook_server, "_check_ai", check_ia_ok)
    monkeypatch.setattr(webhook_server, "_cache_ia_prom", None)
    return TestClient(webhook_server.app)


def test_metrics_prom_texto_y_familias_basicas(cliente: TestClient) -> None:
    resp = cliente.get("/metrics.prom")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    texto = resp.text
    assert "# TYPE rafita_process_uptime_seconds gauge" in texto
    assert "# TYPE rafita_process_start_time_seconds gauge" in texto
    assert "rafita_build_info{version=" in texto
    assert 'rafita_ai_up{provider="ollama"} 1' in texto
    assert 'rafita_ai_health_status{status="ok"} 1' in texto
    assert 'rafita_ai_health_status{status="unhealthy"} 0' in texto
    assert 'rafita_ai_model_available{model="gemma4:12b"} 1' in texto
    assert 'rafita_ai_health_latency_ms{provider="ollama"} 12.5' in texto


def test_metrics_prom_cachea_el_health_check(
    cliente: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    llamadas = {"n": 0}

    async def check_lento() -> dict[str, Any]:
        llamadas["n"] += 1
        return {"status": "ok", "provider": "ollama"}

    monkeypatch.setattr(webhook_server, "_check_ai", check_lento)
    monkeypatch.setattr(webhook_server, "_cache_ia_prom", None)
    assert cliente.get("/metrics.prom").status_code == 200
    assert cliente.get("/metrics.prom").status_code == 200
    assert llamadas["n"] == 1  # TTL de 30 s: dos scrapes, un solo chequeo


def test_metrics_prom_estado_degradado_sigue_up(
    cliente: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check_degraded() -> dict[str, Any]:
        return {"status": "degraded", "provider": "ollama"}

    monkeypatch.setattr(webhook_server, "_check_ai", check_degraded)
    monkeypatch.setattr(webhook_server, "_cache_ia_prom", None)
    texto = cliente.get("/metrics.prom").text
    assert 'rafita_ai_up{provider="ollama"} 1' in texto  # degradado sigue respondiendo
    assert 'rafita_ai_health_status{status="degraded"} 1' in texto
    assert "rafita_ai_model_available" not in texto  # sin modelo en el payload: no se emite


def test_metrics_prom_health_que_revienta_no_tumba_el_endpoint(
    cliente: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check_roto() -> dict[str, Any]:
        raise RuntimeError("ollama caido")

    monkeypatch.setattr(webhook_server, "_check_ai", check_roto)
    monkeypatch.setattr(webhook_server, "_cache_ia_prom", None)
    resp = cliente.get("/metrics.prom")
    assert resp.status_code == 200
    # Estado desconocido por excepción: degradado a unhealthy y ai_up 0.
    assert 'rafita_ai_up{provider="unknown"} 0' in resp.text
    assert 'rafita_ai_health_status{status="unhealthy"} 1' in resp.text


def test_metrics_json_sigue_retrocompatible(cliente: TestClient) -> None:
    resp = cliente.get("/metrics")
    assert resp.status_code == 200
    datos = resp.json()
    assert isinstance(datos, dict)
    assert "counters" in datos or "gauges" in datos
