"""The evidence record every source produces, and the vocabulary of checks the decision engine understands.

A source never decides anything: it returns :class:`Evidence` (what it asked, where, what came back, how strong a signal it is).
The engine (:mod:`motor_ai_sim.verification.engine`) turns evidence into a status.  Every evidence row is stored with the raw
(clipped) answer and the time, so a decision can be re-read years later.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

# results ---------------------------------------------------------------------------------------------------------------
PASS = "pass"                      # the source confirms the statement
FAIL = "fail"                      # the source answered and the statement is not true
CONFLICT = "conflict"              # the source answered and it contradicts what the applicant said (goes to a human)
UNKNOWN = "unknown"                # no answer / not found / could not tell - NEVER a pass
NOT_CONFIGURED = "not_configured"  # the adapter needs a key the operator has not set
MANUAL = "manual"                  # nothing automatic exists for this case (e.g. China): a person must look
RESULTS = (PASS, FAIL, CONFLICT, UNKNOWN, NOT_CONFIGURED, MANUAL)

# strength --------------------------------------------------------------------------------------------------------------
STRONG = "strong"
SUPPORTING = "supporting"
NONE = "none"

# kinds -----------------------------------------------------------------------------------------------------------------
COMPANY = "company"
PERSON = "person"
EMAIL = "email"
DOMAIN = "domain"
INFO = "info"

# the checks the engine reads (a new source may add its own; only these change a decision) -------------------------------
CHECK_REGISTRY_COMPANY = "registry.company"          # kind company: the official registry knows this company (registry_grade)
CHECK_REGISTRY_OFFICER = "registry.officer"          # kind person, strong: the applicant is a current officer
CHECK_EMAIL_VERIFIED = "email.verified"              # value = address; detail.method account | otp
CHECK_EMAIL_CORPORATE = "email.corporate"            # value = address; pass = neither free nor disposable
CHECK_EMAIL_DOMAIN = "email.domain_exists"
CHECK_EMAIL_MX = "email.mx"
CHECK_DOMAIN_MATCH = "domain.match_website"          # value = address; pass = same registrable domain as the official website
CHECK_SITE_RESOLVES = "website.resolves"
CHECK_SITE_NAMES_COMPANY = "website.names_company"
CHECK_SITE_SHOWS_REG = "website.shows_registration"
CHECK_SITE_LISTS_PERSON = "website.lists_person"     # kind person (nominal strong; the engine downgrades on an unconfirmed site)
CHECK_URL_SITE_PAGE = "person.site_page"             # a page the applicant pointed to, on the official domain, names them
CHECK_URL_THIRD_PARTY = "person.third_party_page"    # press / conference page naming the person and the company (supporting)
CHECK_LINKEDIN = "person.linkedin"                   # a LinkedIn profile URL whose slug matches the name (supporting, never alone)
CHECK_SOURCE_ERROR = "source.error"                  # an adapter raised: the case goes to a human


@dataclass
class Evidence:
    source: str
    check: str
    kind: str
    result: str
    strength: str = NONE
    value: str = ""
    summary: str = ""
    registry_grade: bool = False
    source_url: str = ""
    detail: dict = field(default_factory=dict)
    raw: Any = None
    at: float = field(default_factory=time.time)

    def to_dict(self, raw_limit: int = 20000) -> dict:
        d = asdict(self)
        raw = d.get("raw")
        if raw is not None:
            try:
                blob = json.dumps(raw, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                blob = str(raw)
            d["raw"] = raw if len(blob) <= raw_limit else {"clipped": blob[:raw_limit]}
        d["at"] = round(float(d["at"]), 3)
        return d


def from_dict(d: dict) -> Evidence:
    keys = Evidence.__dataclass_fields__
    return Evidence(**{k: v for k, v in d.items() if k in keys})
