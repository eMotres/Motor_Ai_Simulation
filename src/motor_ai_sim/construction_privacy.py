# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""The FULL-CONSTRUCTION boundary on every output that carries bearing CAD data.

Re-audit R1 (Codex Astra, 2026-10-20): #340 added ``bearings.thermal_path`` to a
configuration — shaft segments, holder sections, contact and film areas and the
open fraction of the rotor face, all MEASURED ON THE CAD ASSEMBLY.  That is the
full construction, which only the NDA + verification gate may open
(``routes.construction.require_full_construction`` / ``model3d.can_download``),
yet the catalog tree, the editor context and the thermal payloads handed it to
any account that could see the configuration — and, with ``PUBLIC_EXHIBIT=1``,
to anonymous visitors.

This module is the ONE place that decides what of it leaves the server:

* :func:`public_bearings` — a configuration's ``bearings`` block reduced to its
  public part (the cards, seals, grease, lubrication, preload and bearing
  temperature): a WHITELIST, so any CAD key added later is withheld by default;
* :func:`may_see_full_construction` — the same gate as the full STEP, as a
  boolean (never raises);
* :func:`redact` — a payload (a tree row, a thermal map, a coupled record, a
  job result) with the CAD basis taken out: ``thermal_path`` anywhere, the
  bearing network's measured segments / areas / lengths, and the measured
  rotor-face open fraction with the air speeds that are it times the wash;
* :class:`ConstructionRedactionMiddleware` — the HTTP safety net: a JSON answer
  that carries any of it goes through :func:`redact` unless the caller passes
  the gate.  It costs a substring search on every other answer.

Derived RESULTS (conductances, resistances, temperatures, watts) stay: they are
what the thermal model computed, and an engineer is entitled to them.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, Mapping, Optional

#: What of a configuration's ``bearings`` block is public — the bearing card
#: names and how they run.  Everything else (``thermal_path`` today) is
#: construction data.
PUBLIC_BEARING_KEYS = frozenset({"A", "B", "lubrication", "preload_n",
                                 "temp_source", "temp_c"})
#: What of one end (``A`` / ``B``) is public.
PUBLIC_BEARING_END_KEYS = frozenset({"card", "seals", "grease"})

#: The CAD-measured keys this module knows by name — the MCP's geometry
#: denylist re-checks them on every agent answer.
CAD_KEYS = frozenset({"thermal_path", "shaft_segments_mm", "segments_mm",
                      "arm_segments_mm", "contact_core_mm2", "film_area_mm2",
                      "char_len_mm", "rotor_face_open_fraction",
                      "holder_open_fraction"})

WITHHELD = ("withheld: measured on the CAD assembly — the full construction "
            "needs the signed NDA and a verified account")

#: The bytes that mark a JSON answer as possibly carrying the CAD basis.  A
#: compact or an indented dump, either way.
_MARKERS = (b"thermal_path", b"rotor_face_open_fraction")
_CAD_TRUE = re.compile(rb'"cad"\s*:\s*true')

#: The rotor-channel note that prints the measured open fraction.
_FACE_NOTE = re.compile(
    r"the air reaches the rotor face at [-+0-9.eE]+ m/s \(wash [-+0-9.eE]+ m/s "
    r"× holder open fraction [-+0-9.eE]+, CAD \(bearings\.thermal_path\."
    r"rotor_face_open_fraction\)\)")
_FACE_NOTE_SUB = ("the air reaches the rotor face through the holder's "
                  "measured windows (open fraction %s)" % WITHHELD)


# ── the bearings block ──────────────────────────────────────────────────────

def public_bearings(block: Any) -> Any:
    """``block`` with only its public keys (a whitelist).  ``None`` / a
    non-dict is returned unchanged."""
    if not isinstance(block, Mapping):
        return block
    out: Dict[str, Any] = {}
    for k, v in block.items():
        if k not in PUBLIC_BEARING_KEYS:
            continue
        if k in ("A", "B") and isinstance(v, Mapping):
            v = {kk: vv for kk, vv in v.items() if kk in PUBLIC_BEARING_END_KEYS}
        out[k] = v
    return out


def bearings_for(block: Any, full_construction: bool) -> Any:
    """The block as THIS caller may see it."""
    return block if full_construction else public_bearings(block)


