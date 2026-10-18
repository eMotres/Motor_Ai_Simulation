#!/bin/bash
# Module-wise deploy (owner 2026-10-18: after the launch bugs are fixed continuously - restart only what changed, and
# only when the modules it touches are idle; never kill a running job).
#
#   bash deploy_module.sh <SHA> <PREV> <MODE> [MODULES] [MAX_WAIT_MIN]
#     the source tree is /tmp/deploy_main.tar (git archive of SHA); PREV must be the deployed commit
#     MODE    web  - only the web container (pages); the API, its users' requests and the jobs are not touched
#             api  - only the API container
#             full - both
#     MODULES comma list of modules the backend change touches (live_status.MODULE_PREFIXES names, "all" = any);
#             the API restart waits until none of them has a user or a request and no job is queued or running
#     MAX_WAIT_MIN  how long to wait for idleness (default 20); after that, while no job runs, the users see
#             "a short update in 2 minutes" and the restart follows; with a job running it never restarts (ABORT 4)
#
# Images are built while the old containers keep serving; the previous images stay tagged :prev for the rollback.
set -uo pipefail
SHA=$1; PREV=$2; MODE=$3; MODULES=${4:-all}; MAXWAIT=${5:-20}
APP=/opt/motres/app
case "$MODE" in web|api|full) ;; *) echo "MODE must be web|api|full"; exit 2;; esac
[ "$(cat $APP/.deployed_commit)" = "$PREV" ] || { echo "ABORT commit: deployed $(cat $APP/.deployed_commit) != $PREV"; exit 3; }

api_get() { docker exec deploy-api-1 python -c "import urllib.request as u;print(u.urlopen('http://127.0.0.1:8001$1',timeout=10).read().decode())"; }
api_call() { docker exec deploy-api-1 python -c "import json,urllib.request as u;r=u.Request('http://127.0.0.1:8001$2',data=json.dumps($3).encode(),method='$1',headers={'Content-Type':'application/json'});u.urlopen(r,timeout=10)"; }
busy() {   # prints what is busy; exit 0 = busy
  api_get /api/admin/live | python3 -c "
import json, sys
d = json.load(sys.stdin); mods = sys.argv[1].split(','); out = []
j = d['jobs']
if j['running'] or j['queued']: out.append('jobs running %d queued %d' % (j['running'], j['queued']))
for n, m in d['modules'].items():
    if ('all' in mods or n in mods) and (m['users'] or m['requests']): out.append('%s users %d requests %d' % (n, m['users'], m['requests']))
print('; '.join(out)); sys.exit(0 if out else 1)" "$MODULES"
}
jobs_running() { api_get /api/admin/live | python3 -c "import json,sys; j=json.load(sys.stdin)['jobs']; sys.exit(0 if (j['running'] or j['queued']) else 1)"; }

# 1) unpack next to the running tree and build the new images while the old containers serve
rm -rf $APP.new && mkdir $APP.new && tar -x -C $APP.new -f /tmp/deploy_main.tar || { echo "ABORT unpack"; exit 5; }
for f in deploy/.env web/.env web/.env.local config/end_effect_passports.json; do [ -f $APP/$f ] && cp -p $APP/$f $APP.new/$f; done
[ -d $APP/src/cadquery_cache ] && cp -a $APP/src/cadquery_cache $APP.new/src/
SERVICES=""; [ "$MODE" != api ] && SERVICES="$SERVICES web"; [ "$MODE" != web ] && SERVICES="$SERVICES api"
for s in $SERVICES; do docker image inspect deploy-$s:latest >/dev/null 2>&1 && docker tag deploy-$s:latest deploy-$s:prev; done
export BUILD_ID=$SHA
(cd $APP.new/deploy && docker compose build $SERVICES 2>&1 | tail -3) || { echo "BUILD FAIL - nothing switched"; exit 5; }

# 2) the API restart waits for its modules to be idle (the web switch needs no wait: pages are static files)
if [ "$MODE" != web ]; then
  start=$(date +%s); announced=0
  while b=$(busy); do
    waited=$(( ($(date +%s) - start) / 60 ))
    echo "$(date +%T) busy: $b"
    if [ $waited -ge $MAXWAIT ]; then
      if jobs_running; then
        [ $waited -ge $((MAXWAIT * 3)) ] && { echo "ABORT 4: a job is still running after $waited min - nothing switched"; exit 4; }
      elif [ $announced = 0 ]; then
        api_call PUT /api/admin/notice "{'message': {'en': 'AeroStator will be updated in 2 minutes - a short pause of about one minute.', 'zh': 'AeroStator 将在 2 分钟后更新，约暂停 1 分钟。'}, 'minutes': 10}"
        announced=1; echo "$(date +%T) notice shown, restarting in 2 min"; sleep 120; break
      fi
    fi
    sleep 20
  done
fi

# 3) switch: only the services of this MODE are recreated
rm -rf $APP.prev && mv $APP $APP.prev && mv $APP.new $APP
(cd $APP/deploy && docker compose up -d --no-build --no-deps $SERVICES 2>&1 | tail -3)
echo "$SHA" > $APP/.deployed_commit

rollback() {
  echo "ROLLBACK to $PREV"
  for s in $SERVICES; do docker image inspect deploy-$s:prev >/dev/null 2>&1 && docker tag deploy-$s:prev deploy-$s:latest; done
  rm -rf $APP.failed && mv $APP $APP.failed && mv $APP.prev $APP
  (cd $APP/deploy && docker compose up -d --no-build --no-deps $SERVICES 2>&1 | tail -2)
  echo "$PREV" > $APP/.deployed_commit
}
if [ "$MODE" != web ]; then
  for i in $(seq 1 60); do s=$(curl -s https://aerostator.com/api/health) && echo "$s" | grep -q healthy && break; sleep 5; done
  echo "$s" | grep -q healthy || { docker logs --tail 15 deploy-api-1 2>&1; rollback; exit 6; }
  api_call DELETE /api/admin/notice "{}" 2>/dev/null || true
  systemctl is-enabled motres-cad-rebuild >/dev/null 2>&1 && systemctl restart motres-cad-rebuild
fi
code=$(curl -s -o /dev/null -w '%{http_code}' https://aerostator.com/build.json)
[ "$code" = 200 ] || { echo "web check failed ($code)"; rollback; exit 6; }
echo "deployed $SHA mode=$MODE services=$SERVICES build=$(curl -s https://aerostator.com/build.json)"

# the node agent ships with the tree (no restart of the app containers involved)
sed -i "s/\r$//" $APP/deploy/node-agent/*.sh $APP/deploy/node-agent/*.py 2>/dev/null
install -m 0755 $APP/deploy/node-agent/motres_node_agent.py /opt/motres-node-agent/motres_node_agent.py && systemctl restart motres-node-agent
