"""The compute worker process (deploy stage 2, docs/DEPLOY_STAGE2.md).

    python -m motor_ai_sim.worker            serve (the container's command)
    python -m motor_ai_sim.worker state      print this container's /api/worker/state (JSON)
    python -m motor_ai_sim.worker drain on   take no NEW job; queued and running ones finish
    python -m motor_ai_sim.worker drain off  lift it
    python -m motor_ai_sim.worker idle       exit 0 if nothing is queued, running or in flight (the deploy's check)

It is the same application as the API (one image, ``MOTRES_ROLE=worker``): the routes nginx sends it
(``roles.WORKER_PREFIXES``) run the solves in the existing job queue (``jobs.InProcessQueue`` - priority, fairness per
account, ``QUEUE_WORKERS`` slots), so what "claims a job" here is the queue's own admission, unchanged.  What the worker adds
is the process boundary: an API update never touches it, and it is restarted only when idle - drain, wait, restart.

Port 8002 inside the compose network (``WORKER_PORT``); never published to the host.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_PORT = 8002


def _port() -> int:
    try:
        return int(os.environ.get("WORKER_PORT", "") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def _loopback(method: str, path: str, body=None, timeout: float = 10.0) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (_port(), path), data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:                      # noqa: S310 - loopback
        return json.loads(r.read().decode("utf-8") or "{}")


def idle(state: dict) -> bool:
    live = state.get("live") or {}
    jobs = live.get("jobs") or {}
    return not (jobs.get("running") or jobs.get("queued") or live.get("requests_total"))


def serve() -> None:
    os.environ.setdefault("MOTRES_ROLE", "worker")
    import uvicorn
    uvicorn.run("motor_ai_sim.api:app", host=os.environ.get("WORKER_HOST", "0.0.0.0"), port=_port(),
                log_level=os.environ.get("WORKER_LOG_LEVEL", "info"))


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "serve"
    try:
        if cmd == "serve":
            serve()
            return 0
        if cmd == "state":
            print(json.dumps(_loopback("GET", "/api/worker/state"), indent=1))
            return 0
        if cmd == "drain":
            on = (args[1] if len(args) > 1 else "on").lower() in ("on", "1", "true", "yes")
            print(json.dumps(_loopback("POST", "/api/worker/drain", {"on": on})))
            return 0
        if cmd == "idle":
            st = _loopback("GET", "/api/worker/state")
            return 0 if idle(st) else 1
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print("worker not reachable on 127.0.0.1:%d: %s" % (_port(), exc), file=sys.stderr)
        return 2
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main())
