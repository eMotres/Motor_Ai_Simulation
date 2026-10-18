"""Deploy stage 2: deploy/bluegreen.sh and deploy/cutover_stage2.sh against a fake `docker`.

The scripts are real bash; only `docker` (and the host's `curl`) are replaced by small shims that keep their world in a
directory (running containers, health answers, the "edge" the host reaches nginx through).  This checks the ORDER and the
SAFETY of the switch - the upstream file is replaced atomically, a refused `nginx -t` puts the old line back, a failed
health check starts nothing, the old colour is never stopped while it has requests - not Docker itself.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"


def _bash():
    if os.name == "nt":                       # `bash` on PATH is the WSL launcher there; the scripts need MSYS paths
        for c in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
            if Path(c).exists():
                return c
        return None
    return shutil.which("bash")


BASH = _bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

DOCKER_SHIM = r'''#!/bin/bash
# fake docker: state lives in $FAKE
echo "docker $*" >> "$FAKE/log"
key() { echo "$1" | tr '/?=&' '____'; }
case "$1" in
  image)    [ -e "$FAKE/images/$(echo "$3" | tr ':/' '__')" ];;
  inspect)  [ -e "$FAKE/running/${@: -1}" ] && echo true || echo false;;
  tag)      exit 0;;
  stop)     shift; for c in "$@"; do rm -f "$FAKE/running/$c"; done;;
  rm)       exit 0;;
  create)   echo cid123;;
  cp)       dest=${@: -1}; mkdir -p "$dest/assets";;
  run)      if echo "$*" | grep -q -- '--entrypoint nginx'; then exit "$(cat "$FAKE/nginx_image_t_rc" 2>/dev/null || echo 0)"; fi
            echo SIGNEW;;
  ps)       ls "$FAKE/running" 2>/dev/null;;
  compose)
    shift
    case "$1" in
      version) exit 0;;
      build) svc=${@: -1}
             case "$svc" in
               api-image) touch "$FAKE/images/deploy-api_$BUILD_SHA";;
               web)       touch "$FAKE/images/deploy-web_latest";;
             esac
             [ -e "$FAKE/build_fails" ] && exit 1; exit 0;;
      up)  svc=${@: -1}; ctr="deploy-$svc-1"; touch "$FAKE/running/$ctr"
           mkdir -p "$FAKE/http/$ctr"
           if [ -e "$FAKE/up_fails" ]; then rm -f "$FAKE/running/$ctr"; exit 1; fi
           case "$svc" in
             api-blue)  sha=$BLUE_SHA;  inst=blue;;
             api-green) sha=$GREEN_SHA; inst=green;;
             worker)    sha=$WORKER_SHA; inst=worker;;
           esac
           role=api; [ "$svc" = worker ] && role=worker
           if [ "$svc" = web ]; then      # the recreated web container now proxies to the upstream file
             c=$(sed 's/^server api-\([a-z]*\):.*/\1/' "$MOTRES_WEB/upstream/api.conf" 2>/dev/null)
             [ -n "$c" ] && cp "$FAKE/http/deploy-api-$c-1/8001_api_health" "$FAKE/edge_health" 2>/dev/null
             exit 0
           fi
           if [ ! -e "$FAKE/unhealthy" ]; then
             echo "{\"status\":\"healthy\",\"role\":\"$role\",\"instance\":\"$inst\",\"build\":\"$sha\"}" > "$FAKE/http/$ctr/8001_api_health"
             cp "$FAKE/http/$ctr/8001_api_health" "$FAKE/http/$ctr/8002_api_health"
             for p in api_me api_version api_notice; do echo '{}' > "$FAKE/http/$ctr/8001_$p"; done
             echo '{"busy_total": 0}' > "$FAKE/http/$ctr/8001_api_admin_live_local_1"
           fi;;
      stop) svc=${@: -1}; rm -f "$FAKE/running/deploy-$svc-1";;
      *) exit 0;;
    esac;;
  exec)
    shift; [ "$1" = "-i" ] && shift
    ctr=$1; shift
    case "$1" in
      curl) url=${@: -1}; f="$FAKE/http/$ctr/$(key "${url#http://127.0.0.1:}")"
            [ -e "$FAKE/running/$ctr" ] || exit 1
            if echo "$*" | grep -q -- ' -w '; then [ -e "$f" ] && printf 200 || printf 000; exit 0; fi
            [ -e "$f" ] && cat "$f" || exit 7;;
      nginx) if [ "$2" = "-t" ]; then exit "$(cat "$FAKE/nginx_t_rc" 2>/dev/null || echo 0)"; fi
             rc=$(cat "$FAKE/nginx_reload_rc" 2>/dev/null || echo 0)
             if [ "$rc" = 0 ]; then
               c=$(sed 's/^server api-\([a-z]*\):.*/\1/' "$MOTRES_WEB/upstream/api.conf")
               cp "$FAKE/http/deploy-api-$c-1/8001_api_health" "$FAKE/edge_health" 2>/dev/null
               echo "$c" >> "$FAKE/reloads"
             fi
             exit "$rc";;
      wget) exit 0;;
      sh) echo SIGOLD; exit 0;;
      grep) exit 0;;
      python) if [ "$2" = "-m" ]; then               # the worker CLI: drain on|off, idle
                case "${@: -1}" in
                  on)   touch "$FAKE/draining"; exit 0;;
                  off)  rm -f "$FAKE/draining"; exit 0;;
                  idle) if [ -e "$FAKE/worker_busy_once" ]; then rm -f "$FAKE/worker_busy_once"; exit 1; fi; exit 0;;
                esac
              fi
              code="$*"
              case "$code" in
                *admin/live*)   cat "$FAKE/live.json";;
                *admin/notice*) echo "notice $code" | cut -c1-60 >> "$FAKE/notices";;
              esac
              exit 0;;
      *) exit 0;;
    esac;;
esac
'''
CURL_SHIM = r'''#!/bin/bash
# host curl: the edge (nginx) answers from the last reloaded upstream
echo "curl $*" >> "$FAKE/log"
url=${@: -1}
if echo "$*" | grep -q -- ' -D '; then          # response headers: which process answered
  inst=worker
  case "$url" in
    */api/simulation*) [ -e "$FAKE/edge_worker_wrong" ] && inst=blue;;
    *) inst=$(sed 's/^server api-\([a-z]*\):.*/\1/' "$MOTRES_WEB/upstream/api.conf");;
  esac
  printf 'HTTP/1.1 200 OK\r\nx-motres-instance: %s\r\n\r\n' "$inst"; exit 0
fi
if echo "$*" | grep -q -- ' -w '; then printf 200; exit 0; fi
case "$url" in
  */api/health) [ -e "$FAKE/edge_health" ] && cat "$FAKE/edge_health" || exit 7;;
  */api/worker/state) echo '{"detail":"internal only"}';;
  *) exit 0;;
esac
'''


class Stack:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.fake = tmp / "fake"
        self.run = tmp / "run"
        self.web = tmp / "web"
        self.app = tmp / "app"
        self.bin = tmp / "bin"
        for d in (self.fake / "running", self.fake / "images", self.fake / "http", self.bin, self.run,
                  self.web / "upstream", self.app / "deploy"):
            d.mkdir(parents=True, exist_ok=True)
        (self.bin / "docker").write_text(DOCKER_SHIM, newline="\n")
        (self.bin / "curl").write_text(CURL_SHIM, newline="\n")
        for n in ("docker", "curl"):
            (self.bin / n).chmod(0o755)
        for c in ("web",):
            self.running(f"deploy-{c}-1")

    def p(self, path: Path) -> str:
        return path.as_posix()

    def running(self, ctr: str):
        (self.fake / "running" / ctr).touch()

    def image(self, sha: str):
        (self.fake / "images" / f"deploy-api_{sha}").touch()

    def env(self):
        e = dict(os.environ)
        e.update(FAKE=self.p(self.fake), MOTRES_RUN=self.p(self.run), MOTRES_WEB=self.p(self.web), MOTRES_APP=self.p(self.app),
                 DOCKER=self.p(self.bin / "docker"), CURL=self.p(self.bin / "curl"), PYTHON=sys.executable.replace("\\", "/"), MOTRES_POLL_S="0",
                 MOTRES_EDGE="http://edge.invalid", MOTRES_EDGE_TRIES="2", START_TIMEOUT_S="2",
                 PATH=self.p(self.bin) + os.pathsep + e.get("PATH", ""))
        return e

    def bg(self, *args, script="bluegreen.sh"):
        r = subprocess.run([BASH, self.p(DEPLOY / script), *args], env=self.env(), capture_output=True, text=True, timeout=120)
        return r

    def state(self) -> dict:
        out = {}
        for line in (self.run / "state.env").read_text().splitlines():
            k, _, v = line.partition("=")
            out[k] = v
        return out

    def upstream(self) -> str:
        return (self.web / "upstream" / "api.conf").read_text().strip()

    def log(self) -> str:
        p = self.fake / "log"
        return p.read_text() if p.exists() else ""

    def inflight(self, ctr: str, n: int):
        (self.fake / "http" / ctr).mkdir(parents=True, exist_ok=True)
        (self.fake / "http" / ctr / "8001_api_admin_live_local_1").write_text(json.dumps({"busy_total": n}))


@pytest.fixture
def st(tmp_path):
    return Stack(tmp_path)


def _live_blue(st: Stack, sha="aaa111"):
    """The state after the cutover: blue is live on `sha`."""
    st.image(sha)
    assert st.bg("init", sha).returncode == 0
    st.running("deploy-api-blue-1")
    (st.fake / "http" / "deploy-api-blue-1").mkdir(parents=True, exist_ok=True)
    (st.fake / "http" / "deploy-api-blue-1" / "8001_api_health").write_text(
        json.dumps({"status": "healthy", "role": "api", "instance": "blue", "build": sha}))
    (st.fake / "edge_health").write_text(json.dumps({"status": "healthy", "instance": "blue", "build": sha}))
    st.inflight("deploy-api-blue-1", 0)


def test_scripts_are_lf_and_parse():
    for n in ("bluegreen.sh", "deploy_module.sh", "cutover_stage2.sh", "nginx-upstream.sh"):
        raw = (DEPLOY / n).read_bytes()
        assert b"\r" not in raw, n
        assert subprocess.run([BASH, "-n", (DEPLOY / n).as_posix()], capture_output=True).returncode == 0, n


def test_init_writes_state_and_points_nginx_at_blue(st):
    r = st.bg("init", "aaa111")
    assert r.returncode == 0, r.stderr
    s = st.state()
    assert s["ACTIVE"] == "blue" and s["BLUE_SHA"] == "aaa111" and s["WORKER_SHA"] == "aaa111" and s["GREEN_SHA"] == ""
    assert st.upstream() == "server api-blue:8001;"
    assert st.bg("init", "bbb").returncode != 0, "never overwrite the state of a running stack"
    assert st.state()["BLUE_SHA"] == "aaa111"


def test_the_whole_switch_in_order(st):
    _live_blue(st)
    st.image("bbb222")
    r = st.bg("start-idle", "bbb222")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (st.fake / "running" / "deploy-api-green-1").exists()
    assert st.state()["ACTIVE"] == "blue" and st.state()["GREEN_SHA"] == "bbb222"
    assert st.upstream() == "server api-blue:8001;", "starting the idle colour must not move traffic"
    # compose got the image tags from the state file through the environment, and never the live colour
    assert "up -d --no-build --no-deps api-green" in st.log() and "api-blue" not in st.log().split("up -d")[-1]

    r = st.bg("switch")
    assert r.returncode == 0, r.stdout + r.stderr
    s = st.state()
    assert (s["ACTIVE"], s["PREV_ACTIVE"], s["PREV_SHA"], s["GREEN_SHA"]) == ("green", "blue", "aaa111", "bbb222")
    import time
    assert abs(int(s["SWITCHED_AT"]) - time.time()) < 120, "the switch time is recorded (the worker update waits for the heartbeats)"
    assert st.upstream() == "server api-green:8001;"
    assert (st.fake / "reloads").read_text().split() == ["green"], "exactly one reload, onto the new colour"
    assert not list((st.web / "upstream").glob("*.new")), "no half-written upstream file is left behind"

    r = st.bg("drain-old", "0", "20")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (st.fake / "running" / "deploy-api-blue-1").exists(), "draining stops nothing"
    r = st.bg("stop-old")
    assert r.returncode == 0
    assert not (st.fake / "running" / "deploy-api-blue-1").exists() and (st.fake / "running" / "deploy-api-green-1").exists()


def test_a_colour_that_never_gets_healthy_is_stopped_and_nothing_switches(st):
    _live_blue(st)
    st.image("bbb222")
    (st.fake / "unhealthy").touch()
    r = st.bg("start-idle", "bbb222")
    assert r.returncode == 5 and "did not become healthy" in r.stderr
    assert not (st.fake / "running" / "deploy-api-green-1").exists()
    assert st.state()["ACTIVE"] == "blue" and st.upstream() == "server api-blue:8001;"
    assert not (st.fake / "reloads").exists()


def test_start_idle_needs_the_image(st):
    _live_blue(st)
    r = st.bg("start-idle", "ghost999")
    assert r.returncode != 0 and "does not exist" in r.stderr
    assert not (st.fake / "running" / "deploy-api-green-1").exists()


def test_switch_refuses_an_idle_colour_that_is_not_running_or_healthy(st):
    _live_blue(st)
    r = st.bg("switch")
    assert r.returncode == 5 and "not running" in r.stderr
    assert st.upstream() == "server api-blue:8001;"


def test_nginx_refusing_the_new_upstream_puts_the_old_line_back(st):
    _live_blue(st)
    st.image("bbb222")
    assert st.bg("start-idle", "bbb222").returncode == 0
    (st.fake / "nginx_t_rc").write_text("1")
    r = st.bg("switch")
    assert r.returncode == 6
    assert st.upstream() == "server api-blue:8001;" and st.state()["ACTIVE"] == "blue"
    assert not list((st.web / "upstream").glob("*.new"))


def test_a_failed_reload_restores_and_reloads_again(st):
    _live_blue(st)
    st.image("bbb222")
    assert st.bg("start-idle", "bbb222").returncode == 0
    (st.fake / "nginx_reload_rc").write_text("1")
    r = st.bg("switch")
    assert r.returncode == 6 and st.upstream() == "server api-blue:8001;" and st.state()["ACTIVE"] == "blue"


def test_the_edge_not_answering_from_the_new_colour_switches_back(st):
    _live_blue(st)
    st.image("bbb222")
    assert st.bg("start-idle", "bbb222").returncode == 0
    # the edge keeps answering from blue (e.g. the reload did not take): the verification must catch it
    shim = (st.bin / "docker").read_text().replace('cp "$FAKE/http/deploy-api-$c-1/8001_api_health" "$FAKE/edge_health" 2>/dev/null', ':')
    (st.bin / "docker").write_text(shim, newline="\n")
    r = st.bg("switch")
    assert r.returncode == 6 and "switching back" in r.stderr
    assert st.upstream() == "server api-blue:8001;" and st.state()["ACTIVE"] == "blue"


def test_drain_never_stops_a_colour_with_requests_in_flight(st):
    _live_blue(st)
    st.image("bbb222")
    assert st.bg("start-idle", "bbb222").returncode == 0 and st.bg("switch").returncode == 0
    st.inflight("deploy-api-blue-1", 2)                       # the old colour still serves two requests
    r = st.bg("drain-old", "0", "1")
    assert r.returncode == 3 and "leaving it running" in r.stdout
    assert (st.fake / "running" / "deploy-api-blue-1").exists()
    st.inflight("deploy-api-blue-1", 0)
    assert st.bg("drain-old", "0", "20").returncode == 0


def test_rollback_goes_back_to_the_old_colour_while_it_still_runs(st):
    _live_blue(st)
    st.image("bbb222")
    assert st.bg("start-idle", "bbb222").returncode == 0 and st.bg("switch").returncode == 0
    r = st.bg("rollback")
    assert r.returncode == 0, r.stdout + r.stderr
    s = st.state()
    assert (s["ACTIVE"], s["PREV_ACTIVE"]) == ("blue", "green")
    assert st.upstream() == "server api-blue:8001;"
    assert (st.fake / "reloads").read_text().split() == ["green", "blue"]


def test_rollback_restarts_a_stopped_old_colour(st):
    _live_blue(st)
    st.image("bbb222")
    assert st.bg("start-idle", "bbb222").returncode == 0 and st.bg("switch").returncode == 0
    assert st.bg("drain-old", "0", "20").returncode == 0 and st.bg("stop-old").returncode == 0
    r = st.bg("rollback")
    assert r.returncode == 0, r.stdout + r.stderr
    assert st.state()["ACTIVE"] == "blue" and st.state()["BLUE_SHA"] == "aaa111"
    assert (st.fake / "running" / "deploy-api-blue-1").exists()


def test_second_deploy_reuses_the_other_colour(st):
    """blue -> green -> blue: the idle colour alternates and always carries the newest image."""
    _live_blue(st)
    for n, (sha, want) in enumerate((("bbb222", "green"), ("ccc333", "blue"))):
        st.image(sha)
        assert st.bg("start-idle", sha).returncode == 0
        assert st.bg("switch").returncode == 0
        assert st.state()["ACTIVE"] == want and st.state()[want.upper() + "_SHA"] == sha
        assert st.bg("drain-old", "0", "20").returncode == 0 and st.bg("stop-old").returncode == 0


def test_state_command_reports_both_colours_and_the_worker(st):
    _live_blue(st)
    r = st.bg("state")
    assert r.returncode == 0
    assert "active=blue" in r.stdout and "api-blue: running, healthy" in r.stdout and "api-green: stopped" in r.stdout
    assert "worker: stopped" in r.stdout


def test_exec_runs_in_the_live_colour_or_the_worker(st):
    _live_blue(st)
    assert st.bg("exec", "api", "echo", "hi").returncode == 0
    assert "exec -i deploy-api-blue-1 echo hi" in st.log()
    assert st.bg("exec", "worker", "python", "-m", "x").returncode == 0
    assert "exec -i deploy-worker-1 python -m x" in st.log()
    assert st.bg("exec", "nope", "x").returncode != 0


def test_first_start_brings_up_worker_then_the_active_colour_then_the_pages(st):
    (st.fake / "running" / "deploy-web-1").unlink()           # a fresh server: nothing runs yet
    st.image("aaa111")
    assert st.bg("init", "aaa111").returncode == 0
    r = st.bg("first-start")
    assert r.returncode == 0, r.stdout + r.stderr
    ups = [ln.split()[-1] for ln in st.log().splitlines() if " up " in ln and "compose" in ln]
    assert ups == ["worker", "api-blue", "web"], "the worker first, the colour nginx will point at before nginx starts"
    assert "wget" not in st.log(), "no web container yet to ask"


def test_no_state_no_action(st):
    r = st.bg("switch")
    assert r.returncode != 0 and "no" in r.stderr and "state.env" in r.stderr


# ── deploy_module.sh: the worker part (the follow-up mode needs no tarball, no build, no public network) ──────────────────

IDLE = {"jobs": {"running": 0, "queued": 0, "kinds": {}}, "modules": {}, "requests_total": 0,
        "worker": {"reachable": True, "draining": False}}
BUSY = dict(IDLE, jobs={"running": 1, "queued": 0, "kinds": {"coupled.run": 1}})


def _deploy_worker(st: Stack, live: dict, sha="bbb222", maxwait="0"):
    (st.fake / "live.json").write_text(json.dumps(live))
    (st.app / ".deployed_commit").write_text(sha)
    st.image(sha)
    return subprocess.run([BASH, (DEPLOY / "deploy_module.sh").as_posix(), sha, sha, "worker", "all", maxwait],
                          env=st.env(), capture_output=True, text=True, timeout=120)


def _worker_world(st: Stack, old="aaa111"):
    """Live stack on `old`, the worker running on `old`."""
    _live_blue(st, old)
    st.running("deploy-worker-1")
    (st.fake / "http" / "deploy-worker-1").mkdir(parents=True, exist_ok=True)
    (st.fake / "http" / "deploy-worker-1" / "8002_api_health").write_text(
        json.dumps({"status": "healthy", "role": "worker", "instance": "worker", "build": old}))


def test_worker_follow_up_restarts_an_idle_worker_after_draining_it(st):
    _worker_world(st)
    r = _deploy_worker(st, IDLE)
    assert r.returncode == 0, r.stdout + r.stderr
    log = st.log()
    assert log.index("motor_ai_sim.worker drain on") < log.index("up -d --no-build --no-deps worker")
    ups = [ln for ln in log.splitlines() if "compose" in ln and (" up " in ln or " stop " in ln)]
    assert ups and all(ln.endswith(" worker") for ln in ups), "only the worker is (re)started: %s" % ups
    assert st.state()["WORKER_SHA"] == "bbb222" and st.state()["BLUE_SHA"] == "aaa111"
    assert (st.app / ".worker_commit").read_text().strip() == "bbb222"
    assert "worker: running bbb222" in r.stdout


def test_worker_follow_up_never_restarts_a_worker_that_has_a_job(st):
    _worker_world(st)
    r = _deploy_worker(st, BUSY, maxwait="0")
    assert r.returncode == 7 and "WORKER PENDING" in r.stdout
    log = st.log()
    assert "drain on" not in log and "--no-deps worker" not in log, "nothing was drained or restarted"
    assert st.state()["WORKER_SHA"] == "aaa111"
    assert (st.app / ".worker_commit").read_text().strip() == "aaa111", "plan_deploy must still see the old worker commit"
    assert "deploy_module.sh bbb222 bbb222 worker" in r.stdout, "the message names the follow-up command"


def test_a_request_that_slips_in_while_draining_is_waited_for(st):
    _worker_world(st)
    (st.fake / "worker_busy_once").touch()
    r = _deploy_worker(st, IDLE)
    assert r.returncode == 0, r.stdout + r.stderr
    log = st.log()
    assert "arrived while draining" in r.stdout
    assert log.count("worker drain on") == 2 and log.count("worker drain off") == 1
    assert log.rindex("worker drain on") < log.index("--no-deps worker")


def test_a_worker_that_is_not_running_is_just_started(st):
    _worker_world(st)
    (st.fake / "running" / "deploy-worker-1").unlink()
    (st.fake / "http" / "deploy-worker-1" / "8002_api_health").unlink()
    r = _deploy_worker(st, BUSY)               # (the live answer is irrelevant: there is no worker to wait for)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "not running - starting it" in r.stdout and "drain on" not in st.log()
    assert st.state()["WORKER_SHA"] == "bbb222"


def test_a_new_worker_that_never_gets_healthy_is_rolled_back(st):
    _worker_world(st)
    (st.fake / "unhealthy").touch()
    r = _deploy_worker(st, IDLE)
    assert r.returncode == 6 and "ROLLBACK of the worker to aaa111" in r.stdout
    assert st.state()["WORKER_SHA"] == "aaa111"
    assert st.log().count("--no-deps worker") == 2


def test_mode_worker_without_its_image_is_refused(st):
    _worker_world(st)
    (st.fake / "live.json").write_text(json.dumps(IDLE))
    (st.app / ".deployed_commit").write_text("ccc")
    r = subprocess.run([BASH, (DEPLOY / "deploy_module.sh").as_posix(), "ccc", "ccc", "worker"], env=st.env(),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 5 and "follow-up" in r.stdout


def test_deploy_module_needs_the_cutover_first(st):
    r = subprocess.run([BASH, (DEPLOY / "deploy_module.sh").as_posix(), "a", "b", "api"], env=st.env(),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 3 and "cutover" in r.stdout
    r = subprocess.run([BASH, (DEPLOY / "deploy_module.sh").as_posix(), "a", "b", "bogus"], env=st.env(),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2


# ── the cutover, run for real against the fake docker ───────────────────────────────────────────────────────────────────────

import tarfile

OLD_LIVE = {"requests_total": 0, "jobs": {"running": 0, "queued": 0, "kinds": {}}, "modules": {}}


def _stage1_world(st: Stack, prev="prev000", live=None):
    """Stage 1: the old single `api` container serves, `web` runs, the deployed commit is `prev`."""
    st.running("deploy-api-1")
    (st.fake / "http" / "deploy-api-1").mkdir(parents=True, exist_ok=True)
    (st.fake / "http" / "deploy-api-1" / "8001_api_admin_live").write_text(json.dumps(live or OLD_LIVE))
    (st.fake / "images" / "deploy-web_latest").touch()
    (st.fake / "images" / "deploy-web_prev").touch()
    (st.app / ".deployed_commit").write_text(prev)
    (st.app / "marker_stage1").write_text("stage 1 tree")
    (st.tmp / "state_root" / "workspaces").mkdir(parents=True, exist_ok=True)
    (st.tmp / "state_root" / "identity").mkdir(parents=True, exist_ok=True)
    (st.tmp / "api.env").write_text("AUTH_SECRET=x\n")
    with tarfile.open(st.tmp / "deploy_main.tar", "w") as tf:           # `git archive` of the new commit: the real deploy dir
        for f in DEPLOY.rglob("*"):
            if f.is_file() and "__pycache__" not in f.parts:
                tf.add(f, arcname=("deploy/" + f.relative_to(DEPLOY).as_posix()))
        tf.add(ROOT / "VERSION", arcname="VERSION")


def _cutover(st: Stack, *args, extra=None):
    env = st.env()
    tar_path = st.p(st.tmp / "deploy_main.tar")
    if os.name == "nt":                                    # GNU tar reads "C:/x" as host "C": use the MSYS spelling
        tar_path = "/" + tar_path[0].lower() + tar_path[2:]
    env.update(MOTRES_UID="0", MOTRES_TARBALL=tar_path, MOTRES_API_ENV=st.p(st.tmp / "api.env"),
               MOTRES_STATE_ROOT=st.p(st.tmp / "state_root"), MOTRES_SKIP_RESOURCE_CHECKS="1", API_SETTLE_S="0")
    env.update(extra or {})
    return subprocess.run([BASH, (DEPLOY / "cutover_stage2.sh").as_posix(), *args], env=env, capture_output=True, text=True,
                          timeout=300)


def test_cutover_check_passes_on_a_stage1_server_and_changes_nothing(st):
    _stage1_world(st)
    r = _cutover(st, "--check", "new111", "prev000")
    assert r.returncode == 0 and "PREFLIGHT OK" in r.stdout, r.stdout + r.stderr
    assert not (st.run / "state.env").exists() and "compose build" not in st.log()


def test_cutover_go_moves_to_the_new_stack_in_the_right_order(st):
    _stage1_world(st)
    r = _cutover(st, "--go", "new111", "prev000")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "CUTOVER DONE on new111" in r.stdout
    s = st.state()
    assert (s["ACTIVE"], s["BLUE_SHA"], s["WORKER_SHA"]) == ("blue", "new111", "new111")
    assert st.upstream() == "server api-blue:8001;"
    run = lambda n: (st.fake / "running" / n).exists()      # noqa: E731
    assert run("deploy-worker-1") and run("deploy-api-blue-1") and run("deploy-web-1") and not run("deploy-api-1"), \
        "the old api is stopped, the new stack runs"
    log = [ln for ln in st.log().splitlines() if ln.startswith("docker")]
    pos = lambda needle: next(i for i, ln in enumerate(log) if needle in ln)            # noqa: E731
    assert pos("compose build api-image") < pos("--entrypoint nginx") < pos("--no-deps worker") < pos("--no-deps api-blue") \
        < pos("--no-deps web") < pos("stop deploy-api-1"), "build + parse the config first, the old api is stopped last"
    assert not any("remove-orphans" in ln or " down" in ln or "prune" in ln for ln in log)
    assert (st.app / ".deployed_commit").read_text().strip() == "new111"
    assert (st.app / ".worker_commit").read_text().strip() == "new111"
    assert (st.tmp / "app.stage1" / "marker_stage1").exists(), "the stage-1 tree is kept for --rollback"
    assert (st.web / "builds" / "new111").is_dir(), "step 9 published the pages of the new commit"


def test_cutover_never_starts_while_a_job_runs(st):
    _stage1_world(st, live=dict(OLD_LIVE, jobs={"running": 1, "queued": 0, "kinds": {"coupled.run": 1}}))
    r = _cutover(st, "--go", "new111", "prev000", "0")
    assert r.returncode == 4 and "ABORT 4" in r.stderr
    assert not (st.run / "state.env").exists()
    assert (st.app / "marker_stage1").exists(), "the stage-1 tree is untouched"
    assert (st.fake / "running" / "deploy-api-1").exists() and not (st.fake / "running" / "deploy-worker-1").exists()


def test_cutover_an_unparsable_nginx_config_changes_nothing(st):
    _stage1_world(st)
    (st.fake / "nginx_image_t_rc").write_text("1")
    r = _cutover(st, "--go", "new111", "prev000")
    assert r.returncode == 5 and "nginx -t fails" in r.stderr
    assert not (st.run / "state.env").exists() and (st.app / "marker_stage1").exists()


def test_cutover_rolls_back_when_the_worker_never_gets_healthy(st):
    _stage1_world(st)
    (st.fake / "unhealthy").touch()
    r = _cutover(st, "--go", "new111", "prev000")
    assert r.returncode == 5 and "rolled back" in r.stderr
    assert (st.app / "marker_stage1").exists(), "the stage-1 tree is back"
    assert not (st.run / "state.env").exists() and not (st.web / "upstream" / "api.conf").exists()
    assert (st.fake / "running" / "deploy-api-1").exists(), "stage 1 never stopped serving"
    assert not (st.fake / "running" / "deploy-worker-1").exists()


def test_cutover_rolls_back_when_the_edge_does_not_reach_the_worker(st):
    _stage1_world(st)
    (st.fake / "edge_worker_wrong").touch()                # a worker route answered by the API: the nginx regex is not live
    r = _cutover(st, "--go", "new111", "prev000")
    assert r.returncode == 6 and "not answered by the worker" in r.stderr
    assert (st.app / "marker_stage1").exists() and (st.fake / "running" / "deploy-api-1").exists()
    assert not (st.run / "state.env").exists()
    log = st.log()
    assert log.rindex("tag deploy-web:prev deploy-web:latest") < log.rindex("--no-deps web"), \
        "the stage-1 web image is restored before the web container is recreated"


# ── the cutover script: only its guards (the full run needs a real docker) ───────────────────────────────────────────

def test_cutover_usage_and_preflight_refuse_without_a_server(st):
    r = st.bg("--go", script="cutover_stage2.sh")
    assert r.returncode == 2
    r = st.bg("--check", "abc", "def", script="cutover_stage2.sh")
    assert r.returncode == 1 and "PREFLIGHT FAILED" in r.stdout
    assert "FAIL" in r.stdout
    assert not (st.run / "state.env").exists(), "--check changes nothing"