def _is_config_bearings(v: Any) -> bool:
    """Does ``v`` look like a configuration's ``bearings`` block (and not a
    list / result block that happens to sit under the same key)?"""
    if not isinstance(v, Mapping):
        return False
    if "thermal_path" in v:
        return True
    ends = [v.get(e) for e in ("A", "B")]
    return (any(isinstance(e, Mapping) and "card" in e for e in ends)
            and any(k in v for k in ("lubrication", "temp_source", "preload_n",
                                     "temp_c")))


# ── the gate ────────────────────────────────────────────────────────────────

def may_see_full_construction(authorization: Optional[str] = None,
                              identity: Optional[Mapping[str, Any]] = None) -> bool:
    """The gate of the full STEP / the part tree / ``cad_params``
    (``model3d.can_download``: the admin; an account with an administrator's
    NDA flag; an online NDA signer once VERIFIED), as a boolean.

    ``identity`` wins; else the ``Authorization`` header; else the identity the
    middleware resolved for this request (``workspace.caller``), which is what
    an in-process caller with no header (the MCP) is acting as.  Fails CLOSED.
    """
    try:
        from motor_ai_sim import model3d as _m3
        who: Optional[Mapping[str, Any]] = identity
        if who is None and authorization:
            from motor_ai_sim.auth import caller_identity
            who = caller_identity(authorization)
        if who is None:
            from motor_ai_sim import workspace as _ws
            who = _ws.caller()
        if who is None:
            from motor_ai_sim.auth import caller_identity
            who = caller_identity(None)
        who = dict(who or {})
        rec: Dict[str, Any] = {}
        if not who.get("is_admin") and who.get("id"):
            from motor_ai_sim import users as _U
            rec = _U.get_user(who["id"]) or {}
        return bool(_m3.can_download(who, rec))
    except Exception:  # noqa: BLE001 — never open the gate on an error
        return False


# ── a payload ───────────────────────────────────────────────────────────────

def _redact_bearing_node(bn: Dict[str, Any]) -> Dict[str, Any]:
    """The bearing network's report without the measured geometry."""
    out = dict(bn)
    ends = []
    for e in bn.get("ends") or []:
        if not isinstance(e, Mapping):
            ends.append(e)
            continue
        e = dict(e)
        rows = []
        for r in e.get("resistances") or []:
            if not isinstance(r, Mapping):
                rows.append(r)
                continue
            r = {k: v for k, v in r.items() if k != "segments_mm"}
            path = str(r.get("path") or "")
            if path.startswith("shaft") or path.startswith("holder"):
                r["source"] = WITHHELD
            rows.append(r)
        if "resistances" in e:
            e["resistances"] = rows
        if isinstance(e.get("film"), Mapping):
            f = e["film"]
            e["film"] = {k: f[k] for k in ("mode", "regime", "G_W_per_K",
                                           "air_speed_mps") if k in f}
            e["film"]["source"] = WITHHELD
        ends.append(e)
    if "ends" in bn:
        out["ends"] = ends
    out["construction"] = WITHHELD
    return out


