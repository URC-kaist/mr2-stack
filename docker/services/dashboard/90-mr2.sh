#!/bin/sh
# nginx start hook: render the repo's template the way
# scripts/deploy_dashboard.bash does, with a certificate for this host.
set -eu
for v in MR2_GATEWAY_HOST MR2_GATEWAY_PORT MR2_ROVER_IP MR2_ROSBRIDGE_PORT \
         MR2_BASE_IP; do
  eval "val=\${$v:-}"
  [ -n "$val" ] || { echo "90-mr2: $v is required" >&2; exit 1; }
done
: "${MR2_BASE_ROCKET_IP:=unset}"
: "${MR2_DRONE_ROCKET_IP:=unset}" "${MR2_ROVER_ROCKET_IP:=unset}"

# nginx resolves upstream names once, at load: wait for them.
for h in "$MR2_GATEWAY_HOST" "$MR2_ROVER_IP"; do
  i=0
  until getent hosts "$h" >/dev/null 2>&1; do
    i=$((i + 1)); [ $i -le 60 ] || { echo "90-mr2: $h unresolved" >&2; exit 1; }
    sleep 1
  done
done

ssl=/etc/nginx/ssl
if [ ! -s "$ssl/mr2-dashboard.key" ] || [ ! -s "$ssl/mr2-dashboard.crt" ]; then
  mkdir -p "$ssl"
  case "$MR2_BASE_IP" in
    *[!0-9.]*) san="DNS:$MR2_BASE_IP" ;;
    *) san="IP:$MR2_BASE_IP" ;;
  esac
  openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout "$ssl/mr2-dashboard.key" -out "$ssl/mr2-dashboard.crt" \
    -subj "/CN=$MR2_BASE_IP" -addext "subjectAltName=$san" 2>/dev/null
  chmod 0600 "$ssl/mr2-dashboard.key"; chmod 0644 "$ssl/mr2-dashboard.crt"
fi

sed \
  -e "s/__MR2_GATEWAY_HOST__/$MR2_GATEWAY_HOST/g" \
  -e "s/__MR2_GATEWAY_PORT__/$MR2_GATEWAY_PORT/g" \
  -e "s/__MR2_ROVER_IP__/$MR2_ROVER_IP/g" \
  -e "s/__MR2_ROSBRIDGE_PORT__/$MR2_ROSBRIDGE_PORT/g" \
  -e "s/__MR2_BASE_IP__/$MR2_BASE_IP/g" \
  -e "s/__MR2_BASE_ROCKET_IP__/$MR2_BASE_ROCKET_IP/g" \
  -e "s/__MR2_DRONE_ROCKET_IP__/$MR2_DRONE_ROCKET_IP/g" \
  -e "s/__MR2_ROVER_ROCKET_IP__/$MR2_ROVER_ROCKET_IP/g" \
  /etc/mr2/mr2-dashboard.conf.template > /etc/nginx/conf.d/mr2.conf
if grep -q "__MR2_" /etc/nginx/conf.d/mr2.conf; then
  echo "90-mr2: unrendered token left" >&2; exit 1
fi
rm -f /etc/nginx/conf.d/default.conf
nginx -t
