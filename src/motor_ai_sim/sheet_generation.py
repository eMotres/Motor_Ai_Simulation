"""The SHEET a result was computed on, and the refusal of a result computed on an earlier sheet of its die (owner 2026-11-23: a
die's sheet can be replaced inside the die record - one die per size and pole count - and no result of the previous sheet may then
be saved as current).

* The sheet is the 12 keys of the stamped lamination (``SHEET_KEYS``); its hash (``sheet_hash``) is over their canonical values only
  - never the die's name, never the configuration-level geometry (stack length, turns, wire), which stay the business of the
  existing build check (``routes/family.duty_geometry_diff``).  A result solved on die X with sheet hash h is therefore valid for die
  Y whenever Y's current sheet hash is h (the same sheet content).
* A die carries ``sheet_generation`` (absent = 1) and, from generation 2 on, ``sheet_hash`` - written only by the sheet
  replacement.  For generation 1 NOTHING here asks anything: every save behaves exactly as before.
* A solve issues ``result_sheet`` - {hash, geo_sig, issued_at, sig} - signed with an HMAC derived from the deployment's existing
  auth secret (``users._secret``; no new secret).  A client save echoes it; an unsigned or altered block counts as no block.  This
  protects against accidents and stale browser tabs, NOT against a hostile admin, who can sign anything the server can.
* In-process records (``duty_results.record``) run at the tail of the solve request and stamp the hash of the machine that request
  solved; ``refusals()`` lists what a request refused so its response can say "not saved"."""
from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import time
from typing import Any, Dict, List, Optional

SHEET_KEYS = ("tooth_width", "tooth2_width", "cut_width", "slot_height", "stator_inner_radius", "rotor_outer_radius",
              "rotor_inner_radius", "magnet_height", "magnet_fill_up", "magnet_up_gap", "rotor_hole", "rotor_house_height")
BLOCK = "result_sheet"
REFUSED_TEXT = ("not saved: computed on an earlier sheet of this die (its sheet was replaced) - reload the page and run the "
                "point again")
_REFUSALS: contextvars.ContextVar[Optional[List[str]]] = contextvars.ContextVar("sheet_refusals", default=None)


class EarlierSheet(ValueError):
    """A result of an earlier sheet (or one that cannot prove its sheet) for a die whose sheet was replaced."""


def canonical(geometry: Any) -> Dict[str, Optional[float]]:
    """The 12 sheet values as stored: numbers rounded to 1e-6 mm (a re-serialised float does not move them), None when absent."""
    g = geometry if isinstance(geometry, dict) else {}
    out: Dict[str, Optional[float]] = {}
    for k in SHEET_KEYS:
        v = g.get(k)
        out[k] = round(float(v), 6) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    return out


def sheet_hash(geometry: Any) -> str:
    return hashlib.sha256(json.dumps(canonical(geometry), sort_keys=True).encode()).hexdigest()[:20]


def generation(die_doc: Any) -> int:
    try:
        return max(1, int((die_doc or {}).get("sheet_generation") or 1))
    except (TypeError, ValueError, AttributeError):
        return 1


def current_hash(die_doc: Any) -> str:
    d = die_doc if isinstance(die_doc, dict) else {}
    return str(d.get("sheet_hash") or sheet_hash(d.get("geometry")))


def _key() -> bytes:
    from motor_ai_sim import users as _U
    return hmac.new(_U._secret().encode("utf-8"), b"motres-sheet-stamp-v1", hashlib.sha256).digest()


def _sign(h: str, geo_sig: str, issued_at: int) -> str:
    msg = json.dumps([h, geo_sig, issued_at], separators=(",", ":")).encode()
    return hmac.new(_key(), msg, hashlib.sha256).hexdigest()


def issue(geometry: Any, geo_sig: Any = "") -> Dict[str, Any]:
    """The signed block of a solve: the sheet hash of the geometry it solved, its configuration-level signature, when."""
    h, gs, at = sheet_hash(geometry), str(geo_sig or ""), int(time.time())
    return {"hash": h, "geo_sig": gs, "issued_at": at, "sig": _sign(h, gs, at)}


def verified_hash(block: Any) -> Optional[str]:
    """The sheet hash of a block the server issued, else None (unsigned, altered or malformed = no block)."""
    if not isinstance(block, dict):
        return None
    h, gs, at, sig = block.get("hash"), block.get("geo_sig", ""), block.get("issued_at"), block.get("sig")
    if not (isinstance(h, str) and isinstance(gs, str) and isinstance(at, int) and not isinstance(at, bool) and isinstance(sig, str)):
        return None
    try:
        good = _sign(h, gs, at)
    except Exception:                                       # noqa: BLE001 - no secret: nothing verifies (fail closed)
        return None
    return h if hmac.compare_digest(good, sig) else None


def _die_doc(die: str) -> dict:
    from motor_ai_sim.routes import family as F
    return F._load_yaml(F._die_file(die), "die")


def check_save(die: str, block: Any) -> None:
    """A client save of a result for ``die``: nothing to check at generation 1; from generation 2 the echoed block must be signed
    and name the die's current sheet.  Raises :class:`EarlierSheet` with the text the user reads."""
    d = _die_doc(die)
    if generation(d) <= 1:
        return
    h = verified_hash(block)
    if h is None or h != current_hash(d):
        raise EarlierSheet(REFUSED_TEXT)


def live_on_current(die: str) -> bool:
    """An in-process record: is the machine this request solved (the live configuration) on the die's current sheet?  Always True
    at generation 1."""
    d = _die_doc(die)
    if generation(d) <= 1:
        return True
    from motor_ai_sim.config import get_config
    return sheet_hash((get_config() or {}).get("geometry") or {}) == current_hash(d)


def row_allowed(die_doc: Any, row: Any) -> bool:
    """An imported / mirrored / restored stored row: at generation 1 every row; from generation 2 only a row stamped with the die's
    current sheet (no stamp = refused)."""
    if generation(die_doc) <= 1:
        return True
    return isinstance(row, dict) and row.get("sheet_hash") == current_hash(die_doc)


def note_refusal(text: str) -> None:
    rows = _REFUSALS.get()
    if rows is None:
        rows = []
        _REFUSALS.set(rows)
    if text not in rows:
        rows.append(text)


def refusals() -> List[str]:
    """What this request refused to save (for its response); empty when nothing was refused."""
    return list(_REFUSALS.get() or [])


def install(app) -> None:
    """The response side of ``note_refusal``: every request gets its own refusal list (the thread pool copies the context, so a
    sync route appends to this same list); when the request refused to save anything, its JSON answer gets ``not_saved`` (the
    texts) and the header ``X-Result-Not-Saved``.  A request that refused nothing is passed through untouched."""
    from starlette.responses import Response

    @app.middleware("http")
    async def _sheet_refusals(request, call_next):
        rows: List[str] = []
        token = _REFUSALS.set(rows)
        try:
            response = await call_next(request)
        finally:
            _REFUSALS.reset(token)
        if not rows:
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        headers["X-Result-Not-Saved"] = rows[0][:200]
        if "json" in str(response.headers.get("content-type", "")):
            try:
                doc = json.loads(body or b"null")
                if isinstance(doc, dict):
                    doc["not_saved"] = list(rows)
                    body = json.dumps(doc).encode()
            except ValueError:
                pass
        return Response(content=body, status_code=response.status_code, headers=headers, media_type=response.media_type)
