"""WHOSE machine is this call about?  (migration Stage 1)

WHY
===
Auth, tiers, sessions and per-user die grants already work.  What is single-user
is the PROCESS: one ``config/`` directory, one parsed-config slot, and ~25 disk
stores all derived from ``config.DEFAULT_CONFIG_PATH`` — a constant bound once,
at import, from the ``MOTOR_AI_SIM_CONFIG`` env var.  With several users on one
server that constant means *one customer's solve overwrites another's machine*:
the same class of incident as 2026-08-06 (a test run replaced the user's live
150 mm CIANO28 while they were working, ``config.py``), except with paying
customers on both ends.

``MOTOR_AI_SIM_CONFIG`` was already the right LEVER — the 2026-09-15 audit
(``tests/test_config_redirect_is_complete.py``) enumerated all 25 stores that
move with it.  This module generalises that lever from a process-wide env var
to a per-request resolver, so the whole enumerated list comes along for free.

THE PROMISE OF THIS FILE
------------------------
**With ``WORKSPACES_ROOT`` unset, nothing changes.**  Every function here falls
back to the *process workspace* — the folder holding
``config.DEFAULT_CONFIG_PATH`` — so scripts, ``refine_proc`` eval subprocesses,
pytest and today's single-user server read and write exactly the files they
always did, byte for byte.  The env var is the switch that turns multi-user on,
and until it exists on a machine this module is an alias for the status quo.

MECHANISM
---------
A ContextVar, for the reason ``run_recording.py`` spells out at length: the
solve routes run in FastAPI's threadpool, several requests at a time, and a
module global would leak one request's machine into another's answer.  An
app-level ASGI middleware (:class:`WorkspaceMiddleware`) resolves
``Authorization`` -> ``auth.caller_identity()`` -> :func:`workspace_for_identity`
and sets the var around the request.  A *pure ASGI* middleware and not
``BaseHTTPMiddleware`` on purpose: it runs in the request's own task, so the
value is set in the same context the endpoint is dispatched from, and Starlette
copies that context into the threadpool worker that runs a sync handler — the
same path ``material_context.py`` already relies on.

The default is deliberately NOT "raise if unset".  A CLI run, an optimizer eval
subprocess and a pytest process have no request and no identity; they get the
process workspace, which is the file they are pointed at.  That is what makes
the unset case free.

LAYOUT (§2.1 of the migration plan) — Stage 1 creates only the first line::

    <WORKSPACES_ROOT>/<ws_id>/motor_config.yaml      <- ws.root
    <SHARED_ROOT>/materials_library.yaml  dies/ ...  <- ws.shared_root

``ws_id = sha1(lowercased e-mail)[:16]``.  Stable, filesystem-safe on ext4 and
NTFS alike, and it does not put an e-mail address in a directory name.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional

__all__ = [
    "Workspace", "WorkspaceState",
    "workspace", "root", "shared_root", "config_file",
    "use_workspace", "workspace_for_identity", "workspace_for_request",
    "workspace_id", "workspaces_root", "ensure_layout", "process_workspace",
    "WorkspaceMiddleware", "install_workspace_resolver", "module_attrs",
]

log = logging.getLogger(__name__)

#: Set this to a directory and the server becomes multi-user: every signed-in
#: caller gets ``<WORKSPACES_ROOT>/<ws_id>/``.  UNSET (the state of this
#: workstation) = every call resolves to the process workspace = today.
ENV_WORKSPACES_ROOT = "WORKSPACES_ROOT"
#: The read-only library layer (materials, bearings, fusion map, die templates).
#: Unset = the process config directory, i.e. where they live today.
ENV_SHARED_ROOT = "SHARED_ROOT"

#: The id of the process workspace.  Never a sha1, so it can never collide with
#: a real identity's id and a log line says plainly which one answered.
PROCESS_WS_ID = "process"


# ─────────────────────────────────────────────────────────────────────────────
#  The objects
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WorkspaceState:
    """The per-workspace IN-MEMORY stores.

    A placeholder in Stage 1 and deliberately so: Stage 3 moves the geometry
    singleton, the four ``_fem_*`` caches, the three ``_LAST`` slots, the
    transient ref, the field snapshots and the static-3D caches in here, keyed
    per workspace instead of per process.  Stage 1 only has to make sure every
    workspace already OWNS one, so Stage 3 is a move and not a redesign.
    """

    #: Free-form slots until Stage 3 gives them names.  Guarded by ``lock``.
    slots: Dict[str, Any] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


@dataclass
class Workspace:
    """One user's world: where their machine and everything derived from it lives."""

    id: str
    email: str
    root: Path
    shared_root: Path
    #: The motor config for this workspace.  For a per-identity workspace it is
    #: ``root / "motor_config.yaml"``.  For the PROCESS workspace it is
    #: ``config.DEFAULT_CONFIG_PATH`` VERBATIM — filename included — because the
    #: redirect names a file, not a folder, and the suite points it at files
    #: called ``ten.yaml`` / ``explicit.yaml``
    #: (``tests/test_config_redirect_is_complete.py``).  Rebuilding it as
    #: ``root / "motor_config.yaml"`` would silently read a different file.
    config_file: Optional[Path] = None
    state: WorkspaceState = field(default_factory=WorkspaceState)
    #: True only for the fallback workspace — see :func:`process_workspace`.
    is_process: bool = False

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.shared_root = Path(self.shared_root)
        self.config_file = (Path(self.config_file) if self.config_file is not None
                            else self.root / "motor_config.yaml")


