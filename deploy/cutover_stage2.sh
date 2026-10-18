#!/bin/bash
# THE ONE-TIME CUTOVER from the single `api` service (stage 1) to  web + api-blue/api-green + worker  (stage 2).
# docs/DEPLOY_STAGE2.md, "Cutover runbook", has the same steps with the reasons.  Run on the server as root.
#
#   bash cutover_stage2.sh --check <SHA> <PREV>        STRICTLY read-only preflight + server report (no tag, no build, no
#                                                      write outside /tmp); ends with a GO / FIX list
#   bash cutover_stage2.sh --go    <SHA> <PREV> [MAX_WAIT_MIN]
#   bash cutover_stage2.sh --rollback [--force]        put stage 1 back (works until the old container was removed;
#                                                      afterwards it re-creates it from the saved image).  Refuses while the
#                                                      worker has a job, unless --force
#
# RUN IT DETACHED (an SSH drop must not cut it):  setsid nohup bash cutover_stage2.sh --go ... > /root/cutover.log 2>&1 &
# and  tail -f /root/cutover.log.  The script ignores SIGHUP anyway; SIGINT / SIGTERM stop it cleanly (before the switch:
# back to stage 1; after it: it says what is left to do by hand).
#
#   <SHA>   the commit being deployed (/tmp/deploy_main.tar is its `git archive`);  <PREV> the commit now deployed.
#
# What --go does, in order (each step prints what it did; any failure before step 8 leaves stage 1 serving):
#   1  preflight (same checks as --check)             2  unpack + build the images while stage 1 serves
#   3  wait until the OLD api is idle (never kills a job; ABORT 4 after MAX_WAIT_MIN x 3)
#   4  swap the source tree; write state.env + the upstream file (api-blue)
#   5  start the worker and api-blue next to the old api; wait healthy; warm up
#      (the worker's start-up recovery of "running" job records is postponed: the old api may still be computing them)
#   5b look at the old api's jobs once more: a job that started since step 3 is waited for (bounded), else rolled back
#   6  recreate the web container with the new nginx.conf  <- the ONE blip (2-3 s of 502 on the edge); traffic now flows to
#      the new stack; the old api receives nothing
#   7  verify through the public edge (health, a worker route, an API route, the pages)
#   8  drain the old api (its in-flight requests and jobs finish), stop and remove it (the stage-1 images stay as
#      deploy-api:stage1 / deploy-web:stage1, tagged at step 2 from the RUNNING containers)
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
WEB_CTR=${MOTRES_WEB_CTR:-deploy-web-1}
WORKER_CTR=deploy-worker-1
DEFER_MARK=$STATE_ROOT/workspaces/.recovery_deferred   # jobs.RECOVERY_DEFER_FILE: the worker keeps its hands off job records
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
  for n in $WORKER_CTR deploy-api-blue-1 deploy-api-green-1; do
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
old_jobs_count() {   # running + queued jobs of the old api; '?' when it cannot be read (then nobody may assume 'none')
  old_get /api/admin/live | "$PYTHON" -c "import json,sys
try:
    j = json.load(sys.stdin)['jobs']; print(int(j['running']) + int(j['queued']))
except Exception:
    print('?')"
}
notice_old() { "$DOCKER" exec "$OLD" python -c "import json,urllib.request as u;r=u.Request('http://127.0.0.1:8001/api/admin/notice',data=json.dumps($1).encode(),method='$2',headers={'Content-Type':'application/json'});u.urlopen(r,timeout=10)"; }

