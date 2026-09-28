"""OAuth 2.1 authorization server for the MCP endpoint (Stage 2, 2026-09-28).

Follows the MCP authorization spec so ChatGPT connectors and claude.ai custom
connectors can connect to ``/mcp`` without a hand-copied key:

* RFC 9728 protected-resource metadata  -> ``/.well-known/oauth-protected-resource``
* RFC 8414 authorization-server metadata -> ``/.well-known/oauth-authorization-server``
* RFC 7591 dynamic client registration  -> ``POST /oauth/register``
* authorization code + PKCE (S256 only) -> ``GET /oauth/authorize`` -> web consent
  page ``/agent-consent`` -> ``POST /oauth/token``
* short-lived access tokens (``emo_``) + rotating refresh tokens (``emr_``),
  only SHA-256 hashes stored; a replayed (already rotated) refresh token revokes
  the whole grant (RFC 9700 refresh-token reuse detection).

Every access token resolves to the same ``agent_keys.Principal`` as a key
(``kind="oauth"``, ``credential_id`` = grant id), so scopes, quotas and audit in
``mcp_app.McpGate`` apply unchanged.  Storage: ``oauth_grants.json`` next to
``agent_keys.json`` (``json_store.mutate_json``: lock + atomic replace).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlsplit

from motor_ai_sim import agent_keys as _keys
from motor_ai_sim.json_store import mutate_json, read_json

ACCESS_PREFIX = "emo_"
REFRESH_PREFIX = "emr_"
ACCESS_TTL_S = 3600               # 1 h
REFRESH_TTL_S = 30 * 86400        # 30 days, rotated on every use
CODE_TTL_S = 300                  # authorization code: 5 min, single use
REQUEST_TTL_S = 900               # pending consent: 15 min
MAX_CLIENTS = 500                 # DCR is anonymous: cap the table
CONSENT_PATH = "/agent-consent"


# ── URLs ──────────────────────────────────────────────────────────────────────

def base_url() -> str:
    """Public origin of the app (issuer).  Env ``PUBLIC_BASE_URL``."""
    return (os.environ.get("PUBLIC_BASE_URL", "").strip()
            or "https://aerostator.com").rstrip("/")


def resource_url() -> str:
    return base_url() + "/mcp"


def resource_metadata_url() -> str:
    return base_url() + "/.well-known/oauth-protected-resource/mcp"


def www_authenticate(error: str = "") -> str:
    extra = f', error="{error}"' if error else ""
    return (f'Bearer realm="emotres-mcp", resource_metadata="{resource_metadata_url()}"'
            f'{extra}')


def protected_resource_metadata() -> Dict[str, Any]:
    return {"resource": resource_url(),
            "authorization_servers": [base_url()],
            "scopes_supported": list(_keys.SCOPES),
            "bearer_methods_supported": ["header"],
            "resource_name": "eMotres motor catalog (MCP)",
            "resource_documentation": base_url() + "/"}


def authorization_server_metadata() -> Dict[str, Any]:
    b = base_url()
    return {"issuer": b,
            "authorization_endpoint": b + "/oauth/authorize",
            "token_endpoint": b + "/oauth/token",
            "registration_endpoint": b + "/oauth/register",
            "revocation_endpoint": b + "/oauth/revoke",
            "scopes_supported": list(_keys.SCOPES),
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": [
                "none", "client_secret_post", "client_secret_basic"],
            "revocation_endpoint_auth_methods_supported": [
                "none", "client_secret_post", "client_secret_basic"]}


# ── store ─────────────────────────────────────────────────────────────────────

def store_path():
    return _keys._config_dir() / "oauth_grants.json"


def _now() -> float:
    return time.time()


def _h(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _load() -> Dict[str, Any]:
    d = read_json(store_path(), default=None)
    return d if isinstance(d, dict) else {}


def _mutate(fn):
    def _m(d):
        d = d if isinstance(d, dict) else {}
        for k in ("clients", "requests", "codes", "grants"):
            d.setdefault(k, {})
        d.setdefault("version", 1)
        fn(d)
        _prune(d)
        return d
    mutate_json(store_path(), _m, default={})


def _prune(d: Dict[str, Any]) -> None:
    now = _now()
    for tbl in ("requests", "codes"):
        for k in [k for k, r in d[tbl].items() if (r.get("expires_at") or 0) < now]:
            del d[tbl][k]


class OAuthError(Exception):
    """An RFC 6749 error: ``error`` code, description, HTTP status."""

    def __init__(self, error: str, description: str = "", status: int = 400):
        super().__init__(description or error)
        self.error, self.description, self.status = error, description, status

    def body(self) -> Dict[str, str]:
        b = {"error": self.error}
        if self.description:
            b["error_description"] = self.description
        return b


# ── redirect URIs ─────────────────────────────────────────────────────────────

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


def redirect_uri_ok(uri: str) -> bool:
    """https only; http only for localhost (dev); no fragment; absolute."""
    if not isinstance(uri, str) or not uri or len(uri) > 2000:
        return False
    try:
        u = urlsplit(uri)
    except ValueError:
        return False
    if u.fragment or not u.netloc or not u.hostname:
        return False
    if u.scheme == "https":
        return True
    return u.scheme == "http" and u.hostname.lower() in _LOCAL_HOSTS


# ── dynamic client registration (RFC 7591) ───────────────────────────────────

def register_client(meta: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(meta, dict):
        raise OAuthError("invalid_client_metadata", "JSON object expected")
    uris = meta.get("redirect_uris")
    if not isinstance(uris, list) or not uris:
        raise OAuthError("invalid_redirect_uri", "redirect_uris is required")
    for u in uris:
        if not redirect_uri_ok(u):
            raise OAuthError("invalid_redirect_uri",
                             f"redirect URI must be https (http only for localhost): {u!r}")
    method = str(meta.get("token_endpoint_auth_method") or "none")
    if method not in ("none", "client_secret_post", "client_secret_basic"):
        raise OAuthError("invalid_client_metadata",
                         f"unsupported token_endpoint_auth_method {method!r}")
    grants = meta.get("grant_types") or ["authorization_code", "refresh_token"]
    if not isinstance(grants, list) or any(
            g not in ("authorization_code", "refresh_token") for g in grants):
        raise OAuthError("invalid_client_metadata", "unsupported grant_types")
    rtypes = meta.get("response_types") or ["code"]
    if rtypes != ["code"]:
        raise OAuthError("invalid_client_metadata", "response_types must be ['code']")
    scope = str(meta.get("scope") or " ".join(_keys.SCOPES))
    bad = [s for s in scope.split() if s not in _keys.SCOPES]
    if bad:
        raise OAuthError("invalid_client_metadata", f"unknown scope(s): {bad}")
    name = str(meta.get("client_name") or "").strip()[:80] or "Unnamed agent"
    cid = "emc_" + secrets.token_urlsafe(16)
    secret = secrets.token_urlsafe(32) if method != "none" else None
    now = _now()
    rec = {"client_id": cid, "client_name": name, "redirect_uris": list(uris),
           "token_endpoint_auth_method": method, "grant_types": grants,
           "response_types": ["code"], "scope": scope,
           "client_uri": str(meta.get("client_uri") or "")[:300] or None,
           "secret_hash": _h(secret) if secret else None,
           "client_id_issued_at": int(now)}

    def _fn(d):
        if len(d["clients"]) >= MAX_CLIENTS:
            # drop the oldest client that holds no live grant
            live = {g.get("client_id") for g in d["grants"].values()
                    if not g.get("revoked_at")}
            idle = sorted((c for c in d["clients"].values()
                           if c["client_id"] not in live),
                          key=lambda c: c.get("client_id_issued_at") or 0)
            if not idle:
                raise OAuthError("temporarily_unavailable", "client table full", 503)
            del d["clients"][idle[0]["client_id"]]
        d["clients"][cid] = rec
    _mutate(_fn)
    out = {k: v for k, v in rec.items() if k not in ("secret_hash",) and v is not None}
    if secret:
        out["client_secret"] = secret
        out["client_secret_expires_at"] = 0
    return out


def get_client(cid: str) -> Optional[Dict[str, Any]]:
    return (_load().get("clients") or {}).get(cid or "")


def _authenticate_client(cid: str, secret: Optional[str]) -> Dict[str, Any]:
    c = get_client(cid)
    if not c:
        raise OAuthError("invalid_client", "unknown client", 401)
    if c.get("token_endpoint_auth_method", "none") != "none":
        if not secret or not hmac.compare_digest(str(c.get("secret_hash") or ""), _h(secret)):
            raise OAuthError("invalid_client", "client authentication failed", 401)
    return c


def parse_basic(authorization: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not authorization or not authorization.lower().startswith("basic "):
        return None, None
    try:
        raw = base64.b64decode(authorization[6:].strip()).decode("utf-8")
    except Exception:                                   # noqa: BLE001
        return None, None
    from urllib.parse import unquote
    cid, _, sec = raw.partition(":")
    return unquote(cid), unquote(sec)


# ── authorize ─────────────────────────────────────────────────────────────────

def _check_resource(resource: Optional[str]) -> None:
    if not resource:
        return
    ok = {resource_url(), base_url(), base_url() + "/"}
    if resource.rstrip("/") not in {x.rstrip("/") for x in ok}:
        raise OAuthError("invalid_target", f"unknown resource {resource!r}")


def start_authorization(q: Dict[str, str]) -> str:
    """Validate an /oauth/authorize request; return the pending request id.

    Raises ``OAuthError`` with ``redirect=False`` semantics for a bad client or
    redirect URI (must NOT redirect); ``AuthorizeRedirectError`` for errors that
    go back to the client's redirect URI."""
    cid = q.get("client_id") or ""
    c = get_client(cid)
    if not c:
        raise OAuthError("invalid_client", "unknown client_id")
    ruri = q.get("redirect_uri") or ""
    if not ruri and len(c["redirect_uris"]) == 1:
        ruri = c["redirect_uris"][0]
    if ruri not in c["redirect_uris"]:                   # exact string match
        raise OAuthError("invalid_request", "redirect_uri does not match the registration")
    state = q.get("state")

    def back(err, desc):
        return AuthorizeRedirectError(ruri, state, err, desc)
    if q.get("response_type") != "code":
        raise back("unsupported_response_type", "response_type must be code")
    chal = q.get("code_challenge") or ""
    if not chal:
        raise back("invalid_request", "PKCE code_challenge is required")
    if (q.get("code_challenge_method") or "") != "S256":
        raise back("invalid_request", "code_challenge_method must be S256")
    if not (43 <= len(chal) <= 128):
        raise back("invalid_request", "malformed code_challenge")
    scope = (q.get("scope") or c.get("scope") or " ".join(_keys.SCOPES)).split()
    scope = list(dict.fromkeys(scope))
    if not scope or any(s not in _keys.SCOPES for s in scope):
        raise back("invalid_scope", f"allowed scopes: {' '.join(_keys.SCOPES)}")
    try:
        _check_resource(q.get("resource"))
    except OAuthError as e:
        raise back(e.error, e.description)
    rid = secrets.token_urlsafe(24)
    rec = {"id": rid, "client_id": cid, "redirect_uri": ruri, "state": state,
           "scopes": scope, "code_challenge": chal,
           "resource": q.get("resource") or resource_url(),
           "created_at": _now(), "expires_at": _now() + REQUEST_TTL_S}
    _mutate(lambda d: d["requests"].__setitem__(rid, rec))
    return rid


