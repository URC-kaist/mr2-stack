#!/usr/bin/env bash
# Non-browser test suite for a running local stack. Browser checks (dashboard,
# gamepad drive, live video) run in the visible Playwright session.
#   tests/run_suite.sh <out_dir>      (or: make test N=<n>)
# Writes one file per check plus summary.json; exit 0 only if all pass.
set -u
here="$(cd "$(dirname "$0")/.." && pwd)"
dc() { docker compose -f "${COMPOSE_FILE:-$here/compose/local/compose.local.yaml}" "$@"; }
out="${1:?out dir}"
mkdir -p "$out"
declare -a names=() results=()
record() { names+=("$1"); results+=("$2"); echo "[$2] $1"; }

# 1. services running and healthy
dc ps --format '{{.Service}} {{.Health}}' > "$out/services.txt"
want="autonomy gateway nginx rover"
ok=pass
for s in $want; do
  grep -q "^$s healthy$" "$out/services.txt" || ok=fail
done
record services "$ok"

# 2. readiness (real checks)
for r in rover autonomy; do
  if dc exec -T "$r" /opt/mr2/with-ros.sh python3 /opt/mr2/ready.py "$r" \
       > "$out/ready-$r.json" 2>&1; then record "ready-$r" pass
  else record "ready-$r" fail; fi
done

# 3. radio both ways through the real gateway
if dc exec -T autonomy /opt/mr2/with-ros.sh python3 \
     /opt/mr2/tests/links/radio_link.py > "$out/radio.json" 2>&1
then record radio pass; else record radio fail; fi

# 4. nginx: page, JSON, three WebSocket upgrades, video availability
{
  code=$(curl -sk -o /dev/null -w '%{http_code}' https://localhost/)
  echo "GET / $code"
  curl -sk https://localhost/video/streams > "$out/video-streams.json"
  for p in xbee-ws video-ws rosbridge-ws; do
    c=$(curl -sk -o /dev/null -w '%{http_code}' --http1.1 --max-time 3 \
      -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
      -H 'Sec-WebSocket-Version: 13' \
      -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' "https://localhost/$p")
    echo "upgrade /$p $c"
  done
} > "$out/nginx.txt" 2>&1
if grep -q "GET / 200" "$out/nginx.txt" && \
   [ "$(grep -c ' 101$' "$out/nginx.txt")" = 3 ]; then record nginx pass
else record nginx fail; fi
sleep 3
if curl -sk https://localhost/video/streams | grep -q '"available":true'
then record video-available pass; else record video-available fail; fi

# 5. GNSS mission scored with Gazebo ground truth
dc exec -T rover /opt/mr2/with-ros.sh timeout 200 \
  python3 /opt/mr2/gz_pose.py --follow \
  > "$out/poses.jsonl" 2>&1 &
pose_pid=$!
sleep 2
dc exec -T autonomy /opt/mr2/with-ros.sh \
  python3 /opt/mr2/mission.py --timeout 180 > "$out/mission.jsonl" 2>&1
mrc=$?
sleep 2
kill "$pose_pid" 2>/dev/null
python3 - "$out" <<'EOF' > "$out/mission-score.json"
import json, math, sys
out = sys.argv[1]
lat0, lon0, lat, lon = 38.406739, -110.791394, 38.406766, -110.791394
gy = (lat - lat0) * 111132.95
gx = (lon - lon0) * 111319.49 * math.cos(math.radians(lat0))
P = [json.loads(l) for l in open(f"{out}/poses.jsonl") if l.startswith("{")]
res = {"goal_world": [round(gx, 3), round(gy, 3)]}
if P:
    p = P[-1]
    res["final_world"] = [round(p["x"], 3), round(p["y"], 3)]
    res["error_m"] = round(math.hypot(p["x"] - gx, p["y"] - gy), 3)
print(json.dumps(res))
EOF
err=$(python3 -c "import json;print(json.load(open('$out/mission-score.json')).get('error_m',99))")
if [ "$mrc" = 0 ] && python3 -c "import sys;sys.exit(0 if $err<=0.75 else 1)"
then record "mission(err=${err}m)" pass; else record "mission(err=${err}m)" fail; fi

# 6. gateway unit tests in its image
if dc exec -T gateway npm test > "$out/gateway-npm-test.log" 2>&1
then record gateway-npm-test pass; else record gateway-npm-test fail; fi

# summary
python3 - "$out" "${names[@]}" -- "${results[@]}" <<'EOF'
import json, sys
a = sys.argv[2:]; i = a.index("--")
names, res = a[:i], a[i+1:]
s = {"checks": dict(zip(names, res)), "ok": all(r == "pass" for r in res)}
json.dump(s, open(sys.argv[1] + "/summary.json", "w"), indent=1)
print(json.dumps(s))
EOF
for r in "${results[@]}"; do [ "$r" = pass ] || exit 1; done
