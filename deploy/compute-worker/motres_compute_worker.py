#!/usr/bin/env python3
"""eMotres compute worker: runs YOUR jobs on YOUR server (docs/BYO_COMPUTE.md).

Outbound HTTPS only (no inbound ports, works behind NAT).  Config from the
environment (the systemd unit loads /etc/motres-compute-worker.env, 0600):

  MOTRES_URL               platform base URL, e.g. https://app.emotres.com
  MOTRES_NODE_TOKEN        mcnode_... (from "My compute nodes"; shown once)
  MOTRES_POLL_S            idle poll interval, default 10
  MOTRES_PLATFORM_VERSION  solver version this worker runs (baked by the
                           installer / image); must equal the platform's
  MOTRES_IMAGE_DIGEST      container image digest, reported as provenance

Loop: heartbeat (host sample) -> lease -> verify the bundle signature and
version -> run the job's handler IN-PROCESS with the installed motor_ai_sim
(the same code the platform runs) -> stream progress (which also renews the
lease and carries Stop) -> post the signed result + provenance.

``--once`` leases at most one job, runs it, and exits (smoke test).

Idle sharing (docs/BYO_COMPUTE.md section 14).  If the node's owner switched on
"Share idle time with MOTRES", the platform may lease a MOTRES job (signed and
version-pinned like any other) when the owner has nothing queued.  Such a
bundle carries ``shared: true`` and ``limits``; the worker then

* runs it in a CHILD PROCESS at nice 19 / idle IO, pinned to at most
  ``max_cores`` CPUs and watched against ``max_ram_mb``, so the owner's own
  jobs and the rest of the box are not slowed;
* polls the platform every few seconds; the answer says ``preempt`` the moment
  the owner submits a job or presses "stop sharing now", and the child is
  killed and the job handed back with ``/release`` (a re-queue, not a failure;
  a checkpoint is sent along when the job kind supports it).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

PROGRESS_S = 10.0
HEARTBEAT_S = 15.0


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign(token: str, obj: Any) -> str:
    """HMAC-SHA256 keyed by sha256(token): the key the platform also holds."""
    key = hashlib.sha256(token.encode("utf-8")).hexdigest().encode("ascii")
    return hmac.new(key, _canon(obj), hashlib.sha256).hexdigest()


def local_version() -> str:
    v = os.environ.get("MOTRES_PLATFORM_VERSION", "").strip()
    if v:
        return v
    try:
        from motor_ai_sim import compute_nodes as CN
        return CN.platform_version()
    except Exception:                                   # noqa: BLE001
        return "0"


def host_sample() -> Dict[str, Any]:
    """Linux: the #34 node agent's sampler if it sits next to us; else a
    minimal portable sample (cores + load)."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, os.path.join(here, "..", "node-agent"))
        import motres_node_agent as NA                  # type: ignore
        s = NA.Sampler()
        time.sleep(0.5)
        out = s.sample()
        out["procs"] = out.get("procs", [])[:10]
        return out
    except Exception:                                   # noqa: BLE001
        load = list(os.getloadavg()) if hasattr(os, "getloadavg") else [0, 0, 0]
        return {"cores": os.cpu_count() or 1, "load": load,
                "cpu": {"total": 0.0, "per_core": []}}


class Api:
    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.token = token

    def post(self, path: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        req = urllib.request.Request(
            self.url + path, method="POST", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.token})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}


def _handler(kind: str) -> Callable[[Dict[str, Any]], Any]:
    from motor_ai_sim import compute_nodes  # noqa: F401  (registers kinds)
    from motor_ai_sim import jobs as J
    fn = J.HANDLERS.get(kind)
    if fn is None:
        raise RuntimeError("this worker has no handler for %r" % kind)
    return fn