# ---- --check: the server report.  STRICTLY READ-ONLY: no tag, no build, no up / stop / rm, no file written outside /tmp ------
FIXLIST=""
CHECK_TMP=""
fix() { FIXLIST="$FIXLIST$1"$'\n'; echo "FIX  $1"; }
cleanup_check_tmp() {
  case "$CHECK_TMP" in "${MOTRES_CHECK_TMP:-/tmp}"/cutover_check.*) rm -rf "$CHECK_TMP";; esac
}
server_report() {   # <SHA>
  local sha=$1 t=${MOTRES_CHECK_TMP:-/tmp} w out img fst pat hits d erp_dirs live_n file_n
  echo "--- server report (read-only) ---"
  echo "docker compose: $("$DOCKER" compose version 2>&1 | head -1)"
  w=$(mktemp -d "$t/cutover_check.XXXXXX") || { fix "cannot create a temp directory under $t"; return; }
  CHECK_TMP=$w

  # 1. the NEW compose file (profiles, the `<<: [*a, *b]` merge) on THIS server's compose version.  The tree is unpacked to /tmp.
  if [ -f "$TARBALL" ] && tar -x -C "$w" -f "$TARBALL" deploy 2>/dev/null && [ -f "$w/deploy/docker-compose.yml" ]; then
    [ -f "$APP/deploy/.env" ] && cp -p "$APP/deploy/.env" "$w/deploy/.env"
    if (cd "$w/deploy" && BUILD_SHA=$sha BLUE_SHA=$sha GREEN_SHA=$sha WORKER_SHA=$sha "$DOCKER" compose --profile stack config -q 2>"$w/compose.err"); then
      echo "OK   compose config (--profile stack, with the <<: merge) parses"
    else
      fix "compose config fails on the new docker-compose.yml: $(head -c 400 "$w/compose.err" | tr '\n' ' ')"
    fi
  else
    fix "cannot unpack deploy/ from $TARBALL - the new compose file and nginx.conf were not checked"
  fi

  # 2. nginx -t of the NEW conf with the upstream include, in a throwaway container of the CURRENT web image; every file is a
  #    temp copy mounted read-only.  (api-blue / api-green resolve to loopback only so that the parse can succeed.)
  if [ -f "$w/deploy/nginx.conf" ]; then
    mkdir -p "$w/up" "$w/realip"
    printf 'server api-blue:8001;\n'                > "$w/up/api.conf"
    printf 'include /srv/web/upstream/api.conf;\n'  > "$w/api-upstream.conf"
    printf 'root /usr/share/nginx/html;\n'          > "$w/webroot.conf"
    printf 'set_real_ip_from 127.0.0.1;\n'          > "$w/realip/trusted.conf"
    img=$("$DOCKER" inspect -f '{{.Image}}' "$WEB_CTR" 2>/dev/null)
    if [ -z "$img" ]; then
      fix "cannot find the image of the running $WEB_CTR for the nginx -t"
    elif out=$("$DOCKER" run --rm --add-host api:127.0.0.1 --add-host api-blue:127.0.0.1 --add-host api-green:127.0.0.1 \
          -v "$w/deploy/nginx.conf:/etc/nginx/conf.d/default.conf:ro" -v "$w/api-upstream.conf:/etc/nginx/api-upstream.conf:ro" \
          -v "$w/up/api.conf:/srv/web/upstream/api.conf:ro" -v "$w/webroot.conf:/etc/nginx/webroot.conf:ro" \
          -v "$w/realip/trusted.conf:/etc/nginx/realip/trusted.conf:ro" \
          --entrypoint nginx "$img" -t 2>&1); then
      echo "OK   nginx -t of the new conf (current web image, temp copy, upstream include) passes"
    else
      fix "nginx -t fails on the new nginx.conf: $(echo "$out" | tail -3 | tr '\n' ' ')"
    fi
  fi

  # 3. flock only means something between processes on a LOCAL filesystem
  fst=$("${MOTRES_STAT:-stat}" -f -c %T "$STATE_ROOT" 2>/dev/null || true)
  case "$fst" in
    "")  echo "WARN cannot tell the filesystem type of $STATE_ROOT (stat -f failed) - check that it is local";;
    ext2/ext3|ext4|xfs|btrfs|zfs|f2fs|tmpfs|overlayfs|overlay) echo "OK   $STATE_ROOT is on a local filesystem ($fst)";;
    nfs*|cifs|smb*|fuse*|9p|ncpfs|afs|ceph|lustre|gfs*|glusterfs) fix "$STATE_ROOT is on a NETWORK filesystem ($fst): the cross-process flock locks are not reliable there";;
    *)   echo "WARN $STATE_ROOT filesystem type '$fst' - make sure it is local";;
  esac

  # 4. what the box carries now (the ERP and ppg2-* included): the worker + two API colours must fit next to it
  echo "containers (docker stats --no-stream: name, memory, cpu):"
  "$DOCKER" stats --no-stream --format '  {{.Name}}  {{.MemUsage}}  {{.CPUPerc}}' 2>&1 | head -60
  echo "idle RSS of $OLD: container $("$DOCKER" stats --no-stream --format '{{.MemUsage}}' "$OLD" 2>/dev/null | head -1), main process $("$DOCKER" exec "$OLD" sh -c 'grep VmRSS /proc/1/status' 2>/dev/null | tr -s ' ' | head -1) (measured with $(old_jobs_count) job(s) on it)"

  # 5. job records that say running / queued (a job of the old api, or a leftover of a crash)
  file_n=$("$PYTHON" -c "
import glob, json, os, sys
n = 0
for f in glob.glob(os.path.join(sys.argv[1], '*', '.jobs.json')):
    try:
        d = json.load(open(f, encoding='utf-8'))
    except Exception:
        continue
    rows = d.get('jobs') if isinstance(d, dict) else d
    if isinstance(rows, dict):
        rows = list(rows.values())
    n += sum(1 for r in rows or [] if isinstance(r, dict) and str(r.get('state') or r.get('status')) in ('running', 'queued'))
print(n)" "$STATE_ROOT/workspaces")
  live_n=$(old_jobs_count)
  echo "job records: ${file_n:-?} running/queued in .jobs.json; the old api itself counts ${live_n:-?}"
  if [ "${live_n:-?}" = 0 ] && [ "${file_n:-0}" != 0 ] && [ "${file_n:-0}" != '?' ]; then
    echo "WARN ${file_n} stale running/queued record(s) while the old api is idle: leftovers of a crash.  --go marks the ones older than its switch 'interrupted' once the old api is gone"
  elif [ "${live_n:-0}" != 0 ]; then
    echo "WARN the old api has ${live_n} job(s) now: --go waits for them (it never kills one; ABORT 4 after 3 x MAX_WAIT)"
  fi

  # 6. things outside the compose project that name the OLD container or its port (they stop working at the cutover)
  pat='deploy-api-1|api:8001|127\.0\.0\.1:8001'
  echo "systemd motres-cad-rebuild: $(systemctl cat motres-cad-rebuild 2>&1 | grep -E '^Exec[A-Za-z]*=' | head -3 | tr '\n' ' ')"
  echo "root crontab: $(crontab -l 2>/dev/null | grep -vc '^#') active line(s)"
  hits=$({ systemctl cat motres-cad-rebuild 2>/dev/null | grep -nE "$pat" | sed 's/^/motres-cad-rebuild unit: /'
           crontab -l 2>/dev/null | grep -nE "$pat" | sed 's/^/root crontab: /'; } || true)
  erp_dirs=${MOTRES_ERP_DIRS:-"/srv/erp /etc/erp /opt/erp /opt/motres-erp"}
  for d in $erp_dirs; do
    [ -d "$d" ] || continue
    hits="$hits$(grep -rIE --include='.env*' --include='*.env' -n "$pat" "$d" 2>/dev/null | sed 's/^/ERP env: /' | head -10)"
  done
  if [ -n "$(echo "$hits" | tr -d '[:space:]')" ]; then
    echo "$hits" | sed 's/^/     /'
    fix "something outside the project names $OLD / api:8001 / 127.0.0.1:8001 directly (listed above) - it breaks when the old api is removed; point it at 'bash $APP/deploy/bluegreen.sh exec api ...' or at the edge"
  else
    echo "OK   no direct use of $OLD / api:8001 / 127.0.0.1:8001 in motres-cad-rebuild, root's crontab or the ERP env files"
  fi
}

