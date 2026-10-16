"""Who may see WHICH motors of the shared catalog.

The catalog (config/dies) is one shared library; the accounts that read it are
not one audience.  Until 2026-09-02 a signed-in non-admin saw the motors that
happened to carry a passported catalog card — a marketing rule ("show only what
a visitor can actually open") standing in for an access rule, which is why the
owner's own second account could not see the two 85 mm dies.

Access is now REGISTRY data: `motors: {"all": bool, "dies": [names]}` on the
user record (motor_ai_sim.users).  Three answers:

* ``all``        — admins (role admin / ADMIN_EMAILS), any account granted
                   ``all``, and — when CATALOG_GRANT_ALL_REGISTERED is on —
                   every signed-in account.  Sees the whole catalog.
* ``granted``    — a signed-in account sees exactly its granted dies.  A new
                   account has no ``motors`` key at all and therefore sees
                   NOTHING until the vendor assigns motors to it.
* ``anonymous``  — no credentials: the public exhibit, still filtered by the
                   passport rule in routes/family.tree.  DELIBERATELY unchanged
                   here — it is the marketing landing, and the one place a
                   follow-up decision may flip (open everything to registered
                   users, or close the exhibit entirely).

CONFIGURATION-level visibility (owner 2026-10-15, "the die stays ONE for all; we
hide the configuration"): a configuration yaml may carry ``visibility: private``
(default ``public``).  A private configuration inside a die the caller may see is
shown only to admins and to accounts holding a per-user CONFIG grant
(``motors.configs = ["<die>/<config>"]``).  ``may_see_config`` is the one answer,
used by every route, tool and listing that can name a configuration; a hidden
configuration is indistinguishable from a non-existent one (404, not listed, not
counted).  See ``may_see_config`` below.

That follow-up decision arrived on 2026-09-16, and it was "close it" — but NOT
here.  An internet-facing deployment sets ``PUBLIC_EXHIBIT=0`` and then no
anonymous request reaches this module at all: the door is one gate in
``auth.RoleGateMiddleware`` (``auth.public_exhibit`` / ``auth.anonymous_allowed``)
and it answers 401 before any route runs.  ``MODE_ANONYMOUS`` stays exactly as
it is because the workstation — where the variable is unset — still uses it, and
a second copy of the rule per route is how such rules drift apart.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Optional

from motor_ai_sim.auth import ANON_OWNER, caller_identity
from motor_ai_sim.config import DEFAULT_CONFIG_PATH

import contextvars as _contextvars

from starlette.requests import Request as _Request

#: The ``Authorization`` header of the HTTP request being served, bound by
#: :func:`config_path_gate` (it runs for EVERY route).  The workspace middleware
#: binds the caller only when multi-user layering is on; this makes the same
#: question answerable for a helper deep inside a handler on a single-user
#: install too.  ``_UNBOUND`` outside a request (a CLI, a nightly script, a
#: background thread): the configuration flag is then NOT enforced - there is
#: no caller to refuse.
_UNBOUND = object()
_REQ_AUTH: "_contextvars.ContextVar" = _contextvars.ContextVar(
    "motor_access_request_auth", default=_UNBOUND)

MODE_ALL = "all"
MODE_GRANTED = "granted"
MODE_ANONYMOUS = "anonymous"

_ENV_GRANT_ALL = "CATALOG_GRANT_ALL_REGISTERED"

VIS_PRIVATE = "private"
VIS_PUBLIC = "public"
VIS_SELECTED = "selected"
VISIBILITIES = frozenset({VIS_PRIVATE, VIS_PUBLIC, VIS_SELECTED})


def grant_all_registered() -> bool:
    """The 'later' mode the user asked to keep switchable: every REGISTERED
    account sees the whole catalog.  Read per call, so flipping the env (or a
    test monkeypatching it) takes effect without a restart.  Default OFF."""
    return os.environ.get(_ENV_GRANT_ALL, "0").strip().lower() in (
        "1", "true", "yes", "on")


def catalog_access(authorization: Optional[str] = None) -> dict:
    """`{"mode", "dies", "email", "is_admin"}` for the calling credentials.

    One identity resolution for the whole request — `is_admin` here is the same
    answer `caller_identity` gives every other route, so `can_write` and
    visibility can never drift apart.
    """
    who = None
    if not (isinstance(authorization, str) and authorization.strip()):
        # No header in hand (a helper deep in a request, an MCP tool): the
        # identity bound for THIS request is the caller - the same answer
        # ``caller_identity(header)`` gave at the door.
        try:
            from motor_ai_sim import workspace as _WS
            who = _WS.caller()
        except Exception:                           # noqa: BLE001
            who = None
        if not isinstance(who, dict):
            bound = _REQ_AUTH.get()
            if bound is not _UNBOUND:
                authorization = bound
    if not isinstance(who, dict):
        who = caller_identity(authorization)
    if who.get("is_admin"):
        return {"mode": MODE_ALL, "dies": frozenset(), "email": None,
                "is_admin": True, "configs": frozenset()}
    ident = str(who.get("id") or "")
    if not ident or ident == ANON_OWNER:
        return {"mode": MODE_ANONYMOUS, "dies": frozenset(), "email": None,
                "is_admin": False, "configs": frozenset()}
    # Configuration grants ride with EVERY signed-in answer below: an ``all`` or
    # a grant-everyone account still sees a PRIVATE configuration only when it
    # was granted that configuration by name.
    from motor_ai_sim import users as _users
    grants = _users.get_motor_grants(ident)
    cfgs = frozenset(grants.get("configs") or [])
    if grant_all_registered():
        return {"mode": MODE_ALL, "dies": frozenset(), "email": ident,
                "is_admin": False, "configs": cfgs}
    if grants.get("all"):
        return {"mode": MODE_ALL, "dies": frozenset(), "email": ident,
                "is_admin": False, "configs": cfgs}
    return {"mode": MODE_GRANTED, "dies": frozenset(grants.get("dies") or []),
            "email": ident, "is_admin": False, "configs": cfgs}


def may_see_die(access: dict, die: str) -> bool:
    """May THIS caller read the named die at all?

    Anonymous is allowed through here on purpose: the public exhibit is
    filtered where it is BUILT (routes/family.tree) and its per-die reads have
    always been open — closing them is the follow-up decision documented above,
    not part of the per-user grant work.  For a signed-in non-admin the answer
    is the grant, OR the die's own owner-set visibility (public / selected
    clients — see ``get_die_access`` below), so hiding a motor in the tree is
    not cosmetic: the payload / datasheet / context routes 404 on it, and MCP
    tools (list_machines, check_fit) ask this same function.
    """
    mode = access.get("mode")
    if mode in (MODE_ALL, MODE_ANONYMOUS):
        return True
    if str(die) in access.get("dies", frozenset()):
        return True
    da = get_die_access(die)
    vis = da.get("visibility")
    if vis == VIS_PUBLIC:
        return True
    if vis == VIS_SELECTED:
        email = (access.get("email") or "").strip().lower()
        return bool(email) and email in da.get("clients", ())
    return False


# ── per-die access (public / selected clients) ───────────────────────────────
# A SECOND, ORTHOGONAL visibility rule on top of the per-user grant above: the
# grant says "this account may see its own list of dies"; this says "this die,
# regardless of who is asking, is public (any signed-in account) or shared
# with a short list of named accounts".  One admin-only store, keyed by die
# name, not duplicated per user — the owner sets it once on the die.
#
# Deliberately NOT anonymous-facing: "every signed-in user" is the whole
# promise here, same as the docstring above. The anonymous public exhibit
# stays exactly the passport-filtered set routes/family.tree already builds.
#
# Recipients are read-only: this module only ever answers "may see", nothing
# here grants write access to the owner's die (require_catalog_write is the
# separate, unaffected gate for that).

_DIE_ACCESS_FILE = Path(DEFAULT_CONFIG_PATH).parent / "die_access.json"
_DA_LOCK = threading.Lock()


def die_access_file() -> Path:
    """The path itself, for callers (routes/family._tree_signature) that only
    need its mtime — kept as a function so tests can monkeypatch the module
    attribute and have both readers/writers and this follow along."""
    return _DIE_ACCESS_FILE


def _load_die_access() -> dict:
    try:
        with open(_DIE_ACCESS_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}          # unreadable → treat as "nothing set" (fail private)
    return d if isinstance(d, dict) else {}


def _save_die_access(d: dict) -> None:
    _DIE_ACCESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _DIE_ACCESS_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, ensure_ascii=False, sort_keys=True)
    tmp.replace(_DIE_ACCESS_FILE)


def normalize_die_access(raw: Optional[dict]) -> dict:
    raw = raw or {}
    vis = str(raw.get("visibility") or VIS_PRIVATE).strip().lower()
    if vis not in VISIBILITIES:
        vis = VIS_PRIVATE
    clients = raw.get("clients")
    if not isinstance(clients, (list, tuple, set)):
        clients = []
    return {"visibility": vis,
            "clients": sorted({str(c).strip().lower() for c in clients if str(c).strip()})}


def get_die_access(die: str) -> dict:
    return normalize_die_access(_load_die_access().get(str(die)))


def all_die_access() -> dict:
    """``{die_name: {"visibility", "clients"}}`` for every die that has a
    non-default entry — used by the admin list (dies with no entry are
    private by default and not worth a row of zeros in the store)."""
    return {k: normalize_die_access(v) for k, v in _load_die_access().items()}


def set_die_access(die: str, *, visibility: str, clients: Optional[list] = None) -> dict:
    """Replace one die's access.  Same atomic-write discipline as
    users.set_motor_grants: one writer, one lock, one file."""
    die = str(die).strip()
    entry = normalize_die_access({"visibility": visibility, "clients": clients or []})
    with _DA_LOCK:
        store = _load_die_access()
        if entry["visibility"] == VIS_PRIVATE and not entry["clients"]:
            store.pop(die, None)   # back to default — do not carry dead rows
        else:
            store[die] = entry
        _save_die_access(store)
    return get_die_access(die)


# ── per-CONFIGURATION visibility (owner 2026-10-15) ──────────────────────────
# "The die stays ONE for all; we hide the configuration."  A configuration yaml
# may carry a top-level ``visibility: private`` (absent or anything else =
# ``public``).  The key lives IN the configuration file on purpose: it travels
# with the configuration (history, backups, duplicate), there is no second store
# to drift, and the owner flips it with one ``yaml`` edit or the Admin toggle.
#
#   may_see_config(access, die, cfg) =
#       may_see_die(access, die)
#       AND ( admin
#             OR the configuration is public
#             OR "<die>/<cfg>" is in the account's CONFIG grants )
#
# Two deliberate edges:
#   * the caller's OWN workspace copy of a die is theirs (layering on, layer
#     ``workspace``): the flag guards the SHARED catalog, like every grant;
#   * an unreadable file is treated as PRIVATE (fail closed), a MISSING file is
#     "public" (there is nothing to hide, and the route 404s for its own reason).

CFG_PRIVATE = "private"
CFG_PUBLIC = "public"

_CFG_VIS_RE = None
#: path -> (mtime_ns, size, "private"|"public"); a stat per lookup, a read only
#: when the file changed (a duty save rewrites the file, so it re-reads then).
_CFG_VIS_CACHE: dict = {}
_CFG_VIS_LOCK = threading.Lock()


def _scan_visibility(text: str) -> str:
    """The top-level ``visibility:`` scalar of a configuration document.

    A line scan, not a YAML parse: configuration files carry every duty and its
    saved results (megabytes), and this runs for every row of every tree
    request.  Only an UNINDENTED ``visibility:`` line counts, so a nested key of
    that name (a duty, a result) can never flip it.
    """
    global _CFG_VIS_RE
    if _CFG_VIS_RE is None:
        import re
        _CFG_VIS_RE = re.compile(r"^visibility[ \t]*:[ \t]*['\"]?([A-Za-z_-]*)",
                                 re.MULTILINE)
    m = _CFG_VIS_RE.search(text)
    if not m:
        return CFG_PUBLIC
    return CFG_PRIVATE if m.group(1).strip().lower() == CFG_PRIVATE else CFG_PUBLIC


def file_visibility(path) -> str:
    """``"private"`` / ``"public"`` for one configuration FILE (cached by mtime)."""
    p = Path(str(path))
    try:
        st = p.stat()
    except FileNotFoundError:
        return CFG_PUBLIC
    except OSError:
        return CFG_PRIVATE                    # cannot tell -> hide
    key = str(p)
    with _CFG_VIS_LOCK:
        hit = _CFG_VIS_CACHE.get(key)
        if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2]
    try:
        vis = _scan_visibility(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return CFG_PRIVATE
    with _CFG_VIS_LOCK:
        if len(_CFG_VIS_CACHE) > 4096:
            _CFG_VIS_CACHE.clear()
        _CFG_VIS_CACHE[key] = (st.st_mtime_ns, st.st_size, vis)
    return vis


def _own_workspace_die(die: str) -> bool:
    """True when ``die`` resolves into the CALLER'S OWN workspace (layering on)."""
    try:
        from motor_ai_sim.routes import family as _fam
        if not _fam._ws_layering():
            return False
        from motor_ai_sim import workspace as _WS
        return _fam._die_layer(str(die)) == _WS.LAYER_WORKSPACE
    except Exception:                           # noqa: BLE001
        return False


