"""Persistent, capped run history — "don't recompute what was already solved".

WHY (owner, 2026-09-22, in Russian): *"если я запускаю те же параметры
каплинга, он не считается, а подгружает уже рассчитанный вариант; это нужно
не только для каплинга — нужна проверка и хранить небольшую историю, 10
вычислений"* — every solve kind (coupled, EM transient, thermal, mechanical
rotor stress / modes / critical speeds, limit speed) must recognise an
IDENTICAL request and hand back the stored answer instead of re-solving, and
keep the last :data:`DEFAULT_CAP` results per kind so a repeat launch is
instant even after the backend restarted.

This is deliberately a SEPARATE layer from the in-memory session caches each
route already has (``rotor_stress.cache_get``/``cache_put``,
``routes.simulation._fem_transient_cache``, ``routes.thermal._FIELD_CACHE`` …):
those exist to make a Solve press on the SAME live process instant and are
cleared on a Run / config change; they own nothing on disk and answer nothing
after a restart.  ``run_history`` is the layer that survives a restart, is
capped and eviction-ordered on purpose (an explicit, browsable "last 10", not
an unbounded memoisation table), and is addressable by the API/UI ("History"
popover, "loaded from history … · Recompute").  A route that already checks
its session cache should check history SECOND, on a miss, before it solves —
and only ONE of the two layers should be the one that actually persists a
result to disk; see each route's integration comment for which one that is.

STORAGE
=======
Per WORKSPACE (``motor_ai_sim.workspace.root()`` — ``config/`` on this
workstation, ``<WORKSPACES_ROOT>/<ws_id>/`` on the server) and per KIND::

    <workspace root>/.run_history/<kind>/index.json       <- metadata, newest last
    <workspace root>/.run_history/<kind>/<key>.pkl         <- one payload per entry

``index.json`` is small (10 entries x a few hundred bytes) and human-readable
on purpose — it is what a support session reads by hand.  The payload is
pickled because a solved result is not always JSON-safe as it stands (numpy
arrays in a stress/field map, the mechanical route's own ``.last_*.pkl`` are
the precedent) — small numbers of writes, so the cost is nothing next to the
solve it replaces.

Both files are written tmp-then-``os.replace`` (same directory, so the
rename is atomic on NTFS and ext4 alike) under a per-(workspace, kind) RLock
(``workspace.ws_lock``, so it is the correct lock for whichever workspace the
calling request is in, never the process-wide one).

THE KEY
=======
A history entry is addressed by a 16-hex-character key: ``sha1`` of a
canonical JSON document ``{"kind": <kind>, "p": <canonical params dict>}``.
"Canonical" means the route's key-builder has already rounded every float to
a fixed precision and turned every unordered collection (a material
assignment, a contact-pair map) into a sorted tuple/list BEFORE handing the
dict to :func:`make_key` — ``json.dumps(..., sort_keys=True)`` only takes
care of dict KEY order, not float formatting or list/set order, and a test
that "reordering/float formatting does not change the key" is a test of the
route's key-builder, not of this module (:func:`make_key` itself is a pure
hash of whatever canonical structure it is given).  Each route's key-builder
carries a docstring enumerating every field that must be in the key — get
that enumeration wrong and either two different machines share an entry
(wrong answer served) or the cache never hits (silent regression to "always
recomputes"); both are why :func:`make_key` never invents rounding or
sorting itself and always makes the caller do it up front, in one visible
place per kind.

CODE VERSION
============
Every entry is stamped with :func:`code_version` at write time, and
:meth:`RunHistory.get` refuses a hit whose stamp does not match the RUNNING
code's version: a solver fix changes what "the same request" computes to, and
serving yesterday's answer under today's build would silently un-fix the bug
for every repeat launch.  Resolution order — the first that is available
wins:

1. ``.deployed_commit`` next to the app root (the server's own marker file,
   written by the deploy script beside ``src/`` — see
   ``AGENT_RULES_motor_ai_sim.md`` §5.5).
2. ``git rev-parse HEAD`` of the checkout this file lives in, read ONCE at
   first use and cached (a dev checkout does not change SHA mid-process).
3. ``"unknown"`` — no deploy marker, no git (a packaged install with neither);
   entries written under "unknown" only ever match other entries written
   under "unknown" in the SAME process, so this degrades to "no history
   across restarts" rather than ever serving a stale answer confidently.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import pickle
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from motor_ai_sim import workspace as _WSP

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_CAP", "code_version", "make_key", "RunHistory", "history_for",
    "register_kind", "known_kinds", "register_loader", "loader_for",
]

#: The owner's number, verbatim ("хранить небольшую историю, 10 вычислений").
DEFAULT_CAP = 10

_DIRNAME = ".run_history"


# ─────────────────────────────────────────────────────────────────────────────
#  code_version
# ─────────────────────────────────────────────────────────────────────────────

_CV_LOCK = threading.Lock()
_CV_CACHE: Optional[str] = None


def _repo_root() -> Path:
    # src/motor_ai_sim/run_history.py -> repo root is two parents up.
    return Path(__file__).resolve().parents[2]


def _from_deployed_commit_marker() -> Optional[str]:
    """``.deployed_commit`` beside the app root, or in this file's ancestry.

    Checked a few levels up from this file (not just the repo root) because
    the server layout (``/opt/motres/app/.deployed_commit`` beside
    ``/opt/motres/app/src``) and a plain checkout both put ``src`` one level
    below the marker.
    """
    here = Path(__file__).resolve()
    for parent in list(here.parents)[:6]:
        marker = parent / ".deployed_commit"
        if marker.is_file():
            try:
                sha = marker.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if sha:
                return sha[:40]
    return None


def _from_git_head() -> Optional[str]:
    try:
        import subprocess
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(_repo_root()),
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode("ascii", "ignore").strip()
        return sha[:40] if sha else None
    except Exception:                                       # noqa: BLE001
        return None


def code_version() -> str:
    """The running code's identity — see the module docstring for the order.

    Computed once per process and cached: neither the deploy marker nor
    ``HEAD`` changes while this process is alive, and every route calls this
    on every solve, so it must be free after the first call.
    """
    global _CV_CACHE
    if _CV_CACHE is not None:
        return _CV_CACHE
    with _CV_LOCK:
        if _CV_CACHE is not None:
            return _CV_CACHE
        v = _from_deployed_commit_marker() or _from_git_head() or "unknown"
        _CV_CACHE = v
        return v


def _reset_code_version_cache_for_tests() -> None:
    """Test-only escape hatch — see ``tests/test_run_history.py``."""
    global _CV_CACHE
    _CV_CACHE = None


# ─────────────────────────────────────────────────────────────────────────────
#  The key
# ─────────────────────────────────────────────────────────────────────────────

def make_key(kind: str, canonical: Dict[str, Any]) -> str:
    """``sha1(json({"kind": kind, "p": canonical}))[:16]``.

    ``canonical`` must already be JSON-serialisable and normalised by the
    caller (rounded floats, sorted unordered collections) — see the module
    docstring.  ``sort_keys=True`` here only protects against dict INSERTION
    order, which is the one normalisation this function can safely do for
    every caller without knowing what any particular field means.
    """
    blob = json.dumps({"kind": str(kind), "p": canonical},
                      sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────────────────────
#  The store
# ─────────────────────────────────────────────────────────────────────────────

class RunHistory:
    """The capped history of one solve KIND, in the CALLING workspace.

    Stateless beyond ``kind``/``cap``: every method resolves
    ``motor_ai_sim.workspace.root()`` and a per-(workspace, kind) lock fresh,
    on each call, exactly like ``workspace.ws_map``/``ws_lock`` do — so a
    ``RunHistory("mechanical.rotor_stress")`` built once at import time and
    used from many requests is still correctly isolated per caller.
    """

    def __init__(self, kind: str, cap: int = DEFAULT_CAP) -> None:
        self.kind = str(kind)
        self.cap = int(cap)

    # ── paths ────────────────────────────────────────────────────────────────
    def _dir(self) -> Path:
        d = _WSP.root() / _DIRNAME / self.kind
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _index_path(self) -> Path:
        return self._dir() / "index.json"

    def _lock(self):
        # A fresh name per kind, resolved against THIS call's workspace —
        # see workspace.ws_lock's own contract.
        return _WSP.ws_lock(f"run_history.{self.kind}")

    # ── index I/O ────────────────────────────────────────────────────────────
    def _read_index_locked(self) -> List[Dict[str, Any]]:
        p = self._index_path()
        if not p.is_file():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:                            # noqa: BLE001
            log.warning("run_history[%s]: corrupt index %s (%s) — starting "
                       "fresh; the payload files it named are now orphaned "
                       "and harmless", self.kind, p, exc)
            return []
        return data if isinstance(data, list) else []

    def _write_index_locked(self, entries: List[Dict[str, Any]]) -> None:
        p = self._index_path()
        tmp = p.with_name(f"{p.name}.tmp-{os.getpid()}-{threading.get_ident()}")
        tmp.write_text(json.dumps(entries, default=str), encoding="utf-8")
        os.replace(str(tmp), str(p))

    def _payload_path(self, key: str) -> Path:
        return self._dir() / f"{key}.pkl"

    def _unlink_payload(self, key: Optional[str]) -> None:
        if not key:
            return
        try:
            self._payload_path(key).unlink(missing_ok=True)
        except OSError:                                     # noqa: BLE001
            pass

    # ── the public surface ───────────────────────────────────────────────────
    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """``{"entry": <metadata dict>, "payload": <the stored result>}`` on a
        hit, else ``None``.  A version mismatch is a MISS, not an error — the
        route falls through to solving exactly as an empty history would.

        A hit is touched (moved to the newest end) without changing
        ``computed_at``: re-loading an old answer must not make it look
        freshly solved, but it should survive the NEXT cap-eviction over
        entries nobody has asked for again.
        """
        with self._lock():
            entries = self._read_index_locked()
            entry = next((e for e in entries if e.get("key") == key), None)
            if entry is None:
                return None
            if entry.get("code_version") != code_version():
                log.info("run_history[%s]: %s is from an older build (%s != "
                        "%s) — not served", self.kind, key,
                        entry.get("code_version"), code_version())
                return None
            payload_path = self._payload_path(key)
            if not payload_path.is_file():
                log.warning("run_history[%s]: %s has no payload file — "
                           "dropping the stale index row", self.kind, key)
                self._write_index_locked(
                    [e for e in entries if e.get("key") != key])
                return None
            try:
                with open(payload_path, "rb") as fh:
                    payload = pickle.load(fh)
            except Exception as exc:                        # noqa: BLE001
                log.warning("run_history[%s]: could not read payload for %s: "
                           "%s", self.kind, key, exc)
                return None
            entries = [e for e in entries if e.get("key") != key]
            entries.append(entry)
            self._write_index_locked(entries)
            return {"entry": dict(entry), "payload": payload}

    def put(self, key: str, *, params: Dict[str, Any], summary: str,
           payload: Any, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Store (or overwrite) ``key`` as the newest entry; evict beyond cap.

        Overwriting an EXISTING key moves it to the newest end rather than
        adding a duplicate row — the same "re-assigning moves it to the end"
        rule ``workspace.BoundedStore`` documents, for the same reason: a
        request solved again (``fresh=true``) must not both keep its old row
        AND push out an unrelated one.
        """
        with self._lock():
            entries = self._read_index_locked()
            entries = [e for e in entries if e.get("key") != key]
            payload_path = self._payload_path(key)
            tmp = payload_path.with_name(
                f"{payload_path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
            with open(tmp, "wb") as fh:
                pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(str(tmp), str(payload_path))
            entry = {
                "key": str(key),
                "kind": self.kind,
                "computed_at": _dt.datetime.now(_dt.timezone.utc)
                                  .isoformat(timespec="seconds"),
                "code_version": code_version(),
                "params": params,
                "summary": str(summary),
            }
            if extra:
                entry.update(extra)
            entries.append(entry)
            while len(entries) > self.cap:
                dead = entries.pop(0)
                self._unlink_payload(dead.get("key"))
            self._write_index_locked(entries)
            return dict(entry)

    def list(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Metadata only (no payload), newest first."""
        with self._lock():
            entries = list(reversed(self._read_index_locked()))
        if limit is not None:
            entries = entries[: max(int(limit), 0)]
        return entries

    def delete(self, key: str) -> bool:
        with self._lock():
            entries = self._read_index_locked()
            keep = [e for e in entries if e.get("key") != key]
            if len(keep) == len(entries):
                return False
            self._unlink_payload(key)
            self._write_index_locked(keep)
            return True

    def __len__(self) -> int:
        with self._lock():
            return len(self._read_index_locked())


def history_for(kind: str, cap: int = DEFAULT_CAP) -> RunHistory:
    """The usual way to get a :class:`RunHistory` — a name, not a singleton
    (see the class docstring for why one instance is already safe to share,
    and why a fresh one is just as cheap)."""
    return RunHistory(kind, cap=cap)


# ─────────────────────────────────────────────────────────────────────────────
#  The generic API's registries — ``routes/history.py``
# ─────────────────────────────────────────────────────────────────────────────
# Process-wide, populated at IMPORT time by each route module that wires a
# kind in (``routes/mechanical.py`` etc.) — never per-workspace, because
# "which kinds exist" and "how to load one" are properties of the running
# CODE, not of any one caller's data.

_KNOWN_KINDS: List[str] = []
_LOADERS: Dict[str, Any] = {}


def register_kind(kind: str) -> None:
    """Make ``kind`` show up in ``GET /api/history`` when ``?kind=`` is
    omitted.  Idempotent — safe to call at module import time even if the
    module reloads (tests)."""
    kind = str(kind)
    if kind not in _KNOWN_KINDS:
        _KNOWN_KINDS.append(kind)


def known_kinds() -> List[str]:
    return list(_KNOWN_KINDS)


def register_loader(kind: str, fn) -> None:
    """Say what "load this history entry as the current result" means for
    ``kind`` — e.g. the mechanical route's ``_remember_last``, the EM route's
    field-snapshot restore, the coupled route's S1 bookkeeping.  ``fn(entry,
    payload) -> response dict``.  A kind with no registered loader is served
    by ``routes/history.py`` as the raw stored payload, stamped
    ``served_from_history`` — correct for a kind whose "current result" IS
    just its response body, wrong for one that also has to update other
    state, which is exactly why this exists as a per-kind hook rather than
    one generic implementation.
    """
    _LOADERS[str(kind)] = fn


def loader_for(kind: str):
    return _LOADERS.get(str(kind))
