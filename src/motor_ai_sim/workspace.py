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

import functools
import hashlib
import logging
import os
import shutil
import threading
from collections import OrderedDict
from collections.abc import MutableMapping, MutableSequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional

__all__ = [
    "Workspace", "WorkspaceState",
    "workspace", "root", "shared_root", "published_root", "config_file",
    "use_workspace", "workspace_for_identity", "workspace_for_request",
    "workspace_id", "workspaces_root", "ensure_layout", "provision",
    "process_workspace",
    "WorkspaceMiddleware", "install_workspace_resolver", "module_attrs",
    # Stage 2 — the three layers
    "Layer", "layering", "layers", "resolve_die_dir", "resolve_config_file",
    "iter_dies", "is_admin", "owner_display_name", "published_label",
    "source_die_dir", "caller", "use_caller",
    "split_published_label", "write_layer", "use_write_layer",
    "LAYER_WORKSPACE", "LAYER_PUBLISHED", "LAYER_SHARED",
    # Stage 3 — the in-memory stores
    "BoundedStore", "StateMapping", "StateList", "StateLock",
    "state", "ws_map", "ws_list", "ws_lock", "bind", "bind_thread",
    "MAX_WORKSPACES", "registry_size", "registry_ids", "evict_workspace",
    "evict_all_workspaces", "audit_cache_keys",
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

#: Returned by a slot lookup that found nothing.  A module-level sentinel and
#: not ``None``, because ``None`` is a legitimate stored value.
_MISS = object()

#: How many workspaces may hold LIVE MEMORY at once.  The registry is an LRU:
#: the 33rd distinct account to make a request evicts the least-recently-used
#: one's caches (its DISK stores are untouched — they reload on its next call).
#: 32 × the per-workspace caps below is the server's in-memory ceiling; it is a
#: bound, not a target, because a workspace that solved nothing holds nothing.
MAX_WORKSPACES = 32


class BoundedStore(MutableMapping):
    """A bounded, insertion-ordered mapping whose REAL keys carry the workspace.

    Two properties, both load-bearing, and one deliberate non-property:

    * **Bounded.**  Every store this replaces was an unbounded ``dict`` or an
      ``OrderedDict`` trimmed by hand at one call site.  A field payload holds a
      value per node and a vector per triangle; a server that is being swept
      grows without bound otherwise.  ``cap`` is the number of ENTRIES, evicted
      oldest-first.  ``cap <= 0`` means unbounded (never used here).
    * **Namespaced.**  The key actually stored is ``(ws_id, key)``.  The mapping
      presents the plain key everywhere — ``store[k]``, ``k in store``,
      ``dict(store)`` and ``==`` behave exactly as the dict they replace — while
      :meth:`raw_keys` shows the namespaced ones.  Isolation does not DEPEND on
      that prefix (each workspace owns its own instance), so the prefix is the
      *audit*: ``tests/test_workspace_state.py`` walks every live store and
      refuses a key that does not start with the workspace it was produced
      under.  A cache that leaked across workspaces would have to lie twice.

    * **Not LRU on read by default.**  Several call sites read the ORDER back —
      ``next(reversed(_transient_field_snap))`` means "the newest run", and
      ``list(values())[-1]`` means the same thing.  Touching on read would
      reorder those under a mere lookup, so ``lru_on_read`` is opt-in and set
      only on the pure hit/miss caches, where recency IS the right eviction
      order and nothing reads the order back.

    One semantic difference from the ``dict`` it replaces, stated out loud:
    re-assigning an EXISTING key moves it to the end, because eviction must
    not throw away the entry just written.  Nothing reads insertion order of a
    key it then rewrites; the only visible effect is the field order of a
    record serialised straight to JSON, which every reader addresses by name.
    """

    __slots__ = ("_d", "_ws", "_name", "cap", "lru_on_read", "evicted", "_lock")

    def __init__(self, ws_id: str, name: str, cap: int,
                 lru_on_read: bool = False) -> None:
        self._d: "OrderedDict[tuple, Any]" = OrderedDict()
        self._ws = str(ws_id)
        self._name = str(name)
        self.cap = int(cap)
        self.lru_on_read = bool(lru_on_read)
        self.evicted = 0
        # Every structural touch of ``_d`` happens under this.  See
        # :meth:`snapshot` for why a store that looked single-threaded is not.
        # RLock, so ``__setitem__`` may call ``_trim`` and ``copy`` may call
        # ``snapshot`` without a second thought.  It is NOT part of the mapping
        # contract and nothing outside this class takes it; lock order is
        # always WorkspaceState.lock -> this, never the reverse.
        self._lock = threading.RLock()

    # ── the namespaced key ───────────────────────────────────────────────────
    def _k(self, key):
        return (self._ws, key)

    @property
    def workspace_id(self) -> str:
        return self._ws

    @property
    def name(self) -> str:
        return self._name

    def raw_keys(self) -> list:
        """The keys AS STORED: ``(ws_id, key)``.  The audit's window."""
        with self._lock:
            return list(self._d.keys())

    def snapshot(self) -> dict:
        """A shallow, PLAIN-KEY copy taken under the lock.

        What this is for (2026-09-15).  The three ``_LAST`` route stores are
        written by whichever thread just finished a solve and read by the
        persist path that writes ``config/.last_*.pkl`` — and the persist path
        built its payload with ``{k: v for k, v in store.items()}``, which walks
        the LIVE ``OrderedDict``: ``__iter__`` below materialises its key list
        out of ``self._d.keys()``.  A concurrent ``__setitem__`` (a second
        solve, or this store's own cap-eviction inside ``_trim``) resized the
        dict mid-walk and the comprehension raised

            RuntimeError: dictionary changed size during iteration

        which the persist path caught, logged at WARNING and swallowed — so the
        pickle was silently not written and the tab came back blank after a
        restart.  Taking the copy here, with the writers holding the same lock,
        is the fix; the shallow values are the very objects the store holds, and
        the caller only ever pickles or JSON-dumps them.
        """
        with self._lock:
            return {k[1]: v for k, v in self._d.items()}

    # ── Mapping ──────────────────────────────────────────────────────────────
    def __getitem__(self, key):
        rk = self._k(key)
        with self._lock:
            v = self._d[rk]
            if self.lru_on_read:
                self._d.move_to_end(rk)
            return v

    def __setitem__(self, key, value) -> None:
        rk = self._k(key)
        with self._lock:
            self._d[rk] = value
            self._d.move_to_end(rk)
            self._trim()

    def __delitem__(self, key) -> None:
        with self._lock:
            del self._d[self._k(key)]

    def __iter__(self):
        # A SNAPSHOT, on purpose: ``items()`` reads through ``__getitem__``,
        # which may reorder, and several callers delete while iterating.  Built
        # under the lock, because materialising it is itself an iteration of a
        # dict another thread may be resizing.
        with self._lock:
            return iter([k[1] for k in self._d.keys()])

    def __reversed__(self):
        with self._lock:
            return iter([k[1] for k in reversed(self._d.keys())])

    def __len__(self) -> int:
        return len(self._d)

    def __contains__(self, key) -> bool:
        return self._k(key) in self._d

    def __repr__(self) -> str:                                  # pragma: no cover
        return (f"<BoundedStore {self._name} ws={self._ws} "
                f"{len(self._d)}/{self.cap}>")

    # ── the OrderedDict surface the call sites already use ───────────────────
    def move_to_end(self, key, last: bool = True) -> None:
        with self._lock:
            self._d.move_to_end(self._k(key), last=last)

    def popitem(self, last: bool = True):
        with self._lock:
            rk, v = self._d.popitem(last=last)
        return (rk[1], v)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()

    def copy(self) -> dict:
        return self.snapshot()

    def _trim(self) -> None:
        # Called with ``_lock`` held.
        while 0 < self.cap < len(self._d):
            self._d.popitem(last=False)
            self.evicted += 1


class _BoundedList(list):
    """A plain list that remembers which workspace it belongs to.

    ``_ENTRY_ORDER`` and ``_motor_geom_ghash`` are lists, and both are trimmed
    by their own call site; this only gives the audit something to name.
    """

    __slots__ = ("_ws", "_name")

    def __init__(self, ws_id: str, name: str, seed=()) -> None:
        super().__init__(seed)
        self._ws = str(ws_id)
        self._name = str(name)


@dataclass
class WorkspaceState:
    """The per-workspace IN-MEMORY stores.

    Stage 1 created it empty so Stage 3 would be a move and not a redesign.
    Stage 3 fills it: the geometry singleton, the four ``_fem_*`` caches, the
    three ``_LAST`` slots, the transient ref, the field snapshots, the thermal /
    mechanical / static-3D / family / freecad caches and the optimizer's
    campaign slots all live here now, one set per workspace.

    Nothing in here is addressed by name from outside: the modules keep their
    old module-level NAMES (:class:`StateMapping` proxies) and reach this object
    per call.  ``slots`` is therefore an implementation detail with one public
    promise — :meth:`evict` drops all of it, and drops nothing on disk.
    """

    #: name -> the store.  Guarded by ``lock``.
    slots: Dict[str, Any] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    #: The workspace this state belongs to; stamped into every store key.
    ws_id: str = PROCESS_WS_ID
    #: Small scalars that used to be module globals — the ``*_LOADED`` "have I
    #: read my pickle yet" flags.  Separate from ``slots`` only so ``evict``
    #: can reset them and the next call re-reads the disk store.
    flags: Dict[str, Any] = field(default_factory=dict)

    # ── stores ───────────────────────────────────────────────────────────────
    def store(self, name: str, factory: Callable[[str], Any],
              warm: Optional[Callable[[Any], None]] = None) -> Any:
        """The named store, created on first touch.

        ``warm`` runs ONCE, right after creation, with the store already parked
        in ``slots`` — so a warmer that repopulates the store from disk by
        writing through the module's own proxy (which is the honest way to
        write it) re-enters here and finds the container instead of recursing.
        The lock is an ``RLock`` for the same reason.
        """
        with self.lock:
            obj = self.slots.get(name, _MISS)
            if obj is not _MISS:
                return obj
            obj = factory(self.ws_id)
            self.slots[name] = obj
            if warm is not None:
                try:
                    warm(obj)
                except Exception as exc:            # noqa: BLE001
                    log.warning("workspace %s: could not warm %s: %s",
                                self.ws_id, name, exc)
            return obj

    def peek(self, name: str, default=None):
        """The named store IF it exists — never creates one.  For the audit."""
        with self.lock:
            obj = self.slots.get(name, _MISS)
            return default if obj is _MISS else obj

    # ── flags ────────────────────────────────────────────────────────────────
    def flag(self, name: str, default=None):
        with self.lock:
            return self.flags.get(name, default)

    def set_flag(self, name: str, value) -> None:
        with self.lock:
            self.flags[name] = value

    # ── memory ───────────────────────────────────────────────────────────────
    def evict(self) -> int:
        """Drop every in-memory store.  Touches NOTHING on disk.

        What an evicted workspace loses is time, never data: the ``.last_*``
        stores, the ledger and the warm cache are files, and the next request
        from that account reloads them lazily exactly as a fresh process does.
        Returns the number of slots dropped, for the log line and the test.
        """
        with self.lock:
            n = len(self.slots)
            for obj in list(self.slots.values()):
                try:
                    obj.clear()
                except Exception:                   # noqa: BLE001
                    pass
            self.slots.clear()
            self.flags.clear()
            return n

    def audit(self) -> Dict[str, list]:
        """``{slot name: [raw keys]}`` for every mapping store.

        The reflective test's window: every raw key must be a 2-tuple whose
        first element is this state's ``ws_id``.
        """
        with self.lock:
            out: Dict[str, list] = {}
            for name, obj in self.slots.items():
                if isinstance(obj, BoundedStore):
                    out[name] = obj.raw_keys()
            return out


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
        # Stage 3: every store this workspace creates stamps its keys with this.
        self.state.ws_id = self.id


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


#: The live workspaces, most-recently-used LAST.  Bounded by
#: :data:`MAX_WORKSPACES`: an LRU, because a server that has served a thousand
#: accounts must not be holding a thousand geometry singletons.  Eviction drops
#: MEMORY only (:meth:`WorkspaceState.evict`) — see there.
_REG: "OrderedDict[tuple, Workspace]" = OrderedDict()
_REG_LOCK = threading.Lock()


def _reg_touch_locked(key: tuple) -> None:
    """Mark a workspace used, and evict the oldest beyond the cap.

    Called with ``_REG_LOCK`` held.  The eviction is done OUTSIDE that lock's
    critical work — ``state.evict()`` takes the workspace's own ``RLock`` and a
    long-running solve may be holding it — so the entry is dropped from the
    registry first and cleared afterwards.
    """
    _REG.move_to_end(key)
    dead = []
    while len(_REG) > MAX_WORKSPACES:
        _k, _ws = _REG.popitem(last=False)
        dead.append(_ws)
    for _ws in dead:
        try:
            n = _ws.state.evict()
            log.info("workspace %s evicted from memory (%d stores); its disk "
                     "stores are untouched", _ws.id, n)
        except Exception as exc:                    # noqa: BLE001
            log.warning("workspace %s eviction failed: %s", _ws.id, exc)


def registry_size() -> int:
    """How many workspaces currently hold memory (the process one excluded)."""
    with _REG_LOCK:
        return len(_REG)


def registry_ids() -> list:
    """The live workspace ids, least-recently-used first."""
    with _REG_LOCK:
        return [w.id for w in _REG.values()]


def evict_workspace(ws: "Workspace") -> int:
    """Drop one workspace's memory by hand (admin, tests, a shutdown hook)."""
    return ws.state.evict()


def evict_all_workspaces() -> int:
    """Drop every registered workspace's memory.  The process one is kept."""
    with _REG_LOCK:
        live = list(_REG.values())
    return sum(w.state.evict() for w in live)


def audit_cache_keys(ws: "Workspace") -> Dict[str, list]:
    """``{store name: [raw keys]}`` for one workspace — the reflective test."""
    return ws.state.audit()


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
        # Stage 3: LRU order + the registry cap.  Cheap (an OrderedDict move)
        # and done on EVERY resolution, so "recently used" means what it says.
        _reg_touch_locked(key)
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


def provision(email: Optional[str]) -> Optional[Workspace]:
    """Make sure `email` HAS a workspace, right now — sign-in and invite.

    ``workspace_for_identity`` already seeds a workspace the first time it
    resolves one, but that happens on the first REQUEST, inside the middleware,
    with the answer of a route hanging on it.  Stage 10's requirement is that
    the folder exists before anything needs it: an admin invites somebody, or
    somebody signs in, and the machine they will open is already seeded.

    Idempotent and never raises: ``ensure_layout`` is a ``mkdir -p`` plus one
    ``exists()`` on the config file, so calling it on every sign-in costs two
    stat calls and repairs the case a directory was removed underneath a
    workspace this process still has cached.  A no-op when ``WORKSPACES_ROOT``
    is unset (this workstation) or the identity is empty/anonymous.
    """
    ident = (email or "").strip().lower()
    if workspaces_root() is None or not ident:
        return None
    from motor_ai_sim.auth import ADMIN_OWNER, ANON_OWNER
    if ident in (ANON_OWNER, ADMIN_OWNER):
        return None
    try:
        return ensure_layout(workspace_for_identity(ident))
    except Exception as exc:                # noqa: BLE001 — never fail a sign-in
        log.warning("workspace provisioning for %s failed: %s", ident, exc)
        return None


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


# ─────────────────────────────────────────────────────────────────────────────
#  Stage 3 — the module-level NAMES of the per-workspace stores
# ─────────────────────────────────────────────────────────────────────────────
#
# WHY A PROXY AND NOT A ``__getattr__``
# ------------------------------------
# ``module_attrs`` above is right for a PATH: it is read from outside, by name,
# and never from inside the module.  The in-memory stores are the opposite —
# ``routes/simulation.py`` alone touches ``_transient_field_snap`` at 20 sites,
# by plain global name, and a global name lookup does NOT consult a module
# ``__getattr__``.  Renaming 200 such sites to accessor calls is the churn this
# stage cannot afford in the three largest files in the repo.
#
# So the module keeps a real attribute — a PROXY — and every operation on it
# resolves ``workspace().state`` first.  Three things fall out of that:
#
#   * every existing call site is byte-identical, inside the module and out;
#   * ``monkeypatch.setattr(mod, "_LAST", {})`` still works, and works BETTER
#     than a ``__getattr__`` would: it replaces the proxy with a plain dict in
#     the module dict, the module's own code reads that dict, and ``undo``
#     puts the proxy back.  Two dozen tests do exactly this;
#   * ``from x import _EVAL_CACHE as _ec`` binds the proxy, so even a
#     from-import keeps following the caller's workspace.
#
# The proxies are created at import and are stateless; the state they point at
# is created lazily, per workspace, on first touch.


def state() -> WorkspaceState:
    """The in-memory stores of the workspace THIS call is about."""
    return workspace().state


def module_override_applies() -> bool:
    """May a module-level assignment stand in for this workspace's flag?

    The scalar flags this stage relocated (``thermal._LAST_LOADED``,
    ``geometry_service._current_geometry`` and their kin) keep working as plain
    module NAMES, because two dozen tests assign them and the accessors honour a
    real module attribute once one exists.  The catch is that a module attribute
    cannot be un-created: ``monkeypatch.setattr(th, "_LAST_LOADED", True)``
    leaves the name in the module dict after ``undo`` (it re-*sets* the old
    value rather than deleting it), so from the first such patch onwards the
    accessor would read ONE process-wide value — which is precisely the
    single-user global this stage exists to remove, silently reinstated.

    So the escape hatch is scoped to the workspace it was always about: the
    PROCESS one.  With no ``WORKSPACES_ROOT`` that is the only workspace there
    is, so every test that assigns the name sees exactly the pre-stage
    behaviour; inside a real per-account workspace the flag is the workspace's
    own and a stranger's leftover module attribute cannot reach it.
    """
    try:
        return workspace().is_process
    except Exception:                                   # noqa: BLE001
        return True


class StateMapping(MutableMapping):
    """The module-level name of a per-workspace :class:`BoundedStore`."""

    __slots__ = ("_name", "_cap", "_lru_on_read", "_seed", "_warm")

    def __init__(self, name: str, cap: int, *, lru_on_read: bool = False,
                 seed: Optional[Callable[[], dict]] = None,
                 warm: Optional[Callable[[Any], None]] = None) -> None:
        self._name = str(name)
        self._cap = int(cap)
        self._lru_on_read = bool(lru_on_read)
        self._seed = seed
        self._warm = warm

    # ── resolution ───────────────────────────────────────────────────────────
    def _factory(self, ws_id: str) -> BoundedStore:
        st = BoundedStore(ws_id, self._name, self._cap,
                          lru_on_read=self._lru_on_read)
        if self._seed is not None:
            for k, v in (self._seed() or {}).items():
                st[k] = v
        return st

    @property
    def target(self) -> BoundedStore:
        return state().store(self._name, self._factory, self._warm)

    @property
    def store_name(self) -> str:
        return self._name

    # ── Mapping, forwarded ───────────────────────────────────────────────────
    def __getitem__(self, key):
        return self.target[key]

    def __setitem__(self, key, value) -> None:
        self.target[key] = value

    def __delitem__(self, key) -> None:
        del self.target[key]

    def __iter__(self):
        return iter(self.target)

    def __reversed__(self):
        return reversed(self.target)

    def __len__(self) -> int:
        return len(self.target)

    def __contains__(self, key) -> bool:
        return key in self.target

    def __repr__(self) -> str:                                  # pragma: no cover
        return f"<StateMapping {self._name} -> {self.target!r}>"

    def move_to_end(self, key, last: bool = True) -> None:
        self.target.move_to_end(key, last=last)

    def popitem(self, last: bool = True):
        return self.target.popitem(last=last)

    def clear(self) -> None:
        self.target.clear()

    def copy(self) -> dict:
        return self.target.copy()

    def snapshot(self) -> dict:
        """:meth:`BoundedStore.snapshot` — the persist paths' entry point."""
        return self.target.snapshot()

    def raw_keys(self) -> list:
        return self.target.raw_keys()


class StateList(MutableSequence):
    """The module-level name of a per-workspace list (``_ENTRY_ORDER``)."""

    __slots__ = ("_name", "_seed")

    def __init__(self, name: str, *, seed: Optional[Callable[[], list]] = None) -> None:
        self._name = str(name)
        self._seed = seed

    def _factory(self, ws_id: str) -> _BoundedList:
        return _BoundedList(ws_id, self._name,
                            (self._seed() or []) if self._seed else ())

    @property
    def target(self) -> _BoundedList:
        return state().store(self._name, self._factory)

    def __getitem__(self, i):
        return self.target[i]

    def __setitem__(self, i, v) -> None:
        self.target[i] = v

    def __delitem__(self, i) -> None:
        del self.target[i]

    def __len__(self) -> int:
        return len(self.target)

    def insert(self, i, v) -> None:
        self.target.insert(i, v)

    def clear(self) -> None:
        self.target.clear()

    def __repr__(self) -> str:                                  # pragma: no cover
        return f"<StateList {self._name} -> {list(self.target)!r}>"


class StateLock:
    """The module-level name of a per-workspace ``RLock``.

    Where a module had ONE lock guarding ONE store, the store is now per
    workspace and so is the lock: A's campaign must not queue behind B's.  It
    is an ``RLock`` and the originals were plain ``Lock``s — strictly more
    permissive, so no call site that worked can stop working.

    ``__enter__`` and ``__exit__`` both resolve through :func:`state`, and the
    workspace cannot change inside one ``with`` block: the ContextVar is set
    once per request (or once per bound thread) and never mid-call.
    """

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = str(name)

    @property
    def target(self) -> "threading.RLock":
        return state().store(self._name, lambda _ws: threading.RLock())

    def acquire(self, *a, **kw):
        return self.target.acquire(*a, **kw)

    def release(self) -> None:
        self.target.release()

    def __enter__(self):
        return self.target.__enter__()

    def __exit__(self, *exc):
        return self.target.__exit__(*exc)

    def __repr__(self) -> str:                                  # pragma: no cover
        return f"<StateLock {self._name}>"


def ws_lock(name: str) -> StateLock:
    """Declare a per-workspace lock under a module-level name."""
    return StateLock(name)


def ws_map(name: str, cap: int, *, lru_on_read: bool = False,
           seed: Optional[Callable[[], dict]] = None,
           warm: Optional[Callable[[Any], None]] = None) -> StateMapping:
    """Declare a per-workspace bounded store under a module-level name."""
    return StateMapping(name, cap, lru_on_read=lru_on_read, seed=seed, warm=warm)


def ws_list(name: str, *, seed: Optional[Callable[[], list]] = None) -> StateList:
    """Declare a per-workspace list under a module-level name."""
    return StateList(name, seed=seed)


# ─────────────────────────────────────────────────────────────────────────────
#  Carrying the workspace into a thread
# ─────────────────────────────────────────────────────────────────────────────

def bind(fn: Callable) -> Callable:
    """Wrap ``fn`` so it runs in the workspace of the call that wrapped it.

    A ``ContextVar`` is per-thread: a ``threading.Thread`` starts with an EMPTY
    context, so a persist thread or a campaign worker spawned from a request
    would resolve to the process workspace and write one user's answer into the
    owner's folder.  Starlette's threadpool copies the context; ``Thread`` does
    not, and this is the difference.

    With ``WORKSPACES_ROOT`` unset both ends are the process workspace and this
    is a no-op wrapper.
    """
    ws = _WS.get()
    who = _CALLER.get()
    layer = _WRITE_LAYER.get()

    @functools.wraps(fn)
    def _bound(*args, **kwargs):
        with use_workspace(ws), use_caller(who), use_write_layer(layer):
            return fn(*args, **kwargs)

    return _bound


def bind_thread(target: Callable, **kwargs) -> threading.Thread:
    """``threading.Thread`` with :func:`bind` already applied to its target."""
    return threading.Thread(target=bind(target), **kwargs)