wait_worker() {  # <sha>
  local i got
  for i in $(seq 1 60); do
    got=$("$DOCKER" exec $WORKER_CTR curl -s -m 5 http://127.0.0.1:8002/api/health 2>/dev/null)
    echo "$got" | grep -q '"healthy"' && [ "$(echo "$got" | json_get build)" = "$1" ] && [ "$(echo "$got" | json_get role)" = worker ] && return 0
    nap 5
  done
  return 1
}

# ---- rollback -----------------------------------------------------------------------------------------------------------
FORCE=0
worker_running() { "$DOCKER" ps --format '{{.Names}}' | grep -qx "$WORKER_CTR"; }
worker_idle_or_gone() {   # 0 = no worker container runs, or `python -m motor_ai_sim.worker idle` succeeds
  worker_running || return 0
  "$DOCKER" exec $WORKER_CTR python -m motor_ai_sim.worker idle >/dev/null 2>&1
}
stage2_present() {        # something of stage 2 exists that a rollback would take down
  [ -f "$RUN/state.env" ] && return 0
  local n
  for n in $WORKER_CTR deploy-api-blue-1 deploy-api-green-1; do "$DOCKER" ps --format '{{.Names}}' | grep -qx "$n" && return 0; done
  return 1
}

do_rollback() {
  note "ROLLBACK to stage 1"
  local old=$APP.prev
  [ -d $APP.stage1 ] && old=$APP.stage1          # kept by step 8, which leaves $APP.prev for step 9
  # only a STAGE-2 tree is swapped out (a stage-1 tree has no bluegreen.sh): a second rollback must not swap it back
  if [ -d "$old" ] && [ -f "$APP/deploy/bluegreen.sh" ]; then rm -rf $APP.stage2 && mv $APP $APP.stage2 && mv "$old" $APP; fi
  rm -f "$DEFER_MARK"
  if ! "$DOCKER" ps --format '{{.Names}}' | grep -qx "$OLD"; then
    "$DOCKER" image inspect deploy-api:stage1 >/dev/null 2>&1 && "$DOCKER" tag deploy-api:stage1 deploy-api:latest
    (cd $APP/deploy && "$DOCKER" compose up -d --no-build --no-deps api 2>&1 | tail -2)
  fi
  # The stage-2 web container includes /srv/web/upstream/api.conf (api-blue) and proxies /api to it: take that file (and the
  # state) away BEFORE the stage-1 web is recreated, so /api never points at a container this rollback is about to stop.
  rm -f "$RUN/state.env" "$WEB/upstream/api.conf"
  # the stage-1 web image: step 2 tagged it from the RUNNING container (deploy-web:prev is overwritten by every later web deploy)
  "$DOCKER" image inspect deploy-web:stage1 >/dev/null 2>&1 && "$DOCKER" tag deploy-web:stage1 deploy-web:latest
  (cd $APP/deploy && "$DOCKER" compose up -d --no-build --no-deps web 2>&1 | tail -2)
  if [ $FORCE = 1 ] || worker_idle_or_gone; then
    "$DOCKER" stop $WORKER_CTR deploy-api-blue-1 deploy-api-green-1 >/dev/null 2>&1 || true
    note "stage 1 is serving again (the stage-2 containers are stopped; remove them with: docker rm $WORKER_CTR deploy-api-blue-1)"
  else
    "$DOCKER" stop deploy-api-blue-1 deploy-api-green-1 >/dev/null 2>&1 || true
    note "stage 1 is serving again.  The WORKER still has a job and was left running (nothing reaches it any more): stop it when it is idle:  docker exec $WORKER_CTR python -m motor_ai_sim.worker idle && docker stop $WORKER_CTR"
  fi
}

# ---- signals ------------------------------------------------------------------------------------------------------------
# PHASE: 0 nothing changed yet   1 the tree is swapped, stage 1 still serves   2 the web container was switched to stage 2
PHASE=0
on_signal() {   # <name>
  trap '' INT TERM HUP
  echo "CUTOVER: SIG$1 received (phase $PHASE)" >&2
  case "$PHASE" in
    1) note "interrupted before the switch: rolling back to stage 1"; do_rollback;;
    2) note "INTERRUPTED AFTER THE SWITCH: the new stack is serving.  Finish by hand: check  bash $APP/deploy/bluegreen.sh state ; the old api ($OLD) may still run - when it has no job:  docker stop $OLD && docker rm $OLD ; then rm -f $DEFER_MARK ; or put stage 1 back with  --rollback";;
    *) note "nothing had been changed";;
  esac
  exit 130
}
trap '' HUP                      # a dropped SSH session must not cut the cutover (run it with setsid nohup ... > log, see the header)
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM
trap cleanup_check_tmp EXIT

