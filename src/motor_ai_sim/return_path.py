r"""Where a sign-up may send the visitor back to: a same-origin app PATH, never a URL.

A shared link to a page of the app (today: the documents view of a production,
``/production/<die>/<config>/documents?since=2026-10-19``) is opened by a visitor who
may still have to register.  The registration carries the path through the
confirmation mail (``users.issue_short_link(... next_path=...)``) and the server-rendered
"confirmed" page links to it.  The path comes from the browser, so it is validated HERE
and rebuilt from its parts; the caller only ever puts the result behind the app's own
origin (``auth_email.app_url()``).

What is accepted is an ALLOW-LIST of route shapes, not "anything that looks relative":

* the path (after one round of percent-decoding) matches a known shape, every segment
  made of letters, digits, space, ``.``, ``_``, ``-`` (no ``..``, no ``/``, no ``\``);
* only the listed query keys survive, each with a strict value; everything else
  (including ``signin=1``, ``verify=``, a fragment) is dropped;
* no scheme, no host, no ``//`` or ``/\`` prefix, no control characters, at most
  ``MAX_LEN`` characters.

Anything else gives ``None`` and the caller falls back to the home page.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qsl, quote, unquote, urlsplit

MAX_LEN = 280
_SEG = r"[A-Za-z0-9][A-Za-z0-9 ._\-]{0,79}"
_DOCS = re.compile(r"^/production/(%s)/(%s)/documents$" % (_SEG, _SEG))
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
MAX_IDS = 50


def _valid_date(v: str) -> bool:
    if not _DATE.match(v):
        return False
    import datetime
    try:
        datetime.date.fromisoformat(v)
    except ValueError:
        return False
    return True


def production_documents_path(die: str, config: str, since: str = "", ids: Optional[list] = None) -> Optional[str]:
    """The canonical documents link path of one production (None when a part is not valid)."""
    path = "/production/%s/%s/documents" % (quote(die, safe=""), quote(config, safe=""))
    q = []
    if since:
        if not _valid_date(since):
            return None
        q.append("since=" + since)
    if ids:
        if len(ids) > MAX_IDS or not all(_ID.match(str(i)) for i in ids):
            return None
        q.append("ids=" + ",".join(str(i) for i in ids))
    out = path + ("?" + "&".join(q) if q else "")
    return safe_app_path(out)


def safe_app_path(raw) -> Optional[str]:
    """The canonical same-origin path for ``raw``, or None when it is not an allowed app route."""
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw or len(raw) > MAX_LEN:
        return None
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in raw) or "\\" in raw:
        return None
    if not raw.startswith("/") or raw.startswith("//"):
        return None
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if parts.scheme or parts.netloc:
        return None
    path = unquote(parts.path)
    if "\\" in path or "\x00" in path or ".." in path or "//" in path:
        return None
    m = _DOCS.match(path)
    if not m:
        return None
    die, config = m.group(1), m.group(2)
    out = "/production/%s/%s/documents" % (quote(die, safe=""), quote(config, safe=""))
    since, ids = "", []
    for k, v in parse_qsl(parts.query, keep_blank_values=False, max_num_fields=20):
        if k == "since" and not since:
            if not _valid_date(v):
                return None
            since = v
        elif k == "ids" and not ids:
            ids = [i for i in v.split(",") if i]
            if not ids or len(ids) > MAX_IDS or not all(_ID.match(i) for i in ids):
                return None
    q = []
    if since:
        q.append("since=" + since)
    if ids:
        q.append("ids=" + ",".join(ids))
    return out + ("?" + "&".join(q) if q else "")
