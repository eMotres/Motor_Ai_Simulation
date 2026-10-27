"""Admin routes of the owner report: a preview (nothing sent, no drafts made) and "send now" (to the owner's address only)."""
from __future__ import annotations

import re
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from motor_ai_sim import auth
from motor_ai_sim.owner_report import build as B
from motor_ai_sim.owner_report import run as R
from motor_ai_sim.owner_report import send as S

router = APIRouter(prefix="/api/admin/owner-report", tags=["owner-report"])
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _date(value: Optional[str]) -> Optional[str]:
    if value in (None, ""):
        return None
    if not _DATE.match(value):
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")
    try:
        B.window_for(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be a real calendar date")
    return value


@router.get("/preview")
def preview(date: Optional[str] = None, _admin: dict = Depends(auth.require_admin)):
    """The report as it would be mailed (text + HTML).  Reads only: no digest drafts are made, nothing is sent."""
    rep = R.build_report(_date(date), now=time.time(), make_drafts=False)
    return {"date": rep.date, "subject": rep.subject, "summary": rep.summary, "counts": rep.counts, "text": rep.text, "html": rep.html,
            "errors": rep.errors, "to": S.recipients(), "last_sent_date": S.get_state().get("last_sent_date")}


@router.post("/send")
async def send_now(request: Request, _admin: dict = Depends(auth.require_admin)):
    """Make the digest drafts, build the report and mail it NOW to the owner address only (never to anyone in the body)."""
    try:
        body = await request.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    res = R.run_daily(date=_date(body.get("date")), force=True)
    out = {k: v for k, v in res.items() if k != "report"}
    if res.get("report") is not None:
        out.update(summary=res["report"].summary, counts=res["report"].counts)
    if res["status"] == "failed":
        raise HTTPException(status_code=409 if res.get("error") == "smtp_not_configured" else 502, detail=out)
    return out
