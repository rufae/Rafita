#!/bin/bash
# Mide el hueco sin readiness 200 durante un recreate/upgrade (tarea 3.7).
# Uso: bash measure-readiness.sh docker compose ... up -d --build
rm -f /tmp/ready_poll.log /tmp/ready_poll.stop
cat > /tmp/ready_poll.sh <<'POLL'
#!/bin/bash
while [ ! -f /tmp/ready_poll.stop ]; do
  ts=$(date +%s.%N)
  code=$(curl -s -o /dev/null -w "%{http_code}" -m 2 http://127.0.0.1:8010/ready 2>/dev/null)
  [ -n "$code" ] || code=000
  echo "$ts $code" >> /tmp/ready_poll.log
  sleep 0.2
done
POLL
nohup bash /tmp/ready_poll.sh >/dev/null 2>&1 &
sleep 2
t0=$(date +%s.%N)
"$@" > /tmp/measure_cmd.log 2>&1
t1=$(date +%s.%N)
# esperar a que el servicio vuelva a estar ready (hasta 3 min)
for _ in $(seq 1 360); do
  code=$(curl -s -o /dev/null -w "%{http_code}" -m 2 http://127.0.0.1:8010/ready 2>/dev/null)
  [ "$code" = "200" ] && break
  sleep 0.5
done
touch /tmp/ready_poll.stop
sleep 0.5
echo "wall del comando: $(echo "$t1 - $t0" | bc)s"
python3 - <<'PY'
times = []
for line in open("/tmp/ready_poll.log"):
    p = line.split()
    if len(p) == 2:
        times.append((float(p[0]), p[1]))
down_start = up_end = None
last_ok_before = None
for ts, code in times:
    if code != "200":
        if down_start is None:
            down_start = ts
    else:
        if down_start is not None and up_end is None:
            up_end = ts
        last_ok_before = ts
if down_start and up_end:
    print("downtime hasta readiness: %.1f s" % (up_end - down_start))
else:
    print("no se detecto caida completa; muestras:", len(times))
codes = {}
for _, c in times:
    codes[c] = codes.get(c, 0) + 1
print("codigos:", codes)
PY
