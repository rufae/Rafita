"""Cobertura de src/utils/telemetry.py: IDs de correlación y métricas locales."""

import contextvars

from src.utils import telemetry
from src.utils.telemetry import MetricsRegistry, TimingContext, metrics


def test_new_correlation_id_sets_and_returns_hex():
    cid = telemetry.new_correlation_id()
    assert len(cid) == 12
    int(cid, 16)
    assert telemetry.get_correlation_id() == cid


def test_get_correlation_id_defaults_to_empty_in_fresh_context():
    assert contextvars.Context().run(telemetry.get_correlation_id) == ""


def test_counters_inc_default_and_amount():
    registry = MetricsRegistry()
    registry.inc("hits")
    registry.inc("hits", amount=4)
    assert registry.get_counters() == {"hits": 5}


def test_gauges_set_and_overwrite():
    registry = MetricsRegistry()
    registry.set_gauge("temp", 36.5)
    registry.set_gauge("temp", 37.0)
    assert registry.get_gauges() == {"temp": 37.0}


def test_get_counters_returns_copy():
    registry = MetricsRegistry()
    registry.inc("a")
    snapshot = registry.get_counters()
    snapshot["a"] = 999
    assert registry.get_counters() == {"a": 1}


def test_histogram_stats_unknown_name_is_empty():
    registry = MetricsRegistry()
    assert registry.get_histogram_stats("nope") == {"count": 0}


def test_histogram_stats_computes_percentiles():
    registry = MetricsRegistry()
    for value in range(1, 11):
        registry.observe("lat", float(value))
    stats = registry.get_histogram_stats("lat")
    assert stats["count"] == 10
    assert stats["min"] == 1.0
    assert stats["max"] == 10.0
    assert stats["avg"] == 5.5
    assert stats["p50"] == 6.0
    assert stats["p95"] == 10.0
    assert stats["p99"] == 10.0


def test_histogram_stats_single_value():
    registry = MetricsRegistry()
    registry.observe("once", 2.5)
    stats = registry.get_histogram_stats("once")
    assert stats == {
        "count": 1,
        "min": 2.5,
        "max": 2.5,
        "avg": 2.5,
        "p50": 2.5,
        "p95": 2.5,
        "p99": 2.5,
    }


def test_snapshot_combines_all_series():
    registry = MetricsRegistry()
    registry.inc("c1", 2)
    registry.set_gauge("g1", 1.5)
    registry.observe("h1", 3.0)
    snapshot = registry.snapshot()
    assert snapshot["counters"] == {"c1": 2}
    assert snapshot["gauges"] == {"g1": 1.5}
    assert snapshot["histograms"]["h1"]["count"] == 1


def test_reset_clears_everything():
    registry = MetricsRegistry()
    registry.inc("c")
    registry.set_gauge("g", 1.0)
    registry.observe("h", 1.0)
    registry.reset()
    assert registry.get_counters() == {}
    assert registry.get_gauges() == {}
    assert registry.get_histogram_stats("h") == {"count": 0}


def test_timing_context_sync_observes_latency():
    metrics.reset()
    with TimingContext("op_sync") as timing:
        assert timing._start > 0
    stats = metrics.get_histogram_stats("op_sync")
    assert stats["count"] == 1
    assert stats["min"] >= 0


async def test_timing_context_async_observes_latency():
    metrics.reset()
    async with TimingContext("op_async"):
        pass
    stats = metrics.get_histogram_stats("op_async")
    assert stats["count"] == 1
    assert stats["min"] >= 0