# ─────────────────────────────────────────────────────────────────────────────
#  Environment
# ─────────────────────────────────────────────────────────────────────────────

def workspaces_root() -> Optional[Path]:
    """``WORKSPACES_ROOT`` as a Path, or None when multi-user is off.

    Read PER CALL, never captured at import: that is the whole lesson of
    ``_EVAL_ENV`` and of ``DEFAULT_CONFIG_PATH`` itself, and it is what lets a
    test turn multi-user on and off around one assertion.
    """
    raw = os.environ.get(ENV_WORKSPACES_ROOT, "").strip()
    return Path(raw).expanduser() if raw else None


def _shared_root_env() -> Optional[Path]:
    raw = os.environ.get(ENV_SHARED_ROOT, "").strip()
    return Path(raw).expanduser() if raw else None


# ─────────────────────────────────────────────────────────────────────────────
#  The process workspace — the fallback that makes the unset case free
# ─────────────────────────────────────────────────────────────────────────────

_PROC_LOCK = threading.Lock()
_PROC_CACHE: Optional[tuple] = None


def process_workspace() -> Workspace:
    """The workspace of a call with no request and no identity.

    Its root is ``Path(config.DEFAULT_CONFIG_PATH).parent`` — the expression
    every one of the 25 stores used to spell inline — and it FOLLOWS that module
    attribute rather than snapshotting it, because the suite monkeypatches
    ``config.DEFAULT_CONFIG_PATH`` directly and every honest reader must follow.
    """
    global _PROC_CACHE
    from motor_ai_sim import config as _config       # late: config imports us back

    cfg = Path(str(_config.DEFAULT_CONFIG_PATH))
    shared = _shared_root_env()
    key = (str(cfg), str(shared) if shared else "")

    cache = _PROC_CACHE
    if cache is not None and cache[0] == key:
        return cache[1]
    with _PROC_LOCK:
        cache = _PROC_CACHE
        if cache is not None and cache[0] == key:
            return cache[1]
        ws = Workspace(id=PROCESS_WS_ID, email="", root=cfg.parent,
                       shared_root=shared if shared else cfg.parent,
                       config_file=cfg, is_process=True)
        _PROC_CACHE = (key, ws)
        return ws


# ─────────────────────────────────────────────────────────────────────────────
#  The ContextVar
# ─────────────────────────────────────────────────────────────────────────────

