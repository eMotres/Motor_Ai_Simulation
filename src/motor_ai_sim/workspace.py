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
    "workspace", "root", "shared_root", "published_root", "config_file",
    "use_workspace", "workspace_for_identity", "workspace_for_request",
    "workspace_id", "workspaces_root", "ensure_layout", "process_workspace",
    "WorkspaceMiddleware", "install_workspace_resolver", "module_attrs",
    # Stage 2 — the three layers
    "Layer", "layering", "layers", "resolve_die_dir", "resolve_config_file",
    "iter_dies", "is_admin", "owner_display_name", "published_label",
    "source_die_dir", "caller", "use_caller",
    "split_published_label", "write_layer", "use_write_layer",
    "LAYER_WORKSPACE", "LAYER_PUBLISHED", "LAYER_SHARED",
]

log = logging.getLogger(__name__)

#: Set this to a directory and the server becomes multi-user: every signed-in
#: caller gets ``<WORKSPACES_ROOT>/<ws_id>/``.  UNSET (the state of this
#: workstation) = every call resolves to the process workspace = today.
ENV_WORKSPACES_ROOT = "WORKSPACES_ROOT"
#: The read-only library layer (materials, bearings, fusion map, die templates).
#: Unset = the process config directory, i.e. where they live today.
ENV_SHARED_ROOT = "SHARED_ROOT"
#: The COMMUNITY layer: what one user published for every other registered one.
#: Unset with ``WORKSPACES_ROOT`` set = ``<WORKSPACES_ROOT>/../published``;
#: unset with multi-user off = the process config directory, which has no
#: ``<ws_id>/`` children and is therefore an empty layer.
ENV_PUBLISHED_ROOT = "PUBLISHED_ROOT"

#: The three layers, outermost (most specific) first.  A name is resolved in
#: this order and the FIRST hit wins: a workspace copy shadows a published one,
#: a published one shadows the admin-curated shared catalog.
LAYER_WORKSPACE = "workspace"
LAYER_PUBLISHED = "published"
LAYER_SHARED = "shared"

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


def _published_root_env() -> Optional[Path]:
    raw = os.environ.get(ENV_PUBLISHED_ROOT, "").strip()
    return Path(raw).expanduser() if raw else None


def published_root() -> Path:
    """Where PUBLISHED work lives: ``<published_root>/<ws_id>/<die>/…``.

    ``PUBLISHED_ROOT`` wins; otherwise it is the sibling of the workspaces tree
    (``/srv/motres/published`` beside ``/srv/motres/workspaces``), which is the
    §2.1 layout.  With multi-user off it is the process config directory — a
    folder that has no ``<ws_id>`` children, so the layer resolves to nothing
    and this machine behaves exactly as it does today.
    """
    env = _published_root_env()
    if env is not None:
        return env
    base = workspaces_root()
    if base is not None:
        return base.parent / "published"
    return workspace().root


def layering() -> bool:
    """Is the three-layer catalog ACTIVE?

    Only when ``WORKSPACES_ROOT`` is set.  This is the single switch that keeps
    the promise at the top of this file: with it unset there is one layer — the
    folder the config path names — and every read-through helper below degrades
    to the expression it replaced.  It also keeps the pytest sandbox honest: a
    suite that redirects the catalog to a throwaway tree must not suddenly see
    the user's real ``config/dies`` through a fall-back.
    """
    return workspaces_root() is not None


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

# ─────────────────────────────────────────────────────────────────────────────
#  Stage 2 — the three layers
# ─────────────────────────────────────────────────────────────────────────────
#
# THREE and not two (the user's decision, 2026-09-15):
#
#   shared/      the admin-curated catalog.  Read-only for everyone but an
#                admin, who writes it EXPLICITLY (``?layer=shared``).
#   published/   the community layer.  A user publishes a die / configuration /
#                duty out of their own workspace and every registered account
#                sees it read-only, with the author's name on it.  Namespaced
#                per owner (``published/<ws_id>/<die>/``) so two people may both
#                publish "CIANO28 85".
#   workspaces/  the user's own space.  EVERY write lands here — if the die
#                being written exists only in published/ or shared/, its yaml
#                documents (never its results) are copied across first.
#
# Read order is workspace → published → shared, first hit wins.  Writing is
# never a fall-through: a read that found a shared die does not make the shared
# die writable, it makes the NEXT write a copy-on-write.

@dataclass(frozen=True)
class Layer:
    """One place dies may be found, and what it means that they were found there."""

    name: str
    #: The directory whose immediate children are die folders.
    dies_dir: Path
    #: The publishing identity — published layer only.
    owner: str = ""
    owner_id: str = ""

    @property
    def writable(self) -> bool:
        return self.name == LAYER_WORKSPACE


def _ws_dies_dir() -> Path:
    """The workspace's own die folder, honouring ``family._DIES_DIR``.

    Probed out of ``sys.modules`` rather than imported: this module is reached
    from the config layer, and importing the catalog router here would make the
    whole FastAPI surface a dependency of reading a yaml path.  Eight test
    modules monkeypatch that name and a value in the module dict must keep
    winning.
    """
    import sys
    fam = sys.modules.get("motor_ai_sim.routes.family")
    if fam is not None:
        ov = fam.__dict__.get("_DIES_DIR")
        if ov is not None:
            return Path(str(ov))
    return root() / "dies"


