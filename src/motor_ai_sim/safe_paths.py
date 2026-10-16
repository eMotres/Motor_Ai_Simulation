"""One definition of "a name taken from a request may become a file name".

Audit 2026-09-29 finding #7: isolation between workspaces is application-level
(``workspace_for_request`` + a directory per account), so a die / config /
duty / card / file name that escapes its folder IS a cross-workspace read.  The
family routes validate names with a strict regex (``routes/family._check_name``)
but other readers (device cards, catalog cards, layered die resolution) joined
request strings onto a directory directly.

Three layers of defence, each independently sufficient for the case it covers:

1. :class:`TraversalGuardMiddleware` — refuses (400) any /api request whose
   raw path or query string carries a traversal pattern after up to three
   rounds of percent-decoding: a ``..`` segment, a backslash, a NUL / control
   character, an encoded slash inside a segment, an absolute path or a drive
   letter in a query value that names a file-ish thing.  Cheap, runs before
   routing, and catches double-encoded variants a route never sees decoded.
2. :func:`check_segment` — the per-name rule for code that builds a path from
   one name (raise :class:`PathRejected`, which routes turn into 422).
3. :func:`safe_join` — resolve-then-contain: the joined path, with symlinks
   resolved, must stay inside the base (also resolved).  A symlink planted in a
   workspace that points at another workspace fails here.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Iterable, Optional, Tuple
from urllib.parse import parse_qsl, unquote

log = logging.getLogger(__name__)


class PathRejected(ValueError):
    """A request-supplied name that must not become (part of) a path."""


_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")

#: Query parameters whose VALUE names a file-system object somewhere.  A value
#: of any other parameter may legitimately contain '/' (a geometry override
#: JSON, a URL) and is only checked for '..' segments and control characters.
FILEISH_PARAMS = frozenset({
    "die", "cfg", "config", "configuration", "duty", "name", "file", "path",
    "filename", "part", "id", "kind", "preset", "preset_id", "motor", "motor_id",
    "mat", "material", "pictures", "day", "panel", "component", "key", "run_id",
})


def _decode(s: str, rounds: int = 3) -> str:
    prev = s
    for _ in range(rounds):
        cur = unquote(prev)
        if cur == prev:
            break
        prev = cur
    return prev


def segment_problem(name: str) -> Optional[str]:
    """Why ``name`` is not a safe single path segment, or None when it is."""
    raw = "" if name is None else str(name)
    n = _decode(raw)
    if not n.strip():
        return "empty name"
    if _CTRL_RE.search(n):
        return "control character in name"
    if "/" in n or "\\" in n:
        return "path separator in name"
    if n in (".", "..") or n.strip() in (".", ".."):
        return "relative path segment"
    if _DRIVE_RE.match(n):
        return "drive letter in name"
    if n.startswith("~"):
        return "home-directory reference in name"
    return None


def check_segment(name: str, what: str = "name") -> str:
    """``name`` unchanged when it is a safe single segment; else PathRejected."""
    why = segment_problem(name)
    if why:
        raise PathRejected(f"{what} {name!r} rejected: {why}")
    return str(name)


def is_within(path: Path, base: Path) -> bool:
    """Does ``path`` (symlinks resolved) lie inside ``base`` (resolved)?"""
    try:
        rp = Path(os.path.realpath(str(path)))
        rb = Path(os.path.realpath(str(base)))
    except Exception:                                       # noqa: BLE001
        return False
    return rp == rb or rb in rp.parents


def safe_join(base: Path, *parts: str, what: str = "name") -> Path:
    """``base / part1 / part2 …`` — every part a safe segment and the result,
    symlinks resolved, inside ``base``.  Returns the UNresolved join (callers
    keep the path spelling they had), raises PathRejected otherwise."""
    p = Path(base)
    for part in parts:
        p = p / check_segment(part, what)
    if not is_within(p, base):
        raise PathRejected(f"{what} {'/'.join(map(str, parts))!r} resolves "
                           f"outside its folder")
    return p


# ─────────────────────────────────────────────────────────────────────────────
#  Request-level guard
# ─────────────────────────────────────────────────────────────────────────────

def _path_problem(raw_path: str) -> Optional[str]:
    # Work segment by segment on the RAW path so an encoded slash (%2F) inside
    # one segment is seen as what it is.
    for seg in raw_path.split("/"):
        if not seg:
            continue
        dec = _decode(seg)
        if dec in (".", ".."):
            return "dot segment in path"
        if "/" in dec or "\\" in dec:
            return "encoded path separator in path segment"
        if _CTRL_RE.search(dec):
            return "control character in path"
    return None


def _query_problem(query: str) -> Optional[str]:
    if not query:
        return None
    try:
        pairs: Iterable[Tuple[str, str]] = parse_qsl(query, keep_blank_values=True)
    except Exception:                                       # noqa: BLE001
        return "unparsable query string"
    for k, v in pairs:
        dec = _decode(v)
        if "\x00" in dec:
            return f"NUL in query parameter {k!r}"
        if any(s in ("..",) for s in re.split(r"[\\/]", dec)):
            return f"'..' segment in query parameter {k!r}"
        if k.lower() in FILEISH_PARAMS:
            if _CTRL_RE.search(dec):
                return f"control character in query parameter {k!r}"
            if dec.startswith(("/", "\\", "~")) or _DRIVE_RE.match(dec):
                return f"absolute path in query parameter {k!r}"
    return None


def request_problem(raw_path: str, query: str) -> Optional[str]:
    return _path_problem(raw_path) or _query_problem(query)


class TraversalGuardMiddleware:
    """Pure ASGI: 400 for a traversal-shaped /api request, before routing."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        raw = scope.get("raw_path")
        try:
            raw_path = raw.decode("latin-1") if isinstance(raw, (bytes, bytearray)) \
                else str(scope.get("path") or "")
        except Exception:                                   # noqa: BLE001
            raw_path = str(scope.get("path") or "")
        raw_path = raw_path.split("?", 1)[0]
        if not raw_path.startswith("/api"):
            await self.app(scope, receive, send)
            return
        try:
            query = (scope.get("query_string") or b"").decode("latin-1")
        except Exception:                                   # noqa: BLE001
            query = ""
        why = request_problem(raw_path, query)
        if why is None:
            await self.app(scope, receive, send)
            return
        log.warning("safe_paths: refused %s %s (%s)", scope.get("method"),
                    raw_path[:200], why)
        import json
        body = json.dumps({"detail": f"rejected: {why}"}).encode("utf-8")
        await send({"type": "http.response.start", "status": 400,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def install(app) -> None:
    """Add the guard.  Call it LAST among the request middlewares (outermost
    of the app's own) so nothing downstream sees a traversal request."""
    app.add_middleware(TraversalGuardMiddleware)
