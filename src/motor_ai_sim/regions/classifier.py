"""Propose a home region for every account, with the reason (docs/regions/DESIGN.md, "Home region").

THE RULE (owner 2026-11-08: «американцы в Америке, все остальные пока в Европе»):

1. an admin override stays as it is;
2. otherwise the FIRST country found on record decides — ``US`` -> ``us``, any other country -> ``eu``.  Sources, in this
   order (the company first, then what the person stated):
     a. the directory company the account is CONFIRMED in (``platform_agreement.company_of``),
     b. the applicant-verification case (``application.country``),
     c. the Platform Agreement acceptance line (the country where the business is established),
     d. the account's signed NDA record,
     e. a ``country`` on the account record (profile);
3. no country anywhere -> the sign-up IP country, ONLY if the account record carries one (``signup_country``).  The app
   records the sign-up IP but no country today and this module adds no GeoIP database: stage 4 (edge routing) adds the
   country as a header set by the host nginx geo map, then new accounts carry it;
4. nothing -> ``eu``.

The report is a DRY RUN unless ``apply`` is asked for; ``apply`` writes only accounts whose proposal differs from what
is stored, never an admin's choice, and moves no data.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from motor_ai_sim.regions import config as RC
from motor_ai_sim.regions import store as RS

log = logging.getLogger(__name__)

US_COUNTRIES = frozenset({"US"})


def _cc(v: Any) -> str:
    s = str(v or "").strip().upper()
    return s if len(s) == 2 and s.isalpha() else ""


def _company_country(email: str, rec: dict) -> str:
    from motor_ai_sim import platform_agreement as PA
    c = PA.company_of(email)
    return _cc((c or {}).get("country"))


def _verification_country(email: str, rec: dict) -> str:
    from motor_ai_sim.verification import store as VS
    case = VS.latest_of(email)
    return _cc(((case or {}).get("application") or {}).get("country"))


def _agreement_country(email: str, rec: dict) -> str:
    from motor_ai_sim import legal_texts as LT
    for r in reversed(LT.records(email)):
        cc = _cc(r.get("country"))
        if cc:
            return cc
    return ""


def _nda_country(email: str, rec: dict) -> str:
    from motor_ai_sim import nda as N
    rid = (rec.get("nda") or {}).get("record_id") if isinstance(rec.get("nda"), dict) else ""
    r = N.get_record(rid) if rid else None
    return _cc((r or {}).get("country"))


def _profile_country(email: str, rec: dict) -> str:
    return _cc(rec.get("country"))


def _signup_country(email: str, rec: dict) -> str:
    return _cc(rec.get("signup_country"))


#: (reason code, lookup).  The order IS the rule; each lookup is isolated (a broken store is "no country", logged).
COUNTRY_SOURCES: List[Tuple[str, Callable[[str, dict], str]]] = [
    ("company", _company_country),
    ("verification", _verification_country),
    ("agreement", _agreement_country),
    ("nda", _nda_country),
    ("profile", _profile_country),
]
SIGNUP_SOURCE: Tuple[str, Callable[[str, dict], str]] = ("signup_ip_country", _signup_country)


def region_for_country(cc: str) -> str:
    return "us" if cc in US_COUNTRIES else "eu"


def propose(email: str, rec: Optional[dict] = None) -> Dict[str, Any]:
    """-> ``{email, current, source, proposed, reason, country, change}`` for one account."""
    from motor_ai_sim import users as U
    em = U._norm(email)
    rec = rec if isinstance(rec, dict) else (U.get_user(em) or {})
    current, source = RS.effective_region(rec), RS.region_source(rec)
    out = {"email": em, "current": current, "source": source}
    if source == "admin":
        return {**out, "proposed": current, "reason": "admin_override", "country": "", "change": False}
    for code, fn in [*COUNTRY_SOURCES, SIGNUP_SOURCE]:
        try:
            cc = fn(em, rec)
        except Exception as e:                              # noqa: BLE001 - one store's problem never stops the report
            log.warning("regions: %s lookup failed for an account (%s)", code, type(e).__name__)
            cc = ""
        if cc:
            proposed = region_for_country(cc)
            return {**out, "proposed": proposed, "reason": code, "country": cc,
                    "change": proposed != current or source == "default"}
    return {**out, "proposed": RC.DEFAULT_REGION, "reason": "no_country", "country": "",
            "change": current != RC.DEFAULT_REGION}


def classify_all(*, apply: bool = False, actor: str = "classifier") -> Dict[str, Any]:
    """The report for every account; with ``apply`` the changed proposals are stored (admin choices are kept)."""
    from motor_ai_sim import users as U
    rows = [propose(em, rec) for em, rec in sorted(U._load_soft().items()) if isinstance(rec, dict)]
    applied = 0
    if apply:
        for r in rows:
            if r["change"] and r["reason"] != "admin_override":
                res = RS.set_home_region(r["email"], r["proposed"], by="classifier", actor=actor,
                                         reason=f"{r['reason']}:{r['country'] or '-'}", respect_admin=True)
                applied += 1 if res["changed"] else 0
    by_reason: Dict[str, int] = {}
    for r in rows:
        by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
    return {"dry_run": not apply, "accounts": len(rows), "applied": applied,
            "proposed": {reg: sum(1 for r in rows if r["proposed"] == reg) for reg in RC.REGIONS},
            "changes": sum(1 for r in rows if r["change"]), "by_reason": by_reason, "rows": rows}
