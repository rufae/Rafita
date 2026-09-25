#!/usr/bin/env bash
# Nodo Dell — benchmark del LLM (tarea 3.1). Solo lectura sobre la API.
#
# Local:  bash 03-benchmark.sh
# Remoto: ssh server@192.168.1.201 'bash -s' < 03-benchmark.sh
# Desde el HP contra el Dell:
#   HOST=http://100.x.y.z:11434 bash deploy/dell/03-benchmark.sh
#
# Variables:
#   HOST     endpoint (default http://127.0.0.1:11434)
#   MODEL    modelo (default gemma4:12b)
#   PROMPT   prompt de prueba
#   PREDICT  tokens maximos a generar (default 128)
#   RUNS     repeticiones (default 2; la primera suele incluir carga)
set -euo pipefail

HOST="${HOST:-http://127.0.0.1:11434}"
MODEL="${MODEL:-gemma4:12b}"
PROMPT="${PROMPT:-Explica en dos frases que es un segundo cerebro digital.}"
PREDICT="${PREDICT:-128}"
RUNS="${RUNS:-2}"

payload="$(python3 - "$MODEL" "$PROMPT" "$PREDICT" <<'PY'
import json
import sys

print(json.dumps({
    "model": sys.argv[1],
    "prompt": sys.argv[2],
    "stream": False,
    "options": {"num_predict": int(sys.argv[3])},
}))
PY
)"

echo "host=${HOST} model=${MODEL} predict=${PREDICT} runs=${RUNS}"
for run in $(seq 1 "${RUNS}"); do
    curl -s "${HOST}/api/generate" -d "${payload}" -o /tmp/ollama_bench.json \
        -w "run=${run} http_ttfb_s=%{time_starttransfer} http_total_s=%{time_total}\n"
    python3 - <<'PY'
import json

data = json.load(open("/tmp/ollama_bench.json"))
gen_count = data.get("eval_count", 0)
gen_ns = data.get("eval_duration", 0) or 1
prompt_count = data.get("prompt_eval_count", 0)
prompt_ns = data.get("prompt_eval_duration", 0) or 1
print(
    "  model=%s gen_tok_s=%.2f prompt_tok_s=%.2f load_s=%.2f total_s=%.2f chars=%d"
    % (
        data.get("model", "?"),
        gen_count / (gen_ns / 1e9),
        prompt_count / (prompt_ns / 1e9),
        data.get("load_duration", 0) / 1e9,
        data.get("total_duration", 0) / 1e9,
        len(data.get("response", "")),
    )
)
PY
done