class AuthorizeRedirectError(OAuthError):
    def __init__(self, redirect_uri: str, state: Optional[str], error: str, desc: str):
        super().__init__(error, desc, 302)
        self.redirect_uri, self.state = redirect_uri, state

    def location(self) -> str:
        p = {"error": self.error, "error_description": self.description}
        if self.state is not None:
            p["state"] = self.state
        return _append_query(self.redirect_uri, p)


def _append_query(uri: str, params: Dict[str, str]) -> str:
    return uri + ("&" if "?" in uri else "?") + urlencode(params)


def describe_request(rid: str) -> Optional[Dict[str, Any]]:
    d = _load()
    r = (d.get("requests") or {}).get(rid or "")
    if not r or (r.get("expires_at") or 0) < _now():
        return None
    c = (d.get("clients") or {}).get(r["client_id"]) or {}
    return {"id": rid, "client_name": c.get("client_name") or "Unnamed agent",
            "client_uri": c.get("client_uri"),
            "redirect_host": urlsplit(r["redirect_uri"]).netloc,
            "scopes": r["scopes"], "resource": r["resource"]}


def decide(rid: str, owner: str, approve: bool) -> str:
    """The signed-in owner's answer.  Returns the redirect URL for the browser."""
    owner = (owner or "").strip().lower()
    out: Dict[str, Any] = {}

    def _fn(d):
        r = d["requests"].pop(rid or "", None)
        if not r or (r.get("expires_at") or 0) < _now():
            raise OAuthError("invalid_request", "this authorization request expired", 404)
        out["r"] = r
        if not approve:
            return
        code = secrets.token_urlsafe(32)
        out["code"] = code
        d["codes"][_h(code)] = {"client_id": r["client_id"], "owner": owner,
                                "redirect_uri": r["redirect_uri"],
                                "scopes": r["scopes"], "resource": r["resource"],
                                "code_challenge": r["code_challenge"],
                                "expires_at": _now() + CODE_TTL_S}
    _mutate(_fn)
    r = out["r"]
    p: Dict[str, str] = ({"code": out["code"]} if approve else
                         {"error": "access_denied",
                          "error_description": "the user denied access"})
    if r.get("state") is not None:
        p["state"] = r["state"]
    p["iss"] = base_url()
    return _append_query(r["redirect_uri"], p)


