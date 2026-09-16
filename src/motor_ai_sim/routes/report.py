"""``GET /api/family/report/{die}/{cfg}`` — the full motor report, Word or PDF.

A sibling of ``routes.family``'s ``/datasheet/{die}/{cfg}`` and gated exactly
like it (``_require_die_access``: 404, not 403, for a die the caller was never
granted — a motor an account cannot see must not be distinguishable from one
that does not exist).  It lives in its own module rather than in the 2 300-line
``family.py`` because it reads FOUR other routers' last-result stores, and that
import surface does not belong in the catalog CRUD file.

Unlike the datasheet, this export is not built only from the configuration's own
yaml: it also reads what every solver last answered — the electromagnetic
transient, the thermal map, the coupled loop, the rotor stress, the critical
speeds — and the SKF bearing model.  All of that is READ ONLY.  No solve is
started here, nothing is written, and a store that is empty becomes one short
"not solved yet" line in the document instead of a 500.

FORMAT.  ``docx`` is the DEFAULT since 2026-09-09 — user: *"репорт лучше
выдавать в формате doc"*, *"выводи всё-таки в doc формате"*.  He edits the
report before it goes to a client and Word exports its own PDF from it, so a
Word file is the useful artefact and the PDF is the last step of one.
``?format=pdf`` still serves exactly what it always did, byte for byte.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Response

from motor_ai_sim import report_progress as _RP

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/family", tags=["family"])


#: What each format ships as.  Word's media type is the long one because that is
#: what Office registers; a .docx served as octet-stream downloads fine but
#: opens in the wrong application on half the machines that get it by e-mail.
_MEDIA = {
    "docx": ("application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document"),
    "pdf": "application/pdf",
}

#: Everything a run id may NOT contain.  A uuid stem is what the client sends;
#: anything else is trimmed down to it rather than refused, because the id is a
#: nonce for a progress bar and a report is not worth failing over one.
_RUN_ID_OK = re.compile(r"[^A-Za-z0-9_.:-]")


@router.get("/report/progress")
def report_progress(run_id: str = Query(
        default="", description="the id the download was started with")
        ) -> Dict[str, Any]:
    """How far the build under ``run_id`` has got.  A STATUS READ.

    A second request, because the file itself is the download's own body and a
    50-second GET with nothing on the wire is exactly what the user reads as a
    hung server.  Ungated for the same reason the three solver progress routes
    are (``auth._GATED`` lists neither): the BUILD is gated, its counter is
    not, and a run id is an opaque nonce.

    An id the registry has never seen answers the idle shape with
    ``stage: "unknown"`` rather than 404 — the first poll routinely beats the
    build's first line, and a ring that error-flashes on that is worse than
    the silence it replaced.
    """
    return _RP.snapshot(run_id)


@router.get("/report/{die}/{cfg}")
def report(die: str, cfg: str, duty: Optional[str] = Query(default=None),
           format: str = Query(default="docx"),
           pictures: Optional[str] = Query(
               default=None,
               description="duty whose STORED fields the maps are drawn from; "
                           "default: the rated duty, then the loaded one"),
           run_id: Optional[str] = Query(
               default=None,
               description="id to publish this build's progress under; poll it "
                           "at /api/family/report/progress?run_id=…"),
           authorization: str = Header(default=None)):
    """Eight sections: the machine, and every solver's last answer about it.

    ``duty`` names the operating point the cover is about; the first saved duty
    is used when it is omitted.  A configuration with no duty at all is still a
    valid report — the electromagnetic section then quotes the last run on this
    server (flagged when that run was solved on a different machine), which is
    the case the datasheet refuses with a 400 because a duty-column spreadsheet
    would genuinely be empty.

    ``format`` is ``docx`` (the default) or ``pdf``.  Anything else is a 422
    naming the field, not a silent fallback: a caller who asked for ``doc`` or
    ``word`` and got a PDF would only find out when the client did.

    Costs no solve time: every figure comes out of a store some solver already
    wrote.
    """
    from motor_ai_sim.routes.family import (_check_name, _cfg_file, _die_file,
                                            _load_yaml, _require_die_access)

    fmt = str(format or "").strip().lower()
    if fmt not in _MEDIA:
        raise HTTPException(422, detail=[{
            "loc": ["query", "format"], "type": "value_error",
            "msg": ("format must be 'docx' (default) or 'pdf'; got %r"
                    % (format,))}])

    die, cfg = _check_name(die, "die"), _check_name(cfg, "configuration")
    _require_die_access(die, authorization)
    d = _load_yaml(_die_file(die), "die")
    c = _load_yaml(_cfg_file(die, cfg), "configuration")
    if duty:
        duty = _check_name(duty, "duty")
        if not any(str(x.get("name") or "") == duty
                   for x in (c.get("duties") or []) if isinstance(x, dict)):
            raise HTTPException(404, detail=f"duty '{duty}' not found in {die}/{cfg}")
    # `pictures` is the user's pick of WHICH duty's maps go into the document
    # (2026-09-11).  Same validation as `duty`: a name this configuration does
    # not have is a 404 naming it, not a silent fallback.
    if pictures:
        pictures = _check_name(pictures, "pictures")
        if not any(str(x.get("name") or "") == pictures
                   for x in (c.get("duties") or []) if isinstance(x, dict)):
            raise HTTPException(404, detail=f"duty '{pictures}' not found in "
                                            f"{die}/{cfg} (pictures)")

    # The bar this build publishes under.  The client sends an id so that it
    # can poll from the moment it clicks — before the server has answered
    # anything at all; one is minted here when it does not, so the route is
    # unchanged for curl and for the tests that predate the ring.
    # …and it is sanitised before it goes anywhere: it ends up in a response
    # HEADER, and a latin-1-hostile id would turn a finished 43-page report
    # into a 500 on the way out the door.
    rid = _RUN_ID_OK.sub("", str(run_id or "").strip())[:64] or uuid.uuid4().hex
    try:
        # Opened around the WHOLE build, so an exception finalises the entry
        # with its own sentence instead of leaving a ring spinning on a run
        # that died (progress entries are only evicted by TTL).
        with _RP.build(rid, fmt, key=f"{die}/{cfg}/{fmt}"):
            # Wire coating is pure geometry — measured here (cached), never a
            # solve.  Best-effort, exactly as the datasheet route treats it: a
            # CAD that cannot build the slot is one missing row, not a failed
            # export.
            try:
                from motor_ai_sim.masses import slot_fill_from_cad
                _geo = dict(d.get("geometry") or {})
                _geo.update(c.get("geometry_overrides") or {})
                _slot = slot_fill_from_cad(_geo)
            except Exception:                                 # noqa: BLE001
                _slot = None

            if fmt == "docx":
                from motor_ai_sim.report_docx import build_motor_report_docx
                blob = build_motor_report_docx(die=die, cfg=cfg, die_doc=d,
                                               cfg_doc=c, duty=duty,
                                               slot=_slot, pictures=pictures)
            else:
                from motor_ai_sim.report import build_motor_report
                blob = build_motor_report(die=die, cfg=cfg, die_doc=d,
                                          cfg_doc=c, duty=duty, slot=_slot,
                                          pictures=pictures)
    except HTTPException:
        raise
    except Exception as e:                                    # noqa: BLE001
        log.exception("report build failed for %s/%s (%s)", die, cfg, fmt)
        raise HTTPException(500, detail=f"report build failed: {e}")

    fname = f"{die} {cfg} report.{fmt}".replace('"', "")
    return Response(
        content=blob, media_type=_MEDIA[fmt],
        headers={"Content-Disposition": f'attachment; filename="{fname}"',
                 "Content-Length": str(len(blob)),
                 # Which bar this file came off — for a client that did not
                 # send an id, and for a log that has to match the two up.
                 "X-Run-Id": rid})
