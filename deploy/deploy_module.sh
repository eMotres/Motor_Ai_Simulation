#!/bin/bash
# Module-wise deploy (owner 2026-10-18: after the launch bugs are fixed continuously - restart only what changed, and
# only when the modules it touches are idle; never kill a running job).  STAGE 2 (docs/DEPLOY_STAGE2.md): three parts.
#
#   bash deploy_module.sh <SHA> <PREV> <MODE> [MODULES] [MAX_WAIT_MIN]
#     the source tree is /tmp/deploy_main.tar (git archive of SHA); PREV must be the deployed commit
#     MODE    web    - only the pages (published, the web container is not restarted unless its nginx setup changed)
#             api    - only the API: blue-green - the idle colour is built into a container, warmed up, nginx is switched,
#                      the old colour drains and stops.  Nobody waits, nothing is restarted in place, no 502.
#             worker - only the compute worker: drain, wait until it is idle, restart (never while a job runs)
#             webapi - web + api            back   - api + worker        full - web + api + worker
#     MODULES comma list of live_status modules the WORKER change touches ("all" = any): the worker restart waits until
#             none of them has a user or a request and no job is queued or running anywhere
#     MAX_WAIT_MIN  how long to wait for the worker to be idle (default 20); after that, while no job runs, the users see
#             "a short update in 2 minutes" and the restart follows; with a job running the worker is NOT restarted:
#             the API and the pages are already deployed, the script ends with WORKER PENDING (exit 7) and the worker part
#             is repeated later with  deploy_module.sh <SHA> <SHA> worker  (the image is already built).
#
# Images are built while the old containers keep serving: deploy-api:<SHA> (api + worker, one image) and deploy-web.
# The previous API colour stays tagged and stopped for the rollback; deploy-web:prev for the pages.
#
# RUN IT DETACHED (an SSH drop must not cut it):  setsid nohup bash deploy_module.sh ... > /root/deploy.log 2>&1 &  and tail -f the log.
# SIGHUP is ignored; SIGINT / SIGTERM end the script, and an exit trap lifts the worker's drain if the script ends (for any
# reason) between `drain on` and the end of the worker restart - a worker must never be left refusing new jobs.
#
# Exit codes: 0 done, 2 usage, 3 PREV is not the deployed commit (or no cutover yet), 5 build / start failed (nothing
# switched), 6 a check failed after the switch (rolled back), 7 api/web done, worker pending.
set -uo pipefail
SHA=${1:?sha}; PREV=${2:?prev}; MODE=${3:?mode}; MODULES=${4:-all}; MAXWAIT=${5:-20}
APP=${MOTRES_APP:-/opt/motres/app}
RUN=${MOTRES_RUN:-/srv/motres/run}
WEB=${MOTRES_WEB:-/srv/motres/web}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BG="bash $HERE/bluegreen.sh"
PYTHON=${PYTHON:-python3}
CURL=${CURL:-curl}
DOCKER=${DOCKER:-docker}
nap() { sleep "${MOTRES_POLL_S:-$1}"; }          # (tests set MOTRES_POLL_S=0)
DRAINED=0
undrain() {   # exit trap: if this script ends while it holds the worker drained, open the door again
  if [ "$DRAINED" = 1 ]; then
    DRAINED=0
    echo "$(date +%T) leaving with the worker drained - lifting the drain"
    "$DOCKER" exec deploy-worker-1 python -m motor_ai_sim.worker drain off >/dev/null 2>&1 \
      || echo "WARNING: could not lift the worker drain - run: bash $HERE/bluegreen.sh exec worker python -m motor_ai_sim.worker drain off"
  fi
}
trap '' HUP
trap 'echo "SIGINT received" >&2; exit 130' INT
trap 'echo "SIGTERM received" >&2; exit 143' TERM
trap undrain EXIT
TARBALL=${MOTRES_TARBALL:-/tmp/deploy_main.tar}
case "$MODE" in
  web)    WANT_WEB=1 WANT_API=0 WANT_WORKER=0;;
  api)    WANT_WEB=0 WANT_API=1 WANT_WORKER=0;;
  webapi) WANT_WEB=1 WANT_API=1 WANT_WORKER=0;;
  worker) WANT_WEB=0 WANT_API=0 WANT_WORKER=1;;
  back)   WANT_WEB=0 WANT_API=1 WANT_WORKER=1;;
  full)   WANT_WEB=1 WANT_API=1 WANT_WORKER=1;;
  *) echo "MODE must be web|api|webapi|worker|back|full"; exit 2;;