# ── token endpoint ────────────────────────────────────────────────────────────

def _pkce_ok(verifier: str, challenge: str) -> bool:
    if not verifier or not (43 <= len(verifier) <= 128):
        return False
    dig = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii", "replace")).digest())
    return hmac.compare_digest(dig.rstrip(b"=").decode(), challenge)


def _mint(g: Dict[str, Any]) -> Dict[str, Any]:
    at = f"{ACCESS_PREFIX}{g['id']}_{secrets.token_urlsafe(32)}"
    rt = f"{REFRESH_PREFIX}{g['id']}_{secrets.token_urlsafe(32)}"
    now = _now()
    old = g.get("refresh_hash")
    if old:
        g.setdefault("rotated", []).append(old)
        g["rotated"] = g["rotated"][-50:]
    g["access_hash"], g["access_exp"] = _h(at), now + ACCESS_TTL_S
    g["refresh_hash"], g["refresh_exp"] = _h(rt), now + REFRESH_TTL_S
    return {"access_token": at, "token_type": "Bearer", "expires_in": ACCESS_TTL_S,
            "refresh_token": rt, "scope": " ".join(g["scopes"])}


def token(form: Dict[str, str], authorization: Optional[str] = None) -> Dict[str, Any]:
    cid, sec = parse_basic(authorization)
    cid = cid or form.get("client_id") or ""
    sec = sec or form.get("client_secret")
    client = _authenticate_client(cid, sec)
    gt = form.get("grant_type")
    if gt not in (client.get("grant_types") or []):
        raise OAuthError("unsupported_grant_type", f"grant_type {gt!r} not allowed")
    if gt == "authorization_code":
        return _exchange_code(client, form)
    return _refresh(client, form)