#: None means "nobody set one" and resolves to :func:`process_workspace` — i.e.
#: the DEFAULT of this var is the process workspace, computed late so that a
#: monkeypatched ``DEFAULT_CONFIG_PATH`` still moves it.
_WS: "ContextVar[Optional[Workspace]]" = ContextVar(
    "motor_ai_sim_workspace", default=None)


def workspace() -> Workspace:
    """The workspace THIS call is about."""
    ws = _WS.get()
    return ws if ws is not None else process_workspace()


def root() -> Path:
    """Replaces ``Path(DEFAULT_CONFIG_PATH).parent`` everywhere it was spelled."""
    return workspace().root


def shared_root() -> Path:
    """The read-only library layer.  Defaults to the process config directory,
    so materials / bearings / the fusion map stay exactly where they are."""
    return workspace().shared_root


def config_file() -> Path:
    """The ``motor_config.yaml`` this call must read or write."""
    cf = workspace().config_file
    return cf if cf is not None else workspace().root / "motor_config.yaml"


@contextmanager
def use_workspace(ws: Optional[Workspace]) -> Iterator[Workspace]:
    """``with use_workspace(ws):`` — for the middleware, for a background job
    that must continue its owner's work, and for tests."""
    token = _WS.set(ws)
    try:
        yield ws if ws is not None else process_workspace()
    finally:
        _WS.reset(token)


# ─────────────────────────────────────────────────────────────────────────────
#  Identity -> workspace
# ─────────────────────────────────────────────────────────────────────────────

def workspace_id(email: str) -> str:
    """``sha1(lowercased e-mail)[:16]`` — the directory name for an identity."""
    ident = (email or "").strip().lower()
    return hashlib.sha1(ident.encode("utf-8")).hexdigest()[:16]


_REG: Dict[tuple, Workspace] = {}
_REG_LOCK = threading.Lock()


def workspace_for_identity(email: Optional[str]) -> Workspace:
    """The workspace of a signed-in identity, or the process one.

    ``WORKSPACES_ROOT`` unset -> the process workspace, ALWAYS.  That is the
    line that keeps this machine byte-identical to yesterday: until the env var
    exists here, an identity resolves to the same folder anonymity does.
    """
    base = workspaces_root()
    ident = (email or "").strip().lower()
    if base is None or not ident:
        return process_workspace()

    wsid = workspace_id(ident)
    key = (str(base), wsid)
    ws = _REG.get(key)
    if ws is not None:
        return ws
    with _REG_LOCK:
        ws = _REG.get(key)
        if ws is None:
            shared = _shared_root_env() or process_workspace().root
            ws = Workspace(id=wsid, email=ident, root=base / wsid,
                           shared_root=shared)
            _REG[key] = ws
            # Once per new workspace, never per request.
            log.info("workspace %s -> %s", wsid, ws.root)
            _new = True
        else:
            _new = False
    if _new:
        ensure_layout(ws)
    return ws


def ensure_layout(ws: Workspace) -> Workspace:
    """Create the workspace directory and seed its machine.

    Seed order: ``shared_root()/motor_config.yaml``, else the process config —
    a brand-new user must open a WORKING machine, not a 404.  A no-op when
    ``WORKSPACES_ROOT`` is unset or for the process workspace itself: this must
    never create or touch anything on a single-user install.
    """
    if ws is None or ws.is_process or workspaces_root() is None:
        return ws
    try:
        ws.root.mkdir(parents=True, exist_ok=True)
        cfg = Path(str(ws.config_file))
        if cfg.exists():
            return ws
        seed = ws.shared_root / "motor_config.yaml"
        if not seed.is_file():
            seed = Path(str(process_workspace().config_file))
        if seed.is_file():
            # tmp + replace: two first requests from the same account can race
            # here, and a half-copied machine is worse than none.
            tmp = cfg.with_name(cfg.name + ".seed-%d.tmp" % os.getpid())
            shutil.copyfile(str(seed), str(tmp))
            os.replace(str(tmp), str(cfg))
            log.info("workspace %s seeded from %s", ws.id, seed)
    except OSError as exc:              # noqa: BLE001 — never fail a request here
        log.warning("workspace %s layout unavailable: %s", ws.id, exc)
    return ws


