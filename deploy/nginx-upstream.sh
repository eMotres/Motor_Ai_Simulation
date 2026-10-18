#!/bin/sh
# API upstream (deploy stage 2, docs/DEPLOY_STAGE2.md): the nginx upstream `motres_api` includes /etc/nginx/api-upstream.conf.
#
# The member of the upstream (api-blue or api-green) is chosen by the HOST, not baked into the image: deploy/bluegreen.sh
# writes /srv/motres/web/upstream/api.conf (one line, `server api-green:8001;`) atomically and runs `nginx -s reload` in
# this container.  This hook only decides, at container start, where the include points:
#   * the host file exists  -> include it (every later switch is a file write + a reload, no container restart);
#   * it does not           -> the single-API layout of stage 1 / a standalone image: `server api:8001;`.
if [ -s /srv/web/upstream/api.conf ]; then
  echo "include /srv/web/upstream/api.conf;" > /etc/nginx/api-upstream.conf
else
  echo "server api:8001;" > /etc/nginx/api-upstream.conf
fi