def _exchange_code(client, form) -> Dict[str, Any]:
    code = form.get("code") or ""
    out: Dict[str, Any] = {}

    def _check(c) -> Optional[OAuthError]:
        if not c or (c.get("expires_at") or 0) < _now():
            return OAuthError("invalid_grant", "invalid or expired code")
        if c["client_id"] != client["client_id"]:
            return OAuthError("invalid_grant", "code was issued to another client")
        if (form.get("redirect_uri") or c["redirect_uri"]) != c["redirect_uri"]:
            return OAuthError("invalid_grant", "redirect_uri mismatch")
        if not _pkce_ok(form.get("code_verifier") or "", c["code_challenge"]):
            return OAuthError("invalid_grant", "PKCE verification failed")
        try:
            _check_resource(form.get("resource"))
        except OAuthError as e:
            return e
        return None

    def _fn(d):
        # single use: the code is burnt by ANY attempt, failed ones included
        # (the error is returned, not raised, so the removal is written)
        c = d["codes"].pop(_h(code), None)
        e = _check(c)
        if e:
            out["err"] = e
            return
        gid = secrets.token_hex(6)
        g = {"id": gid, "owner": c["owner"], "client_id": client["client_id"],
             "client_name": client.get("client_name"), "scopes": c["scopes"],
             "created_at": _now(), "last_used_at": None, "revoked_at": None}
        out["tok"] = _mint(g)
        d["grants"][gid] = g
    _mutate(_fn)
    if "err" in out:
        raise out["err"]
    return out["tok"]


def _split(tok: str, prefix: str) -> Tuple[str, str]:
    if not isinstance(tok, str) or not tok.startswith(prefix):
        return "", ""
    gid, sep, rest = tok[len(prefix):].partition("_")
    return (gid, tok) if sep and gid and rest else ("", "")