def workspace_for_request(authorization: Optional[str]) -> Workspace:
    """``Authorization`` header -> workspace.  Never raises.

    Anonymous, unauthenticated and local-dev-admin callers all get the PROCESS
    workspace: they are the owner of this box, and their machine is the one the
    config path already names.  ``auth.caller_identity`` spells those two cases
    as the ``ANON_OWNER`` / ``ADMIN_OWNER`` sentinels, which are not identities
    and must never become directory names.
    """
    if workspaces_root() is None:
        return process_workspace()
    try:
        from motor_ai_sim.auth import (ADMIN_OWNER, ANON_OWNER,
                                       caller_identity)
        who = str((caller_identity(authorization) or {}).get("id") or "").strip()
        if not who or who in (ANON_OWNER, ADMIN_OWNER):
            return process_workspace()
        return workspace_for_identity(who)
    except Exception as exc:            # noqa: BLE001 — auth trouble is not a 500
        log.warning("workspace resolution fell back to the process config: %s", exc)
        return process_workspace()


# ─────────────────────────────────────────────────────────────────────────────
#  The app-level resolver
# ─────────────────────────────────────────────────────────────────────────────

class WorkspaceMiddleware:
    """Set the workspace ContextVar for the duration of one HTTP request.

    A pure ASGI middleware, not ``BaseHTTPMiddleware``: the latter runs the
    downstream app in a task it spawns itself, and while a var set before
    ``call_next`` does reach it today, that is an implementation detail of the
    copy point.  This runs the app in the SAME task, so the endpoint — and the
    threadpool worker Starlette hands a sync handler to — is dispatched from the
    very context this sets.  Same guarantee ``material_context`` relies on.

    With ``WORKSPACES_ROOT`` unset it does not even look at the headers, so the
    single-user server pays nothing and behaves identically.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or workspaces_root() is None:
            await self.app(scope, receive, send)
            return
        auth_hdr: Optional[str] = None
        for _k, _v in (scope.get("headers") or ()):
            if _k.lower() == b"authorization":
                try:
                    auth_hdr = _v.decode("latin-1")
                except Exception:       # noqa: BLE001
                    auth_hdr = None
                break
        token = _WS.set(workspace_for_request(auth_hdr))
        try:
            await self.app(scope, receive, send)
        finally:
            _WS.reset(token)


def install_workspace_resolver(app) -> None:
    """Attach the resolver.  Call BEFORE the tier gate and CORS so it ends up
    INNERMOST — the gate's 401/403 keep their CORS headers, and every handler
    that actually runs has its workspace already set."""
    app.add_middleware(WorkspaceMiddleware)


# ─────────────────────────────────────────────────────────────────────────────
#  Keeping the old constant NAMES alive
# ─────────────────────────────────────────────────────────────────────────────

def module_attrs(**resolvers: Callable[[], Any]):
    """A PEP 562 module ``__getattr__`` for constants that are now per-call.

    ``routes.saved_sims._STORE`` and its two dozen siblings were module
    constants; the completeness test reads them BY NAME
    (``tests/test_config_redirect_is_complete.py::_every_store_path``) and so do
    a handful of other tests that monkeypatch them.  Inside their own module
    they are now function calls — a global name lookup does not consult module
    ``__getattr__`` — but the NAME stays readable from outside and resolves
    against the caller's workspace:

        __getattr__ = workspace.module_attrs(_STORE=lambda: workspace.root() / "x.json")

    ``monkeypatch.setattr(mod, "_STORE", …)`` still works: a real attribute in
    the module dict shadows ``__getattr__`` entirely.
    """

    def __getattr__(name: str) -> Any:
        try:
            resolve = resolvers[name]
        except KeyError:
            raise AttributeError(name) from None
        return resolve()

    return __getattr__