def config_visibility(die: str, cfg: str) -> str:
    """The stored visibility of ``<die>/<cfg>`` as the CURRENT caller's catalog
    resolves it (``"private"`` | ``"public"``).  Not an access decision."""
    try:
        from motor_ai_sim.routes import family as _fam
        path = _fam._cfg_file(str(die), str(cfg))
    except Exception:                           # noqa: BLE001 — bad name: nothing to hide
        return CFG_PUBLIC
    if _own_workspace_die(die):
        return CFG_PUBLIC
    return file_visibility(path)


def private_config_index() -> dict:
    """``{casefolded "<die> <config>": (die, config)}`` of every PRIVATE
    configuration in the catalog the caller reads (the caller's own workspace
    copies excluded).  How a catalog CARD - which is tied to its machine only by
    that name - finds out it describes a hidden configuration."""
    out: dict = {}
    try:
        from motor_ai_sim.routes import family as _fam
        from motor_ai_sim import workspace as _WS
        layering = _fam._ws_layering()
        for e in _fam._iter_die_entries():
            if layering and e.get("layer") == _WS.LAYER_WORKSPACE:
                continue
            die = str(e["name"])
            for f in Path(str(e["dir"])).glob("*.yaml"):
                if f.name == "die.yaml" or file_visibility(f) != CFG_PRIVATE:
                    continue
                out[f"{die} {f.stem}".casefold()] = (die, f.stem)
    except Exception:                           # noqa: BLE001 - a listing, never fatal
        pass
    return out