class ThreadRunner:
    """An owner's own job: the handler runs in-process on a thread (Stage 1)."""

    def __init__(self, bundle: Dict[str, Any]) -> None:
        self.bundle = bundle
        self.rid = str(bundle.get("run_id") or "")
        self.box: Dict[str, Any] = {}
        self.th = threading.Thread(target=self._run, name="job-" + self.rid, daemon=True)

    def _run(self) -> None:
        from motor_ai_sim import compute_nodes as CN
        from motor_ai_sim import jobs as J
        t0 = time.thread_time()
        token_ = J._IN_JOB.set(self.rid)
        ck = CN.CHECKPOINT_SINK.set(lambda obj: self.box.__setitem__("checkpoint", obj))
        try:
            body = dict(self.bundle.get("body") or {})
            if self.bundle.get("resume_from") is not None:
                body["_resume"] = self.bundle["resume_from"]
            self.box["result"] = _handler(str(self.bundle.get("kind")))(body)
        except BaseException as e:                      # noqa: BLE001
            self.box["error"] = e
            self.box["tb"] = traceback.format_exc(limit=3)
        finally:
            CN.CHECKPOINT_SINK.reset(ck)
            J._IN_JOB.reset(token_)
            self.box["cpu_s"] = time.thread_time() - t0

    def start(self) -> None:
        self.th.start()

    def alive(self) -> bool:
        return self.th.is_alive()

    def wait(self, timeout: float) -> None:
        self.th.join(timeout)

    def cpu_now(self) -> float:
        return time.process_time()

    def rss_mb(self) -> float:
        return 0.0

    def stop(self) -> None:
        from motor_ai_sim import jobs as J
        J.queue().cancel(self.rid, requester="", is_admin=True)   # handlers poll this
        self.th.join(30)


def shared_plan(limits: Dict[str, Any]) -> Dict[str, Any]:
    """How a shared job is boxed in on THIS host, from the bundle's ``limits``
    (the owner's caps; ``None`` = take half of what this host has).  Pure, so it
    is testable anywhere."""
    host_cores = os.cpu_count() or 1
    cores = limits.get("max_cores")
    cores = max(1, min(int(cores), host_cores)) if cores else max(1, host_cores // 2)
    ram = limits.get("max_ram_mb")
    if not ram:
        pages = os.sysconf("SC_PHYS_PAGES") if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names else 0
        psize = os.sysconf("SC_PAGE_SIZE") if pages else 0
        ram = max(256, int(pages * psize / (1024 * 1024) / 2)) if pages else 0
    env = {k: str(cores) for k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS",
                                   "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS")}
    return {"cores": cores, "ram_mb": int(ram or 0), "nice": int(limits.get("nice") or 19),
            "ionice_idle": (limits.get("ionice") or "idle") == "idle", "env": env}


def _rss_mb(pid: int) -> float:
    try:
        import psutil                                    # type: ignore
        pr = psutil.Process(pid)
        return sum(c.memory_info().rss for c in [pr] + pr.children(recursive=True)) / 1048576.0
    except Exception:                                   # noqa: BLE001
        pass
    try:
        with open("/proc/%d/status" % pid) as fh:
            for ln in fh:
                if ln.startswith("VmRSS:"):
                    return int(ln.split()[1]) / 1024.0
    except (OSError, ValueError):
        pass
    return 0.0


