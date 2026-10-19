"""Which process is this?  (deploy stage 2, docs/DEPLOY_STAGE2.md)

WHY
===
Until stage 2 one uvicorn process was everything: the pages' API, the FEM solves, the job queue, the optimizer
threads.  A restart of the API therefore had to wait for - or kill - every running computation.  Stage 2 runs the SAME
image as three kinds of process, selected by one variable:

``MOTRES_ROLE=all``      (default) today's behaviour, one process does everything - the workstation, a BYO install,
                         every test.
``MOTRES_ROLE=api``      the web-facing half: accounts, admin, catalog, production, support, the OAuth endpoints.  It never
                         runs a queued computation; two of them (blue / green) are started and switched by nginx so an
                         update is invisible to the users.
``MOTRES_ROLE=worker``   the compute half: every route that runs, watches or stops a computation, plus the routes that
                         read or write the live in-memory workbench state next to them (geometry, caches, last results).
                         It is restarted only when it is idle.

Which request goes where is decided by nginx (``deploy/nginx.conf``) from the table below - this module is the single
source, and ``tests/test_deploy_stage2_roles.py`` fails if the two drift apart or a route that queues a job is missing
from the table.

The state of a role is plain environment, read per call (a test can flip it).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from typing import Mapping, Optional

ENV_ROLE = "MOTRES_ROLE"
ENV_INSTANCE = "MOTRES_INSTANCE"          # blue | green | worker (informational, shown by /api/health)
ENV_BUILD_ID = "MOTRES_BUILD_ID"          # the git sha of the image (informational)
ENV_WORKER_URL = "MOTRES_WORKER_URL"      # how an API process reaches the worker
ENV_MULTI = "MOTRES_MULTI_PROCESS"        # force the cross-process file locks on (tests); implied by api / worker

ROLE_ALL, ROLE_API, ROLE_WORKER = "all", "api", "worker"
ROLES = (ROLE_ALL, ROLE_API, ROLE_WORKER)

DEFAULT_WORKER_URL = "http://worker:8002"
INTERNAL_HEADER = "x-motres-internal"


def role() -> str:
    r = str(os.environ.get(ENV_ROLE, "") or "").strip().lower()
    return r if r in ROLES else ROLE_ALL


def is_api() -> bool:
    return role() == ROLE_API


def is_worker() -> bool:
    return role() == ROLE_WORKER


def serves_compute() -> bool:
    """May THIS process run queued computations?  Everything but the ``api`` role."""
    return role() != ROLE_API


def multi_process() -> bool:
    """Do other processes of this deployment write the same files?  (-> cross-process file locks, state epoch)"""
    if role() != ROLE_ALL:
        return True
    return str(os.environ.get(ENV_MULTI, "")).strip().lower() in ("1", "true", "yes", "on")


def instance() -> str:
    return str(os.environ.get(ENV_INSTANCE, "") or "").strip()


def build_id() -> str:
    return str(os.environ.get(ENV_BUILD_ID, "") or "").strip()


def worker_url() -> str:
    return (str(os.environ.get(ENV_WORKER_URL, "") or "").strip() or DEFAULT_WORKER_URL).rstrip("/")


# ─────────────────────────────────────────────────────────────────────────────
#  The routing table: which request paths belong to the compute worker
# ─────────────────────────────────────────────────────────────────────────────

#: Path prefixes (matched on a segment boundary) served by the worker.  Why each one is here:
#:
#: * the solvers and their progress / cancel / last-result companions:
#:   simulation, static3d, mechanical, thermal, coupled, optimization, controller, system, kernel;
#: * ``/api/jobs`` - the job list / status / cancel read the in-memory queue and progress registry;
#: * ``/api/history``, ``/api/sims`` - run history and saved runs, written and loaded next to the solvers;
#: * ``/api/geometry``, ``/api/config``, ``/api/winding``, ``/api/mesh``, ``/api/parts`` and the material ASSIGNMENT
#:   (exact ``/api/materials``): they read and write the workspace's live in-memory machine (the geometry singleton, the
#:   mesh and physics caches), which the solvers read in the same process;
#: * ``/api/family/report`` - the report reads the four solver routers' in-memory last-result stores;
#: * ``/mcp`` - an agent's ``simulate`` queues a job and ``get_job`` reads it back from the queue it was queued on.
WORKER_PREFIXES = (
    "/api/simulation", "/api/static3d", "/api/mechanical", "/api/thermal", "/api/coupled", "/api/optimization",
    "/api/controller", "/api/system", "/api/kernel",
    "/api/jobs", "/api/history", "/api/sims",
    "/api/geometry", "/api/config", "/api/winding", "/api/mesh", "/api/parts",
    "/api/family/report",
    "/mcp",
)
#: Exact paths (the same path with a sub-path is NOT the worker's): the material assignment is the worker's, the
#: material library (``/api/materials/library``) and the global-material admin writes are not.
WORKER_EXACT = ("/api/materials",)
#: Regexes (matched from the start of the path) for the worker routes that do not form a prefix: the catalog passport is
#: generated by ``POST /api/catalog/{id}/passport`` - minutes of FEM, a 30-60 min Stage A subprocess on a miss - inside the
#: request, so it is a computation like any other.
WORKER_PATTERNS = (r"^/api/catalog/[^/]+/passport$",)
#: Prefixes that stay on the API although they sit under a worker prefix (none today; the hook is here so a future
#: carve-out is one line and a test, not an nginx regex edit).
API_CARVE_OUTS: tuple = ()


def is_worker_path(path: str) -> bool:
    """Does this request path belong to the compute worker?  (The Python twin of the nginx regex.)"""
    p = str(path or "").split("?", 1)[0]
    for c in API_CARVE_OUTS:
        if p == c or p.startswith(c + "/"):
            return False
    if p in WORKER_EXACT:
        return True
    if any(re.search(rx, p) for rx in WORKER_PATTERNS):
        return True
    return any(p == pfx or p.startswith(pfx + "/") for pfx in WORKER_PREFIXES)


def nginx_regex() -> str:
    """The ``location ~`` regex of ``deploy/nginx.conf`` for the worker's paths, built from the table above."""
    alts = "|".join(sorted(p.lstrip("/") for p in WORKER_PREFIXES))
    exact = "|".join(sorted(p.lstrip("/") for p in WORKER_EXACT))
    return "^/(?:%s)(?:/|$)|^/(?:%s)$" % (alts, exact) + "".join("|" + rx for rx in WORKER_PATTERNS)