def drop_private_config_cards(cards: list, access: dict) -> list:
    """The catalog cards whose machine the caller may see: a card named
    ``"<die> <config>"`` for a PRIVATE configuration without a grant is dropped.
    Admins keep everything."""
    if access.get("is_admin") or not cards:
        return cards
    idx = private_config_index()
    if not idx:
        return cards
    granted = access.get("configs") or frozenset()
    keep = []
    for m in cards:
        hit = idx.get(str(m.get("name") or "").strip().casefold())
        if hit is None and m.get("die") and m.get("config"):
            hit = idx.get(f"{m.get('die')} {m.get('config')}".casefold())
        if hit is not None and config_grant_key(*hit) not in granted:
            continue
        keep.append(m)
    return keep


def config_grant_key(die: str, cfg: str) -> str:
    return f"{die}/{cfg}"


def may_see_config(access: dict, die: str, cfg: Optional[str]) -> bool:
    """May THIS caller read configuration ``cfg`` of ``die``?

    ``cfg`` falsy = the question is about the die only.  Anonymous callers
    never hold a grant, so a private configuration is hidden from the public
    exhibit too.  Always false when ``may_see_die`` is false.
    """
    if not may_see_die(access, die):
        return False
    if not cfg:
        return True
    if access.get("is_admin"):
        return True
    if config_visibility(die, cfg) != CFG_PRIVATE:
        return True
    return config_grant_key(str(die), str(cfg)) in (access.get("configs") or frozenset())


