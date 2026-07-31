"""Read-latest → merge → atomic-write for the small JSON stores under config/.

Why this exists.  ``motor_presets.json`` and ``motor_catalog.json`` were each
edited by the pattern "load the whole file into a dict, change one entry, write
the whole dict back".  Between the load and the write anything else can touch
that file — a second request on the uvicorn threadpool, an optimizer subprocess,
a text editor, ``git checkout`` — and the write does not merge with it, it
REPLACES it.  The other writer's entry does not conflict; it silently ceases to
exist.  That is the same failure as serving a stale cache, one layer down: a
decision made from a copy of the world that has since moved on.

The rule here is that the copy a write is built from must be read at WRITE time,
under a lock, and only the target key may be touched::

    mutate_json(path, lambda d: d.__setitem__(pid, preset))

The mutator receives the freshly-re-read document and edits it in place, so an
entry added by someone else after our first read survives.  The write itself is
temp-file + ``os.replace`` — atomic on both POSIX and Windows — so a concurrent
READER can never catch the file mid-truncate and parse it as empty (which, for a
store whose loader falls back to ``{}`` on a parse error, would erase it on the
next save).

Locks are keyed by the resolved path, so two modules that write the same file
(presets.py and catalog.py both write the catalog) share one lock without having
to know about each other.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

# path -> lock.  Guarded by _LOCKS_GUARD so two threads racing to create the
# lock for the same file cannot end up with one lock each (which would lock
# nothing at all).
_LOCKS: Dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def lock_for(path: Path) -> threading.RLock:
    """The one lock guarding this file, shared by every module that writes it."""
    key = str(Path(path).resolve())
    with _LOCKS_GUARD:
        lk = _LOCKS.get(key)
        if lk is None:
            lk = threading.RLock()
            _LOCKS[key] = lk
        return lk


def read_json(path: Path, default: Any = None) -> Any:
    """Parse the file, or return ``default`` if it is missing/corrupt.

    Never raises: these stores are read on request paths where a broken file
    must degrade to "empty", not to a 500 for every endpoint that touches it.
    """
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("could not parse %s (%s) — treating as empty", p, e)
        return default


def atomic_write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    """Write via temp file + replace, so a reader never sees a partial document."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=indent, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, p)


def mutate_json(path: Path, mutator: Callable[[Any], Optional[Any]], *,
                default: Any = None, indent: int = 2) -> Any:
    """Re-read under the lock, apply ``mutator`` to the LATEST document, write it.

    ``mutator`` may edit the document in place (return ``None``) or return the
    document to write.  Its input is never a snapshot the caller took earlier —
    that is the whole point.

    Returns the document that was written, so a caller that needs to report what
    it ended up with does not have to read the file a third time.
    """
    with lock_for(path):
        doc = read_json(path, default if default is not None else {})
        out = mutator(doc)
        if out is None:
            out = doc
        atomic_write_json(path, out, indent=indent)
        return out
