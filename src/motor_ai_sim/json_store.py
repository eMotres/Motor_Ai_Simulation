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
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

# ``os.replace`` retry budget.  On Windows a rename fails with PermissionError
# while ANY other handle is open on the destination — and these stores are read
# on request paths, so a concurrent reader (or an editor, or the virus scanner)
# is normal, not exceptional.  Measured 2026-09-03: two
# ``motor_catalog.json.tmp -> motor_catalog.json`` PermissionErrors, and BOTH
# writes were simply lost.  10 tries with exponential backoff ≈ 1.9 s, which is
# far longer than a reader holds the file.
_REPLACE_TRIES = 10
_REPLACE_BACKOFF_S = 0.05
_REPLACE_BACKOFF_MAX_S = 0.25

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
    """Write via temp file + replace, so a reader never sees a partial document.

    The replace is RETRIED, and if it still cannot happen the document is
    written in place instead.  A lost write is the worst outcome available
    here: the caller has already merged its change into the latest document
    (see ``mutate_json``) and believes it is saved, so a silently dropped
    ``os.replace`` erases an edit AND reports success.  A torn read, which the
    in-place fallback risks for a reader that is mid-parse, is recoverable —
    ``read_json`` treats an unparsable file as empty and the next write fixes
    it — and it happens under ``lock_for(path)``, so no other writer in THIS
    process can be interleaved with it.  The fallback is logged as a WARNING
    naming the file, because "your catalog was written the unsafe way" is
    something the owner has to be able to find afterwards.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=indent, ensure_ascii=False)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    last: Optional[BaseException] = None
    delay = _REPLACE_BACKOFF_S
    for attempt in range(1, _REPLACE_TRIES + 1):
        try:
            os.replace(tmp, p)
            if attempt > 1:
                log.info("%s: os.replace succeeded on attempt %d "
                         "(the file was locked by another reader)",
                         p.name, attempt)
            return
        except PermissionError as e:      # Windows: destination handle open
            last = e
            if attempt < _REPLACE_TRIES:
                time.sleep(delay)
                delay = min(_REPLACE_BACKOFF_MAX_S, delay * 2)
    # Still locked after the whole budget: write in place rather than lose the
    # document.  Under the store lock, so this process cannot be racing itself.
    with lock_for(p):
        p.write_text(text, encoding="utf-8")
    log.warning("%s: os.replace stayed locked after %d tries (%s) — wrote the "
                "document IN PLACE under the store lock instead of losing it; "
                "a reader parsing the file at that instant may have seen it "
                "truncated and will recover on the next read",
                p, _REPLACE_TRIES, last)
    try:
        tmp.unlink()
    except OSError:
        pass


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