def _redact_rotor_channels(rc: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(rc)
    out["holder_open_fraction"] = None
    out["holder_open_fraction_source"] = "CAD (%s)" % WITHHELD
    for k in ("face_speed_mps",):
        if k in out:
            out[k] = None
    chans = []
    for c in rc.get("channels") or []:
        if isinstance(c, Mapping):
            c = {k: (None if k in ("v_mps", "re", "m_dot_g_s") else v)
                 for k, v in c.items()}
        chans.append(c)
    if "channels" in rc:
        out["channels"] = chans
    return out


def _redact_end_faces(ef: Any) -> Any:
    """The rotor-face films in the wash run at the wash × the MEASURED open
    fraction: their air speed (and the Reynolds numbers of it) is the
    fraction, so it goes too."""
    def _one(blk: Any) -> Any:
        if not isinstance(blk, Mapping):
            return blk
        if str(blk.get("film_kind") or "") != "forced":
            return blk
        return {k: (None if k in ("air_speed_mps", "re", "re_plate") else v)
                for k, v in blk.items()}
    if isinstance(ef, Mapping):
        return {k: _one(v) for k, v in ef.items()}
    if isinstance(ef, list):
        return [_one(v) for v in ef]
    return ef


def _redact_cooling(c: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(c)
    bn = out.get("bearing_node")
    if isinstance(bn, Mapping) and bn.get("cad"):
        out["bearing_node"] = _redact_bearing_node(dict(bn))
    rc = out.get("rotor_channels")
    face_cad = str(out.get("holder_open_fraction_source") or "").startswith("CAD")
    if isinstance(rc, Mapping) and str(
            rc.get("holder_open_fraction_source") or "").startswith("CAD"):
        out["rotor_channels"] = _redact_rotor_channels(dict(rc))
        face_cad = True
    if face_cad:
        out["holder_open_fraction_source"] = "CAD (%s)" % WITHHELD
        if "end_faces" in out:
            out["end_faces"] = _redact_end_faces(out["end_faces"])
    return out


def redact(obj: Any) -> Any:
    """``obj`` without the CAD basis (a new object; ``obj`` is not touched)."""
    if isinstance(obj, Mapping):
        d: Dict[str, Any] = {}
        for k, v in obj.items():
            if k == "thermal_path":
                continue
            if k == "bearings" and _is_config_bearings(v):
                d[k] = public_bearings(v)
                continue
            d[k] = redact(v)
        if isinstance(d.get("bearing_node"), Mapping) or isinstance(
                d.get("rotor_channels"), Mapping) or "holder_open_fraction_source" in d:
            d = _redact_cooling(d)
        return d
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(redact(v) for v in obj)
    if isinstance(obj, str):
        if "holder open fraction" in obj:
            return _FACE_NOTE.sub(_FACE_NOTE_SUB, obj)
        return obj
    return obj


def needs_redaction(body: bytes) -> bool:
    """Might this JSON body carry the CAD basis?  A substring search."""
    if not body:
        return False
    return any(m in body for m in _MARKERS) or bool(_CAD_TRUE.search(body))


def redact_for(obj: Any, *, authorization: Optional[str] = None,
               identity: Optional[Mapping[str, Any]] = None) -> Any:
    """``obj`` as this caller may see it."""
    if may_see_full_construction(authorization, identity):
        return obj
    return redact(obj)


# ── the HTTP safety net ─────────────────────────────────────────────────────

class ConstructionRedactionMiddleware:
    """Pure ASGI: every JSON answer under ``/api/`` that carries the CAD basis
    is redacted unless the caller passes the full-construction gate.

    Only ``application/json`` bodies are buffered (they are built whole in
    memory anyway); a stream, a file or an encoded body passes untouched.  The
    gate is evaluated only when the body carries a marker, so an ordinary
    answer pays one substring search.
    """

    def __init__(self, app, prefixes: Iterable[str] = ("/api/",)):
        self.app = app
        self.prefixes = tuple(prefixes)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not str(scope.get("path") or "").startswith(self.prefixes):
            await self.app(scope, receive, send)
            return
        auth_hdr: Optional[str] = None
        for k, v in scope.get("headers") or ():
            if k == b"authorization":
                auth_hdr = v.decode("latin-1")
                break
        state: Dict[str, Any] = {"start": None, "buffer": [], "json": False}

        async def _send(message):
            t = message.get("type")
            if t == "http.response.start":
                headers = list(message.get("headers") or ())
                ctype = b""
                enc = False
                for k, v in headers:
                    lk = k.lower()
                    if lk == b"content-type":
                        ctype = v.lower()
                    elif lk == b"content-encoding":
                        enc = True
                state["json"] = ctype.startswith(b"application/json") and not enc
                if not state["json"]:
                    await send(message)
                    return
                state["start"] = message
                return
            if t == "http.response.body" and state["json"]:
                state["buffer"].append(message.get("body") or b"")
                if message.get("more_body"):
                    return
                body = b"".join(state["buffer"])
                start = state["start"]
                if needs_redaction(body) and not may_see_full_construction(auth_hdr):
                    try:
                        body = json.dumps(redact(json.loads(body)),
                                          ensure_ascii=False,
                                          separators=(",", ":")).encode("utf-8")
                    except Exception:  # noqa: BLE001 — fail CLOSED
                        body = json.dumps({"detail": {
                            "code": "nda_required",
                            "message": "This answer carries construction data; "
                                       "it needs the signed NDA and a verified "
                                       "account."}}).encode("utf-8")
                        start = dict(start, status=403)
                    start = dict(start)
                    start["headers"] = [(k, v) for k, v in start.get("headers") or ()
                                        if k.lower() != b"content-length"] + [
                        (b"content-length", str(len(body)).encode("latin-1"))]
                await send(start)
                await send({"type": "http.response.body", "body": body,
                            "more_body": False})
                return
            await send(message)

        await self.app(scope, receive, _send)


def install(app) -> None:
    app.add_middleware(ConstructionRedactionMiddleware)
