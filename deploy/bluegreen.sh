#!/bin/bash
# Blue-green API deploy + the state it needs (deploy stage 2, docs/DEPLOY_STAGE2.md).  Host side, run by root on the server.
#
#   bash bluegreen.sh state                      what runs, which colour is live, the images (read-only)
#   bash bluegreen.sh init <sha> [worker_sha]    first-time state file + upstream (the cutover; no docker action)
#   bash bluegreen.sh start-idle <sha>           start the IDLE colour with image deploy-api:<sha>; wait healthy; warm it up
#   bash bluegreen.sh start-active               (the cutover) start the colour state.env calls active, same checks
#   bash bluegreen.sh first-start                (a fresh server) worker, the active colour, then the web container
#   bash bluegreen.sh switch                     point nginx at the idle colour (atomic file + nginx -t + reload), verify
#   bash bluegreen.sh drain-old [min_s] [max_s]  wait until the OLD colour has no request in flight (never kills one)
#   bash bluegreen.sh stop-old                   stop the old colour (only after drain-old said it is empty)
#   bash bluegreen.sh stop-idle                  stop the colour nginx does not point at (a failed start / switch)
#   bash bluegreen.sh rollback                   switch nginx back to the previous colour (starts it if it was stopped)
#   bash bluegreen.sh exec <api|worker> CMD...   run CMD in the active API container / the worker (systemd units, README)
#
# The upstream is ONE line in $WEB/upstream/api.conf (`server api-green:8001;`), included by the web container's nginx
# (deploy/nginx.conf).  `switch` rewrites it with a rename (atomic), runs `nginx -t` and `nginx -s reload`: new connections
# go to the new colour, the old nginx workers finish their requests on the old one.  If `nginx -t` or the reload fails the
# old line is put back - the site never points at a colour that does not answer.
#
# State: $RUN/state.env  (ACTIVE, PREV_ACTIVE, BLUE_SHA, GREEN_SHA, WORKER_SHA, PREV_SHA).  The scripts export it to
# `docker compose` (the compose file takes the image of each service from it).
#
# Testable without a server: DOCKER, CURL, MOTRES_RUN, MOTRES_WEB, MOTRES_APP, MOTRES_EDGE, PYTHON, MOTRES_POLL_S override the
# defaults below (tests/test_deploy_stage2_bluegreen.py runs this script against a fake `docker`).
set -uo pipefail
trap '' HUP          # a dropped SSH session must not cut a switch half way (drain-old can wait for minutes); `exec` resets it

RUN=${MOTRES_RUN:-/srv/motres/run}
WEB=${MOTRES_WEB:-/srv/motres/web}
APP=${MOTRES_APP:-/opt/motres/app}
DOCKER=${DOCKER:-docker}
CURL=${CURL:-curl}                                 # the HOST's curl (the probes inside containers use the image's)
PYTHON=${PYTHON:-python3}
PREFIX=${MOTRES_CTR_PREFIX:-deploy}               # compose project -> container names <prefix>-<service>-1
EDGE=${MOTRES_EDGE:-http://127.0.0.1:8080}        # the web container, as the host reaches it (host nginx -> here)
POLL=${MOTRES_POLL_S:-3}
STATE=$RUN/state.env
UPSTREAM=$WEB/upstream/api.conf
WEB_CTR=$PREFIX-web-1

die()  { echo "bluegreen: $*" >&2; exit "${2:-1}"; }
note() { echo "$(date +%T) $*"; }
ctr()  { echo "$PREFIX-$1-1"; }
other() { [ "$1" = blue ] && echo green || echo blue; }

# ---- state ------------------------------------------------------------------------------------------------------------
ACTIVE=blue PREV_ACTIVE="" BLUE_SHA="" GREEN_SHA="" WORKER_SHA="" PREV_SHA="" SWITCHED_AT=0
load_state() {
  [ -f "$STATE" ] || die "no $STATE - run '$0 init <sha>' first (the cutover)"
  local k v
  while IFS='=' read -r k v; do
    case "$k" in ACTIVE|PREV_ACTIVE|BLUE_SHA|GREEN_SHA|WORKER_SHA|PREV_SHA|SWITCHED_AT) printf -v "$k" '%s' "$v";; esac
  done < "$STATE"
  case "$ACTIVE" in blue|green) ;; *) die "state.env: ACTIVE must be blue or green, got '$ACTIVE'";; esac
}
save_state() {
  mkdir -p "$RUN"
  { echo "ACTIVE=$ACTIVE"; echo "PREV_ACTIVE=$PREV_ACTIVE"; echo "BLUE_SHA=$BLUE_SHA"; echo "GREEN_SHA=$GREEN_SHA"
    echo "WORKER_SHA=$WORKER_SHA"; echo "PREV_SHA=$PREV_SHA"; echo "SWITCHED_AT=${SWITCHED_AT:-0}"; } > "$STATE.new" && mv -f "$STATE.new" "$STATE"
}
sha_of() { case "$1" in blue) echo "$BLUE_SHA";; green) echo "$GREEN_SHA";; esac; }
set_sha() { case "$1" in blue) BLUE_SHA=$2;; green) GREEN_SHA=$2;; esac; }
# what `docker compose` needs to resolve image tags
export_for_compose() { export BLUE_SHA GREEN_SHA WORKER_SHA; }
dc() { export_for_compose; (cd "$APP/deploy" && "$DOCKER" compose "$@"); }

