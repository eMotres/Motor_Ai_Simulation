"""Parsed-file cache: a yaml / json file is parsed ONCE per version of the file (owner 2026-10-19: "why build it
every time, it must already be ready").

Configure's load read the same die / configuration yaml files dozens of times per request (the family document of
a card, the die's configurations, the cooling registry, the applications ...) with the pure-Python yaml parser:
seconds of CPU under the GIL per load, which stalled every other request of the single API process.  Here a file's
parsed content is kept against its stamp ``(st_mtime_ns, st_size)`` (a write through the API, an rsync or an editor
changes it), and every reader gets its OWN deep copy - a caller that mutates what it read can never poison the next
one.  ``yaml.CSafeLoader`` (libyaml) is used when the installed PyYAML has it.

Nothing here writes anything.  ``stamp_of`` is the cheap input hash the stored answers (``configure_load_store``)
are checked against.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable, Optional, Tuple

import yaml

try:                                                    # libyaml: ~10x the pure-Python parser
    _Loader = yaml.CSafeLoader                          # type: ignore[attr-defined]
except AttributeError:                                  # pragma: no cover - PyYAML without libyaml
    _Loader = yaml.SafeLoader

#: entries kept (LRU); the catalogue is a few hundred files
MAX_ENTRIES = 2048

_CACHE: "OrderedDict[Tuple[str, str], Tuple[Tuple[int, int], Any]]" = OrderedDict()
_LOCK = threading.Lock()


#: a file written less than this long ago is parsed again: a same-size rewrite inside one filesystem time tick must
#: never be served from the cache (the tick is coarse on some filesystems)
FRESH_NS = 2_000_000_000


def file_stamp(path: Any) -> Optional[Tuple[int, int, int]]:
    """``(st_mtime_ns, st_size, st_ino)`` of a file (an atomic replace is a new inode), None when it does not exist."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def _cached(kind: str, path: Any, parse) -> Any:
    p = str(path)
    st = file_stamp(p)
    if st is None:
        raise FileNotFoundError(p)
    key = (kind, p)
    recent = time.time_ns() - st[0] < FRESH_NS
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] == st and not recent:
            _CACHE.move_to_end(key)
            return hit[1]
    with open(p, encoding="utf-8") as f:                 # parse OUTSIDE the lock: readers never wait on a parse
        val = parse(f)
    with _LOCK:
        _CACHE[key] = (st, val)
        _CACHE.move_to_end(key)
        while len(_CACHE) > MAX_ENTRIES:
            _CACHE.popitem(last=False)
    return val


def read_yaml(path: Any, *, copy_: bool = True) -> Any:
    """``yaml.safe_load`` of a file, parsed once per version.  FileNotFoundError like ``open``."""
    v = _cached("yaml", path, lambda f: yaml.load(f, Loader=_Loader))
    return copy.deepcopy(v) if copy_ else v


def read_json(path: Any, *, copy_: bool = True) -> Any:
    """``json.load`` of a file, parsed once per version."""
    v = _cached("json", path, json.load)
    return copy.deepcopy(v) if copy_ else v


def clear() -> None:
    with _LOCK:
        _CACHE.clear()


def stamp_of(paths: Iterable[Any], dirs: Iterable[Any] = (), *, salt: str = "") -> str:
    """A cheap hash of the inputs' versions: every file's stamp, and for every directory the stamps of the files
    directly and one level below it (a die folder's configurations, a propeller vendor's cards).  A file that does
    not exist adds nothing: two callers whose optional files are both absent share one stamp."""
    h = hashlib.sha1(salt.encode("utf-8"))
    for p in paths:
        st = file_stamp(p) if p else None
        if st is not None:
            h.update(f"{p}:{st}\n".encode("utf-8"))
    for d in dirs:
        try:
            for e in sorted(os.scandir(d), key=lambda x: x.name):
                if e.is_dir(follow_symlinks=False):
                    try:
                        for e2 in sorted(os.scandir(e.path), key=lambda x: x.name):
                            if e2.is_file():
                                s = e2.stat()
                                h.update(f"{e2.path}:{s.st_mtime_ns}:{s.st_size}\n".encode("utf-8"))
                    except OSError:
                        continue
                elif e.is_file():
                    s = e.stat()
                    h.update(f"{e.path}:{s.st_mtime_ns}:{s.st_size}\n".encode("utf-8"))
        except OSError:
            h.update(f"{d}:missing\n".encode("utf-8"))
    return h.hexdigest()
