"""Structured logging with correlation IDs and local metrics.

Uses Python contextvars for automatic per-request context propagation
across async tasks, avoiding manual ID threading through every function.
"""

import contextvars
import time
import uuid
from threading import Lock
from typing import Any

_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)


def new_correlation_id() -> str:
    """Generate and set a new correlation ID for the current context."""
    cid = uuid.uuid4().hex[:12]
    _correlation_id.set(cid)
    return cid


def get_correlation_id() -> str:
    """Get the current correlation ID, or empty string if not set."""
    return _correlation_id.get("")


class MetricsRegistry:
    """Thread-safe in-memory metrics store (no external backend)."""

    def __init__(self):
        self._lock = Lock()
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}

    def inc(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            if name not in self._histograms:
                self._histograms[name] = []
            self._histograms[name].append(value)

    def get_counters(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counters)

    def get_gauges(self) -> dict[str, float]:
        with self._lock:
            return dict(self._gauges)

    def get_histogram_stats(self, name: str) -> dict[str, Any]:
        with self._lock:
            values = self._histograms.get(name, [])
            if not values:
                return {"count": 0}
            sorted_vals = sorted(values)
            return {
                "count": len(values),
                "min": sorted_vals[0],
                "max": sorted_vals[-1],
                "avg": sum(values) / len(values),
                "p50": sorted_vals[len(values) // 2],
                "p95": sorted_vals[int(len(values) * 0.95)],
                "p99": sorted_vals[int(len(values) * 0.99)],
            }

    def snapshot(self) -> dict[str, Any]:
        return {
            "counters": self.get_counters(),
            "gauges": self.get_gauges(),
            "histograms": {
                name: self.get_histogram_stats(name)
                for name in self._histograms
            },
        }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


metrics = MetricsRegistry()


class TimingContext:
    """Context manager to track latency of an operation."""

    def __init__(self, metric_name: str):
        self._name = metric_name
        self._start = 0.0

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args):
        elapsed = time.perf_counter() - self._start
        metrics.observe(self._name, elapsed)

    async def __aenter__(self):
        self._start = time.perf_counter()
        return self

    async def __aexit__(self, *args):
        elapsed = time.perf_counter() - self._start
        metrics.observe(self._name, elapsed)
