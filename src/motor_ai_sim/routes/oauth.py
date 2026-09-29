"""OAuth 2.1 for the MCP server (Stage 2) — HTTP surface.  motor_ai_sim.oauth.

Public (no session; outside /api so the tier gate lets them through):
  GET  /.well-known/oauth-protected-resource[/mcp]   RFC 9728
  GET  /.well-known/oauth-authorization-server[/…]   RFC 8414
  POST /oauth/register                               RFC 7591
  GET  /oauth/authorize        -> 302 to the web consent page /agent-consent
  POST /oauth/token            authorization_code (+PKCE) | refresh_token
  POST /oauth/revoke           RFC 7009

Signed-in owner (session token):
  GET    /api/oauth/requests/{id}            what the consent page shows
  POST   /api/oauth/requests/{id}  {approve} -> {redirect}
  GET    /api/oauth/grants                   connected apps
  DELETE /api/oauth/grants/{id}              disconnect (revoke)
"""
from __future__ import annotations

from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from motor_ai_sim import oauth as _o
from motor_ai_sim.routes.agent_keys import _owner

router = APIRouter(tags=["oauth"])

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _err(e: _o.OAuthError) -> JSONResponse:
    h = dict(_NO_STORE)
    if e.status == 401:
        h["WWW-Authenticate"] = 'Basic realm="emotres-oauth"'
    return JSONResponse(e.body(), status_code=e.status, headers=h)


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource():
    return JSONResponse(_o.protected_resource_metadata())


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/oauth-authorization-server/{rest:path}")
@router.get("/.well-known/openid-configuration")
def authorization_server(rest: str = ""):
    return JSONResponse(_o.authorization_server_metadata())


@router.post("/oauth/register")
async def register(request: Request):
    try:
        meta = await request.json()
    except Exception:                                   # noqa: BLE001
        return _err(_o.OAuthError("invalid_client_metadata", "JSON body expected"))
    try:
        return JSONResponse(_o.register_client(meta), status_code=201, headers=_NO_STORE)
    except _o.OAuthError as e:
        return _err(e)


@router.get("/oauth/authorize")
def authorize(request: Request):
    q = dict(request.query_params)
    try:
        rid = _o.start_authorization(q)
    except _o.AuthorizeRedirectError as e:
        return RedirectResponse(e.location(), status_code=302)
    except _o.OAuthError as e:
        # bad client / redirect URI: never redirect to an unverified URI
        return _err(e)
    return RedirectResponse(f"{_o.CONSENT_PATH}?request={quote(rid)}", status_code=302)


async def _form(request: Request) -> dict:
    try:
        return {k: str(v) for k, v in (await request.form()).items()}
    except Exception:                                   # noqa: BLE001
        return {}


@router.post("/oauth/token")
async def token_endpoint(request: Request):
    form = await _form(request)
    try:
        return JSONResponse(_o.token(form, request.headers.get("authorization")),
                            headers=_NO_STORE)
    except _o.OAuthError as e:
        return _err(e)


@router.post("/oauth/revoke")
async def revoke_endpoint(request: Request):
    form = await _form(request)
    try:
        _o.revoke_token(form, request.headers.get("authorization"))
    except _o.OAuthError as e:
        return _err(e)
    return JSONResponse({}, headers=_NO_STORE)


# ── owner side ───────────────────────────────────────────────────────────────

@router.get("/api/oauth/requests/{rid}")
def consent_info(rid: str, authorization: Optional[str] = Header(default=None)):
    owner = _owner(authorization)
    info = _o.describe_request(rid)
    if not info:
        raise HTTPException(404, detail="This authorization request expired. Start again from the app.")
    try:
        # a request resumed by a sign-up is for the account that signed up only
        _o.check_request_account(rid, owner)
    except _o.OAuthError as e:
        raise HTTPException(e.status, detail=e.description)
    from motor_ai_sim import agent_keys as _keys
    return info | {"account": owner,
                   "scope_descriptions": dict(_keys.SCOPE_DESCRIPTIONS)}


class Decision(BaseModel):
    approve: bool
    #: Stage 3: the scopes the owner left ticked (a subset of the request).
    scopes: Optional[List[str]] = None


@router.post("/api/oauth/requests/{rid}")
def consent_decide(rid: str, req: Decision, authorization: Optional[str] = Header(default=None)):
    owner = _owner(authorization)
    try:
        return {"redirect": _o.decide(rid, owner, req.approve, req.scopes)}
    except _o.OAuthError as e:
        raise HTTPException(e.status if e.status >= 400 else 400, detail=e.description)


@router.get("/api/oauth/grants")
def my_grants(authorization: Optional[str] = Header(default=None)):
    return {"grants": _o.list_grants(_owner(authorization))}


@router.delete("/api/oauth/grants/{gid}")
def revoke_my_grant(gid: str, authorization: Optional[str] = Header(default=None)):
    if not _o.revoke_grant(_owner(authorization), gid):
        raise HTTPException(404, detail="no such active connection")
    return {"revoked": gid}
