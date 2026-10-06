"""Benchmark de modelos de Ollama: latencia y tokens por tipo de tarea.

Mide contra la API real (`POST {OLLAMA_HOST}/api/chat`) tres tareas
representativas del uso diario de Rafita — resumen, tool-call y QA corto —
para una o varias modelos, con percentiles p50/p95 y tokens de entrada/salida
por tarea. El historico se acumula en `daily-work/bench_history.json`
(directorio ya ignorado por git), asi que se pueden comparar modelos o
versiones a lo largo del tiempo.

Sin dependencias nuevas: usa `httpx`, que ya esta en `agent/requirements.txt`.

Uso:
    python agent/scripts/bench_models.py
    python agent/scripts/bench_models.py --modelos gemma4:12b,qwen2.5:7b --repeticiones 5
    OLLAMA_HOST=http://127.0.0.1:11434 python agent/scripts/bench_models.py --tareas qa_corto
    python agent/scripts/bench_models.py --salida /tmp/bench.json --sin-historico

Si Ollama no esta disponible (p. ej. en CI) se avisa por stderr y se sale con
codigo 0 para no romper el pipeline; con `--estricto` el codigo de salida es 1.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[2]
HISTORICO_DEFECTO = RAIZ / "daily-work" / "bench_history.json"
# Modelos de respaldo si ni la variable de entorno ni la config del agente
# estan disponibles (coinciden con los valores por defecto de src/config.py).
MODELOS_RESPALDO = ["qwen2.5:7b", "gemma4:12b"]
# Cota de salida por peticion: mantiene comparables las latencias entre tareas.
NUM_PREDICT = 192

# Herramienta de prueba: cualquier modelo capaz de tool-calling deberia
# invocarla con estos argumentos para el prompt de la tarea `tool_call`.
HERRAMIENTA_BENCH: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "guardar_recordatorio",
        "description": "Guarda un recordatorio para el usuario",
        "parameters": {
            "type": "object",
            "properties": {
                "texto": {"type": "string", "description": "Contenido del recordatorio"},
                "minutos": {"type": "integer", "description": "Minutos hasta el aviso"},
            },
            "required": ["texto"],
        },
    },
}

TAREAS: list[dict[str, Any]] = [
    {
        "nombre": "resumen",
        "system": "Eres un asistente conciso. Respondes en espanol con frases cortas.",
        "user": (
            "Resume en tres frases el texto siguiente. No anadas nada mas.\n\n"
            "La semana pasada cerramos la migracion del servidor de correo al nuevo nodo, "
            "con dos horas de ventana de mantenimiento y sin perdida de mensajes. El equipo "
            "de soporte atendio 47 incidencias, la mayoria relacionadas con contrasenas y "
            "con la app movil. Ademas se detecto un cuello de botilla en la base de datos "
            "de facturacion que se planea resolver con un indice compuesto este mes."
        ),
    },
    {
        "nombre": "tool_call",
        "system": "Eres un asistente que usa herramientas cuando te las piden.",
        "user": "Apuntame un recordatorio para beber agua en 30 minutos.",
        "tools": [HERRAMIENTA_BENCH],
    },
    {
        "nombre": "qa_corto",
        "system": "Respondes solo con la respuesta, sin explicaciones.",
        "user": "¿Cual es la capital de Australia? Responde solo con la ciudad.",
    },
]


def modelos_configurados() -> list[str]:
    """Modelos por defecto: variable de entorno, luego la config, luego respaldo."""
    entorno = (os.environ.get("OLLAMA_MODELS") or os.environ.get("OLLAMA_MODEL") or "").strip()
    if entorno:
        return _dedupe([m.strip() for m in entorno.split(",") if m.strip()])
    agente = Path(__file__).resolve().parents[1]
    if str(agente) not in sys.path:
        sys.path.insert(0, str(agente))
    try:
        from src.config import settings

        candidatos = [settings.ollama_model, settings.ollama_vision_model]
    except Exception as exc:  # noqa: BLE001 - el benchmark no depende del arranque
        print(
            "AVISO: no se pudo leer src/config (%s); uso los modelos de respaldo." % exc,
            file=sys.stderr,
        )
        candidatos = MODELOS_RESPALDO
    return _dedupe([str(m).strip() for m in candidatos if str(m).strip()])


def _dedupe(valores: list[str]) -> list[str]:
    """Lista sin duplicados conservando el orden."""
    return list(dict.fromkeys(valores))


def percentil(valores: list[float], pct: float) -> float:
    """Percentil por rango mas cercano (sirve con pocas muestras)."""
    if not valores:
        return 0.0
    ordenados = sorted(valores)
    indice = int(round((pct / 100.0) * (len(ordenados) - 1)))
    indice = max(0, min(len(ordenados) - 1, indice))
    return ordenados[indice]


def _importar_httpx():
    """httpx es dependencia del agente; si falta, se avisa y se degrada."""
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError(
            "Falta httpx (agent/requirements.txt). Instala las dependencias del "
            "agente o usa el entorno del proyecto: %s" % exc
        ) from exc
    return httpx


def _host_base(host: str) -> str:
    return (host or "").strip().rstrip("/") or "http://localhost:11434"


def modelos_disponibles(host: str, timeout: float) -> list[str] | None:
    """Modelos instalados en el servidor, o `None` si no responde."""
    httpx = _importar_httpx()
    try:
        with httpx.Client(timeout=timeout) as cliente:
            respuesta = cliente.get("%s/api/tags" % _host_base(host))
            respuesta.raise_for_status()
            datos = respuesta.json()
    except Exception:
        return None
    nombres = []
    for modelo in datos.get("models") or []:
        nombre = str(modelo.get("name") or modelo.get("model") or "").strip()
        if nombre:
            nombres.append(nombre)
    return nombres


def _disponible(nombre: str, instalados: list[str]) -> bool:
    """El modelo esta si su tag coincide (o, sin tag, si existe cualquier tag)."""
    if ":" in nombre:
        return nombre in instalados
    prefijo = nombre + ":"
    return any(instalado == nombre or instalado.startswith(prefijo) for instalado in instalados)


def ejecutar_peticion(
    cliente: Any,
    host: str,
    modelo: str,
    tarea: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    """Una llamada a `/api/chat` con latencia, tokens y si hubo tool-call."""
    cuerpo: dict[str, Any] = {
        "model": modelo,
        "stream": False,
        "messages": [
            {"role": "system", "content": tarea["system"]},
            {"role": "user", "content": tarea["user"]},
        ],
        "options": {"temperature": 0, "num_predict": NUM_PREDICT},
    }
    if tarea.get("tools"):
        cuerpo["tools"] = tarea["tools"]
    inicio = time.perf_counter()
    respuesta = cliente.post("%s/api/chat" % _host_base(host), json=cuerpo, timeout=timeout)
    latencia_ms = (time.perf_counter() - inicio) * 1000.0
    respuesta.raise_for_status()
    datos = respuesta.json()
    mensaje = datos.get("message") or {}
    return {
        "latencia_ms": latencia_ms,
        "tokens_in": int(datos.get("prompt_eval_count") or 0),
        "tokens_out": int(datos.get("eval_count") or 0),
        "tool_calls": len(mensaje.get("tool_calls") or []),
    }


def benchmark_modelo(
    host: str,
    modelo: str,
    tareas: list[dict[str, Any]],
    repeticiones: int,
    timeout: float,
) -> list[dict[str, Any]]:
    """Ejecuta todas las tareas para un modelo y devuelve una fila por tarea."""
    httpx = _importar_httpx()
    filas: list[dict[str, Any]] = []
    with httpx.Client(timeout=timeout) as cliente:
        for tarea in tareas:
            latencias: list[float] = []
            tokens_in: list[int] = []
            tokens_out: list[int] = []
            tool_calls = 0
            error: str | None = None
            for _ in range(repeticiones):
                try:
                    resultado = ejecutar_peticion(cliente, host, modelo, tarea, timeout)
                except Exception as exc:  # noqa: BLE001 - se reporta y se sigue
                    # En una sola linea: un error multilinea romperia la tabla.
                    error = " ".join(str(exc).split())[:180]
                    continue
                latencias.append(float(resultado["latencia_ms"]))
                tokens_in.append(int(resultado["tokens_in"]))
                tokens_out.append(int(resultado["tokens_out"]))
                tool_calls += int(resultado["tool_calls"])
            p50 = percentil(latencias, 50)
            filas.append(
                {
                    "modelo": modelo,
                    "tarea": tarea["nombre"],
                    "repeticiones": repeticiones,
                    "intentos_ok": len(latencias),
                    "latencia_p50_ms": round(p50, 1) if latencias else None,
                    "latencia_p95_ms": round(percentil(latencias, 95), 1) if latencias else None,
                    "tokens_in": round(sum(tokens_in) / len(tokens_in), 1) if tokens_in else 0,
                    "tokens_out": round(sum(tokens_out) / len(tokens_out), 1) if tokens_out else 0,
                    "tokens_out_por_s": (
                        round((sum(tokens_out) / len(tokens_out)) / (p50 / 1000.0), 1)
                        if tokens_out and p50 > 0
                        else 0
                    ),
                    "tool_calls": tool_calls,
                    "error": error if not latencias else None,
                }
            )
    return filas


def imprimir_tabla(filas: list[dict[str, Any]]) -> None:
    """Resumen legible por terminal (una fila por modelo y tarea)."""
    cabecera = "%-18s %-10s %9s %9s %8s %8s %8s %5s %s" % (
        "modelo",
        "tarea",
        "p50(ms)",
        "p95(ms)",
        "tok_in",
        "tok_out",
        "tok/s",
        "tools",
        "aviso",
    )
    print(cabecera)
    print("-" * len(cabecera))
    for fila in filas:
        p50 = "-" if fila["latencia_p50_ms"] is None else str(fila["latencia_p50_ms"])
        p95 = "-" if fila["latencia_p95_ms"] is None else str(fila["latencia_p95_ms"])
        print(
            "%-18s %-10s %9s %9s %8s %8s %8s %5s %s"
            % (
                fila["modelo"][:18],
                fila["tarea"],
                p50,
                p95,
                fila["tokens_in"],
                fila["tokens_out"],
                fila["tokens_out_por_s"],
                fila["tool_calls"],
                fila["error"] or "",
            )
        )


def guardar_historico(ruta: Path, entrada: dict[str, Any]) -> None:
    """Anade una ejecucion al historico JSON (crea directorio si hace falta)."""
    historico: list[Any] = []
    if ruta.exists():
        try:
            cargado = json.loads(ruta.read_text(encoding="utf-8"))
            historico = cargado if isinstance(cargado, list) else []
        except (json.JSONDecodeError, OSError) as exc:
            print("AVISO: historico ilegible (%s); se empieza uno nuevo." % exc, file=sys.stderr)
    historico.append(entrada)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(historico, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parsear_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark de modelos Ollama (latencia p50/p95 y tokens por tarea)."
    )
    parser.add_argument(
        "--modelos",
        default="",
        help="Lista separada por comas (por defecto: los configurados en el agente)",
    )
    parser.add_argument(
        "--tareas",
        default="",
        help="Subconjunto por comas de: %s" % ", ".join(t["nombre"] for t in TAREAS),
    )
    parser.add_argument(
        "--repeticiones",
        type=int,
        default=3,
        help="Peticiones por tarea y modelo (por defecto: 3)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        help="URL de Ollama (o variable OLLAMA_HOST)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Timeout HTTP por peticion en segundos (por defecto: 120)",
    )
    parser.add_argument(
        "--salida",
        default=str(HISTORICO_DEFECTO),
        help="Ruta del historico JSON (por defecto: daily-work/bench_history.json)",
    )
    parser.add_argument(
        "--sin-historico",
        action="store_true",
        help="No escribir el historico, solo imprimir la tabla",
    )
    parser.add_argument(
        "--estricto",
        action="store_true",
        help="Devolver codigo de salida 1 si Ollama no esta disponible",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parsear_args(argv)
    tareas = TAREAS
    if args.tareas.strip():
        elegidas = {nombre.strip() for nombre in args.tareas.split(",") if nombre.strip()}
        tareas = [t for t in TAREAS if t["nombre"] in elegidas]
        if not tareas:
            print("AVISO: ninguna tarea coincide con --tareas.", file=sys.stderr)
            return 0 if not args.estricto else 1
    modelos = (
        _dedupe([m.strip() for m in args.modelos.split(",") if m.strip()])
        if args.modelos.strip()
        else modelos_configurados()
    )
    if not modelos:
        print("AVISO: no hay modelos que benchmarkar.", file=sys.stderr)
        return 0 if not args.estricto else 1

    try:
        instalados = modelos_disponibles(args.host, min(args.timeout, 15.0))
    except RuntimeError as exc:
        print("AVISO: %s" % exc, file=sys.stderr)
        return 0 if not args.estricto else 1
    if instalados is None:
        print(
            "AVISO: Ollama no responde en %s; se omite el benchmark.\n"
            "       (Normal en CI: no hay servidor de modelos. Usa --estricto para "
            "convertirlo en error.)" % _host_base(args.host),
            file=sys.stderr,
        )
        return 0 if not args.estricto else 1

    filas: list[dict[str, Any]] = []
    for modelo in modelos:
        if not _disponible(modelo, instalados):
            filas.append(
                {
                    "modelo": modelo,
                    "tarea": "-",
                    "repeticiones": args.repeticiones,
                    "intentos_ok": 0,
                    "latencia_p50_ms": None,
                    "latencia_p95_ms": None,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "tokens_out_por_s": 0,
                    "tool_calls": 0,
                    "error": "modelo no instalado en %s" % _host_base(args.host),
                }
            )
            continue
        filas.extend(
            benchmark_modelo(
                host=args.host,
                modelo=modelo,
                tareas=tareas,
                repeticiones=max(1, args.repeticiones),
                timeout=args.timeout,
            )
        )

    imprimir_tabla(filas)
    fallos = [f for f in filas if f["error"]]
    if len(fallos) == len(filas):
        print("AVISO: todas las peticiones fallaron; no se escribe el historico.", file=sys.stderr)
        return 1 if args.estricto else 0

    if not args.sin_historico:
        entrada = {
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
            "host": _host_base(args.host),
            "repeticiones": max(1, args.repeticiones),
            "tareas": [t["nombre"] for t in tareas],
            "resultados": filas,
        }
        ruta = Path(args.salida)
        try:
            guardar_historico(ruta, entrada)
            print("\nHistorico actualizado: %s" % ruta)
        except OSError as exc:
            print("AVISO: no se pudo escribir el historico (%s)." % exc, file=sys.stderr)
    return 1 if (args.estricto and fallos) else 0


if __name__ == "__main__":
    sys.exit(main())