def layers() -> list:
    """``[workspace, published…, shared]`` — the read-through order.

    The published layer expands to ONE entry per owner namespace, because that
    is what a namespace is: ``published/<ws_id>/`` is a whole little catalog of
    its own.  The caller's own namespace comes first among them, so publishing
    a die and then editing it still reads your copy before anyone else's.
    """
    out = [Layer(LAYER_WORKSPACE, _ws_dies_dir())]
    if not layering():
        return out
    ws = workspace()
    pub = published_root()
    try:
        owners = sorted(d for d in pub.iterdir() if d.is_dir())
    except OSError:
        owners = []
    mine = [d for d in owners if d.name == ws.id]
    for d in mine + [d for d in owners if d.name != ws.id]:
        out.append(Layer(LAYER_PUBLISHED, d, owner=_owner_email(d),
                         owner_id=d.name))
    out.append(Layer(LAYER_SHARED, Path(str(shared_root())) / "dies"))
    return out


#: ``published/<ws_id>/.owner.json`` — who this namespace belongs to.  A file
#: and not a lookup table, because the ws_id is a one-way hash of the e-mail.
OWNER_FILE = ".owner.json"


def _owner_email(ns_dir: Path) -> str:
    try:
        import json
        d = json.loads((ns_dir / OWNER_FILE).read_text(encoding="utf-8"))
        return str((d or {}).get("email") or "")
    except Exception:                                       # noqa: BLE001
        return ""


def owner_display_name(email: str) -> str:
    """The author's name as the community listing prints it.

    The registry's display name when there is one, the local part of the e-mail
    otherwise, reduced to the catalog's own filename-safe charset — because the
    label this builds is ALSO a die name the API is addressed by.
    """
    name = ""
    try:
        from motor_ai_sim import users as _users
        name = str((_users.get_user(email) or {}).get("name") or "").strip()
    except Exception:                                       # noqa: BLE001
        name = ""
    if not name:
        name = str(email or "").split("@")[0]
    import re as _re
    name = _re.sub(r"[^A-Za-z0-9_.,()#+°·\- ]+", " ", name).strip()
    name = _re.sub(r"\s{2,}", " ", name)
    return name or "someone"


#: How a published die of ANOTHER account is named everywhere it is addressable.
#: ``·`` is already a legal die-name character (``family._NAME_RE``; real names
#: on disk include "100 mm · 24s-28p mid-torque"), so the decorated label is
#: itself a legal die name and needs no second addressing scheme.
_BY = " · by "


def published_label(die: str, owner: str) -> str:
    return f"{die}{_BY}{owner_display_name(owner)}"


def split_published_label(label: str) -> tuple:
    """``"CIANO28 85 · by Alice"`` -> ``("CIANO28 85", "Alice")``; else ``(label, "")``."""
    s = str(label)
    i = s.rfind(_BY)
    return (s[:i], s[i + len(_BY):]) if i > 0 else (s, "")


def iter_dies() -> list:
    """Every die this call can SEE, deduplicated, tagged with its layer.

    ``[{"name", "die", "dir", "layer", "owner", "owner_id"}]`` — ``die`` is the
    name on disk, ``name`` the one the API is addressed by (they differ only for
    another account's published work).  Deduplicated by ``name`` in read order,
    so a workspace copy hides the shared original rather than appearing twice.

    Access is NOT decided here: this says what exists, ``motor_access`` and the
    route say who may see it.
    """
    seen = {}
    ws = workspace()
    for lay in layers():
        try:
            entries = sorted(lay.dies_dir.iterdir())
        except OSError:
            continue
        for dd in entries:
            if not dd.is_dir() or not (dd / "die.yaml").is_file():
                continue
            name = dd.name
            if lay.name == LAYER_PUBLISHED and lay.owner_id != ws.id:
                name = published_label(dd.name, lay.owner)
            if name in seen:
                continue
            seen[name] = {"name": name, "die": dd.name, "dir": dd,
                          "layer": lay.name, "owner": lay.owner,
                          "owner_id": lay.owner_id}
    return [seen[k] for k in sorted(seen)]


def resolve_die_dir(die: str):
    """Where the named die is READ from — workspace, then published, then shared.

    ``None`` when no layer has it.  The caller decides what "nowhere" means: the
    catalog's own readers keep pointing at the workspace path, so a 404 still
    names the file the user would have created.
    """
    want = str(die)
    ws = workspace()
    base, by = split_published_label(want)
    for lay in layers():
        if lay.name == LAYER_PUBLISHED and lay.owner_id != ws.id:
            # Another account's namespace answers to the decorated label only —
            # otherwise B's "CIANO28 85" would silently become A's.
            if not by or owner_display_name(lay.owner) != by:
                continue
            cand = lay.dies_dir / base
        else:
            cand = lay.dies_dir / want
        if (cand / "die.yaml").is_file():
            return cand
    return None