def config_allowed(access: dict, die: str, cfg: Optional[str]) -> bool:
    """The CONFIGURATION half of :func:`may_see_config` alone (admin / public / granted),
    for a caller that has settled the die question itself - the published (community)
    layer is open to every registered account, so ``may_see_die`` would be the wrong
    question there."""
    if not cfg or access.get("is_admin"):
        return True
    if config_visibility(die, cfg) != CFG_PRIVATE:
        return True
    return config_grant_key(str(die), str(cfg)) in (access.get("configs") or frozenset())


def may_see_config_file(access: dict, die: str, cfg: str, path) -> bool:
    """:func:`may_see_config` for a caller that already holds the file ``path``
    (the tree walks every file once; no second resolution per row)."""
    if access.get("is_admin"):
        return True
    if file_visibility(path) != CFG_PRIVATE:
        return True
    return config_grant_key(str(die), str(cfg)) in (access.get("configs") or frozenset())


def require_config(die: str, cfg: Optional[str], authorization: Optional[str] = None,
                   *, access: Optional[dict] = None) -> dict:
    """404 unless the caller may read ``<die>/<cfg>`` — the SAME message a
    non-existent configuration of a visible die would get ("not found"), so a
    hidden configuration is not an oracle.  Returns the access dict."""
    from fastapi import HTTPException
    acc = access if access is not None else catalog_access(authorization)
    if cfg and not may_see_config(acc, die, cfg):
        raise HTTPException(404, detail=f"configuration not found: {cfg}.yaml")
    return acc