class InstanceHeaderMiddleware:
    """Adds ``X-Motres-Instance: <blue|green|worker>`` to every HTTP response of a process that has an instance name.

    The deploy scripts read it from the public edge to prove WHICH process answered (the blue-green switch, the worker
    routes).  Nothing in it is secret.  No instance name (the workstation, the tests) = no header, no overhead.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        name = instance()
        if scope.get("type") != "http" or not name:
            return await self.app(scope, receive, send)

        async def _send(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"x-motres-instance", name.encode("latin-1", "replace")))
                message = dict(message, headers=headers)
            await send(message)

        return await self.app(scope, receive, _send)


# ─────────────────────────────────────────────────────────────────────────────
#  The internal channel (API -> worker state, deploy script -> drain)
# ─────────────────────────────────────────────────────────────────────────────

def internal_key() -> str:
    """A key only the processes of this deployment can compute: HMAC of the shared auth secret.

    The API reads the worker's live state with it; nothing is stored, nothing new has to be put in ``api.env``.  Empty
    string when the secret store is unavailable (then the internal channel is closed, never open).
    """
    try:
        from motor_ai_sim import users as _U
        secret = _U._secret()
    except Exception:                                   # noqa: BLE001
        return ""
    return hmac.new(secret.encode("utf-8"), b"motres-internal-v1", hashlib.sha256).hexdigest()


def internal_headers() -> dict:
    key = internal_key()
    return {INTERNAL_HEADER: key} if key else {}


def internal_ok(headers: Optional[Mapping[str, str]]) -> bool:
    """Does this request carry the deployment's internal key?  (Constant-time compare.)"""
    if not headers:
        return False
    try:
        presented = headers.get(INTERNAL_HEADER) or headers.get(INTERNAL_HEADER.title()) or ""
    except Exception:                                   # noqa: BLE001
        return False
    key = internal_key()
    return bool(key) and bool(presented) and hmac.compare_digest(str(presented), key)
