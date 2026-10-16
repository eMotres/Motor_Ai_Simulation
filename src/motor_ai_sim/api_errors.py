"""Stable, translatable codes for user-facing API errors (docs/I18N.md).

The web UI is localised (EN source, zh-CN mirror).  It cannot translate a free
English sentence, so every error response ALSO carries a machine-readable
``code`` plus ``params``; the web looks the code up in its ``errors`` namespace
and falls back to the English ``detail`` when it does not know it.

Backward compatible by construction: ``detail`` is never changed, the two
fields are ADDED beside it.  Existing clients (and the MCP/agent surface, which
stays English) keep reading ``detail`` exactly as before.

Two ways a response gets its code:

* new code raises :class:`ApiError` (an ``HTTPException``) with an explicit
  code and params;
* the ~500 existing ``HTTPException(..., detail="...")`` sites are NOT edited
  (several open PRs touch those files).  :func:`classify` maps their English
  text to a code through :data:`_PATTERNS`; anything unmatched gets the generic
  ``http.<status>`` code, which the web renders as "the server said: <detail>".
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException
from fastapi.responses import JSONResponse


class ApiError(HTTPException):
    """An HTTPException with a stable ``code`` and ``params`` for translation."""

    def __init__(self, status_code: int, code: str, message: str,
                 params: Optional[Dict[str, Any]] = None,
                 headers: Optional[Dict[str, str]] = None) -> None:
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.code = code
        self.params = dict(params or {})


#: English message (regex, anchored at the start) -> code.  Named groups become
#: params.  Keep the codes stable: the web's locales/*/errors.json keys on them.
_PATTERNS: Tuple[Tuple[re.Pattern, str], ...] = tuple(
    (re.compile(p, re.IGNORECASE), c) for p, c in (
        (r"sign in first", "auth.sign_in_required"),
        (r"this account is disabled", "auth.account_disabled"),
        (r"too many requests", "rate_limited"),
        (r"unknown material: (?P<name>.+)", "material.unknown"),
        (r"enter a valid e-mail address", "form.invalid_email"),
        (r"enter your name", "form.name_required"),
        (r"give the duty a name", "duty.name_required"),
        (r"give the copy a name", "copy.name_required"),
        (r"'die' is reserved", "name.die_reserved"),
        (r"current_arms and rpm must be positive", "op.current_rpm_positive"),
        (r"machine has no poles configured", "machine.no_poles"),
        (r"live config has no geometry block", "config.no_geometry"),
        (r"empty upload", "upload.empty"),
        (r"no such campaign", "not_found.campaign"),
        (r"no such notice", "not_found.notice"),
        (r"(e-mail|smtp) is not configured", "email.not_configured"),
        (r"(role|mode|duty mode) must be 'motor' or 'generator'", "duty.mode_invalid"),
    ))


def classify(status_code: int, detail: Any) -> Tuple[str, Dict[str, Any]]:
    """The (code, params) for one error detail.  Pure; unit-tested."""
    if isinstance(detail, str):
        for rx, code in _PATTERNS:
            m = rx.match(detail.strip())
            if m:
                return code, {k: v for k, v in m.groupdict().items() if v is not None}
    return f"http.{int(status_code)}", {}


def error_body(status_code: int, detail: Any, code: Optional[str] = None,
               params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """``{"detail": <unchanged>, "code": ..., "params": {...}}``.

    A dict ``detail`` that already names its own ``code`` is left alone."""
    if isinstance(detail, dict) and "code" in detail:
        return {"detail": detail}
    if code is None:
        code, params = classify(status_code, detail)
    return {"detail": detail, "code": code, "params": params or {}}


def install(app) -> None:
    """Register the handler that adds ``code``/``params`` to HTTPExceptions."""
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(StarletteHTTPException)
    async def _coded_http_exception(request, exc: StarletteHTTPException):
        code = getattr(exc, "code", None)
        params = getattr(exc, "params", None)
        return JSONResponse(status_code=exc.status_code,
                            content=error_body(exc.status_code, exc.detail, code, params),
                            headers=getattr(exc, "headers", None))
