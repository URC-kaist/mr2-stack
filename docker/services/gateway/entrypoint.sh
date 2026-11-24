#!/usr/bin/env bash
# Gateway entrypoint: attach a local PTY to the rover's radio over TCP
# (rover:7777), keep it attached, then start the gateway. The gateway
# re-opens its serial path every second on error.
link="${BASE_XBEE_DEVICE:-/tmp/xbee_sim1}"
peer="${RADIO_PEER:-rover:7777}"
if [ -n "$peer" ] && [ "$peer" != "none" ]; then
  (
    while true; do
      socat "PTY,raw,echo=0,link=${link}" "TCP:${peer}" \
        || echo "[radio] socat to ${peer} exited ($?); retrying"
      rm -f "$link"
      sleep 1
    done
  ) &
  for _ in $(seq 1 100); do [ -e "$link" ] && break; sleep 0.2; done
fi
exec "$@"