class ProcessRunner:
    """A SHARED job: a child process at nice 19 / idle IO, limited to N CPUs,
    killed on preemption.  The child is this script with ``--run-bundle``."""

    def __init__(self, bundle: Dict[str, Any]) -> None:
        self.bundle = bundle
        self.rid = str(bundle.get("run_id") or "")
        self.plan = shared_plan(dict(bundle.get("limits") or {}))
        self.box: Dict[str, Any] = {}
        self.proc: Optional[subprocess.Popen] = None
        self.reader: Optional[threading.Thread] = None
        self._rusage0 = 0.0

    def _cmd(self) -> list:
        cmd = [sys.executable, os.path.abspath(__file__), "--run-bundle"]
        if self.plan["ionice_idle"] and shutil.which("ionice"):
            cmd = ["ionice", "-c3"] + cmd
        return cmd

    def _preexec(self) -> None:                         # runs in the child, POSIX only
        os.setsid()
        os.nice(self.plan["nice"])
        if hasattr(os, "sched_setaffinity"):
            cur = sorted(os.sched_getaffinity(0))
            os.sched_setaffinity(0, set(cur[:self.plan["cores"]]))

    def start(self) -> None:
        env = dict(os.environ)
        env.update(self.plan["env"])
        kw: Dict[str, Any] = {}
        if os.name == "posix":
            kw["preexec_fn"] = self._preexec
        self.proc = subprocess.Popen(self._cmd(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=env, text=True, **kw)
        req = {"kind": self.bundle.get("kind"), "run_id": self.rid,
               "body": dict(self.bundle.get("body") or {})}
        if self.bundle.get("resume_from") is not None:
            req["body"]["_resume"] = self.bundle["resume_from"]
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(req))
        self.proc.stdin.close()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for ln in self.proc.stdout:
            try:
                m = json.loads(ln)
            except ValueError:
                continue
            if "checkpoint" in m:
                self.box["checkpoint"] = m["checkpoint"]
            elif "result" in m:
                self.box["result"] = m["result"]
                self.box["cpu_s"] = float(m.get("cpu_s") or 0.0)
            elif "error" in m:
                self.box["error"] = RuntimeError(str(m["error"]))
                self.box["tb"] = str(m.get("tb") or "")
                self.box["cpu_s"] = float(m.get("cpu_s") or 0.0)

    def alive(self) -> bool:
        return self.proc is not None and (self.proc.poll() is None
                                          or (self.reader is not None and self.reader.is_alive()))

    def wait(self, timeout: float) -> None:
        try:
            assert self.proc is not None
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            return
        if self.reader is not None:
            self.reader.join(5)
        if "result" not in self.box and "error" not in self.box:
            self.box["error"] = RuntimeError("shared job process exited with code %s" % self.proc.returncode)

    def cpu_now(self) -> float:
        return float(self.box.get("cpu_s") or 0.0)

    def rss_mb(self) -> float:
        return _rss_mb(self.proc.pid) if self.proc is not None and self.proc.poll() is None else 0.0

    def stop(self) -> None:
        pr = self.proc
        if pr is None or pr.poll() is not None:
            return
        try:
            if os.name == "posix":
                import signal
                os.killpg(os.getpgid(pr.pid), signal.SIGKILL)   # the child's whole group
            else:
                pr.kill()
        except OSError:
            pr.kill()
        try:
            pr.wait(10)
        except subprocess.TimeoutExpired:
            pass
        if self.reader is not None:
            self.reader.join(5)


def run_bundle_child() -> int:
    """``--run-bundle``: read ``{kind, run_id, body}`` on stdin, run the handler,
    write ``checkpoint`` / ``result`` / ``error`` JSON lines on stdout."""
    req = json.loads(sys.stdin.read() or "{}")
    real, sys.stdout = sys.stdout, sys.stderr            # handler prints stay off the wire

    def emit(o: Dict[str, Any]) -> None:
        real.write(json.dumps(o, default=str) + "\n")
        real.flush()

    def cpu() -> float:
        t = time.process_time()
        try:
            import resource
            ru = resource.getrusage(resource.RUSAGE_CHILDREN)
            t += ru.ru_utime + ru.ru_stime
        except ImportError:
            pass
        return t

    from motor_ai_sim import compute_nodes as CN
    from motor_ai_sim import jobs as J
    CN.CHECKPOINT_SINK.set(lambda obj: emit({"checkpoint": obj}))
    J._IN_JOB.set(str(req.get("run_id") or ""))
    try:
        res = _handler(str(req.get("kind")))(dict(req.get("body") or {}))
        emit({"result": json.loads(json.dumps(res, default=str)), "cpu_s": cpu()})
    except BaseException as e:                          # noqa: BLE001
        emit({"error": repr(e)[:300], "tb": traceback.format_exc(limit=3), "cpu_s": cpu()})
    return 0