def source_die_dir(die: str):
    """The published/shared folder this die ALSO lives in, or ``None``.

    Where a read falls THROUGH to.  Deliberately not ``resolve_die_dir``: after
    a copy-on-write the resolver answers "workspace", and the results the other
    layer still holds — somebody else's solved fields, the vendor's runs — must
    stay readable rather than vanish the moment you rename one duty.
    """
    if not layering():
        return None
    ws = workspace()
    base, by = split_published_label(str(die))
    for lay in layers():
        if lay.name == LAYER_WORKSPACE:
            continue
        if lay.name == LAYER_PUBLISHED and lay.owner_id != ws.id:
            if not by or owner_display_name(lay.owner) != by:
                continue
            cand = lay.dies_dir / base
        else:
            cand = lay.dies_dir / str(die)
        if (cand / "die.yaml").is_file():
            return cand
    return None


def resolve_config_file(die: str, cfg: str):
    """``<resolved die dir>/<cfg>.yaml``, or ``None``.

    One die belongs to ONE layer: a copy-on-write copies every configuration of
    the die across, never just the one being edited, so a half-shadowed die —
    workspace ``L180`` beside a shared ``L155`` — cannot exist and no reader has
    to merge two directories.
    """
    d = resolve_die_dir(die)
    if d is None:
        return None
    p = d / f"{cfg}.yaml"
    return p if p.is_file() else None


#: The identity the middleware resolved for THIS request, or None outside one.
#: Set beside the workspace and for the same reason: a write seam deep in the
#: catalog has no ``Authorization`` header in hand, and re-resolving from
#: nothing would answer "anonymous" for a signed-in admin.
_CALLER: "ContextVar[Optional[Dict[str, Any]]]" = ContextVar(
    "motor_ai_sim_caller", default=None)


def caller() -> Optional[Dict[str, Any]]:
    """``auth.caller_identity``'s answer for this request, or ``None``."""
    return _CALLER.get()


def is_admin(identity=None) -> bool:
    """Is this caller an admin (``auth.py`` / ``ADMIN_EMAILS``)?

    ``identity`` may be an ``Authorization`` header, a ``caller_identity``
    mapping, or nothing at all — in which case the identity the middleware
    resolved for this request answers, falling back to ``caller_identity``'s
    credential-less verdict outside a request (a CLI run, the migration script,
    a direct call in a test).
    """
    try:
        from motor_ai_sim.auth import caller_identity
        if isinstance(identity, dict):
            return bool(identity.get("is_admin"))
        if identity is None:
            who = _CALLER.get()
            if who is not None:
                return bool(who.get("is_admin"))
        who = caller_identity(identity if isinstance(identity, str) else None)
        return bool(who.get("is_admin"))
    except Exception:                                       # noqa: BLE001
        return False


@contextmanager
def use_caller(who: Optional[Dict[str, Any]]) -> Iterator[Optional[Dict[str, Any]]]:
    """Pin the resolved identity — the middleware, and tests calling in directly."""
    token = _CALLER.set(who)
    try:
        yield who
    finally:
        _CALLER.reset(token)


# ── which layer this request WRITES to ───────────────────────────────────────
# ``?layer=shared`` on a family save route, admin-gated at the write site.  Set
# by the middleware from the query string (so no route signature moves) and by
# :func:`use_write_layer` for a direct call, the migration script and tests.

_WRITE_LAYER: "ContextVar[Optional[str]]" = ContextVar(
    "motor_ai_sim_write_layer", default=None)


def write_layer() -> Optional[str]:
    """``"shared"`` when this call asked for the shared layer, else ``None``
    (= the caller's own workspace, which is where writes belong)."""
    v = _WRITE_LAYER.get()
    return v if v in (LAYER_SHARED, LAYER_PUBLISHED) else None


@contextmanager
def use_write_layer(name: Optional[str]) -> Iterator[Optional[str]]:
    token = _WRITE_LAYER.set(str(name) if name else None)
    try:
        yield write_layer()
    finally:
        _WRITE_LAYER.reset(token)


def _identity_of(auth_hdr: Optional[str]) -> Optional[Dict[str, Any]]:
    try:
        from motor_ai_sim.auth import caller_identity
        return caller_identity(auth_hdr)
    except Exception:                                       # noqa: BLE001
        return None


def _layer_from_query(scope) -> Optional[str]:
    raw = scope.get("query_string") or b""
    if b"layer=" not in raw:
        return None
    try:
        from urllib.parse import parse_qs
        vals = parse_qs(raw.decode("latin-1")).get("layer") or []
        return vals[-1].strip().lower() if vals else None
    except Exception:                                       # noqa: BLE001
        return None


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
        # ``?layer=shared`` rides the request, not the route signature: every
        # family write funnels through one helper and that helper asks here.
        # Admin-ness is checked AT THE WRITE, never at the parse.
        ltok = _WRITE_LAYER.set(_layer_from_query(scope))
        ctok = _CALLER.set(_identity_of(auth_hdr))
        try:
            await self.app(scope, receive, send)
        finally:
            _CALLER.reset(ctok)
            _WRITE_LAYER.reset(ltok)
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
