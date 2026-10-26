"""NDAs signed OUTSIDE the platform, recorded before the person has an account (owner 2026-10-27: a customer that
signed its own NDA with us just registers, with no online click-through).

An administrator lists an e-mail address with the date and a reference of the paper / customer NDA
(``<shared>/legal/nda_preapproved.json``).  When that address signs in with a VERIFIED e-mail, the account is flagged
"NDA signed" exactly as Admin -> Users does by hand (``users.set_nda``, ``by="preapproved: <reference>"``) - the
administrator vouched for it, so ``verification.service.access_ok`` lets it in without the online click-through.
A listed address that already has an account is flagged at once.  Never raises at sign-in.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, Optional

log = logging.getLogger(__name__)

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def path() -> Path:
    from motor_ai_sim.nda import legal_root
    return legal_root() / "nda_preapproved.json"


def load() -> Dict[str, dict]:
    try:
        d = json.loads(path().read_text(encoding="utf-8")) or {}
        return {str(k).strip().lower(): v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001 - no file is the ordinary state
        return {}


def add(email: str, date: str, reference: str, by: str) -> dict:
    email = str(email or "").strip().lower()
    if not _EMAIL.fullmatch(email):
        raise ValueError("a valid e-mail address is required")
    if not _DATE.fullmatch(str(date or "")):
        raise ValueError("date must be YYYY-MM-DD")
    reference = " ".join(str(reference or "").split())[:200]
    if not reference:
        raise ValueError("reference of the NDA is required (e.g. 'customer NDA, accepted 2026-09-24')")
    store = load()
    store[email] = {"date": date, "reference": reference, "by": by, "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    apply(email)                      # an account that already exists is flagged at once
    return store[email]


def apply(email: str) -> Optional[dict]:
    """Flag ``email``'s account "NDA signed" when it is pre-approved and not yet flagged; returns the entry used."""
    try:
        from motor_ai_sim import users as U
        email = str(email or "").strip().lower()
        entry = load().get(email)
        rec = U.get_user(email)
        if not entry or rec is None:
            return None
        if (U.normalize_nda(rec.get("nda")) or {}).get("signed"):
            return None
        U.set_nda(email, True, entry.get("date"), by="preapproved: " + str(entry.get("reference") or ""))
        log.info("nda: pre-approved NDA applied to %s (%s)", email, entry.get("reference"))
        return entry
    except Exception:  # noqa: BLE001 - never blocks a sign-in
        log.exception("nda: pre-approved NDA could not be applied to %s", email)
        return None
