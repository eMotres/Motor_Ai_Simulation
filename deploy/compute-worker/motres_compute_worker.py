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
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
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


def execute(post: Callable[[str, Dict[str, Any]], Tuple[int, Dict[str, Any]]],
            token: str, leased: Dict[str, Any],
            progress_s: float = PROGRESS_S) -> str:
    """Run one leased job end to end.  Returns the final state it reported.

    ``post(path, body)`` is the transport (an :class:`Api` method, or a test
    client), so the whole lifecycle is testable without a network.
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
    from motor_ai_sim import jobs as J
    box: Dict[str, Any] = {}

    def _run() -> None:
        t0 = time.thread_time()
        token_ = J._IN_JOB.set(rid)
        try:
            box["result"] = _handler(str(bundle.get("kind")))(dict(bundle.get("body") or {}))
        except BaseException as e:                      # noqa: BLE001
            box["error"] = e
            box["tb"] = traceback.format_exc(limit=3)
        finally:
            J._IN_JOB.reset(token_)
            box["cpu_s"] = time.thread_time() - t0

    wall0 = time.time()
    th = threading.Thread(target=_run, name="job-" + rid, daemon=True)
    th.start()
    cancelled = False
    while th.is_alive():
        th.join(progress_s)
        if not th.is_alive():
            break
        code, ans = post(base + "/progress", {"phase": "running",
                                              "cpu_s": time.process_time()})
        if ans.get("cancel") or code in (404, 409):
            cancelled = True
            J.queue().cancel(rid, requester="", is_admin=True)   # handlers poll this
            th.join(30)
            break
    cpu = float(box.get("cpu_s") or 0.0)
    wall = time.time() - wall0
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