esac
[ -f "$RUN/state.env" ] || { echo "ABORT: no $RUN/state.env - the stage-2 cutover (deploy/cutover_stage2.sh) has not been done"; exit 3; }
# shellcheck disable=SC1090
. "$RUN/state.env"
[ "$MODE" = worker ] && [ "$PREV" = "$SHA" ] || [ "$(cat $APP/.deployed_commit)" = "$PREV" ] \
  || { echo "ABORT commit: deployed $(cat $APP/.deployed_commit) != $PREV"; exit 3; }

# --- probes through the ACTIVE API colour (its /api/admin/live merges the worker's jobs and requests) --------------------
ctr_api() { echo "deploy-api-$ACTIVE-1"; }
api_get() { "$DOCKER" exec "$(ctr_api)" python -c "import urllib.request as u;print(u.urlopen('http://127.0.0.1:8001$1',timeout=10).read().decode())"; }
api_call() { "$DOCKER" exec "$(ctr_api)" python -c "import json,urllib.request as u;r=u.Request('http://127.0.0.1:8001$2',data=json.dumps($3).encode(),method='$1',headers={'Content-Type':'application/json'});u.urlopen(r,timeout=10)"; }
busy() {   # prints what is busy; exit 0 = busy
  api_get /api/admin/live | "$PYTHON" -c "
import json, sys
d = json.load(sys.stdin); mods = sys.argv[1].split(','); out = []
j = d['jobs']
if j['running'] or j['queued']: out.append('jobs running %d queued %d' % (j['running'], j['queued']))
for n, m in d['modules'].items():
    if ('all' in mods or n in mods) and (m['users'] or m['requests']): out.append('%s users %d requests %d' % (n, m['users'], m['requests']))
w = d.get('worker') or {}
if w.get('reachable') is False: out.append('worker not reachable')
print('; '.join(out)); sys.exit(0 if out else 1)" "$MODULES"
}
jobs_running() { api_get /api/admin/live | "$PYTHON" -c "import json,sys; j=json.load(sys.stdin)['jobs']; sys.exit(0 if (j['running'] or j['queued']) else 1)"; }
worker_exec() { "$DOCKER" exec deploy-worker-1 "$@"; }
worker_health() { "$DOCKER" exec deploy-worker-1 curl -s -m 5 http://127.0.0.1:8002/api/health 2>/dev/null; }
worker_build() { worker_health | "$PYTHON" -c "import json,sys; print(json.load(sys.stdin).get('build',''))" 2>/dev/null; }

have_image() { "$DOCKER" image inspect "deploy-api:$SHA" >/dev/null 2>&1; }

# 1) unpack next to the running tree and build the new images while the old containers serve.
#    MODE=worker is only the FOLLOW-UP of a deploy whose API part is already done (the image exists, the tree is deployed).
NEED_TREE=1; { [ "$MODE" = worker ] && have_image; } && NEED_TREE=0
[ "$MODE" = worker ] && [ $NEED_TREE = 1 ] && { echo "ABORT: MODE=worker is the follow-up of an api/back/full deploy - image deploy-api:$SHA does not exist yet"; exit 5; }
if [ $NEED_TREE = 1 ]; then
  rm -rf $APP.new && mkdir $APP.new && tar -x -C $APP.new -f "$TARBALL" || { echo "ABORT unpack"; exit 5; }
  for f in deploy/.env web/.env web/.env.local config/end_effect_passports.json; do [ -f $APP/$f ] && cp -p $APP/$f $APP.new/$f; done
  [ -d $APP/src/cadquery_cache ] && cp -a $APP/src/cadquery_cache $APP.new/src/
  sed -i "s/\r$//" $APP.new/deploy/*.sh 2>/dev/null
  if [ $WANT_WEB = 1 ]; then
    "$DOCKER" image inspect deploy-web:latest >/dev/null 2>&1 && "$DOCKER" tag deploy-web:latest deploy-web:prev
    (cd $APP.new/deploy && BUILD_ID=$SHA "$DOCKER" compose build web 2>&1 | tail -3) || { echo "BUILD FAIL (web) - nothing switched"; exit 5; }
  fi
  if [ $WANT_API = 1 ] || [ $WANT_WORKER = 1 ]; then
    if have_image; then echo "image deploy-api:$SHA already built"; else
      (cd $APP.new/deploy && BUILD_SHA=$SHA "$DOCKER" compose build api-image 2>&1 | tail -3) && have_image \
        || { echo "BUILD FAIL (api image) - nothing switched"; exit 5; }
    fi
    "$DOCKER" tag "deploy-api:$SHA" deploy-api:latest
  fi
  rm -rf $APP.prev && mv $APP $APP.prev && mv $APP.new $APP
fi
restore_tree() { [ $NEED_TREE = 1 ] && [ -d $APP.prev ] && { rm -rf $APP.failed && mv $APP $APP.failed && mv $APP.prev $APP; }; }

# 2) THE API: blue-green.  No idle wait - the old colour keeps serving until the new one is warm, then drains.
if [ $WANT_API = 1 ]; then
  $BG start-idle "$SHA" || { echo "ABORT: the new API colour did not come up - nothing switched"; restore_tree; exit 5; }
  $BG switch || { echo "ABORT: switch failed - the live colour is unchanged"; $BG stop-idle >/dev/null 2>&1; restore_tree; exit 6; }
  . "$RUN/state.env"
  # post-switch checks through the public edge; any failure = back to the old colour (still running, so instant)
  ok=0
  for i in $(seq 1 12); do s=$("$CURL" -s https://aerostator.com/api/health) && echo "$s" | grep -q healthy && { ok=1; break; }; nap 5; done
  if [ $ok = 0 ] || [ "$("$CURL" -s https://aerostator.com/api/health | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin).get("instance",""))' 2>/dev/null)" != "$ACTIVE" ]; then
    "$DOCKER" logs --tail 15 "$(ctr_api)" 2>&1
    $BG rollback; echo "ROLLBACK of the API colour done"; restore_tree; exit 6
  fi
  api_call DELETE /api/admin/notice "{}" 2>/dev/null || true
  # the old colour finishes the requests it has (never killed), then stops
  if $BG drain-old 30 "${DRAIN_MAX_S:-1800}"; then $BG stop-old; else echo "NOTE: the old API colour still serves a request - left running; later: bash $HERE/bluegreen.sh drain-old && bash $HERE/bluegreen.sh stop-old"; fi
  systemctl is-enabled motres-cad-rebuild >/dev/null 2>&1 && systemctl restart motres-cad-rebuild
  echo "$SHA" > $APP/.deployed_commit
  . "$RUN/state.env"; echo "api: api-$ACTIVE is live on $SHA"
fi

# 3) THE PAGES.  Published, not restarted: the new image's bundle is copied to /srv/motres/web/builds/<sha> (with the
#    previous builds' hashed assets hard-linked in, so pages already open still load their lazy chunks) and the symlink
#    /srv/motres/web/current is switched atomically - the web container keeps serving without a pause.  The web container
#    itself is recreated only when its nginx setup changed (or it does not serve the builds yet).
PREV_WEB=$(readlink $WEB/current 2>/dev/null || true)
publish_web() {
  mkdir -p $WEB/builds
  local B=$WEB/builds/$SHA cid
  rm -rf $B.tmp
  cid=$("$DOCKER" create deploy-web:latest) && "$DOCKER" cp $cid:/usr/share/nginx/html $B.tmp && "$DOCKER" rm $cid >/dev/null || return 1
  [ -n "$PREV_WEB" ] && [ -d "$WEB/$PREV_WEB/assets" ] && cp -aln "$WEB/$PREV_WEB/assets/." $B.tmp/assets/ 2>/dev/null
  rm -rf $B && mv $B.tmp $B
  ln -sfn builds/$SHA $WEB/current.new && mv -T $WEB/current.new $WEB/current
  ls -1dt $WEB/builds/*/ 2>/dev/null | tail -n +11 | xargs -r rm -rf          # keep the last 10 builds
}
web_conf_sig() { md5sum /etc/nginx/conf.d/default.conf /docker-entrypoint.d/16-webroot.sh /docker-entrypoint.d/17-upstream.sh 2>/dev/null | cut -c1-32 | tr -d '\n'; }
web_rollback() {
  echo "ROLLBACK of the pages to $PREV"
  "$DOCKER" image inspect deploy-web:prev >/dev/null 2>&1 && "$DOCKER" tag deploy-web:prev deploy-web:latest
  [ -n "$PREV_WEB" ] && ln -sfn $PREV_WEB $WEB/current.new && mv -T $WEB/current.new $WEB/current
  (cd $APP/deploy && "$DOCKER" compose up -d --no-build --no-deps web 2>&1 | tail -2)
}
if [ $WANT_WEB = 1 ]; then
  publish_web || { echo "web publish failed"; }
  new_sig=$("$DOCKER" run --rm --entrypoint sh deploy-web:latest -c "$(declare -f web_conf_sig); web_conf_sig")
  old_sig=$("$DOCKER" exec deploy-web-1 sh -c "$(declare -f web_conf_sig); web_conf_sig" 2>/dev/null || true)
  if [ "$new_sig" != "$old_sig" ] || ! "$DOCKER" exec deploy-web-1 grep -q /srv/web/current /etc/nginx/webroot.conf 2>/dev/null; then
    echo "web container recreated (nginx setup changed)"; (cd $APP/deploy && "$DOCKER" compose up -d --no-build --no-deps web 2>&1 | tail -2)
  else
    echo "web published without restart: current -> $(readlink $WEB/current)"
  fi
  code=$("$CURL" -s -o /dev/null -w '%{http_code}' https://aerostator.com/build.json)
  [ "$code" = 200 ] || { echo "web check failed ($code)"; web_rollback; exit 6; }
  [ $WANT_API = 0 ] && echo "$SHA" > $APP/.deployed_commit
  echo "pages: $("$CURL" -s https://aerostator.com/build.json)"
fi

# 4) THE WORKER.  Restarted only when idle: wait for idle, drain (no new job), look again, restart, verify.
#    The "who is on the site" half of the idle check lives in the API's memory (tab heartbeats, about one a minute), so a
#    freshly switched API colour knows nobody yet: give it the time to hear the open tabs before trusting an empty answer.
settle() {
  local left
  left=$(( ${SWITCHED_AT:-0} + ${API_SETTLE_S:-75} - $(date +%s) ))
  [ "$left" -gt 0 ] && { echo "waiting ${left}s: the new API colour has not heard the open tabs yet"; nap "$left"; }
  return 0
}
worker_pending() {
  . "$RUN/state.env"; echo "$WORKER_SHA" > $APP/.worker_commit          # plan_deploy's 3rd argument: what the worker really runs
  echo "WORKER PENDING: api/pages are on $SHA, the worker still runs ${WORKER_SHA:-?}.  Repeat when it is idle:  bash $HERE/deploy_module.sh $SHA $SHA worker $MODULES"
  exit 7
}
if [ $WANT_WORKER = 1 ]; then
  . "$RUN/state.env"
  OLD_WORKER=$WORKER_SHA
  settle
  if [ "$OLD_WORKER" = "$SHA" ] && [ "$(worker_build)" = "$SHA" ]; then echo "worker already runs $SHA"; else
    start=$(date +%s); announced=0; deadline=$(( $(date +%s) + MAXWAIT * 60 * 3 + 600 ))
    # a worker that is not running at all (crashed, stopped) has nothing to wait for or drain: just start it on the new image
    [ -z "$(worker_health)" ] && { echo "the worker is not running - starting it on $SHA"; deadline=0; }
    while [ "$deadline" != 0 ]; do
      while b=$(busy); do
        waited=$(( ($(date +%s) - start) / 60 ))
        echo "$(date +%T) worker busy: $b"
        if [ $waited -ge $MAXWAIT ]; then
          if jobs_running; then
            [ $waited -ge $((MAXWAIT * 3)) ] && worker_pending
          elif [ $announced = 0 ]; then
            api_call PUT /api/admin/notice "{'message': {'en': 'AeroStator will be updated in 2 minutes - a short pause of about one minute.', 'zh': 'AeroStator 将在 2 分钟后更新，约暂停 1 分钟。'}, 'minutes': 10}"
            announced=1; echo "$(date +%T) notice shown, restarting in 2 min"; nap 120; break
          fi
        fi
        nap 20
      done
      # idle now: close the door for NEW jobs, then look again - a job that slipped in while we were deciding is waited for
      worker_exec python -m motor_ai_sim.worker drain on >/dev/null || { echo "ABORT: cannot reach the worker to drain it"; worker_pending; }
      DRAINED=1
      nap 2
      if worker_exec python -m motor_ai_sim.worker idle; then break; fi
      echo "$(date +%T) a request or job arrived while draining - waiting again"
      worker_exec python -m motor_ai_sim.worker drain off >/dev/null
      DRAINED=0
      [ "$(date +%s)" -ge "$deadline" ] && worker_pending
      nap 5
    done
    echo "worker is idle and drained - restarting it on $SHA"
    sed -i "s/^WORKER_SHA=.*/WORKER_SHA=$SHA/" "$RUN/state.env"
    (. "$RUN/state.env"; export BLUE_SHA GREEN_SHA WORKER_SHA; cd $APP/deploy && "$DOCKER" compose up -d --no-build --no-deps worker 2>&1 | tail -3)
    ok=0
    for i in $(seq 1 48); do [ "$(worker_build)" = "$SHA" ] && { ok=1; break; }; nap 5; done
    if [ $ok = 0 ]; then
      "$DOCKER" logs --tail 15 deploy-worker-1 2>&1
      echo "ROLLBACK of the worker to $OLD_WORKER"
      sed -i "s/^WORKER_SHA=.*/WORKER_SHA=$OLD_WORKER/" "$RUN/state.env"
      (. "$RUN/state.env"; export BLUE_SHA GREEN_SHA WORKER_SHA; cd $APP/deploy && "$DOCKER" compose up -d --no-build --no-deps worker 2>&1 | tail -2)
      DRAINED=0                  # (the rolled-back worker is a fresh process: no drain flag)
      exit 6
    fi
    DRAINED=0                    # (the new worker is a fresh process: no drain flag)
    api_call DELETE /api/admin/notice "{}" 2>/dev/null || true
    echo "worker: running $SHA"
  fi
fi
. "$RUN/state.env"; echo "$WORKER_SHA" > $APP/.worker_commit       # what the worker really runs (plan_deploy's 3rd argument)
echo "deployed $SHA mode=$MODE"; $BG state

# the node agent ships with the tree (no restart of the app containers involved)
if [ $NEED_TREE = 1 ] && [ -d /opt/motres-node-agent ]; then
  sed -i "s/\r$//" $APP/deploy/node-agent/*.sh $APP/deploy/node-agent/*.py 2>/dev/null
  install -m 0755 $APP/deploy/node-agent/motres_node_agent.py /opt/motres-node-agent/motres_node_agent.py && systemctl restart motres-node-agent
fi
