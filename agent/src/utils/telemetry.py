"""Structured logging with correlation IDs and local metrics.

Uses Python contextvars for automatic per-request context propagation
across async tasks, avoiding manual ID threading through every function.
"""

import contextvars
import math
import time
import uuid
from threading import Lock
from typing import Any

_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="")


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
            "histograms": {name: self.get_histogram_stats(name) for name in self._histograms},
        }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


metrics = MetricsRegistry()


def record_llm_usage(response: Any) -> None:
    """Registra tokens de una respuesta LLM en las métricas (mejora 2).

    El coste de los modelos locales es 0 €, pero los tokens son la señal de
    coste real: crecen con el contexto y con las herramientas ofrecidas.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    metrics.inc("llm_requests", 1)
    metrics.inc("llm_prompt_tokens", int(getattr(usage, "prompt_tokens", 0) or 0))
    metrics.inc("llm_completion_tokens", int(getattr(usage, "completion_tokens", 0) or 0))


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


# --- Texto exposition de Prometheus (sin dependencias) ----------------------
# El registro solo guarda valores crudos y estadisticos (p50/p95/p99), NO
# buckets: por eso las latencias se exponen como gauges con label `quantile`
# y jamas como histogramas, que solo serian honestos con buckets reales.
# El texto se genera a mano para no anadir `prometheus_client` al proyecto.

_PROM_PREFIJO = "rafita_"

# Ayuda conocida por familia (la clave es la cabeza del nombre, sin labels).
_PROM_AYUDAS: dict[str, str] = {
    "llm_requests": "Peticiones enviadas al proveedor de IA (Ollama o compatible).",
    "llm_prompt_tokens": "Tokens de prompt consumidos en las peticiones al LLM.",
    "llm_completion_tokens": "Tokens generados por el LLM.",
    "tool_calls": "Llamadas a herramientas del agente.",
    "tool_calls_total": "Llamadas a herramientas del agente (contador global).",
    "tool_calls_failed": "Llamadas a herramientas que terminaron en error.",
    "infra_checks_ok": (
        "1 si la ultima ronda de comprobaciones de infraestructura fue correcta, 0 si alguna fallo."
    ),
    "tool_latency_ms": "Latencia de las herramientas del agente, en milisegundos.",
    "llm_chat_latency": "Latencia de un turno de chat con el LLM, en segundos.",
    "embedding_query_latency": "Latencia de las consultas de embeddings, en segundos.",
}


def _prom_ayuda(cabeza: str) -> str:
    """Texto de HELP de una familia; genérico si no esta documentada."""
    if cabeza in _PROM_AYUDAS:
        return _PROM_AYUDAS[cabeza]
    if cabeza.startswith("tool_calls"):
        return "Llamadas a herramientas del agente."
    return "Metrica '%s' registrada por el agente." % cabeza


def _prom_escapar_ayuda(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace("\n", "\\n")


def _prom_escapar_label(valor: str) -> str:
    return valor.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _prom_valor(valor: float) -> str:
    """Numero en el formato que acepta el exposition format de Prometheus."""
    v = float(valor)
    if math.isnan(v):
        return "NaN"
    if math.isinf(v):
        return "+Inf" if v > 0 else "-Inf"
    if v.is_integer():
        return str(int(v))
    return repr(v)


def _prom_partir(nombre: str) -> tuple[str, str | None]:
    """Separa el nombre interno en familia y label `tool`.

    `tool_latency_ms.search_web` -> ("tool_latency_ms", "search_web").
    El punto no es valido en un nombre de metrica de Prometheus, y el label
    permite ademas `sum by (tool) (...)` sobre las herramientas.
    """
    cabeza, punto, etiqueta = nombre.partition(".")
    familia = cabeza or "metrica"
    return familia, ((etiqueta or None) if punto else None)


def _prom_saneado(familia: str) -> str:
    """Nombre valido de metrica: prefijo `rafita_` y solo [a-zA-Z0-9_:]."""
    limpio = "".join(
        ch if (ch.isascii() and (ch.isalnum() or ch in "_:")) else "_" for ch in familia
    )
    return _PROM_PREFIJO + (limpio or "metrica")


def prom_block(
    familia: str,
    tipo: str,
    ayuda: str,
    muestras: list[tuple[dict[str, str], float]],
) -> str:
    """Bloque exposition (HELP + TYPE + muestras) de una familia de metricas.

    `familia` va sin prefijo (se le anade `rafita_` al sanearla). Cada muestra
    es una tupla (labels, valor). Devuelve cadena vacia si no hay muestras.
    """
    if not muestras:
        return ""
    nombre = _prom_saneado(familia)
    lineas = [
        "# HELP %s %s" % (nombre, _prom_escapar_ayuda(ayuda)),
        "# TYPE %s %s" % (nombre, tipo),
    ]
    for labels, valor in sorted(muestras, key=lambda m: sorted(m[0].items())):
        sufijo = ""
        if labels:
            pares = ",".join(
                '%s="%s"' % (clave, _prom_escapar_label(val))
                for clave, val in sorted(labels.items())
            )
            sufijo = "{%s}" % pares
        lineas.append("%s%s %s" % (nombre, sufijo, _prom_valor(valor)))
    return "\n".join(lineas) + "\n"


def render_prometheus(snapshot: dict[str, Any]) -> str:
    """Convierte un `MetricsRegistry.snapshot()` en texto exposition de Prometheus.

    - Contadores y gauges conservan su nombre con el prefijo `rafita_`; los
      nombres con punto (`tool_calls.search_web`) se separan en familia base +
      label `tool`.
    - Las latencias salen como gauges con `quantile` (p50/p95/p99) mas
      `_count`/`_sum` como contadores: el registro no tiene buckets, y fingir
      un histograma inventaria acumulaciones que nunca se midieron.
    - Las unidades no estan en el nombre (a diferencia de `/metrics` JSON),
      pero si en cada HELP (`tool_latency_ms` va en ms, `llm_chat_latency` en s).
    """
    familias: dict[str, tuple[str, str, list[tuple[dict[str, str], float]]]] = {}

    def agregar(familia: str, tipo: str, ayuda: str, labels: dict[str, str], valor: float) -> None:
        entrada = familias.get(familia)
        if entrada is None:
            familias[familia] = (tipo, ayuda, [(labels, valor)])
        else:
            entrada[2].append((labels, valor))

    for nombre, valor in (snapshot.get("counters") or {}).items():
        cabeza, tool = _prom_partir(nombre)
        etiquetas = {"tool": tool} if tool else {}
        agregar(cabeza, "counter", _prom_ayuda(cabeza), etiquetas, float(valor))

    for nombre, valor in (snapshot.get("gauges") or {}).items():
        cabeza, tool = _prom_partir(nombre)
        etiquetas = {"tool": tool} if tool else {}
        agregar(cabeza, "gauge", _prom_ayuda(cabeza), etiquetas, float(valor))

    for nombre, stats in (snapshot.get("histograms") or {}).items():
        if not isinstance(stats, dict):
            continue
        cabeza, tool = _prom_partir(nombre)
        base_labels = {"tool": tool} if tool else {}
        cuenta = float(stats.get("count") or 0)
        ayuda_pct = (
            "%s Percentiles calculados sobre las muestras retenidas en memoria "
            "(gauges, no histograma: no hay buckets)." % _prom_ayuda(cabeza)
        )
        for cuantil in ("p50", "p95", "p99"):
            valor = stats.get(cuantil)
            if cuenta > 0 and valor is not None:
                etiquetas = dict(base_labels)
                etiquetas["quantile"] = cuantil
                agregar(cabeza, "gauge", ayuda_pct, etiquetas, float(valor))
        promedio = float(stats.get("avg") or 0.0)
        agregar(
            "%s_sum" % cabeza,
            "counter",
            "Suma de todas las muestras de '%s' (misma unidad que la metrica)." % cabeza,
            dict(base_labels),
            promedio * cuenta,
        )
        agregar(
            "%s_count" % cabeza,
            "counter",
            "Numero de muestras de '%s' retenidas en memoria." % cabeza,
            dict(base_labels),
            cuenta,
        )

    return "".join(
        prom_block(familia, tipo, ayuda, muestras)
        for familia, (tipo, ayuda, muestras) in familias.items()
    )