mode=${1:-}; SHA=${2:-}; PREV=${3:-}; MAXWAIT=${4:-20}
case "$mode" in
  --check)
    [ -n "$SHA" ] && [ -n "$PREV" ] || die "usage: --check <SHA> <PREV>" 2
    pf_out=$(preflight "$SHA" "$PREV"); pf_rc=$?
    echo "$pf_out"
    while IFS= read -r ln; do case "$ln" in FAIL*) FIXLIST="$FIXLIST${ln#FAIL }"$'\n';; esac; done <<< "$pf_out"
    server_report "$SHA"
    echo "--- GO / FIX ---"
    if [ $pf_rc = 0 ] && [ -z "$FIXLIST" ]; then
      echo "GO: nothing blocks the cutover"
      echo "PREFLIGHT OK"
    else
      echo "FIX before --go:"
      printf '%s' "$FIXLIST" | sed 's/^/  - /'
      echo "PREFLIGHT FAILED"; exit 1
    fi
    ;;
  --rollback)
    [ "$SHA" = --force ] && FORCE=1
    stage2_present || die "nothing to roll back: there is no $RUN/state.env and no stage-2 container is running" 2
    if [ $FORCE = 0 ] && ! worker_idle_or_gone; then
      die "REFUSED: the worker has a queued or running job ('python -m motor_ai_sim.worker idle' failed) and a rollback would stop it.  Wait until it is idle, or run  --rollback --force  to cut it." 4
    fi
    do_rollback
    ;;
  --go)
    [ -n "$SHA" ] && [ -n "$PREV" ] || die "usage: --go <SHA> <PREV> [MAX_WAIT_MIN]" 2
    note "1/8 preflight"; preflight "$SHA" "$PREV" || die "preflight failed - nothing changed" 1

    note "2/8 unpack and build (stage 1 keeps serving)"
    # The rollback images: the stage-1 images as the RUNNING containers use them (by image id) - not whatever `:latest` /
    # `:prev` happen to be by then (the step-9 web deploy re-tags `deploy-web:prev` as the stage-2 web).
    img=$("$DOCKER" inspect -f '{{.Image}}' "$OLD" 2>/dev/null) && [ -n "$img" ] && "$DOCKER" tag "$img" deploy-api:stage1 \
      || die "cannot tag the image of the running $OLD as deploy-api:stage1 - nothing changed" 5
    img=$("$DOCKER" inspect -f '{{.Image}}' "$WEB_CTR" 2>/dev/null) && [ -n "$img" ] && "$DOCKER" tag "$img" deploy-web:stage1 \
      || die "cannot tag the image of the running $WEB_CTR as deploy-web:stage1 - nothing changed" 5
    rm -rf $APP.new && mkdir $APP.new && tar -x -C $APP.new -f "$TARBALL" || die "unpack failed - nothing changed" 5
    for f in deploy/.env web/.env web/.env.local config/end_effect_passports.json; do [ -f $APP/$f ] && cp -p $APP/$f $APP.new/$f; done
    [ -d $APP/src/cadquery_cache ] && cp -a $APP/src/cadquery_cache $APP.new/src/
    sed -i "s/\r$//" $APP.new/deploy/*.sh 2>/dev/null
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
    PHASE=1
    $BG init "$SHA" || { do_rollback; die "init failed" 5; }

    note "5/8 start the worker and api-blue next to the old api"
    # The worker marks job records it finds "running" as interrupted when it starts (a crash left them).  Here the OLD api may
    # still be computing exactly those: postpone that (jobs.RECOVERY_DEFER_FILE); step 8 removes the marker and recovers the
    # records older than the switch once the old api is gone.
    : > "$DEFER_MARK"
    (cd $APP/deploy && BLUE_SHA=$SHA GREEN_SHA= WORKER_SHA=$SHA "$DOCKER" compose up -d --no-build --no-deps worker 2>&1 | tail -3)
    wait_worker "$SHA" || { "$DOCKER" logs --tail 20 $WORKER_CTR 2>&1; do_rollback; die "the worker did not become healthy - rolled back" 5; }
    $BG start-active || { do_rollback; die "api-blue did not come up - rolled back" 5; }

    # A user may have started a job on the old api since step 3 (the builds and the start took minutes).  After step 6 nothing
    # reaches that job (its progress / cancel routes are the worker's): wait for it, bounded, or give up before the switch.
    note "5b/8 the old api once more: nothing may have started since step 3"
    t5=$(date +%s)
    while :; do
      n=$(old_jobs_count)
      [ "$n" = 0 ] && break
      w5=$(( ($(date +%s) - t5) / 60 ))
      echo "$(date +%T) the old api has job(s) now (running + queued: ${n:-?})"
      if [ $w5 -ge $MAXWAIT ]; then
        do_rollback; die "ABORT 4: a job (${n:-?}) started on the old api after step 3 and is still there after $w5 min - rolled back, nothing switched; rerun --go when it is done" 4
      fi
      nap 20
    done

    note "6/8 recreate the web container (the new nginx.conf): expect 2-3 s of 502 on the edge"
    CUTOFF=$(date +%s); PHASE=2
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
      [ $(( $(date +%s) - t0 )) -ge 1800 ] && { note "the old api still has requests after 30 min - left running; finish by hand: docker stop $OLD && docker rm $OLD && rm -f $DEFER_MARK"; break; }
      nap 5
    done
    PHASE=3
    if [ "$zero" -ge 3 ]; then
      "$DOCKER" stop "$OLD" >/dev/null && "$DOCKER" rm "$OLD" >/dev/null && note "$OLD stopped and removed (its image stays as deploy-api:stage1)"
      # the old api is gone: a record that still says running / queued and is older than the switch is a leftover; the
      # worker's own jobs (started after the switch) are not touched
      rm -f "$DEFER_MARK"
      "$DOCKER" exec $WORKER_CTR python -c "from motor_ai_sim import jobs; print('stale job records marked interrupted:', jobs.recover_stale_records(before=$CUTOFF))" 2>&1 | tail -1
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
    sed -n '2,36p' "$0"; exit 2;;
esac