def _refresh(client, form) -> Dict[str, Any]:
    gid, rt = _split(form.get("refresh_token") or "", REFRESH_PREFIX)
    out: Dict[str, Any] = {}
    err: Dict[str, OAuthError] = {}

    def _fn(d):
        g = d["grants"].get(gid)
        if not g or g.get("client_id") != client["client_id"]:
            err["e"] = OAuthError("invalid_grant", "unknown refresh token")
            return
        if g.get("revoked_at"):
            err["e"] = OAuthError("invalid_grant", "grant revoked")
            return
        h = _h(rt)
        if h in (g.get("rotated") or []):
            # a rotated token came back: somebody holds a copy -> kill the grant
            g["revoked_at"] = _now()
            g["revoked_reason"] = "refresh_token_reuse"
            err["e"] = OAuthError("invalid_grant", "refresh token reuse; grant revoked")
            return
        if not hmac.compare_digest(str(g.get("refresh_hash") or ""), h) \
                or (g.get("refresh_exp") or 0) < _now():
            err["e"] = OAuthError("invalid_grant", "invalid or expired refresh token")
            return
        want = (form.get("scope") or "").split()
        if want:
            if any(s not in g["scopes"] for s in want):
                err["e"] = OAuthError("invalid_scope", "cannot widen scopes on refresh")
                return
            g["scopes"] = list(dict.fromkeys(want))
        if _keys._account_disabled(g["owner"]):
            err["e"] = OAuthError("invalid_grant", "account disabled")
            return
        out["tok"] = _mint(g)
    _mutate(_fn)
    if "e" in err:
        raise err["e"]
    return out["tok"]


def revoke_token(form: Dict[str, str], authorization: Optional[str] = None) -> None:
    """RFC 7009: revoking either token of a grant revokes the grant."""
    cid, sec = parse_basic(authorization)
    client = _authenticate_client(cid or form.get("client_id") or "",
                                  sec or form.get("client_secret"))
    tok = form.get("token") or ""
    gid = _split(tok, ACCESS_PREFIX)[0] or _split(tok, REFRESH_PREFIX)[0]

    def _fn(d):
        g = d["grants"].get(gid)
        if g and g.get("client_id") == client["client_id"] and not g.get("revoked_at") \
                and _h(tok) in (g.get("access_hash"), g.get("refresh_hash")):
            g["revoked_at"] = _now()
    _mutate(_fn)


# ── resource-server side ─────────────────────────────────────────────────────

_touched: Dict[str, float] = {}


def verify_access(token_str: str) -> Tuple[Optional[_keys.Principal], str]:
    gid, tok = _split(token_str, ACCESS_PREFIX)
    if not gid:
        return None, "malformed"
    g = (_load().get("grants") or {}).get(gid)
    if not g or not hmac.compare_digest(str(g.get("access_hash") or ""), _h(tok)):
        return None, "invalid_token"
    if g.get("revoked_at"):
        return None, "revoked"
    if (g.get("access_exp") or 0) < _now():
        return None, "expired"
    owner = str(g.get("owner") or "")
    if _keys._account_disabled(owner):
        return None, "disabled"
    now = _now()
    if now - _touched.get(gid, 0.0) >= 60.0:
        _touched[gid] = now

        def _fn(d):
            if gid in d["grants"]:
                d["grants"][gid]["last_used_at"] = now
        try:
            _mutate(_fn)
        except Exception:                               # noqa: BLE001
            pass
    return _keys.Principal(email=owner, credential_id=gid, kind="oauth",
                           scopes=tuple(g.get("scopes") or ())), "ok"


# ── owner's view ("Access for agents") ───────────────────────────────────────

def list_grants(owner: str) -> List[Dict[str, Any]]:
    owner = (owner or "").strip().lower()
    out = []
    for g in (_load().get("grants") or {}).values():
        if g.get("owner") != owner:
            continue
        out.append({k: g.get(k) for k in ("id", "client_id", "client_name", "scopes",
                                          "created_at", "last_used_at", "revoked_at")}
                   | {"active": not g.get("revoked_at")
                      and (g.get("refresh_exp") or 0) > _now()})
    return sorted(out, key=lambda r: -(r.get("created_at") or 0))


def revoke_grant(owner: str, gid: str) -> bool:
    owner = (owner or "").strip().lower()
    hit = {"ok": False}

    def _fn(d):
        g = d["grants"].get(gid)
        if g and g.get("owner") == owner and not g.get("revoked_at"):
            g["revoked_at"] = _now()
            hit["ok"] = True
    _mutate(_fn)
    return hit["ok"]