def set_config_visibility(die: str, cfg: str, visibility: str) -> str:
    """Write ``visibility`` onto the configuration file (admin routes only).

    A text-level edit of the single top-level line: the file is NOT re-dumped,
    so nothing else in it (key order, comments, saved results) is touched.
    ``public`` removes the line (the default), ``private`` adds / rewrites it.
    """
    from fastapi import HTTPException
    vis = CFG_PRIVATE if str(visibility).strip().lower() == CFG_PRIVATE else CFG_PUBLIC
    from motor_ai_sim.routes import family as _fam
    # The file the (admin) caller READS is the one edited: an admin resolves the
    # curated shared catalog first, so this is the shared configuration.
    path = _fam._cfg_file(str(die), str(cfg))
    if not path.is_file():
        raise HTTPException(404, detail=f"configuration not found: {cfg}.yaml")
    import re
    raw = path.read_bytes()                       # bytes: the file's own line endings / BOM survive untouched
    pat = re.compile(rb"^visibility[ \t]*:[^\r\n]*(?:\r?\n|$)", re.MULTILINE)
    raw = pat.sub(b"", raw)
    if vis == CFG_PRIVATE:
        nl = b"\r\n" if b"\r\n" in raw[:4096] else b"\n"
        # the key goes first so it is easy to find (after a BOM, when the file has one)
        bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
        raw = bom + b"visibility: private" + nl + raw[len(bom):]
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_bytes(raw)
        tmp.replace(path)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise HTTPException(409, detail=(
            f"the configuration file is not writable here ({exc.__class__.__name__}: {path}) - "
            "add or remove the single line `visibility: private` at the top of it on the server"))
    return vis


