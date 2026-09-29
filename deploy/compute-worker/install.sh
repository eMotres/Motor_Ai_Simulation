#!/usr/bin/env bash
# Install / update the eMotres compute worker on YOUR server (docs/BYO_COMPUTE.md).
#   curl -fsSL <URL>/api/nodes/worker/install.sh | sudo bash -s -- --url <URL> --token <mcnode_...>
# Pulls the solver code at the platform's version into a venv, writes the token
# to /etc/motres-compute-worker.env (0600) and starts a systemd service that
# runs as an unprivileged user.  Outbound HTTPS only; no ports are opened.
# Re-run to rotate the token or follow a platform upgrade.
set -euo pipefail
URL="" TOKEN="" REF="" REPO="https://github.com/eMotres/motor_ai_sim"
while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL="$2"; shift 2;;
    --token) TOKEN="$2"; shift 2;;
    --ref) REF="$2"; shift 2;;
    --repo) REPO="$2"; shift 2;;
    *) echo "unknown arg $1" >&2; exit 2;;
  esac
done
[ -n "$URL" ] && [ -n "$TOKEN" ] || { echo "usage: install.sh --url URL --token TOKEN [--ref vX.Y.Z]" >&2; exit 2; }
command -v python3 >/dev/null || { echo "python3 (>=3.10) required" >&2; exit 1; }
command -v git >/dev/null || { echo "git required" >&2; exit 1; }
VERSION="$(curl -fsSL "$URL/api/version" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("version",""))')"
[ -n "$VERSION" ] || { echo "could not read the platform version from $URL" >&2; exit 1; }
REF="${REF:-v$VERSION}"
id motres-worker >/dev/null 2>&1 || useradd --system --home /opt/motres-worker --shell /usr/sbin/nologin motres-worker
install -d -o motres-worker -m 0755 /opt/motres-worker
rm -rf /opt/motres-worker/src
git clone --depth 1 --branch "$REF" "$REPO" /opt/motres-worker/src
python3 -m venv /opt/motres-worker/venv
/opt/motres-worker/venv/bin/pip install --quiet --upgrade pip
/opt/motres-worker/venv/bin/pip install --quiet -e /opt/motres-worker/src
chown -R motres-worker /opt/motres-worker
umask 077
printf 'MOTRES_URL=%s\nMOTRES_NODE_TOKEN=%s\nMOTRES_PLATFORM_VERSION=%s\nMOTRES_IMAGE_DIGEST=git:%s\n' \
  "$URL" "$TOKEN" "$VERSION" "$REF" > /etc/motres-compute-worker.env
chmod 0600 /etc/motres-compute-worker.env
install -m 0644 /opt/motres-worker/src/deploy/compute-worker/motres-compute-worker.service \
  /etc/systemd/system/motres-compute-worker.service
systemctl daemon-reload
systemctl enable --now motres-compute-worker.service
systemctl restart motres-compute-worker.service
sleep 3
systemctl --no-pager --lines=5 status motres-compute-worker.service || true
