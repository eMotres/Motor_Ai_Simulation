"""Applicant verification before confidential 3-D / STEP access (docs/APPLICANT_VERIFICATION.md).

Signing the NDA online no longer opens the 3-D tools and the STEP download by itself.  A case is opened from the signature, the
checks run (pluggable sources: e-mail, website, GLEIF, UK Companies House, EU VIES ...), a pure engine decides VERIFIED /
MORE_INFORMATION_REQUIRED / MANUAL_REVIEW, the applicant is asked for the specific missing proof, and an administrator can approve,
ask, deny or re-run.  Every step is an event in an append-only log.

The functions the rest of the application uses:

    access_ok(identity, user_rec)      the gate (model3d.can_download)
    public_access(identity, user_rec)  what the web shows (status line, link)
    on_nda_signed(user, record, body)  opens the case (routes/nda.py)
    note_download(row)                 puts a delivery into the case timeline (model3d.log_download)
"""
from motor_ai_sim.verification.engine import (  # noqa: F401
    DENIED, MANUAL_REVIEW, MORE_INFORMATION_REQUIRED, PENDING, STATUSES, VERIFIED, Decision, decide)
from motor_ai_sim.verification.service import (  # noqa: F401
    ActionError, access_ok, basis_of, ensure_case, governing_case, is_verified, note_download, on_nda_signed, public_access,
    reply_url, validate_intake)
