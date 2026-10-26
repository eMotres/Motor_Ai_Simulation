r"""Where a sign-up may send the visitor back to: a same-origin app PATH, never a URL.

A shared link to a page of the app (a production page or one of its tabs,
``/production/<die>/<config>/documents?since=2026-10-19``; a development project,
``/projects/<id>/motors?die=..&config=..``) is opened by a visitor who
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
#: the sub-tabs of a production page (web: ProductionPage TABS) and the views of a development project (web: DEVELOPMENT_VIEWS)
PRODUCTION_TABS = ("bom", "tooling", "assembly", "timeline", "documents", "discussion", "proposals", "changes", "tests")
PROJECT_VIEWS = ("specification", "motors", "messages", "notes")
_PROD = re.compile(r"^/production/(%s)/(%s)(?:/(%s))?$" % (_SEG, _SEG, "|".join(PRODUCTION_TABS)))
_PROJ = re.compile(r"^/projects/([a-z0-9]+(?:-[a-z0-9]+)*)(?:/(%s))?$" % "|".join(PROJECT_VIEWS))
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
_ITEM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,79}$")
_NAME = re.compile(r"^%s$" % _SEG)
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
    return production_path(die, config, "documents", since=since, ids=ids)


def production_path(die: str, config: str, tab: Optional[str] = None, *, item: str = "", since: str = "",
                    ids: Optional[list] = None) -> Optional[str]:
    """The canonical link path of a production page (``tab`` = one of :data:`PRODUCTION_TABS`, or the production itself);
    ``item`` names one revision / thread / proposal / document inside the tab.  None when any part is not valid."""
    path = "/production/%s/%s" % (quote(die, safe=""), quote(config, safe=""))
    if tab:
        path += "/" + tab
    q = []
    if since:
        if not _valid_date(since):
            return None
        q.append("since=" + since)
    if ids:
        if len(ids) > MAX_IDS or not all(_ID.match(str(i)) for i in ids):
            return None
        q.append("ids=" + ",".join(str(i) for i in ids))
    if item:
        if not _ITEM.match(str(item)):
            return None
        q.append("item=" + str(item))
    out = path + ("?" + "&".join(q) if q else "")
    return safe_app_path(out)


def project_path(project_id: str, view: Optional[str] = None, *, die: str = "", config: str = "", item: str = "") -> Optional[str]:
    """The canonical link path of a development project (``view`` = one of :data:`PROJECT_VIEWS`); ``die`` + ``config``
    name one motor step of it.  None when any part is not valid."""
    path = "/projects/" + str(project_id)
    if view:
        path += "/" + view
    q = []
    if die or config:
        if not (die and config):
            return None
        q.append("die=" + quote(die, safe=""))
        q.append("config=" + quote(config, safe=""))
    if item:
        if not _ITEM.match(str(item)):
            return None
        q.append("item=" + str(item))
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
    try:
        fields = parse_qsl(parts.query, keep_blank_values=False, max_num_fields=20)
    except ValueError:              # more than 20 fields (or a malformed query): no return path, never a 500
        return None
    first: dict = {}
    for k, v in fields:
        first.setdefault(k, v)      # the first occurrence of a key wins, later ones are ignored
    m = _PROD.match(path)
    if m:
        return _production(m.group(1), m.group(2), m.group(3), first)
    m = _PROJ.match(path)
    if m:
        return _project(m.group(1), m.group(2), first)
    return None


def _production(die: str, config: str, tab: Optional[str], q: dict) -> Optional[str]:
    out = "/production/%s/%s" % (quote(die, safe=""), quote(config, safe=""))
    if tab:
        out += "/" + tab
    keep = []
    if "since" in q:
        if tab != "documents" or not _valid_date(q["since"]):
            return None
        keep.append("since=" + q["since"])
    if "ids" in q:
        ids = [i for i in q["ids"].split(",") if i]
        if tab != "documents" or not ids or len(ids) > MAX_IDS or not all(_ID.match(i) for i in ids):
            return None
        keep.append("ids=" + ",".join(ids))
    if "item" in q:
        if not _ITEM.match(q["item"]):
            return None
        keep.append("item=" + q["item"])
    return out + ("?" + "&".join(keep) if keep else "")


def _project(project_id: str, view: Optional[str], q: dict) -> Optional[str]:
    if len(project_id) > 64:
        return None
    out = "/projects/" + project_id
    if view:
        out += "/" + view
    keep = []
    if "die" in q or "config" in q:
        if not (_NAME.match(q.get("die", "")) and _NAME.match(q.get("config", ""))):
            return None
        keep.append("die=" + quote(q["die"], safe=""))
        keep.append("config=" + quote(q["config"], safe=""))
    if "item" in q:
        if not _ITEM.match(q["item"]):
            return None
        keep.append("item=" + q["item"])
    return out + ("?" + "&".join(keep) if keep else "")