# ---- probes -----------------------------------------------------------------------------------------------------------
# GET inside a container on loopback (the image has curl; the HEALTHCHECK uses it)
cget() { "$DOCKER" exec "$1" curl -s -m "${3:-10}" "http://127.0.0.1:$2$4"; }
json_get() { "$PYTHON" -c "import json,sys
try:
    d = json.load(sys.stdin)
    for k in sys.argv[1].split('.'):
        d = d[k]
    print(d)
except Exception:
    print('')" "$1"; }
colour_health() {   # <colour>  -> prints "instance build" when /api/health says healthy
  local c=$1 out
  out=$(cget "$(ctr api-$c)" 8001 5 /api/health 2>/dev/null) || return 1
  echo "$out" | grep -q '"healthy"' || return 1
  echo "$(echo "$out" | json_get instance) $(echo "$out" | json_get build)"
}
running() { [ "$("$DOCKER" inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = true ]; }

wait_healthy() {    # <colour> <sha> [seconds]
  local c=$1 sha=$2 limit=${3:-240} t0 got
  t0=$(date +%s)
  while :; do
    got=$(colour_health "$c") && [ "$got" = "$c $sha" ] && return 0
    [ $(( $(date +%s) - t0 )) -ge "$limit" ] && { echo "last health answer of api-$c: '${got:-none}' (wanted '$c $sha')" >&2; return 1; }
    sleep "$POLL"
  done
}

# ---- subcommands ------------------------------------------------------------------------------------------------------
cmd_state() {
  load_state
  echo "active=$ACTIVE previous=${PREV_ACTIVE:-none}"
  echo "blue_image=${BLUE_SHA:-none} green_image=${GREEN_SHA:-none} worker_image=${WORKER_SHA:-none} previous_image=${PREV_SHA:-none}"
  echo "upstream: $(cat "$UPSTREAM" 2>/dev/null || echo 'none')"
  local c h
  for c in blue green; do
    if running "$(ctr api-$c)"; then h=$(colour_health "$c") && echo "api-$c: running, healthy ($h)" || echo "api-$c: running, NOT healthy"
    else echo "api-$c: stopped"; fi
  done
  if running "$(ctr worker)"; then echo "worker: running ($(cget "$(ctr worker)" 8002 5 /api/health 2>/dev/null | json_get build))"
  else echo "worker: stopped"; fi
}

cmd_init() {    # <sha> [worker_sha]
  local sha=${1:-} wsha=${2:-}
  [ -n "$sha" ] || die "usage: init <sha> [worker_sha]"
  [ -f "$STATE" ] && die "$STATE already exists - refusing to overwrite the state of a running stack"
  ACTIVE=blue; PREV_ACTIVE=""; BLUE_SHA=$sha; GREEN_SHA=""; WORKER_SHA=${wsha:-$sha}; PREV_SHA=""
  save_state
  mkdir -p "$WEB/upstream"
  printf 'server %s:8001;\n' "api-blue" > "$UPSTREAM.new" && mv -T "$UPSTREAM.new" "$UPSTREAM"
  note "state written: active=blue image=$sha worker=$WORKER_SHA; upstream -> api-blue"
}

cmd_start_idle() {  # <sha>
  load_state
  [ -n "${1:-}" ] || die "usage: start-idle <sha>"
  bring_up "$(other "$ACTIVE")" "$1"
}

cmd_start_active() {    # the cutover: bring up the colour the state calls active (nginx will point at it)
  load_state
  bring_up "$ACTIVE" "$(sha_of "$ACTIVE")"
}

bring_up() {    # <colour> <sha>: start api-<colour> on deploy-api:<sha>, wait healthy, warm it up, check nginx can reach it
  local c=$1 sha=$2
  "$DOCKER" image inspect "deploy-api:$sha" >/dev/null 2>&1 || die "image deploy-api:$sha does not exist (build it first)"
  if running "$(ctr api-$c)"; then
    # a half-finished earlier attempt: reuse it only when it already runs this image, else replace it (it serves nobody)
    [ "$(sha_of "$c")" = "$sha" ] || { note "api-$c runs another image and serves nobody - replacing it"; dc stop "api-$c" >/dev/null 2>&1; }
  fi
  set_sha "$c" "$sha"
  note "starting api-$c with deploy-api:$sha (live colour stays $ACTIVE)"
  dc up -d --no-build --no-deps "api-$c" 2>&1 | tail -3
  wait_healthy "$c" "$sha" "${START_TIMEOUT_S:-240}" || { dc stop "api-$c" >/dev/null 2>&1; die "api-$c did not become healthy - stopped it, nothing switched" 5; }
  save_state                                    # the idle colour's image is recorded; ACTIVE is not touched
  # warm-up: the first request of a fresh process pays the lazy imports; do them here, not on a user.  Side-effect free GETs.
  local p code
  for p in /api/me /api/version /api/notice /api/health; do
    code=$("$DOCKER" exec "$(ctr api-$c)" curl -s -o /dev/null -m 60 -w '%{http_code}' "http://127.0.0.1:8001$p" 2>/dev/null)
    case "$code" in 2??|3??|401|403) ;; *) die "warm-up GET $p on api-$c answered '$code'" 5;; esac
  done
  # nginx must be able to reach it by name (the reload resolves the upstream host); a first start has no web container yet
  if running "$WEB_CTR"; then
    "$DOCKER" exec "$WEB_CTR" wget -q -O /dev/null -T 5 "http://api-$c:8001/api/health" 2>/dev/null \
      || die "the web container cannot reach api-$c:8001 (same network?)" 5
  fi
  note "api-$c is up, healthy and warm"
}

# write the upstream line, test, reload; on any failure put the previous line back and reload again
point_nginx_at() {  # <colour>
  local c=$1 old
  old=$(cat "$UPSTREAM" 2>/dev/null || true)
  mkdir -p "$WEB/upstream"
  printf 'server %s:8001;\n' "api-$c" > "$UPSTREAM.new" && mv -T "$UPSTREAM.new" "$UPSTREAM" || return 1
  if ! "$DOCKER" exec "$WEB_CTR" nginx -t >/dev/null 2>&1 || ! "$DOCKER" exec "$WEB_CTR" nginx -s reload >/dev/null 2>&1; then
    echo "nginx refused the new upstream - restoring the previous one" >&2
    if [ -n "$old" ]; then printf '%s\n' "$old" > "$UPSTREAM.new" && mv -T "$UPSTREAM.new" "$UPSTREAM"; fi
    "$DOCKER" exec "$WEB_CTR" nginx -s reload >/dev/null 2>&1 || true
    return 1
  fi
}
edge_serves() {     # <colour>  until the web container answers from that colour (reload takes milliseconds)
  local c=$1 i got
  for i in $(seq 1 "${MOTRES_EDGE_TRIES:-20}"); do
    got=$("$CURL" -s -m 5 "$EDGE/api/health" 2>/dev/null | json_get instance)
    [ "$got" = "$c" ] && return 0
    sleep 1
  done
  return 1
}

cmd_switch() {
  load_state
  local c
  c=$(other "$ACTIVE")
  running "$(ctr api-$c)" || die "api-$c is not running - start-idle first" 5
  colour_health "$c" >/dev/null || die "api-$c is not healthy - not switching" 5
  point_nginx_at "$c" || die "nginx did not take the new upstream - the live colour is still $ACTIVE" 6
  if ! edge_serves "$c"; then
    echo "the edge does not answer from api-$c after the reload - switching back" >&2
    point_nginx_at "$ACTIVE" || true
    die "switch verification failed - live colour is still $ACTIVE" 6
  fi
  PREV_ACTIVE=$ACTIVE; PREV_SHA=$(sha_of "$ACTIVE"); ACTIVE=$c; SWITCHED_AT=$(date +%s)
  save_state
  note "switched: api-$ACTIVE ($(sha_of "$ACTIVE")) is live; api-$PREV_ACTIVE ($PREV_SHA) drains next"
}

cmd_drain_old() {   # [min_s] [max_s]
  load_state
  local min=${1:-30} max=${2:-1800} old t0 zero=0 n
  old=$PREV_ACTIVE
  [ -n "$old" ] || die "no previous colour recorded - nothing to drain"
  running "$(ctr api-$old)" || { note "api-$old is already stopped"; return 0; }
  t0=$(date +%s)
  while :; do
    # LOCAL requests in flight of the old colour only (?local=1: not the worker's long solves)
    n=$(cget "$(ctr api-$old)" 8001 10 "/api/admin/live?local=1" 2>/dev/null | json_get busy_total)
    if [ "$n" = "0" ]; then zero=$((zero + 1)); else zero=0; fi
    if [ $(( $(date +%s) - t0 )) -ge "$min" ] && [ "$zero" -ge 3 ]; then note "api-$old has no request in flight"; return 0; fi
    if [ $(( $(date +%s) - t0 )) -ge "$max" ]; then
      note "api-$old still has ${n:-?} request(s) in flight after $max s - leaving it running (nothing is killed); rerun drain-old later"
      return 3
    fi
    sleep "$POLL"
  done
}

cmd_first_start() {   # a fresh server / disaster recovery: worker, the active colour, then the pages (nginx needs the colour to exist)
  load_state
  dc up -d --no-build --no-deps worker 2>&1 | tail -2
  local i got
  for i in $(seq 1 60); do
    got=$(cget "$(ctr worker)" 8002 5 /api/health 2>/dev/null | json_get build)
    [ "$got" = "$WORKER_SHA" ] && break
    sleep "$POLL"
  done
  [ "$got" = "$WORKER_SHA" ] || die "the worker did not become healthy" 5
  bring_up "$ACTIVE" "$(sha_of "$ACTIVE")"
  dc up -d --no-build --no-deps web 2>&1 | tail -2
  note "stack up: worker $WORKER_SHA, api-$ACTIVE $(sha_of "$ACTIVE"), web"
}

cmd_stop_old() {
  load_state
  [ -n "$PREV_ACTIVE" ] || die "no previous colour recorded"
  [ "$PREV_ACTIVE" != "$ACTIVE" ] || die "previous colour equals the live colour - refusing"
  dc stop "api-$PREV_ACTIVE" 2>&1 | tail -2
  note "api-$PREV_ACTIVE stopped (image ${PREV_SHA:-?} stays for a rollback)"
}

cmd_stop_idle() {   # the colour nginx does not point at (a failed start-idle / switch)
  load_state
  dc stop "api-$(other "$ACTIVE")" 2>&1 | tail -2
  note "api-$(other "$ACTIVE") stopped"
}

cmd_rollback() {
  load_state
  [ -n "$PREV_ACTIVE" ] || die "no previous colour recorded - nothing to roll back to"
  local back=$PREV_ACTIVE bad=$ACTIVE
  if ! running "$(ctr api-$back)"; then
    note "api-$back is stopped - starting it with its previous image ${PREV_SHA:-$(sha_of "$back")}"
    dc up -d --no-build --no-deps "api-$back" 2>&1 | tail -2
    wait_healthy "$back" "$(sha_of "$back")" 240 || die "api-$back did not come back - the live colour is still $ACTIVE" 5
  fi
  point_nginx_at "$back" || die "nginx did not take the old upstream" 6
  edge_serves "$back" || die "the edge does not answer from api-$back" 6
  ACTIVE=$back; PREV_ACTIVE=$bad; PREV_SHA=$(sha_of "$bad"); SWITCHED_AT=$(date +%s)
  save_state
  note "rolled back: api-$ACTIVE is live again; api-$bad still runs (inspect its log, then stop-old)"
}

cmd_exec() {    # <api|worker> CMD...
  load_state
  local which=${1:-}; shift || true
  [ $# -gt 0 ] || die "usage: exec <api|worker> CMD..."
  trap - HUP                                    # the command is the caller's own (interactive or a timer's)
  case "$which" in
    api)    exec "$DOCKER" exec -i "$(ctr api-$ACTIVE)" "$@";;
    worker) exec "$DOCKER" exec -i "$(ctr worker)" "$@";;
    *) die "exec: api or worker";;
  esac
}

sub=${1:-}; shift || true
case "$sub" in
  state) cmd_state;;
  init) cmd_init "$@";;
  start-idle) cmd_start_idle "$@";;
  start-active) cmd_start_active;;
  first-start) cmd_first_start;;
  switch) cmd_switch;;
  drain-old) cmd_drain_old "$@";;
  stop-old) cmd_stop_old;;
  stop-idle) cmd_stop_idle;;
  rollback) cmd_rollback;;
  exec) cmd_exec "$@";;
  *) sed -n '2,20p' "$0"; exit 2;;
esac