def execute(post: Callable[[str, Dict[str, Any]], Tuple[int, Dict[str, Any]]],
            token: str, leased: Dict[str, Any],
            progress_s: float = PROGRESS_S,
            runner_factory: Optional[Callable[[Dict[str, Any]], Any]] = None) -> str:
    """Run one leased job end to end.  Returns the final state it reported.

    ``post(path, body)`` is the transport (an :class:`Api` method, or a test
    client), so the whole lifecycle is testable without a network.
    ``runner_factory(bundle)`` picks how the job runs: an owner's job on a
    thread, a SHARED job (``bundle.shared``) in a limited child process.
    """
    bundle = leased.get("bundle") or {}
    rid = str(bundle.get("run_id") or "")
    base = "/api/nodes/jobs/%s" % rid
    if not hmac.compare_digest(str(leased.get("signature") or ""), sign(token, bundle)):
        post(base + "/fail", {"error": "bundle signature invalid; refused"})
        return "refused"
    if str(bundle.get("platform_version")) != local_version():
        post(base + "/fail", {"error": "version mismatch: job %s, worker %s"
                              % (bundle.get("platform_version"), local_version())})
        return "refused"
    shared = bool(bundle.get("shared"))
    factory = runner_factory or (ProcessRunner if shared else ThreadRunner)
    runner = factory(bundle)
    wall0 = time.time()
    runner.start()
    interval = min(progress_s, float(bundle.get("progress_s") or progress_s)) if shared else progress_s
    ram_cap = float((bundle.get("limits") or {}).get("max_ram_mb") or 0) if shared else 0.0
    cancelled = False
    preempt = ""
    sent_ck: Any = None
    while runner.alive():
        runner.wait(interval)
        if not runner.alive():
            break
        body: Dict[str, Any] = {"phase": "running", "cpu_s": runner.cpu_now()}
        ck = runner.box.get("checkpoint")
        if ck is not None and ck is not sent_ck:
            body["checkpoint"] = ck
            sent_ck = ck
        code, ans = post(base + "/progress", body)
        if ans.get("preempt"):
            preempt = str(ans["preempt"])
            break
        if ans.get("cancel") or code in (404, 409):
            cancelled = True
            break
        if ram_cap and runner.rss_mb() > ram_cap:
            preempt = "ram_cap"
            break
    if preempt or cancelled:
        runner.stop()
    box = runner.box
    cpu = float(box.get("cpu_s") or runner.cpu_now() or 0.0)
    wall = time.time() - wall0
    if preempt:
        post(base + "/release", {"reason": preempt, "cpu_s": cpu,
                                 "checkpoint": box.get("checkpoint")})
        return "preempted"
    if cancelled:
        post(base + "/fail", {"error": "cancelled", "cpu_s": cpu})
        return "cancelled"
    if "error" in box:
        post(base + "/fail", {"error": repr(box["error"])[:300], "cpu_s": cpu})
        return "failed"
    result = json.loads(json.dumps(box.get("result"), default=str))
    payload = {"result": result,
               "signature": sign(token, {"run_id": rid, "result": result}),
               "provenance": {"cpu_s": cpu, "wall_s": wall, "version": local_version(),
                              "image_digest": os.environ.get("MOTRES_IMAGE_DIGEST", "")}}
    code, _ = post(base + "/complete", payload)
    return "done" if code == 200 else "rejected:%d" % code


def main() -> int:
    if "--run-bundle" in sys.argv:
        return run_bundle_child()
    url = os.environ.get("MOTRES_URL", "").strip()
    token = os.environ.get("MOTRES_NODE_TOKEN", "").strip()
    poll = float(os.environ.get("MOTRES_POLL_S", "10") or 10)
    if not url or not token:
        print("MOTRES_URL and MOTRES_NODE_TOKEN are required", file=sys.stderr)
        return 2
    api = Api(url, token)
    once = "--once" in sys.argv
    last_hb = 0.0
    while True:
        try:
            if time.time() - last_hb >= HEARTBEAT_S:
                code, hb = api.post("/api/nodes/heartbeat",
                                    {"sample": host_sample(), "version": local_version()})
                last_hb = time.time()
                if code == 401:
                    print("node token rejected (revoked?)", file=sys.stderr)
                    return 3
                if hb.get("version_ok") is False:
                    print("platform is %s, this worker %s: update the worker"
                          % (hb.get("platform_version"), local_version()), file=sys.stderr)
            code, ans = api.post("/api/nodes/jobs/lease", {"version": local_version()})
            job: Optional[Dict[str, Any]] = ans.get("job") if code == 200 else None
            if code == 409:
                print("lease refused: %s" % ans.get("detail"), file=sys.stderr)
            if job:
                state = execute(api.post, token, job)
                print("job %s: %s" % (job["bundle"]["run_id"], state), flush=True)
                if once:
                    return 0
                continue
            if once:
                return 0
        except Exception as e:                          # noqa: BLE001
            print("worker loop: %s: %s" % (type(e).__name__, str(e)[:160]), file=sys.stderr)
        time.sleep(poll)


if __name__ == "__main__":
    sys.exit(main())