# ── the safety net: one dependency over EVERY route ──────────────────────────
# Routes that name a configuration in the PATH or the QUERY (``/payload/{die}/
# {cfg}``, ``?die=&config=``) are gated here once, before the handler runs, so a
# route added tomorrow cannot forget the rule.  Routes whose natural answer for a
# missing configuration is NOT a 404 (an empty 200, a different text) must give
# the same answer for a hidden one, so they gate themselves, in-route, with that
# route's own "missing" behaviour, and are listed in ``SELF_GATED_PREFIXES``.
# Body-addressed routes (``POST /duty`` ...) are gated in-route too.
SELF_GATED_PREFIXES = (
    "/api/controller",      # empty settings / point, like a missing configuration
    "/api/bearings",        # has_bearings:false, like a missing configuration
    "/api/propellers",      # its own 404 text / the cooling-options listing
    "/api/system",          # status "unknown", like a missing configuration
    "/api/model3d",         # "No 3D model for this motor."
    "/api/family/history/",  # an empty snapshot list, like a missing configuration
)
#: …and these single routes (their natural "missing" answer is a 200 with a reason).
SELF_GATED_SUFFIXES = ("/part_materials",)


def request_is_bound() -> bool:
    """Is there a request / tool caller to refuse?  (False in a CLI or a bare
    background thread - those read the catalog as the process, not as a user.)"""
    try:
        from motor_ai_sim import workspace as _WS
        if isinstance(_WS.caller(), dict):
            return True
    except Exception:                               # noqa: BLE001
        pass
    return _REQ_AUTH.get() is not _UNBOUND


def hidden_for_request(die: str, cfg: Optional[str]) -> bool:
    """True when the CURRENT request's caller must not read ``<die>/<cfg>``
    because it is a PRIVATE configuration they hold no grant for.

    The check the low-level readers (``family.config_doc``, ``duty_results.get``)
    make, so every solve / report / controller route that reaches a configuration
    by name through them is covered without each one knowing the rule.  Cheap
    path first: a public configuration costs one cached file stat.
    """
    if not cfg or not request_is_bound():
        return False
    if config_visibility(str(die), str(cfg)) != CFG_PRIVATE:
        return False
    acc = catalog_access(None)
    if acc.get("is_admin"):
        return False
    return config_grant_key(str(die), str(cfg)) not in (acc.get("configs") or frozenset())


async def config_path_gate(request: _Request) -> None:
    """FastAPI dependency (``FastAPI(dependencies=[Depends(...)])``): binds the
    request's credentials for :func:`catalog_access`, then refuses a path / query
    that names a configuration the caller may not see."""
    _REQ_AUTH.set(request.headers.get("authorization"))
    pp = request.path_params or {}
    qp = request.query_params
    if request.method not in ("GET", "HEAD") or not (
            (pp.get("die") or qp.get("die"))
            and (pp.get("cfg") or pp.get("config") or qp.get("cfg") or qp.get("config"))):
        return
    from starlette.concurrency import run_in_threadpool
    await run_in_threadpool(_config_path_check, request)


def _config_path_check(request) -> None:
    pp = request.path_params or {}
    qp = request.query_params
    die = pp.get("die") or qp.get("die")
    cfg = pp.get("cfg") or pp.get("config") or qp.get("cfg") or qp.get("config")
    if not die or not cfg:
        return
    # READS only.  A write names the configuration to CHANGE it: it is gated in
    # the route (``family._require_die_write``) AFTER the route's own auth
    # dependencies, so a refusal there reads exactly like the one a missing
    # configuration gets (403 before 404 for a non-admin).
    if request.method not in ("GET", "HEAD"):
        return
    path = request.url.path
    if path.startswith(SELF_GATED_PREFIXES) or path.endswith(SELF_GATED_SUFFIXES):
        return
    from fastapi import HTTPException
    acc = catalog_access(request.headers.get("authorization"))
    # (the die question belongs to the route: a published die is open to every registered account)
    if config_allowed(acc, str(die), str(cfg)):
        return
    if path.startswith("/api/family/config/"):
        raise HTTPException(404, detail=f"configuration '{die}/{cfg}' not found")
    raise HTTPException(404, detail=f"configuration not found: {cfg}.yaml")
