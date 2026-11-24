#!/usr/bin/env bash
# Carries the rover's fake radio (PTY peer /tmp/xbee_sim1, created by
# xbee_sim.launch.py) over TCP :7777 to the gateway. One client at a
# time; restarts whenever either side drops.
peer="${XBEE_SIM_PEER:-/tmp/xbee_sim1}"
port="${RADIO_PORT:-7777}"
while true; do
  until [ -e "$peer" ]; do sleep 0.2; done
  echo "[radio-bridge] serving $peer on tcp:$port"
  socat "FILE:${peer},raw,echo=0" "TCP-LISTEN:${port},reuseaddr" \
    || echo "[radio-bridge] socat exited ($?)"
  sleep 0.5
done
