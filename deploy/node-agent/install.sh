#!/usr/bin/env bash
# Install / update the eMotres node agent on this host.
#   sudo bash install.sh --url <API base> --token <mnode_...>
# The token goes to /etc/motres-node-agent.env (0600), never onto a command
# line the service runs with. Re-run to rotate the token or update the script.
set -euo pipefail
URL="" TOKEN="" INTERVAL=15
while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL="$2"; shift 2;;
    --token) TOKEN="$2"; shift 2;;
    --interval) INTERVAL="$2"; shift 2;;
    *) echo "unknown arg $1" >&2; exit 2;;
  esac
done
[ -n "$URL" ] && [ -n "$TOKEN" ] || { echo "usage: install.sh --url URL --token TOKEN" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
command -v python3 >/dev/null || { echo "python3 required" >&2; exit 1; }
install -d -m 0755 /opt/motres-node-agent
install -m 0755 "$HERE/motres_node_agent.py" /opt/motres-node-agent/motres_node_agent.py
umask 077
printf 'MOTRES_MONITOR_URL=%s\nMOTRES_NODE_TOKEN=%s\nMOTRES_INTERVAL_S=%s\n' \
  "$URL" "$TOKEN" "$INTERVAL" > /etc/motres-node-agent.env
chmod 0600 /etc/motres-node-agent.env
install -m 0644 "$HERE/motres-node-agent.service" /etc/systemd/system/motres-node-agent.service
systemctl daemon-reload
systemctl enable --now motres-node-agent.service
systemctl restart motres-node-agent.service
sleep 3
systemctl --no-pager --lines=5 status motres-node-agent.service || true
