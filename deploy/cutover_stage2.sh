#!/bin/bash
# THE ONE-TIME CUTOVER from the single `api` service (stage 1) to  web + api-blue/api-green + worker  (stage 2).
# docs/DEPLOY_STAGE2.md, "Cutover runbook", has the same steps with the reasons.  Run on the server as root.
#
#   bash cutover_stage2.sh --check <SHA> <PREV>        read-only preflight; changes nothing
#   bash cutover_stage2.sh --go    <SHA> <PREV> [MAX_WAIT_MIN]
#   bash cutover_stage2.sh --rollback                  put stage 1 back (works until the old container was removed;
#                                                      afterwards it re-creates it from the saved image)
#
#   <SHA>   the commit being deployed (/tmp/deploy_main.tar is its `git archive`);  <PREV> the commit now deployed.
#
# What --go does, in order (each step prints what it did; any failure before step 8 leaves stage 1 serving):
#   1  preflight (same checks as --check)             2  unpack + build the images while stage 1 serves
#   3  wait until the OLD api is idle (never kills a job; ABORT 4 after MAX_WAIT_MIN x 3)
#   4  swap the source tree; write state.env + the upstream file (api-blue)
#   5  start the worker and api-blue next to the old api; wait healthy; warm up
#   6  recreate the web container with the new nginx.conf  <- the ONE blip (2-3 s of 502 on the edge); traffic now flows to
#      the new stack; the old api receives nothing
#   7  verify through the public edge (health, a worker route, an API route, the pages)
#   8  drain the old api (its in-flight requests and jobs finish), stop and remove it (image kept as deploy-api:stage1)
#   9  publish the pages of <SHA> through the normal path (deploy_module.sh <SHA> <SHA> web)
#
# Testable without a server: DOCKER, MOTRES_APP, MOTRES_RUN, MOTRES_WEB, MOTRES_EDGE, PYTHON override the defaults.
set -uo pipefail
APP=${MOTRES_APP:-/opt/motres/app}
RUN=${MOTRES_RUN:-/srv/motres/run}
WEB=${MOTRES_WEB:-/srv/motres/web}
DOCKER=${DOCKER:-docker}
PYTHON=${PYTHON:-python3}
EDGE=${MOTRES_EDGE:-http://127.0.0.1:8080}
PUBLIC=${MOTRES_PUBLIC:-https://aerostator.com}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BG="bash $HERE/bluegreen.sh"
OLD=${MOTRES_OLD_API:-deploy-api-1}
CURL=${CURL:-curl}
TARBALL=${MOTRES_TARBALL:-/tmp/deploy_main.tar}
API_ENV=${MOTRES_API_ENV:-/etc/motres/api.env}
STATE_ROOT=${MOTRES_STATE_ROOT:-/srv/motres}
nap() { sleep "${MOTRES_POLL_S:-$1}"; }          # (tests set MOTRES_POLL_S=0)

die()  { echo "CUTOVER: $*" >&2; exit "${2:-1}"; }
note() { echo "$(date +%T) $*"; }
json_get() { "$PYTHON" -c "import json,sys
try:
    d = json.load(sys.stdin)
    for k in sys.argv[1].split('.'):
        d = d[k]
    print(d)
except Exception:
    print('')" "$1"; }
old_get() { "$DOCKER" exec "$OLD" curl -s -m 10 "http://127.0.0.1:8001$1"; }

preflight() {   # <SHA> <PREV>
  local sha=$1 prev=$2 bad=0 mem disk
  [ "${MOTRES_UID:-$(id -u)}" = 0 ] || { echo "FAIL not root"; bad=1; }
  "$DOCKER" compose version >/dev/null 2>&1 || { echo "FAIL docker compose missing"; bad=1; }
  [ "$(cat $APP/.deployed_commit 2>/dev/null)" = "$prev" ] || { echo "FAIL deployed commit is '$(cat $APP/.deployed_commit 2>/dev/null)', not $prev"; bad=1; }
  [ -f "$TARBALL" ] || { echo "FAIL $TARBALL (git archive of $sha) is missing"; bad=1; }
  [ -f "$API_ENV" ] || { echo "FAIL $API_ENV is missing"; bad=1; }
  grep -q '^AUTH_SECRET=.\+' "$API_ENV" 2>/dev/null || echo "WARN AUTH_SECRET is not set in api.env (the shared identity secret file is used; fine, but set it explicitly)"
  "$DOCKER" ps --format '{{.Names}}' | grep -qx "$OLD" || { echo "FAIL the old api container $OLD is not running"; bad=1; }
  for n in deploy-worker-1 deploy-api-blue-1 deploy-api-green-1; do
    "$DOCKER" ps -a --format '{{.Names}}' | grep -qx "$n" && { echo "FAIL container $n already exists (a half-done cutover? see --rollback)"; bad=1; }
  done
  [ -f "$RUN/state.env" ] && { echo "FAIL $RUN/state.env exists - the cutover was already done"; bad=1; }
  if [ -z "${MOTRES_SKIP_RESOURCE_CHECKS:-}" ]; then
    mem=$(awk '/MemAvailable/ {printf "%d", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo 0)
    [ "${mem:-0}" -ge 12 ] || { echo "FAIL only ${mem:-?} GB memory available - need 12 GB free for the worker + two API colours to coexist briefly"; bad=1; }
    disk=$(df -BG --output=avail /var/lib/docker 2>/dev/null | tail -1 | tr -dc '0-9')
    [ "${disk:-0}" -ge 8 ] || { echo "FAIL only ${disk:-?} GB free under /var/lib/docker (two images + build cache)"; bad=1; }
  fi
  [ -d "$STATE_ROOT/workspaces" ] && [ -d "$STATE_ROOT/identity" ] || { echo "FAIL $STATE_ROOT/{workspaces,identity} missing"; bad=1; }
  echo "old api: $(old_get /api/admin/live | json_get requests_total) request(s), jobs $(old_get /api/admin/live | json_get jobs.running) running / $(old_get /api/admin/live | json_get jobs.queued) queued"
  return $bad
}

busy_old() {   # prints what is busy; 0 = busy
  old_get /api/admin/live | "$PYTHON" -c "
import json, sys
d = json.load(sys.stdin); out = []
j = d['jobs']
if j['running'] or j['queued']: out.append('jobs running %d queued %d' % (j['running'], j['queued']))
for n, m in d['modules'].items():
    if m['users'] or m['requests']: out.append('%s users %d requests %d' % (n, m['users'], m['requests']))
print('; '.join(out)); sys.exit(0 if out else 1)"
}
jobs_old() { old_get /api/admin/live | "$PYTHON" -c "import json,sys; j=json.load(sys.stdin)['jobs']; sys.exit(0 if (j['running'] or j['queued']) else 1)"; }
notice_old() { "$DOCKER" exec "$OLD" python -c "import json,urllib.request as u;r=u.Request('http://127.0.0.1:8001/api/admin/notice',data=json.dumps($1).encode(),method='$2',headers={'Content-Type':'application/json'});u.urlopen(r,timeout=10)"; }

wait_worker() {  # <sha>
  local i got
  for i in $(seq 1 60); do
    got=$("$DOCKER" exec deploy-worker-1 curl -s -m 5 http://127.0.0.1:8002/api/health 2>/dev/null)
    echo "$got" | grep -q '"healthy"' && [ "$(echo "$got" | json_get build)" = "$1" ] && [ "$(echo "$got" | json_get role)" = worker ] && return 0
    nap 5
  done
  return 1
}

do_rollback() {
  note "ROLLBACK to stage 1"
  local old=$APP.prev
  [ -d $APP.stage1 ] && old=$APP.stage1          # kept by step 9, which replaces $APP.prev
  if [ -d "$old" ]; then rm -rf $APP.stage2 && mv $APP $APP.stage2 && mv "$old" $APP; fi
  if ! "$DOCKER" ps --format '{{.Names}}' | grep -qx "$OLD"; then
    "$DOCKER" image inspect deploy-api:stage1 >/dev/null 2>&1 && "$DOCKER" tag deploy-api:stage1 deploy-api:latest
    (cd $APP/deploy && "$DOCKER" compose up -d --no-build --no-deps api 2>&1 | tail -2)
  fi
  # the stage-1 web image (its nginx.conf has no worker location and proxies `api:8001`); step 2 saved it as deploy-web:prev
  "$DOCKER" image inspect deploy-web:prev >/dev/null 2>&1 && "$DOCKER" tag deploy-web:prev deploy-web:latest
  (cd $APP/deploy && "$DOCKER" compose up -d --no-build --no-deps web 2>&1 | tail -2)
  "$DOCKER" stop deploy-worker-1 deploy-api-blue-1 deploy-api-green-1 >/dev/null 2>&1 || true
  rm -f "$RUN/state.env" "$WEB/upstream/api.conf"
  note "stage 1 is serving again (the stage-2 containers are stopped; remove them with: docker rm deploy-worker-1 deploy-api-blue-1)"
}

mode=${1:-}; SHA=${2:-}; PREV=${3:-}; MAXWAIT=${4:-20}
case "$mode" in
  --check)
    [ -n "$SHA" ] && [ -n "$PREV" ] || die "usage: --check <SHA> <PREV>" 2
    preflight "$SHA" "$PREV" && echo "PREFLIGHT OK" || { echo "PREFLIGHT FAILED"; exit 1; }
    ;;
  --rollback)
    do_rollback
    ;;
  --go)
    [ -n "$SHA" ] && [ -n "$PREV" ] || die "usage: --go <SHA> <PREV> [MAX_WAIT_MIN]" 2
    note "1/8 preflight"; preflight "$SHA" "$PREV" || die "preflight failed - nothing changed" 1

    note "2/8 unpack and build (stage 1 keeps serving)"
    "$DOCKER" tag deploy-api:latest deploy-api:stage1 2>/dev/null || true         # the rollback image
    rm -rf $APP.new && mkdir $APP.new && tar -x -C $APP.new -f "$TARBALL" || die "unpack failed - nothing changed" 5
    for f in deploy/.env web/.env web/.env.local config/end_effect_passports.json; do [ -f $APP/$f ] && cp -p $APP/$f $APP.new/$f; done
    [ -d $APP/src/cadquery_cache ] && cp -a $APP/src/cadquery_cache $APP.new/src/
    sed -i "s/\r$//" $APP.new/deploy/*.sh 2>/dev/null
    "$DOCKER" image inspect deploy-web:latest >/dev/null 2>&1 && "$DOCKER" tag deploy-web:latest deploy-web:prev
    (cd $APP.new/deploy && BUILD_SHA=$SHA "$DOCKER" compose build api-image 2>&1 | tail -3) && "$DOCKER" image inspect "deploy-api:$SHA" >/dev/null 2>&1 \
      || die "api image build failed - nothing changed" 5
    (cd $APP.new/deploy && BUILD_ID=$SHA "$DOCKER" compose build web 2>&1 | tail -3) || die "web image build failed - nothing changed" 5
    (cd $APP.new/deploy && BUILD_SHA=$SHA BLUE_SHA=$SHA GREEN_SHA=$SHA WORKER_SHA=$SHA "$DOCKER" compose --profile stack config -q) \
      || die "the new compose file does not validate - nothing changed" 5

    # the nginx setup of the NEW web image must parse before anything is switched (the baked default upstream is `api:8001`,
    # which a plain `docker run` cannot resolve - the host entry stands in for it; the worker is resolved per request)
    "$DOCKER" run --rm --add-host api:127.0.0.1 --entrypoint nginx deploy-web:latest -t 2>&1 | tail -3
    "$DOCKER" run --rm --add-host api:127.0.0.1 --entrypoint nginx deploy-web:latest -t >/dev/null 2>&1 \
      || die "nginx -t fails on the new web image - nothing changed" 5

    note "3/8 wait for the old api to be idle (a running job is never killed)"
    start=$(date +%s); announced=0
    while b=$(busy_old); do
      waited=$(( ($(date +%s) - start) / 60 ))
      echo "$(date +%T) busy: $b"
      if [ $waited -ge $MAXWAIT ]; then
        if jobs_old; then
          [ $waited -ge $((MAXWAIT * 3)) ] && die "ABORT 4: a job is still running after $waited min - nothing switched" 4
        elif [ $announced = 0 ]; then
          notice_old "{'message': {'en': 'AeroStator will be updated in 2 minutes - a short pause of about one minute.', 'zh': 'AeroStator 将在 2 分钟后更新，约暂停 1 分钟。'}, 'minutes': 10}" PUT
          announced=1; note "notice shown, going on in 2 min"; nap 120; break
        fi
      fi
      nap 20
    done

    note "4/8 swap the tree, write the state"
    mkdir -p "$RUN" "$WEB/upstream"
    rm -rf $APP.prev && mv $APP $APP.prev && mv $APP.new $APP
    $BG init "$SHA" || { do_rollback; die "init failed" 5; }

    note "5/8 start the worker and api-blue next to the old api"
    (cd $APP/deploy && BLUE_SHA=$SHA GREEN_SHA= WORKER_SHA=$SHA "$DOCKER" compose up -d --no-build --no-deps worker 2>&1 | tail -3)
    wait_worker "$SHA" || { "$DOCKER" logs --tail 20 deploy-worker-1 2>&1; do_rollback; die "the worker did not become healthy - rolled back" 5; }
    $BG start-active || { do_rollback; die "api-blue did not come up - rolled back" 5; }

    note "6/8 recreate the web container (the new nginx.conf): expect 2-3 s of 502 on the edge"
    (cd $APP/deploy && BUILD_ID=$SHA "$DOCKER" compose up -d --no-build --no-deps web 2>&1 | tail -2)
    nap 5

    note "7/8 verify through the edge"
    ok=0
    for i in $(seq 1 24); do
      h=$("$CURL" -s -m 5 "$EDGE/api/health")
      if echo "$h" | grep -q healthy && [ "$(echo "$h" | json_get role)" = api ] && [ "$(echo "$h" | json_get instance)" = blue ]; then ok=1; break; fi
      nap 5
    done
    [ $ok = 1 ] || { do_rollback; die "the edge does not answer from api-blue - rolled back" 6; }
    # every response carries X-Motres-Instance (roles.InstanceHeaderMiddleware): which process answered
    whead=$("$CURL" -s -D - -o /dev/null -m 20 "$EDGE/api/simulation/config" | tr -d '\r')
    wcode=$(echo "$whead" | head -1 | awk '{print $2}')
    echo "$whead" | grep -qi '^x-motres-instance: worker' || { do_rollback; die "a worker route was not answered by the worker ($wcode) - rolled back" 6; }
    case "$wcode" in 200|401|403) ;; *) do_rollback; die "a worker route answered '$wcode' - rolled back" 6;; esac
    ahead=$("$CURL" -s -D - -o /dev/null -m 20 "$EDGE/api/version" | tr -d '\r')
    acode=$(echo "$ahead" | head -1 | awk '{print $2}')
    echo "$ahead" | grep -qi '^x-motres-instance: blue' || { do_rollback; die "an API route was not answered by api-blue ($acode) - rolled back" 6; }
    [ "$acode" = 200 ] || { do_rollback; die "an API route answered '$acode' - rolled back" 6; }
    pcode=$("$CURL" -s -o /dev/null -m 20 -w '%{http_code}' "$EDGE/build.json")
    [ "$pcode" = 200 ] || { do_rollback; die "the pages answered '$pcode' - rolled back" 6; }
    "$CURL" -s -m 10 "$EDGE/api/worker/state" | grep -q '"live"' && { do_rollback; die "/api/worker/state is reachable from the edge - rolled back" 6; }
    note "the new stack is serving: health api-blue, worker route $wcode, version 200, pages 200, worker state closed"

    note "8/8 drain and remove the old api"
    t0=$(date +%s); zero=0
    while :; do
      # requests in flight AND jobs: an optimizer campaign is a background thread, not a request
      n=$(old_get /api/admin/live | "$PYTHON" -c "import json,sys; d=json.load(sys.stdin); print(d['requests_total'] + d['jobs']['running'] + d['jobs']['queued'])" 2>/dev/null)
      if [ "$n" = 0 ]; then zero=$((zero + 1)); else zero=0; fi
      [ $(( $(date +%s) - t0 )) -ge 30 ] && [ "$zero" -ge 3 ] && break
      [ $(( $(date +%s) - t0 )) -ge 1800 ] && { note "the old api still has requests after 30 min - left running; finish by hand: docker stop $OLD && docker rm $OLD"; break; }
      nap 5
    done
    if [ "$zero" -ge 3 ]; then
      "$DOCKER" stop "$OLD" >/dev/null && "$DOCKER" rm "$OLD" >/dev/null && note "$OLD stopped and removed (its image stays as deploy-api:stage1)"
    fi
    echo "$SHA" > $APP/.deployed_commit; echo "$SHA" > $APP/.worker_commit
    "$DOCKER" tag "deploy-api:$SHA" deploy-api:latest
    $BG state
    rm -rf $APP.stage1 && mv $APP.prev $APP.stage1          # the stage-1 tree, kept for --rollback (step 9 replaces $APP.prev)
    note "9/9 publish the pages of $SHA through the normal path (the web container serves /srv/motres/web/current)"
    bash "$HERE/deploy_module.sh" "$SHA" "$SHA" web || note "WARNING: the pages were not published (exit $?) - rerun: deploy_module.sh $SHA $SHA web"
    note "CUTOVER DONE on $SHA"
    ;;
  *)
    sed -n '2,24p' "$0"; exit 2;;
esac
